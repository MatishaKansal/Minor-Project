import fitz

from backend.parsers.pdf_parser import parse_pdf
from backend.schemas.evidence import Evidence, EvidenceProvenance


def write_pdf(path, pages):
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_single_page_pdf_extracts_fields_and_provenance(tmp_path):
    path = tmp_path / "invoice.pdf"
    write_pdf(path, [
        "Vendor: ACME Supplies\nInvoice No: INV-2026-001\n"
        "Date: 2026-09-09\nDescription: Office supplies\nTOTAL: USD 125.50"
    ])

    result = parse_pdf(path)

    assert result.processing_status == "success"
    assert result.source_type == "pdf"
    assert result.date == "2026-09-09"
    assert result.amount == "125.50"
    assert result.currency == "USD"
    assert result.party_name == "ACME Supplies"
    assert result.invoice_number == "INV-2026-001"
    assert result.description == "Office supplies"
    assert all(isinstance(item, EvidenceProvenance) for item in result.provenance)
    amount = next(item for item in result.provenance if item.field_name == "amount")
    assert amount.source_text == "TOTAL: USD 125.50"
    assert amount.line_number == 5
    assert amount.page_number == 1
    assert amount.confidence is None


def test_multi_page_pdf_preserves_page_aware_provenance(tmp_path):
    path = tmp_path / "multi-page.pdf"
    write_pdf(path, ["Invoice No: INV-2\nPage one", "Notes", "AMOUNT PAYABLE: EUR 48.00"])

    result = parse_pdf(path)

    assert result.processing_status == "success"
    amount = next(item for item in result.provenance if item.field_name == "amount")
    assert result.amount == "48.00"
    assert amount.page_number == 3
    assert amount.line_number == 1
    assert amount.source_text == "AMOUNT PAYABLE: EUR 48.00"


def test_pdf_amount_uses_final_payable_value(tmp_path):
    path = tmp_path / "amount.pdf"
    write_pdf(path, [
        "SUBTOTAL: USD 100.00\nGST: USD 6.00\nDISCOUNT: USD 2.00\n"
        "ROUNDING: USD -0.01\nAMOUNT PAYABLE: USD 104.00\nCASH: USD 110.00\nCHANGE: USD 6.00"
    ])

    result = parse_pdf(path)

    assert result.amount == "104.00"
    assert result.currency == "USD"


def test_invalid_pdf_fails_gracefully(tmp_path):
    path = tmp_path / "invalid.pdf"
    path.write_bytes(b"not a PDF")

    result = parse_pdf(path)

    assert result.processing_status == "failed"
    assert result.source_type == "pdf"
    assert result.error


def test_image_only_pdf_fails_without_ocr(tmp_path):
    path = tmp_path / "image-only.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    result = parse_pdf(path)

    assert result.processing_status == "failed"
    assert "no extractable text" in result.error


def test_pdf_result_is_compatible_with_evidence_schema(tmp_path):
    path = tmp_path / "compatible.pdf"
    write_pdf(path, ["TOTAL: RM 20.00"])

    result = parse_pdf(path)
    round_trip = Evidence.model_validate(result.model_dump())

    assert round_trip == result