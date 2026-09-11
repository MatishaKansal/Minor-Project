"""Text extraction and deterministic financial parsing for PDF documents."""

import logging
import os
from pathlib import Path

import fitz

from backend.schemas.evidence import Evidence, EvidenceProvenance
from backend.services.financial_field_extractor import extract_financial_fields

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".pdf"}
DEFAULT_LANGUAGE = "eng"


def validate_pdf_path(file_path: str | os.PathLike[str]) -> Path:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported PDF format '{path.suffix}'. Supported formats: .pdf")
    return path


def extract_pdf_pages(file_path: str | os.PathLike[str]) -> list[tuple[int, str]]:
    """Return original text for each PDF page, retaining page order."""
    path = validate_pdf_path(file_path)
    try:
        with fitz.open(path) as document:
            if document.page_count == 0:
                raise ValueError("PDF contains no pages")
            pages = [(page_number, document.load_page(page_number - 1).get_text("text"))
                     for page_number in range(1, document.page_count + 1)]
    except (fitz.FitzError, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read PDF '{path}': the file may be corrupted or unreadable") from exc
    if not any(text.strip() for _, text in pages):
        raise ValueError("PDF contains no extractable text; image-only PDFs are not supported")
    return pages


def parse_pdf(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "invoice",
) -> Evidence:
    """Return a successful or failed Evidence response for one PDF."""
    path = Path(file_path)
    try:
        pages = extract_pdf_pages(path)
        extracted_text = "\n\n".join(text.rstrip("\n") for _, text in pages)
        financial_fields = extract_financial_fields(pages)
        provenance = financial_fields.pop("provenance", [])
        evidence = Evidence(
            source_type="pdf",
            file_name=path.name,
            extracted_text=extracted_text,
            language=language,
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