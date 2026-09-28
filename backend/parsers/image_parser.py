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

# Preprocessing tuning constants
_MIN_WIDTH_PX = 1200       # upscale if narrower
_MAX_WIDTH_PX = 4000       # downscale if wider (prevents slow Tesseract on huge images)
_CONTRAST_ENHANCE = 1.6    # Contrast factor for normal images
_DARK_THRESHOLD = 127      # Mean pixel value below which image is considered dark/inverted


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
        for candidate_path in [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ]:
            if Path(candidate_path).exists():
                command = candidate_path
                break

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
            # Convert palette/RGBA/CMYK modes safely to RGB
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read image '{path}': the file may be corrupted or unreadable") from exc


def _deskew(pixels: np.ndarray) -> np.ndarray:
    """Detect and correct document skew using Hough line transform.

    Handles skew up to ~45 degrees. Returns the corrected grayscale image.
    Falls back to the original image if skew detection fails or produces a
    nonsensical angle.
    """
    try:
        # Use Canny edge detection then Hough lines to estimate rotation angle
        edges = cv2.Canny(pixels, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=100)
        if lines is None or len(lines) < 5:
            return pixels  # Not enough lines to estimate skew reliably

        angles = []
        for line in lines:
            rho, theta = line[0]
            # Keep only near-horizontal lines (within 10° of horizontal)
            angle_deg = np.degrees(theta) - 90
            if abs(angle_deg) < 10:
                angles.append(angle_deg)

        if not angles:
            return pixels

        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.3:
            return pixels  # Negligible skew — don't rotate

        h, w = pixels.shape
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
        rotated = cv2.warpAffine(
            pixels, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        LOGGER.debug("Deskewed image by %.2f degrees", median_angle)
        return rotated
    except Exception as exc:
        LOGGER.debug("Deskew failed (ignored): %s", exc)
        return pixels


def preprocess_image(image: Image.Image) -> Image.Image:
    """Prepare a document image for Tesseract OCR.

    Steps applied (in order):
      1. Convert to grayscale
      2. Resize: upscale narrow images, downscale very large images
      3. Detect and correct inverted (dark-background) images
      4. Enhance contrast
      5. Median denoise
      6. Otsu binarisation
      7. Deskew (rotation correction)
    """
    grayscale = image.convert("L")
    width, height = grayscale.size

    # ── Step 2: normalise resolution ────────────────────────────────────────
    if width < _MIN_WIDTH_PX:
        scale = _MIN_WIDTH_PX / max(width, 1)
        grayscale = grayscale.resize(
            (int(width * scale), int(height * scale)), Image.Resampling.LANCZOS
        )
        width, height = grayscale.size
    elif width > _MAX_WIDTH_PX:
        scale = _MAX_WIDTH_PX / width
        grayscale = grayscale.resize(
            (int(width * scale), int(height * scale)), Image.Resampling.LANCZOS
        )

    # ── Step 3: detect inverted (dark background) images ───────────────────
    pixel_array = np.asarray(grayscale)
    mean_intensity = float(pixel_array.mean())
    is_dark_bg = mean_intensity < _DARK_THRESHOLD
    if is_dark_bg:
        # Invert so that text becomes dark on white background
        grayscale = Image.fromarray(255 - pixel_array)
        LOGGER.debug("Detected dark-background image; applied inversion before binarisation")

    # ── Steps 4 + 5: contrast enhancement + median denoising ───────────────
    enhanced = ImageEnhance.Contrast(grayscale).enhance(_CONTRAST_ENHANCE)
    denoised = enhanced.filter(ImageFilter.MedianFilter(size=3))

    # ── Step 6: Otsu binarisation ───────────────────────────────────────────
    pixels = np.asarray(denoised)
    _, thresholded = cv2.threshold(pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # ── Step 7: deskew ──────────────────────────────────────────────────────
    corrected = _deskew(thresholded)

    return Image.fromarray(corrected)


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


class RapidOCREngine:
    """RapidOCR ONNX-backed engine (pure Python/ONNX, zero external binaries required)."""

    def __init__(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        except ImportError as exc:
            raise OCRUnavailableError("rapidocr-onnxruntime is not installed.") from exc

    def extract(self, image: Image.Image, language: str = DEFAULT_LANGUAGE) -> OCRResult:
        np_img = np.array(image.convert("RGB"))
        result, _ = self._engine(np_img)
        if not result:
            return OCRResult(text="", confidence_score=0.0)

        lines: list[str] = []
        confidences: list[float] = []
        for item in result:
            _, text, score = item
            lines.append(text)
            try:
                conf = float(score) * 100.0 if float(score) <= 1.0 else float(score)
                confidences.append(conf)
            except (ValueError, TypeError):
                pass

        avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else None
        return OCRResult(text="\n".join(lines).strip(), confidence_score=avg_conf)


def _get_default_ocr_engine() -> OCREngine:
    """Select the best available OCR engine: configured Tesseract or pure-Python RapidOCR."""
    load_dotenv(dotenv_path=PROJECT_ENV_FILE)
    cmd = os.getenv("TESSERACT_CMD")
    if cmd and Path(cmd).exists():
        try:
            _ensure_tesseract_available()
            return TesseractOCREngine()
        except Exception:
            pass

    try:
        return RapidOCREngine()
    except Exception:
        pass

    return TesseractOCREngine()


def extract_text_with_ocr(
    image: Image.Image,
    language: str = DEFAULT_LANGUAGE,
    engine: OCREngine | None = None,
) -> OCRResult:
    active_engine = engine or _get_default_ocr_engine()
    try:
        return active_engine.extract(image, language)
    except Exception as exc:
        # Fall back to RapidOCR if primary Tesseract failed
        if not isinstance(active_engine, RapidOCREngine):
            try:
                LOGGER.info("Falling back from %s to RapidOCREngine: %s", type(active_engine).__name__, exc)
                return RapidOCREngine().extract(image, language)
            except Exception:
                pass
        raise


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
