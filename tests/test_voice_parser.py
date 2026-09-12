import wave
import struct
from pathlib import Path
import pytest

from backend.parsers.voice_parser import (
    MockSTTEngine,
    SpeechRecognitionEngine,
    parse_voice_bytes,
    parse_voice_file,
)
from backend.schemas.evidence import Evidence, EvidenceProvenance


def _create_dummy_wav(path: Path, duration_seconds: float = 0.5):
    """Generate a minimal valid PCM WAV file for file-validation and reader tests."""
    sample_rate = 16000
    num_samples = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)
        # Write silent samples
        data = struct.pack(f"<{num_samples}h", *([0] * num_samples))
        wav_file.writeframes(data)


def test_parse_voice_file_with_mock_engine(tmp_path: Path):
    wav_path = tmp_path / "expense_note.wav"
    _create_dummy_wav(wav_path)

    mock_text = "I paid 45 dollars at Starbucks on 12/02/2026 for coffee"
    mock_engine = MockSTTEngine(predefined_text=mock_text, confidence_score=94.5)

    result = parse_voice_file(wav_path, engine=mock_engine)

    assert result.processing_status == "success"
    assert result.source_type == "voice"
    assert result.file_name == "expense_note.wav"
    assert result.extracted_text == mock_text
    assert result.confidence_score == 94.5

    assert result.amount == "45.00"
    assert result.currency == "USD"
    assert result.party_name == "Starbucks"
    assert result.description == "coffee"
    assert result.date == "12/02/2026"

    assert len(result.provenance) >= 2
    transcription_prov = result.provenance[0]
    assert transcription_prov.field_name == "voice_transcription"
    assert transcription_prov.extraction_method == "speech_to_text"
    assert transcription_prov.confidence == 94.5


def test_parse_voice_bytes_with_mock_engine():
    audio_bytes = b"RIFF....WAVEfmt ...."
    mock_text = "Transferred 1200 euros to Shell station on 15/08/2025 for fuel"
    mock_engine = MockSTTEngine(predefined_text=mock_text, confidence_score=98.0)

    result = parse_voice_bytes(
        audio_bytes,
        source_name="mic_recording.wav",
        engine=mock_engine,
    )

    assert result.processing_status == "success"
    assert result.source_type == "voice"
    assert result.file_name == "mic_recording.wav"
    assert result.amount == "1200.00"
    assert result.currency == "EUR"
    assert result.party_name == "Shell station"
    assert result.description == "fuel"


def test_conversational_received_payment_voice_pattern(tmp_path: Path):
    wav_path = tmp_path / "invoice_received.wav"
    _create_dummy_wav(wav_path)

    mock_text = "Received payment of 3500 dollars from ACME Corp for invoice INV-404"
    mock_engine = MockSTTEngine(predefined_text=mock_text)

    result = parse_voice_file(wav_path, engine=mock_engine)

    assert result.processing_status == "success"
    assert result.amount == "3500.00"
    assert result.currency == "USD"
    assert result.party_name == "ACME Corp"
    assert result.invoice_number == "INV-404"


def test_parse_voice_file_empty_transcription_fails(tmp_path: Path):
    wav_path = tmp_path / "silent.wav"
    _create_dummy_wav(wav_path)

    mock_engine = MockSTTEngine(predefined_text="   ")

    result = parse_voice_file(wav_path, engine=mock_engine)

    assert result.processing_status == "failed"
    assert result.error is not None
    assert "empty" in result.error.lower()


def test_parse_nonexistent_voice_file_fails(tmp_path: Path):
    missing_file = tmp_path / "missing_recording.wav"

    result = parse_voice_file(missing_file)

    assert result.processing_status == "failed"
    assert "does not exist" in result.error


def test_parse_unsupported_voice_format_fails(tmp_path: Path):
    txt_file = tmp_path / "note.txt"
    txt_file.write_text("dummy", encoding="utf-8")

    result = parse_voice_file(txt_file)

    assert result.processing_status == "failed"
    assert "Unsupported audio format" in result.error


def test_parse_voice_persists_to_supabase_with_mock(tmp_path: Path, monkeypatch):
    import backend.database.supabase_repository as repo

    def mock_persist_voice_evidence(audio_path_or_bytes, evidence, source_name="voice_note.wav", business_id=None, file_type="voice", client=None):
        return {"file_id": "voice-file-uuid-77", "evidence_id": "voice-evidence-uuid-88"}

    monkeypatch.setattr(repo, "persist_voice_evidence", mock_persist_voice_evidence)

    wav_path = tmp_path / "voice_sample.wav"
    _create_dummy_wav(wav_path)

    mock_engine = MockSTTEngine("Spent 25 dollars at Walmart on 2026-05-01")
    result = parse_voice_file(wav_path, engine=mock_engine, persist_to_database=True)

    assert result.processing_status == "success"
    assert result.database_file_id == "voice-file-uuid-77"
    assert result.database_evidence_id == "voice-evidence-uuid-88"


def test_speech_recognition_engine_handles_valid_wav_structure(tmp_path: Path):
    wav_path = tmp_path / "test_pcm.wav"
    _create_dummy_wav(wav_path)

    engine = SpeechRecognitionEngine()
    # Ensure recognizer reads the audio file format without crashing
    with pytest.raises(Exception) as exc_info:
        # A silent wav won't have recognized words, expecting ValueError / UnknownValueError / RequestError
        engine.transcribe(wav_path)
    assert any(term in str(exc_info.value).lower() for term in ["unintelligible", "service", "failed", "speech"])
