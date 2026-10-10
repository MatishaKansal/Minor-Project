"""Text extraction and deterministic financial parsing for PDF documents with OCR fallback."""

import io
import logging
import os
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from backend.parsers.image_parser import OCREngine, extract_text_with_ocr, preprocess_image
from backend.schemas.evidence import Evidence, EvidenceProvenance
from backend.services.financial_field_extractor import extract_financial_fields, _normalize_text

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".pdf"}
DEFAULT_LANGUAGE = "eng"

# Lines shorter than this fraction of the longest line on a page are treated as
# potential header/footer candidates when they repeat across pages.
_HEADER_FOOTER_MAX_RATIO = 0.6
# A line must appear on at least this many pages to be considered a repeated header/footer.
_REPEAT_THRESHOLD = 2
_MIN_NATIVE_TEXT_CHARS = 5


def _normalise_repeated_line(line: str) -> str:
    """Create a stable key for repeated headers/footers despite PDF spacing noise."""
    return " ".join(line.split()).casefold()


def _extract_native_page_text(page: fitz.Page) -> str:
    """Extract text in visual order, falling back to PyMuPDF's plain text mode."""
    try:
        blocks = page.get_text("blocks")
    except (AttributeError, RuntimeError, TypeError):
        blocks = []

    text_blocks: list[tuple[float, float, str]] = []
    for block in blocks:
        if not isinstance(block, (tuple, list)) or len(block) < 5:
            continue
        block_text = block[4]
        if not isinstance(block_text, str) or not block_text.strip():
            continue
        x0, y0 = block[0], block[1]
        if not isinstance(x0, (int, float)) or not isinstance(y0, (int, float)):
            continue
        text_blocks.append((float(y0), float(x0), block_text.strip()))

    if text_blocks:
        text_blocks.sort(key=lambda item: (item[0], item[1]))
        return _normalize_text("\n".join(text for _, _, text in text_blocks).strip())

    raw_text = page.get_text("text")
    return _normalize_text(raw_text.strip() if isinstance(raw_text, str) else "")


def validate_pdf_path(file_path: str | os.PathLike[str]) -> Path:
    """Validate that the given path exists and has a .pdf extension."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported PDF format '{path.suffix}'. Supported formats: .pdf")
    return path


def _strip_repeated_headers_footers(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Remove lines that appear identically on more than one page (headers/footers).

    Only strips from multi-page documents. Single-page docs are returned unchanged.
    """
    if len(pages) < _REPEAT_THRESHOLD:
        return pages

    # Build a frequency map: normalised line → set of page numbers it appears on.
    line_pages: dict[str, set[int]] = {}
    for pno, text in pages:
        seen_on_this_page: set[str] = set()
        nonempty = [raw for raw in text.splitlines() if raw.strip()]
        edge_lines = set(nonempty[:5] + nonempty[-5:])
        longest_line = max((len(raw.strip()) for raw in nonempty), default=0)
        for raw in edge_lines:
            stripped = raw.strip()
            key = _normalise_repeated_line(stripped)
            if (
                key
                and key not in seen_on_this_page
                and (
                    len(nonempty) <= 5
                    or len(stripped) <= longest_line * _HEADER_FOOTER_MAX_RATIO
                )
            ):
                line_pages.setdefault(key, set()).add(pno)
                seen_on_this_page.add(key)

    repeated = {key for key, page_set in line_pages.items() if len(page_set) >= _REPEAT_THRESHOLD}
    if not repeated:
        return pages

    cleaned: list[tuple[int, str]] = []
    for pno, text in pages:
        nonempty = [raw for raw in text.splitlines() if raw.strip()]
        edge_lines = {
            _normalise_repeated_line(raw.strip())
            for raw in nonempty[:5] + nonempty[-5:]
        }
        kept_lines = [
            raw for raw in text.splitlines()
            if (
                _normalise_repeated_line(raw.strip()) not in repeated
                or _normalise_repeated_line(raw.strip()) not in edge_lines
            )
        ]
        cleaned.append((pno, "\n".join(kept_lines)))

    LOGGER.debug("Stripped %d repeated header/footer lines from multi-page PDF", len(repeated))
    return cleaned


def _try_decrypt(document: fitz.Document) -> None:
    """Try to open a permissions-only encrypted PDF with an empty password."""
    if document.is_encrypted:
        if document.authenticate(""):
            LOGGER.debug("PDF had permissions-only encryption; authenticated with empty password")
        else:
            raise ValueError(
                "PDF is password-protected. Provide the password to extract its contents."
            )


def _ocr_first_pdf_page(
    file_path: Path,
    language: str,
    ocr_engine: OCREngine | None,
) -> tuple[str, float | None]:
    """OCR page one when PDF text extraction loses invoice header values."""
    with fitz.open(file_path) as document:
        _try_decrypt(document)
        page = document.load_page(0)
        pixmap = page.get_pixmap(dpi=300)
        with Image.open(io.BytesIO(pixmap.tobytes("png"))) as page_image:
            prepared = preprocess_image(page_image.convert("RGB"))
        result = extract_text_with_ocr(prepared, language=language, engine=ocr_engine)
    return _normalize_text(result.text.strip()), result.confidence_score


