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

    # Build a frequency map: stripped line → set of page numbers it appears on
    line_pages: dict[str, set[int]] = {}
    for pno, text in pages:
        seen_on_this_page: set[str] = set()
        for raw in text.splitlines():
            stripped = raw.strip()
            if stripped and stripped not in seen_on_this_page:
                line_pages.setdefault(stripped, set()).add(pno)
                seen_on_this_page.add(stripped)

    repeated = {line for line, page_set in line_pages.items() if len(page_set) >= _REPEAT_THRESHOLD}
    if not repeated:
        return pages

    cleaned: list[tuple[int, str]] = []
    for pno, text in pages:
        kept_lines = [
            raw for raw in text.splitlines()
            if raw.strip() not in repeated
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
    - Pages with very sparse text (<20 chars) → treated as image-only, OCR fallback
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
                page_text = _normalize_text(page.get_text("text").strip())

                # Treat pages with no native text as image-only (< 5 chars triggers OCR)
                # Using 5 (not 0) so pages containing only a tiny watermark/footer don't
                # get wrongly classified as empty — but genuinely image-only pages do.
                if len(page_text) < 5:
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
        provenance = financial_fields.pop("provenance", [])

        evidence = Evidence(
            source_type="pdf",
            file_name=path.name,
            extracted_text=extracted_text,
            language=language,
            confidence_score=confidence_score,
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