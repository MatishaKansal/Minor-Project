"""Text parsing and deterministic financial extraction for plain text and .txt files."""

import logging
import os
from pathlib import Path

from backend.schemas.evidence import Evidence, EvidenceProvenance
from backend.services.financial_field_extractor import extract_financial_fields

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".txt"}
DEFAULT_LANGUAGE = "eng"


def validate_txt_path(file_path: str | os.PathLike[str]) -> Path:
    """Validate that the given path exists and is a .txt file."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Text file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported text format '{path.suffix}'. Supported formats: .txt")
    return path


def parse_text(
    text: str,
    source_name: str = "chat_input",
    source_type: str = "text",
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "invoice",
) -> Evidence:
    """Parse raw text string (e.g. from chatbox or direct API input)."""
    try:
        if not text or not text.strip():
            raise ValueError("Input text contains no extractable content or is empty")

        clean_text = text.strip()
        financial_fields = extract_financial_fields(clean_text)
        provenance = financial_fields.pop("provenance", [])

        evidence = Evidence(
            source_type=source_type,
            file_name=source_name,
            extracted_text=clean_text,
            language=language,
            processing_status="success",
            provenance=[EvidenceProvenance.model_validate(item) for item in provenance],
            **financial_fields,
        )

        if persist_to_database:
            from backend.database.supabase_repository import persist_text_evidence

            database_ids = persist_text_evidence(
                clean_text, evidence, source_name=source_name, business_id=business_id, file_type=file_type
            )
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
                "database_invoice_id": database_ids.get("invoice_id"),
            })

        return evidence
    except Exception as exc:
        LOGGER.exception("Text parsing failed for source '%s'", source_name)
        return Evidence(
            source_type=source_type,
            file_name=source_name,
            language=language,
            processing_status="failed",
            error=str(exc),
        )


def parse_txt_file(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "invoice",
) -> Evidence:
    """Read and parse a .txt file from disk, returning an Evidence model."""
    path = Path(file_path)
    try:
        validated_path = validate_txt_path(path)
        try:
            content = validated_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Fallback to reading with latin-1 if utf-8 fails
            content = validated_path.read_text(encoding="latin-1")

        if not content.strip():
            raise ValueError(f"Text file '{validated_path.name}' is empty")

        evidence = parse_text(
            text=content,
            source_name=validated_path.name,
            source_type="txt",
            language=language,
            business_id=business_id,
            file_type=file_type,
        )

        if evidence.processing_status == "success" and persist_to_database:
            from backend.database.supabase_repository import persist_evidence

            database_ids = persist_evidence(validated_path, evidence, business_id, file_type)
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
                "database_invoice_id": database_ids.get("invoice_id"),
            })

        return evidence

    except Exception as exc:
        LOGGER.exception("Text file parsing failed for %s", path)
        return Evidence(
            source_type="txt",
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=str(exc),
        )