def extract_pdf_pages(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    ocr_engine: OCREngine | None = None,
) -> tuple[list[tuple[int, str]], float | None]:
    """Return original or OCR text for each PDF page, retaining page order.

    Edge cases handled:
    - Zero-page PDFs → ValueError
    - Permissions-only encryption → authenticates with empty password
    - Password-protected PDFs → raises clear ValueError
    - Image-only pages (no native text) → renders at 300 DPI and runs OCR
    - Pages with very sparse text (<5 chars) → treated as image-only, OCR fallback
    - Repeated headers/footers → stripped from multi-page documents
    - BOM, null bytes, non-breaking spaces → normalised via _normalize_text
    """
    path = validate_pdf_path(file_path)
    pages: list[tuple[int, str]] = []
    ocr_confidences: list[float] = []

    try:
        with fitz.open(path) as document:
            _try_decrypt(document)

            if document.page_count == 0:
                raise ValueError("PDF contains no pages")

            for page_number in range(1, document.page_count + 1):
                page = document.load_page(page_number - 1)
                page_text = _extract_native_page_text(page)

                # A tiny text layer is commonly just a watermark or page number.
                if len(page_text.strip()) < _MIN_NATIVE_TEXT_CHARS:
                    LOGGER.info(
                        "No native text on page %d of %s; falling back to OCR",
                        page_number, path.name,
                    )
                    pixmap = page.get_pixmap(dpi=300)
                    image_bytes = pixmap.tobytes("png")
                    with Image.open(io.BytesIO(image_bytes)) as pil_img:
                        rgb_image = pil_img.convert("RGB")
                    prepared = preprocess_image(rgb_image)
                    ocr_res = extract_text_with_ocr(prepared, language=language, engine=ocr_engine)
                    page_text = _normalize_text(ocr_res.text.strip())
                    if ocr_res.confidence_score is not None:
                        ocr_confidences.append(ocr_res.confidence_score)

                pages.append((page_number, page_text))

    except (fitz.FileDataError, fitz.EmptyFileError, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read PDF '{path}': the file may be corrupted or unreadable") from exc

    if not any(text.strip() for _, text in pages):
        raise ValueError("PDF contains no extractable text or recognizable OCR content")

    # Strip lines that repeat across pages (page numbers, company headers, footers)
    pages = _strip_repeated_headers_footers(pages)

    avg_confidence = (
        round(sum(ocr_confidences) / len(ocr_confidences), 2) if ocr_confidences else None
    )
    return pages, avg_confidence


def parse_pdf(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "invoice",
    ocr_engine: OCREngine | None = None,
) -> Evidence:
    """Return a successful or failed Evidence response for one PDF with automatic OCR fallback."""
    path = Path(file_path)
    try:
        pages, confidence_score = extract_pdf_pages(path, language=language, ocr_engine=ocr_engine)
        extracted_text = "\n\n".join(text.rstrip("\n") for _, text in pages if text.strip())
        financial_fields = extract_financial_fields(pages)
        # Invoice PDFs often contain selectable text but have a broken reading
        # order that drops the invoice number and issue date. OCR only the first
        # page in that case and merge its header fields with the cleaner native
        # tax/item values from the PDF text layer.
        if (
            file_type in {"invoice", "tax_invoice", "credit_note", "debit_note"}
            and (
                not financial_fields.get("invoice_number")
                or not financial_fields.get("invoice_date")
                or len(pages[0][1]) < 80
            )
        ):
            # Native text layers can produce plausible but wrong values (for
            # example a vehicle number after the detached "Invoice No:" label).
            # OCR the first page and prefer its explicitly labeled header values.
            try:
                ocr_text, ocr_confidence = _ocr_first_pdf_page(path, language, ocr_engine)
                ocr_fields = extract_financial_fields([(1, ocr_text)])
            except (OSError, RuntimeError, ValueError) as exc:
                LOGGER.warning(
                    "Header OCR failed for %s; retaining native PDF extraction: %s",
                    path,
                    exc,
                )
            else:
                ocr_field_names = {"invoice_number", "invoice_date", "date", "place_of_supply", "buyer_name"}
                for field_name in ocr_field_names:
                    if ocr_fields.get(field_name):
                        financial_fields[field_name] = ocr_fields[field_name]
                # Keep generic date in sync with the selected invoice date.
                if financial_fields.get("invoice_date"):
                    financial_fields["date"] = financial_fields["invoice_date"]
                ocr_provenance = [
                    {**item, "confidence": ocr_confidence}
                    for item in ocr_fields.get("provenance", [])
                    if item.get("field_name") in ocr_field_names
                ]
                retained_provenance = [
                    item for item in financial_fields.get("provenance", [])
                    if item.get("field_name") not in ocr_field_names
                ]
                financial_fields["provenance"] = retained_provenance + ocr_provenance
                if ocr_confidence is not None:
                    confidence_values = [
                        value for value in (confidence_score, ocr_confidence)
                        if value is not None
                    ]
                    confidence_score = round(
                        sum(confidence_values) / len(confidence_values), 2
                    )
                if ocr_text.strip() and ocr_text.strip() not in extracted_text:
                    extracted_text = f"{ocr_text}\n\n{extracted_text}"
        provenance = financial_fields.pop("provenance", [])

        evidence = Evidence(
            source_type="pdf",
            file_name=path.name,
            extracted_text=extracted_text,
            language=language,
            confidence_score=confidence_score,
            page_count=len(pages),
            processing_status="success",
            provenance=[EvidenceProvenance.model_validate(item) for item in provenance],
            **financial_fields,
        )

        if persist_to_database:
            from backend.database.supabase_repository import persist_evidence

            database_ids = persist_evidence(path, evidence, business_id, file_type)
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
                "database_invoice_id": database_ids.get("invoice_id"),
            })

        return evidence

    except Exception as exc:
        LOGGER.exception("PDF parsing failed for %s", path)
        return Evidence(
            source_type="pdf",
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=str(exc),
        )
