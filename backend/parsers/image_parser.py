"""Image validation, preprocessing, and Tesseract OCR."""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, UnidentifiedImageError
from dotenv import load_dotenv
from pytesseract import Output

from backend.schemas.evidence import Evidence, EvidenceProvenance

LOGGER = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
DEFAULT_LANGUAGE = "eng"
PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class OCRUnavailableError(RuntimeError):
    """Raised when the configured Tesseract executable cannot be used."""


@dataclass
class OCRResult:
    text: str
    confidence_score: float | None


class OCREngine(Protocol):
    def extract(self, image: Image.Image, language: str) -> OCRResult:
        ...


def _configure_tesseract() -> None:
    load_dotenv(dotenv_path=PROJECT_ENV_FILE)
    command = os.getenv("TESSERACT_CMD")
    if not command:
        raise OCRUnavailableError(
            f"TESSERACT_CMD is missing. Add TESSERACT_CMD=<path to tesseract.exe> "
            f"to {PROJECT_ENV_FILE}."
        )

    executable = Path(command)
    if not executable.exists():
        raise OCRUnavailableError(
            f"TESSERACT_CMD points to a missing executable: {command}. "
            "Update TESSERACT_CMD in the project .env file."
        )

    pytesseract.pytesseract.tesseract_cmd = command
    LOGGER.info("Configured Tesseract executable: %s", command)


def _ensure_tesseract_available() -> None:
    _configure_tesseract()
    try:
        pytesseract.get_tesseract_version()
    except (pytesseract.TesseractNotFoundError, OSError) as exc:
        raise OCRUnavailableError(
            f"Configured Tesseract executable could not be started: "
            f"{pytesseract.pytesseract.tesseract_cmd}. Verify TESSERACT_CMD in "
            f"{PROJECT_ENV_FILE}."
        ) from exc


def validate_image_path(file_path: str | os.PathLike[str]) -> Path:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Image file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported image format '{path.suffix}'. Supported formats: {supported}")
    return path


def load_image(file_path: str | os.PathLike[str]) -> Image.Image:
    path = validate_image_path(file_path)
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read image '{path}': the file may be corrupted or unreadable") from exc


def preprocess_image(image: Image.Image) -> Image.Image:
    """Prepare a document image without changing its readable content."""
    grayscale = image.convert("L")
    width, height = grayscale.size
    if width < 1200:
        scale = 1200 / max(width, 1)
        grayscale = grayscale.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    enhanced = ImageEnhance.Contrast(grayscale).enhance(1.6)
    denoised = enhanced.filter(ImageFilter.MedianFilter(size=3))
    pixels = np.asarray(denoised)
    _, thresholded = cv2.threshold(pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(thresholded)


def _confidence_from_data(data: dict[str, list[Any]]) -> float | None:
    values: list[float] = []
    for value, text in zip(data.get("conf", []), data.get("text", [])):
        if str(text).strip():
            try:
                confidence = float(value)
            except (TypeError, ValueError):
                continue
            if confidence >= 0:
                values.append(confidence)
    return round(sum(values) / len(values), 2) if values else None


from backend.services.financial_field_extractor import (
    FieldSource,
    _extract_currency,
    _extract_document_number,
    _extract_monetary_candidates,
    _extract_party_name,
    _first_match,
    extract_final_amount,
    extract_financial_fields,
)


def _extract_total(lines: list[tuple[int, str, str]]) -> tuple[str | None, tuple[int, str] | None]:
    raw_text = "\n".join(raw for _, raw, _ in lines)
    val, src, _ = extract_final_amount(raw_text)
    return (val, (src.line_number, src.source_text)) if src else (None, None)



class TesseractOCREngine:
    """Tesseract-backed OCR adapter, replaceable by another OCREngine later."""

    def extract(self, image: Image.Image, language: str = DEFAULT_LANGUAGE) -> OCRResult:
        _ensure_tesseract_available()
        config = "--psm 6"
        text = pytesseract.image_to_string(image, lang=language, config=config)
        data = pytesseract.image_to_data(image, lang=language, config=config, output_type=Output.DICT)
        return OCRResult(text=text.strip(), confidence_score=_confidence_from_data(data))


def extract_text_with_ocr(
    image: Image.Image,
    language: str = DEFAULT_LANGUAGE,
    engine: OCREngine | None = None,
) -> OCRResult:
    return (engine or TesseractOCREngine()).extract(image, language)


def extract_image_text(file_path: str | os.PathLike[str], language: str = DEFAULT_LANGUAGE) -> OCRResult:
    image = load_image(file_path)
    prepared = preprocess_image(image)
    return extract_text_with_ocr(prepared, language)


def parse_image(
    file_path: str | os.PathLike[str],
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "receipt",
) -> Evidence:
    """Return a successful or failed Evidence response; image failures are isolated."""
    path = Path(file_path)
    try:
        result = extract_image_text(path, language)
        LOGGER.info("OCR completed for %s", path)
        financial_fields = extract_financial_fields(result.text)
        provenance = financial_fields.pop("provenance", [])
        evidence = Evidence(
            file_name=path.name,
            extracted_text=result.text,
            language=language,
            confidence_score=result.confidence_score,
            processing_status="success",
            provenance=[EvidenceProvenance.model_validate(item) for item in provenance],
            **financial_fields,
        )
        if persist_to_database:
            from backend.database.supabase_repository import persist_image_evidence

            database_ids = persist_image_evidence(path, evidence, business_id, file_type)
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
            })
        return evidence
    except Exception as exc:  # OCR is a batch boundary: one bad file must not stop the batch.
        LOGGER.exception("OCR failed for %s", path)
        return Evidence(
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=str(exc),
        )


def parse_images_for_business(
    business_id: str | None,
    image_directory: str | os.PathLike[str],
    file_type: str = "receipt",
) -> list[Evidence]:
    """Parse and persist every supported image as a document for one business."""
    directory = Path(image_directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"Image directory does not exist: {directory}")

    image_paths = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )
    results: list[Evidence] = []
    for image_path in image_paths:
        results.append(
            parse_image(
                image_path,
                business_id=business_id,
                persist_to_database=True,
                file_type=file_type,
            )
        )
    return results
