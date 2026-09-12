import pytest
from pathlib import Path

from backend.parsers.text_parser import parse_text, parse_txt_file
from backend.schemas.evidence import Evidence, EvidenceProvenance


def test_parse_raw_text_chat_input_extracts_fields():
    chat_message = (
        "Vendor: ACME Supplies\n"
        "Invoice No: INV-2026-001\n"
        "Date: 2026-09-09\n"
        "Description: Office supplies\n"
        "TOTAL: USD 125.50"
    )

    result = parse_text(chat_message, source_name="chat_message_1", source_type="text")

    assert result.processing_status == "success"
    assert result.source_type == "text"
    assert result.file_name == "chat_message_1"
    assert result.date == "2026-09-09"
    assert result.amount == "125.50"
    assert result.currency == "USD"
    assert result.party_name == "ACME Supplies"
    assert result.invoice_number == "INV-2026-001"
    assert result.description == "Office supplies"
    assert all(isinstance(item, EvidenceProvenance) for item in result.provenance)

    amount_provenance = next(item for item in result.provenance if item.field_name == "amount")
    assert amount_provenance.source_text == "TOTAL: USD 125.50"
    assert amount_provenance.line_number == 5
    assert amount_provenance.page_number == 1


def test_parse_txt_file_extracts_fields_and_provenance(tmp_path: Path):
    txt_file = tmp_path / "sample_invoice.txt"
    txt_file.write_text(
        "Merchant: SuperMart Store\n"
        "Receipt #: REC-99481\n"
        "Date: 15/08/2025\n"
        "Description: Groceries and beverages\n"
        "SUBTOTAL: EUR 45.00\n"
        "TAX: EUR 4.50\n"
        "AMOUNT PAYABLE: EUR 49.50\n",
        encoding="utf-8",
    )

    result = parse_txt_file(txt_file)

    assert result.processing_status == "success"
    assert result.source_type == "txt"
    assert result.file_name == "sample_invoice.txt"
    assert result.date == "15/08/2025"
    assert result.amount == "49.50"
    assert result.currency == "EUR"
    assert result.party_name == "SuperMart Store"
    assert result.invoice_number == "REC-99481"
    assert result.description == "Groceries and beverages"

    amount_provenance = next(item for item in result.provenance if item.field_name == "amount")
    assert amount_provenance.source_text == "AMOUNT PAYABLE: EUR 49.50"
    assert amount_provenance.line_number == 7


def test_parse_empty_text_fails_gracefully():
    result = parse_text("   \n\t  ")

    assert result.processing_status == "failed"
    assert result.source_type == "text"
    assert result.error is not None
    assert "empty" in result.error.lower()


def test_parse_empty_txt_file_fails_gracefully(tmp_path: Path):
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n", encoding="utf-8")

    result = parse_txt_file(empty_file)

    assert result.processing_status == "failed"
    assert result.source_type == "txt"
    assert result.error is not None
    assert "empty" in result.error.lower()


def test_parse_nonexistent_txt_file_fails_gracefully(tmp_path: Path):
    missing_file = tmp_path / "non_existent.txt"

    result = parse_txt_file(missing_file)

    assert result.processing_status == "failed"
    assert result.source_type == "txt"
    assert "does not exist" in result.error


def test_parse_unsupported_file_extension_fails_gracefully(tmp_path: Path):
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("TOTAL, 100", encoding="utf-8")

    result = parse_txt_file(csv_file)

    assert result.processing_status == "failed"
    assert "Unsupported text format" in result.error


def test_parse_txt_latin1_encoding_fallback(tmp_path: Path):
    latin1_file = tmp_path / "latin1.txt"
    # Write bytes with special non-UTF8 character
    latin1_file.write_bytes(b"Vendor: Caf\xe9 Paris\nDate: 2026-01-01\nTOTAL: EUR 30.00\n")

    result = parse_txt_file(latin1_file)

    assert result.processing_status == "success"
    assert result.amount == "30.00"
    assert result.currency == "EUR"
    assert "Caf" in result.extracted_text


def test_text_result_is_compatible_with_evidence_schema():
    raw_text = "Date: 2026-05-01\nTOTAL: USD 75.00"
    result = parse_text(raw_text)

    # Validate that it serializes and re-validates into Evidence model perfectly
    dumped = result.model_dump()
    round_trip = Evidence.model_validate(dumped)

    assert round_trip == result


def test_parse_text_persists_to_database_with_mock(monkeypatch):
    import backend.database.supabase_repository as repo

    def mock_persist_text_evidence(text, evidence, source_name="chat_input.txt", business_id=None, file_type="receipt", client=None):
        return {"file_id": "file-123-uuid", "evidence_id": "evidence-456-uuid"}

    monkeypatch.setattr(repo, "persist_text_evidence", mock_persist_text_evidence)

    result = parse_text("Date: 2026-05-01\nTOTAL: USD 75.00", persist_to_database=True)

    assert result.processing_status == "success"
    assert result.database_file_id == "file-123-uuid"
    assert result.database_evidence_id == "evidence-456-uuid"


def test_parse_txt_file_persists_to_database_with_mock(tmp_path: Path, monkeypatch):
    import backend.database.supabase_repository as repo

    def mock_persist_evidence(file_path, evidence, business_id=None, file_type="receipt", storage_path=None, client=None):
        return {"file_id": "txt-file-999-uuid", "evidence_id": "txt-evidence-888-uuid"}

    monkeypatch.setattr(repo, "persist_evidence", mock_persist_evidence)

    txt_file = tmp_path / "invoice.txt"
    txt_file.write_text("TOTAL: USD 100.00\nDate: 2026-01-01", encoding="utf-8")

    result = parse_txt_file(txt_file, persist_to_database=True)

    assert result.processing_status == "success"
    assert result.database_file_id == "txt-file-999-uuid"
    assert result.database_evidence_id == "txt-evidence-888-uuid"

