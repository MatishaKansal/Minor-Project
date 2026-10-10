"""Voice and audio parsing with Speech-to-Text transcription and financial extraction."""

import io
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import speech_recognition as sr

from backend.schemas.evidence import Evidence, EvidenceProvenance
from backend.services.financial_field_extractor import extract_financial_fields

LOGGER = logging.getLogger(__name__)
SUPPORTED_VOICE_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif"}
DEFAULT_LANGUAGE = "en-US"


class STTUnavailableError(RuntimeError):
    """Raised when the Speech-to-Text engine encounters a service or initialization error."""


@dataclass
class STTResult:
    text: str
    confidence_score: float | None = None


class STTEngine(Protocol):
    """Protocol for pluggable Speech-to-Text engines."""

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> STTResult:
        ...


class _FlacEncodedAudioData(sr.AudioData):
    """AudioData that encodes FLAC through libsndfile, avoiding a flac.exe subprocess."""

    def get_flac_data(self, convert_rate: int | None = None, convert_width: int | None = None) -> bytes:
        import soundfile as sf

        wav_bytes = self.get_wav_data(convert_rate=convert_rate, convert_width=convert_width)
        samples, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="int16", always_2d=True)
        encoded = io.BytesIO()
        sf.write(encoded, samples, sample_rate, format="FLAC", subtype="PCM_16")
        return encoded.getvalue()


def _read_audio_data(audio_path: Path) -> _FlacEncodedAudioData:
    """Decode supported audio using libsndfile; normalize samples to mono PCM16."""
    import numpy as np
    import soundfile as sf

    samples, sample_rate = sf.read(str(audio_path), dtype="int16", always_2d=True)
    if samples.size == 0:
        raise ValueError(f"Audio file '{audio_path.name}' contains no audio samples")
    mono_samples = np.rint(samples.astype(np.int32).mean(axis=1))
    mono_samples = np.clip(mono_samples, -32768, 32767).astype("<i2")
    return _FlacEncodedAudioData(mono_samples.tobytes(), int(sample_rate), 2)


class SpeechRecognitionEngine:
    """Default STT engine using the SpeechRecognition library and Google Speech Recognition API."""

    def __init__(self) -> None:
        self.recognizer = sr.Recognizer()

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> STTResult:
        if not audio_path.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

        try:
            audio_data = _read_audio_data(audio_path)

            # Request detailed response with confidence score if available
            response = self.recognizer.recognize_google(audio_data, language=language, show_all=True)

            if not response or not isinstance(response, dict) or "alternative" not in response:
                # Fallback: simple text recognition
                simple_text = self.recognizer.recognize_google(audio_data, language=language)
                return STTResult(text=simple_text, confidence_score=None)

            alternatives = response.get("alternative", [])
            if not alternatives:
                raise ValueError("No speech could be recognized in the audio file")

            best = alternatives[0]
            transcript = best.get("transcript", "").strip()
            confidence = best.get("confidence")
            confidence_score = round(confidence * 100, 2) if confidence is not None else None

            return STTResult(text=transcript, confidence_score=confidence_score)

        except sr.UnknownValueError as exc:
            raise ValueError(f"Speech in audio file '{audio_path.name}' was unintelligible") from exc
        except sr.RequestError as exc:
            raise STTUnavailableError(f"Speech recognition service request failed: {exc}") from exc
        except ImportError as exc:
            raise STTUnavailableError(
                "Audio support dependencies are missing. Install them with: "
                "python -m pip install -r requirements.txt"
            ) from exc
        except Exception as exc:
            raise ValueError(f"Failed to process audio file '{audio_path.name}': {exc}") from exc


class MockSTTEngine:
    """Mock STT engine for offline deterministic testing."""

    def __init__(self, predefined_text: str = "Paid 50 dollars at Starbucks on 12/02/2026 for coffee", confidence_score: float = 95.0):
        self.predefined_text = predefined_text
        self.confidence_score = confidence_score

    def transcribe(self, audio_path: Path, language: str = DEFAULT_LANGUAGE) -> STTResult:
        if not audio_path.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {audio_path}")
        return STTResult(text=self.predefined_text, confidence_score=self.confidence_score)


