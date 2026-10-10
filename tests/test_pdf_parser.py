import fitz

from backend.parsers.pdf_parser import extract_pdf_pages, parse_pdf
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


class MockPDFOCREngine:
    def __init__(self, text="Vendor: Scanned Corp\nTOTAL: USD 75.00", confidence=92.5):
        self.text = text
        self.confidence = confidence

    def extract(self, image, language="eng"):
        from backend.parsers.image_parser import OCRResult
        return OCRResult(text=self.text, confidence_score=self.confidence)


def test_scanned_image_only_pdf_uses_ocr_fallback(tmp_path):
    path = tmp_path / "scanned_invoice.pdf"
    # Create empty PDF page with no native text (scanned image page)
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    mock_engine = MockPDFOCREngine()
    result = parse_pdf(path, ocr_engine=mock_engine)

    assert result.processing_status == "success"
    assert result.source_type == "pdf"
    assert result.amount == "75.00"
    assert result.currency == "USD"
    assert result.party_name == "Scanned Corp"
    assert result.confidence_score == 92.5


def test_blank_pdf_without_ocr_content_fails_gracefully(tmp_path):
    path = tmp_path / "blank.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    empty_engine = MockPDFOCREngine(text="", confidence=None)
    result = parse_pdf(path, ocr_engine=empty_engine)

    assert result.processing_status == "failed"
    assert "no extractable text" in result.error.lower()


def test_pdf_result_is_compatible_with_evidence_schema(tmp_path):
    path = tmp_path / "compatible.pdf"
    write_pdf(path, ["TOTAL: RM 20.00"])

    result = parse_pdf(path)
    round_trip = Evidence.model_validate(result.model_dump())

    assert round_trip == result


def test_gstin_extraction_accepts_spaced_registration_labels(tmp_path):
    path = tmp_path / "spaced-gstin.pdf"
    write_pdf(path, [
        "Supplier GST Registration No: 09 AADCR 5842 H 1Z6\n"
        "Bill To GSTIN: 09 AACCM 4684 P 1ZP\nTOTAL: INR 100.00"
    ])

    result = parse_pdf(path, file_type="receipt")

    assert result.seller_gstin == "09AADCR5842H1Z6"
    assert result.buyer_gstin == "09AACCM4684P1ZP"


def test_native_pdf_text_is_read_in_visual_block_order(tmp_path):
    path = tmp_path / "visual-order.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Vendor: ACME")
    page.insert_text((300, 100), "Invoice No: INV-ORDER")
    document.save(path)
    document.close()

    pages, confidence = extract_pdf_pages(path)

    assert confidence is None
    assert pages[0][1].splitlines()[:2] == ["Vendor: ACME", "Invoice No: INV-ORDER"]


def test_repeated_headers_are_removed_even_with_spacing_and_case_variation(tmp_path):
    path = tmp_path / "repeated-header.pdf"
    write_pdf(path, [
        "ACME   SUPPLIES\nInvoice No: INV-1\nTOTAL: USD 10.00",
        " acme supplies \nInvoice No: INV-2\nTOTAL: USD 20.00",
    ])

    pages, _ = extract_pdf_pages(path)

    assert pages[0][1].splitlines() == ["Invoice No: INV-1", "TOTAL: USD 10.00"]
    assert pages[1][1].splitlines() == ["Invoice No: INV-2", "TOTAL: USD 20.00"]


def test_complete_native_invoice_does_not_run_redundant_header_ocr(tmp_path):
    path = tmp_path / "complete-native.pdf"
    write_pdf(path, [
        "Vendor: ACME Supplies\nInvoice No: INV-2026-001\n"
        "Date: 2026-09-09\nDescription: Office supplies\n"
        "TOTAL: USD 125.50\nReference: 1234567890"
    ])

    class FailingOCREngine:
        def extract(self, image, language="eng"):
            raise AssertionError("header OCR should not run for complete native text")

    result = parse_pdf(path, ocr_engine=FailingOCREngine())

    assert result.processing_status == "success"
    assert result.invoice_number == "INV-2026-001"
    assert result.date == "2026-09-09"