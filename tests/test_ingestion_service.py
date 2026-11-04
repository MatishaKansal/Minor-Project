"""Unit tests for the unified ingestion service and router."""

from pathlib import Path
import fitz
from PIL import Image

from backend.schemas.evidence import Evidence
from backend.services.ingestion_service import (
    ALL_SUPPORTED_EXTENSIONS,
    detect_file_type,
    ingest_directory,
    ingest_file,
)


def write_test_pdf(path: Path, pages: list[str]) -> None:
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_detect_file_type():
    assert detect_file_type("invoice.pdf") == "pdf"
    assert detect_file_type("document.PDF") == "pdf"
    assert detect_file_type("receipt.png") == "image"
    assert detect_file_type("receipt.jpg") == "image"
    assert detect_file_type("receipt.jpeg") == "image"
    assert detect_file_type("receipt.webp") == "image"
    assert detect_file_type("receipt.tiff") == "image"
    assert detect_file_type("notes.txt") == "text"
    assert detect_file_type("data.csv") == "bank_csv"
    assert detect_file_type("statement.tsv") == "bank_csv"
    assert detect_file_type("voice.aac") == "voice"
    assert detect_file_type("voice.wav") == "voice"
    assert detect_file_type("doc.docx") == "unsupported"
    assert detect_file_type("file.bin") == "unsupported"


def test_ingest_nonexistent_file_returns_failed_evidence(tmp_path):
    missing_path = tmp_path / "does_not_exist.pdf"
    result = ingest_file(missing_path)

    assert isinstance(result, Evidence)
    assert result.processing_status == "failed"
    assert "does not exist" in result.error.lower()


def test_ingest_unsupported_file_returns_failed_evidence(tmp_path):
    bad_file = tmp_path / "notes.docx"
    bad_file.write_text("Some text here")

    result = ingest_file(bad_file)

    assert isinstance(result, Evidence)
    assert result.processing_status == "failed"
    assert "unsupported file format" in result.error.lower()


def test_ingest_valid_pdf_routes_to_pdf_parser(tmp_path):
    pdf_path = tmp_path / "test_invoice.pdf"
    write_test_pdf(pdf_path, [
        "Vendor: Tech Solutions Inc.\nInvoice No: TS-9901\nDate: 2026-09-15\nTOTAL: USD 450.00"
    ])

    result = ingest_file(pdf_path)

    assert isinstance(result, Evidence)
    assert result.processing_status == "success"
    assert result.source_type == "pdf"
    assert result.party_name == "Tech Solutions Inc."
    assert result.amount == "450.00"
    assert result.currency == "USD"
    assert result.invoice_number == "TS-9901"
    assert result.date == "2026-09-15"


def test_ingest_directory_processes_all_supported_files(tmp_path):
    pdf1 = tmp_path / "doc1.pdf"
    pdf2 = tmp_path / "doc2.pdf"
    unsupported = tmp_path / "ignored.docx"

    write_test_pdf(pdf1, ["Vendor: Alpha Corp\nTOTAL: USD 100.00"])
    write_test_pdf(pdf2, ["Vendor: Beta LLC\nTOTAL: USD 200.00"])
    unsupported.write_text("Ignore this")

    results = ingest_directory(tmp_path)

    assert len(results) == 2
    assert {r.file_name for r in results} == {"doc1.pdf", "doc2.pdf"}
    assert all(r.processing_status == "success" for r in results)