def validate_voice_path(file_path: str | os.PathLike[str]) -> Path:
    """Validate that the given path exists and has a supported audio extension."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Audio file does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_VOICE_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format '{path.suffix}'. Supported formats: "
            f"{', '.join(sorted(SUPPORTED_VOICE_EXTENSIONS))}"
        )
    return path


def parse_voice_file(
    file_path: str | os.PathLike[str],
    engine: STTEngine | None = None,
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "voice",
) -> Evidence:
    """Transcribe an audio voice file and extract deterministic financial fields."""
    path = Path(file_path)
    try:
        validated_path = validate_voice_path(path)
        stt_engine = engine or SpeechRecognitionEngine()

        stt_result = stt_engine.transcribe(validated_path, language=language)
        transcribed_text = stt_result.text.strip()

        if not transcribed_text:
            raise ValueError(f"Audio file '{validated_path.name}' yielded an empty transcription")

        financial_fields = extract_financial_fields(transcribed_text)
        provenance = financial_fields.pop("provenance", [])

        # Add overall transcription provenance
        voice_provenance = [
            EvidenceProvenance(
                field_name="voice_transcription",
                value=transcribed_text,
                source_text=transcribed_text,
                line_number=1,
                page_number=1,
                extraction_method="speech_to_text",
                confidence=stt_result.confidence_score,
            )
        ]
        voice_provenance.extend([EvidenceProvenance.model_validate(item) for item in provenance])

        evidence = Evidence(
            source_type="voice",
            file_name=validated_path.name,
            extracted_text=transcribed_text,
            language=language,
            confidence_score=stt_result.confidence_score,
            processing_status="success",
            provenance=voice_provenance,
            **financial_fields,
        )

        if persist_to_database:
            from backend.database.supabase_repository import persist_voice_evidence

            database_ids = persist_voice_evidence(
                validated_path, evidence, business_id=business_id, file_type=file_type
            )
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
                "database_invoice_id": database_ids.get("invoice_id"),
            })

        return evidence

    except Exception as exc:
        LOGGER.exception("Voice parsing failed for %s", path)
        return Evidence(
            source_type="voice",
            file_name=path.name,
            language=language,
            processing_status="failed",
            error=str(exc),
        )


def parse_voice_bytes(
    audio_bytes: bytes,
    source_name: str = "voice_recording.wav",
    engine: STTEngine | None = None,
    language: str = DEFAULT_LANGUAGE,
    business_id: str | None = None,
    persist_to_database: bool = False,
    file_type: str = "voice",
) -> Evidence:
    """Parse raw audio bytes (e.g. from browser microphone recording)."""
    if not audio_bytes:
        return Evidence(
            source_type="voice",
            file_name=source_name,
            language=language,
            processing_status="failed",
            error="Audio byte buffer is empty",
        )

    suffix = Path(source_name).suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(audio_bytes)

    try:
        evidence = parse_voice_file(
            file_path=temp_path,
            engine=engine,
            language=language,
            business_id=business_id,
            persist_to_database=False,
            file_type=file_type,
        )
        # Rename temporary filename to source_name
        evidence = evidence.model_copy(update={"file_name": source_name})

        if evidence.processing_status == "success" and persist_to_database:
            from backend.database.supabase_repository import persist_voice_evidence

            database_ids = persist_voice_evidence(
                temp_path, evidence, source_name=source_name, business_id=business_id, file_type=file_type
            )
            evidence = evidence.model_copy(update={
                "database_file_id": database_ids["file_id"],
                "database_evidence_id": database_ids["evidence_id"],
                "database_invoice_id": database_ids.get("invoice_id"),
            })

        return evidence
    finally:
        if temp_path.exists():
            temp_path.unlink()
