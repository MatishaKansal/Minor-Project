"""Unified file ingestion service for multimodal financial evidence (images, PDFs, text, bank CSVs, voice)."""

import logging
import os
from pathlib import Path
from typing import Literal

from backend.parsers.bank_csv_parser import (
    SUPPORTED_EXTENSIONS as CSV_EXTENSIONS,
    parse_bank_csv_file,
)
from backend.parsers.image_parser import (
    SUPPORTED_EXTENSIONS as IMAGE_EXTENSIONS,
    parse_image,
)
from backend.parsers.pdf_parser import (
    SUPPORTED_EXTENSIONS as PDF_EXTENSIONS,
    parse_pdf,
)
from backend.parsers.text_parser import (
    SUPPORTED_EXTENSIONS as TEXT_EXTENSIONS,
    parse_txt_file,
)
from backend.parsers.voice_parser import (
    SUPPORTED_VOICE_EXTENSIONS as VOICE_EXTENSIONS,
    parse_voice_file,
)
from backend.schemas.evidence import Evidence

LOGGER = logging.getLogger(__name__)

ALL_SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS | PDF_EXTENSIONS | TEXT_EXTENSIONS | CSV_EXTENSIONS | VOICE_EXTENSIONS
)


def detect_file_type(
    file_path: str | os.PathLike[str],
) -> Literal["image", "pdf", "text", "bank_csv", "voice", "unsupported"]:
    """Detect whether a file is a supported image, PDF, text, bank CSV, voice, or unsupported."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in CSV_EXTENSIONS:
        return "bank_csv"
    if suffix in VOICE_EXTENSIONS:
        return "voice"
    return "unsupported"


def ingest_file(
    file_path: str | os.PathLike[str],
    persist_to_database: bool = False,
    business_id: str | None = None,
    file_type: str | None = None,
    language: str = "eng",
) -> Evidence:
    """Auto-detect document type, dispatch to the correct parser, and return Evidence.
    
    Parameters:
        file_path: Path to the image, PDF, text, bank CSV, or audio voice document.
        persist_to_database: If True, persists evidence and uploaded file to Supabase.
        business_id: Optional UUID of the business context.
        file_type: Optional document type label ('receipt', 'invoice', etc.). Defaults to
                   context-appropriate type if not provided.
        language: OCR / Speech recognition language code.
    """
    path = Path(file_path)
    if not path.is_file():
        return Evidence(
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=f"File does not exist: {path}",
        )

    doc_type = detect_file_type(path)

    if doc_type == "pdf":
        effective_type = file_type or "invoice"
        return parse_pdf(
            file_path=path,
            language=language,
            business_id=business_id,
            persist_to_database=persist_to_database,
            file_type=effective_type,
        )

    if doc_type == "image":
        effective_type = file_type or "receipt"
        return parse_image(
            file_path=path,
            language=language,
            business_id=business_id,
            persist_to_database=persist_to_database,
            file_type=effective_type,
        )

    if doc_type == "text":
        effective_type = file_type or "invoice"
        return parse_txt_file(
            file_path=path,
            language=language,
            business_id=business_id,
            persist_to_database=persist_to_database,
            file_type=effective_type,
        )

    if doc_type == "bank_csv":
        effective_type = file_type or "bank_statement"
        return parse_bank_csv_file(
            file_path=path,
            language=language,
            business_id=business_id,
            persist_to_database=persist_to_database,
            file_type=effective_type,
        )

    if doc_type == "voice":
        effective_type = file_type or "voice"
        return parse_voice_file(
            file_path=path,
            language="en-US" if language == "eng" else language,
            business_id=business_id,
            persist_to_database=persist_to_database,
            file_type=effective_type,
        )

    supported = ", ".join(sorted(ALL_SUPPORTED_EXTENSIONS))
    return Evidence(
        file_name=path.name,
        language=language,
        processing_status="failed",
        error=f"Unsupported file format '{path.suffix}'. Supported formats: {supported}",
    )


def ingest_directory(
    directory_path: str | os.PathLike[str],
    persist_to_database: bool = False,
    business_id: str | None = None,
    language: str = "eng",
) -> list[Evidence]:
    """Process all supported documents in a directory and return a list of Evidence."""
    directory = Path(directory_path)
    if not directory.is_dir():
        raise NotADirectoryError(f"Directory does not exist: {directory}")

    files = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in ALL_SUPPORTED_EXTENSIONS
        ),
        key=lambda p: p.name.lower(),
    )

    results: list[Evidence] = []
    for file_path in files:
        results.append(
            ingest_file(
                file_path=file_path,
                persist_to_database=persist_to_database,
                business_id=business_id,
                language=language,
            )
        )
    return results
