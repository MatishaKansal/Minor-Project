from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from backend.parsers import image_parser
from backend.parsers.image_parser import (
    OCRResult,
    extract_final_amount,
    extract_financial_fields,
    parse_image,
    parse_images_for_business,
)
from backend.database.supabase_repository import persist_image_evidence
from backend.database.supabase_repository import BusinessContextError, get_or_create_default_business
from backend.schemas.evidence import Evidence, EvidenceProvenance


@pytest.fixture
def image_files(tmp_path):
    image = Image.new("RGB", (100, 60), "white")
    paths = {}
    for extension in ("jpg", "png"):
        path = tmp_path / f"receipt.{extension}"
        image.save(path)
        paths[extension] = path
    return paths


def patch_ocr(monkeypatch, text="Invoice 1001\nTotal: $42.50", confidence=92.5):
    monkeypatch.setattr(
        image_parser,
        "extract_text_with_ocr",
        lambda image, language="eng", engine=None: OCRResult(text, confidence),
    )


def test_valid_invoice_receipt_image(image_files, monkeypatch):
    patch_ocr(monkeypatch)
    result = parse_image(image_files["jpg"])
    assert result.processing_status == "success"
    assert result.extracted_text == "Invoice 1001\nTotal: $42.50"
    assert result.page_number == 1


def test_valid_invoice_image_extracts_labeled_fields(image_files, monkeypatch):
    patch_ocr(
        monkeypatch,
        "INVOICE\nVendor: ACME Supplies\nInvoice No: INV-2026-001\n"
        "Invoice Date: 2026-09-09\nGRAND TOTAL: USD 125.50",
    )
    result = parse_image(image_files["png"], file_type="invoice")
    assert result.processing_status == "success"
    assert result.date == "2026-09-09"
    assert result.amount == "125.50"
    assert result.currency == "USD"
    assert result.invoice_number == "INV-2026-001"
    assert result.party_name == "ACME Supplies"


def test_valid_jpg(image_files, monkeypatch):
    patch_ocr(monkeypatch, "JPG receipt")
    assert parse_image(image_files["jpg"]).extracted_text == "JPG receipt"


def test_valid_png(image_files, monkeypatch):
    patch_ocr(monkeypatch, "PNG invoice")
    assert parse_image(image_files["png"]).extracted_text == "PNG invoice"


def test_missing_file_returns_failure(tmp_path):
    result = parse_image(tmp_path / "missing.png")
    assert result.processing_status == "failed"
    assert "does not exist" in result.error


def test_unsupported_file_format(tmp_path):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"not an image")
    result = parse_image(path)
    assert result.processing_status == "failed"
    assert "Unsupported image format" in result.error


def test_corrupted_image(tmp_path):
    path = tmp_path / "corrupt.png"
    path.write_bytes(b"corrupt image bytes")
    result = parse_image(path)
    assert result.processing_status == "failed"
    assert "corrupted or unreadable" in result.error


def test_empty_ocr_text_returns_success_with_empty_text(image_files, monkeypatch):
    patch_ocr(monkeypatch, "", None)
    result = parse_image(image_files["png"])
    assert result.processing_status == "success"
    assert result.extracted_text == ""
    assert result.confidence_score is None


def test_successful_extraction_includes_confidence(image_files, monkeypatch):
    patch_ocr(monkeypatch, "Amount $10.00", 88.75)
    result = parse_image(image_files["jpg"])
    assert result.processing_status == "success"
    assert result.confidence_score == 88.75


def test_extracts_clear_financial_fields_from_ocr(image_files, monkeypatch):
    patch_ocr(monkeypatch, "Vendor: ACME STORE\nInvoice No: INV-1001\nDate: 19/10/2018\nTOTAL: RM 60.21")
    result = parse_image(image_files["jpg"])
    assert result.date == "19/10/2018"
    assert result.amount == "60.21"
    assert result.currency == "RM"
    assert result.party_name == "ACME STORE"
    assert result.invoice_number == "INV-1001"


def test_party_name_is_null_without_label(image_files, monkeypatch):
    patch_ocr(monkeypatch, "random header text\nTOTAL: USD 20.00")
    result = parse_image(image_files["jpg"])
    assert result.party_name is None


@pytest.mark.parametrize(
    ("text", "expected_amount"),
    [
        ("TOTAL RM 33.92\nTOTAL ROUNDED RH 33.90", "33.90"),
        ("lota| RM 30.91\nTUTAL ROUNDED RM 30.90", "30.90"),
        ("TOTAL | eh N63.35\nCASH 953.35\nCHANGE 0.00", "63.35"),
        ("Total (RM) : 8.00\nCASH : 8.00", "8.00"),
        ("Total RM Incl. of GST 49.39\nTotal RU 49.40\nCash -50.00", "49.40"),
        ("TOTAL ANT. BM 60.21\nCASH RH 70.39", "60.21"),
    ],
)
def test_real_document_total_examples(text, expected_amount):
    assert extract_financial_fields(text)["amount"] == expected_amount


def test_normal_total_extraction():
    amount, _, _ = extract_final_amount("TOTAL RM 30.90")
    assert amount == "30.90"


def test_grand_total_extraction():
    amount, _, _ = extract_final_amount("GRAND TOTAL RM 49.40")
    assert amount == "49.40"


def test_rounded_total_extraction():
    amount, _, method = extract_final_amount(
        "TOTAL RM 33.92\nROUNDING -0.02\nTOTAL ROUNDED RM 33.90"
    )
    assert amount == "33.90"
    assert method == "regex_final_total"


def test_final_payable_extraction():
    amount, _, method = extract_final_amount("AMOUNT PAYABLE RM 100.60")
    assert amount == "100.60"
    assert method == "regex_final_total"


def test_gst_exclusion():
    amount, _, _ = extract_final_amount("GST 6.00\nTOTAL RM 50.00")
    assert amount == "50.00"


def test_cash_change_exclusion():
    amount, _, _ = extract_final_amount(
        "TOTAL RM 48.00\nCASH RM 50.00\nCHANGE RM 2.00"
    )
    assert amount == "48.00"


def test_subtotal_exclusion():
    amount, _, _ = extract_final_amount("SUBTOTAL RM 40.00\nTOTAL RM 48.00")
    assert amount == "48.00"


def test_ocr_corrupted_total_labels():
    amount1, _, _ = extract_final_amount("TUTAL ROUNDED RM 30.90")
    assert amount1 == "30.90"

    amount2, _, _ = extract_final_amount("[utal After Rounding 100. 60")
    assert amount2 == "100.60"

    amount3, _, _ = extract_final_amount("TOTAL 4e8.0gK")
    assert amount3 == "48.00"


def test_no_reliable_total_returns_none():
    amount, source, method = extract_final_amount("some document text without any total")
    assert amount is None
    assert source is None
    assert method is None


def test_real_document_numbers_and_merchants():
    fields = extract_financial_fields(
        "tan woon yann\nMR D.I.Y. (JOHOR) SDN BHD\n"
        "Receipt No. C$1802/27714 Date. 12/02/2018"
    )
    assert fields["invoice_number"] == "CS1802/27714"
    assert fields["party_name"] == "MR D.I.Y. (JOHOR) SDN BHD"

    fields = extract_financial_fields(
        "SUNWAY VELOCITY\n01/03/18 19:14 Slip No.: 0010104733"
    )
    assert fields["invoice_number"] == "0010104733"


def test_real_document_provenance_examples():
    fields = extract_financial_fields("TOTAL RM 33.92")
    amount_provenance = next(item for item in fields["provenance"] if item["field_name"] == "amount")
    currency_provenance = next(item for item in fields["provenance"] if item["field_name"] == "currency")
    assert amount_provenance["value"] == "33.92"
    assert amount_provenance["source_text"] == "TOTAL RM 33.92"
    assert amount_provenance["line_number"] == 1
    assert amount_provenance["extraction_method"] == "regex_total_label"
    assert currency_provenance["value"] == "RM"
    assert currency_provenance["source_text"] == "TOTAL RM 33.92"

    fields = extract_financial_fields(
        "Receipt No. C$1802/27714 Date. 12/02/2018\nTOTAL | eh N63.35"
    )
    invoice_provenance = next(item for item in fields["provenance"] if item["field_name"] == "invoice_number")
    assert fields["invoice_number"] == "CS1802/27714"
    assert invoice_provenance["source_text"] == "Receipt No. C$1802/27714 Date. 12/02/2018"
    assert invoice_provenance["line_number"] == 1

    fields = extract_financial_fields(
        "01/03/18 19:14 Slip No.: 0010104733\nTotal RU 49. 40"
    )
    assert fields["amount"] == "49.40"
    assert fields["invoice_number"] == "0010104733"
    assert {item["field_name"] for item in fields["provenance"]} >= {"amount", "invoice_number"}

    fields = extract_financial_fields("PERNIAGAAN ZHENG HUI\nTotal (RM) : 8.00")
    assert fields["party_name"] == "PERNIAGAAN ZHENG HUI"
    assert {item["field_name"] for item in fields["provenance"]} >= {"party_name", "amount"}


def test_provenance_is_attached_to_evidence(image_files, monkeypatch):
    patch_ocr(monkeypatch, "INDAH GIFT & HOME DECO\nTOTAL RM 60.21")
    result = parse_image(image_files["jpg"])
    assert result.extracted_text == "INDAH GIFT & HOME DECO\nTOTAL RM 60.21"
    assert result.provenance
    assert all(item.confidence is None for item in result.provenance)
    assert any(item.field_name == "amount" and item.source_text == "TOTAL RM 60.21" for item in result.provenance)


def test_unrecognized_financial_fields_remain_null(image_files, monkeypatch):
    patch_ocr(monkeypatch, "some document text")
    result = parse_image(image_files["png"])
    assert result.date is None
    assert result.amount is None
    assert result.currency is None


def test_confidence_is_average_of_valid_word_confidences():
    data = {"conf": ["90", "-1", "80", "bad"], "text": ["one", "", "two", "three"]}
    assert image_parser._confidence_from_data(data) == 85.0


def test_optional_sample_image(monkeypatch):
    sample = Path("data/input/images/test.jpg")
    if not sample.is_file():
        pytest.skip("optional sample image is not present")
    patch_ocr(monkeypatch, "sample")
    assert parse_image(sample).processing_status == "success"


def test_parser_does_not_require_database_for_default_mode(image_files, monkeypatch):
    patch_ocr(monkeypatch, "TOTAL: USD 20.00", 90.0)
    monkeypatch.setattr(
        "backend.database.supabase_repository.get_supabase_client",
        lambda: pytest.fail("database should not be accessed"),
    )
    result = parse_image(image_files["jpg"])
    assert result.processing_status == "success"


def test_business_batch_uses_one_business_and_unique_document_ids(monkeypatch, tmp_path):
    for name in ("first.jpg", "second.jpg"):
        (tmp_path / name).write_bytes(b"image")

    calls = []

    def fake_parse_image(path, language="eng", business_id=None, persist_to_database=False, file_type="receipt"):
        calls.append((Path(path).name, business_id, persist_to_database, file_type))
        index = len(calls)
        return Evidence(
            file_name=Path(path).name,
            processing_status="success",
            database_file_id=f"file-{index}",
            database_evidence_id=f"evidence-{index}",
        )

    monkeypatch.setattr(image_parser, "parse_image", fake_parse_image)
    results = parse_images_for_business("business-1", tmp_path, file_type="invoice")

    assert len(results) == 2
    assert calls == [
        ("first.jpg", "business-1", True, "invoice"),
        ("second.jpg", "business-1", True, "invoice"),
    ]
    assert {result.database_file_id for result in results} == {"file-1", "file-2"}
    assert {result.database_evidence_id for result in results} == {"evidence-1", "evidence-2"}


def test_business_batch_allows_automatic_business_resolution(tmp_path):
    assert parse_images_for_business(None, tmp_path) == []


class BusinessResolverClient:
    def __init__(self, businesses):
        self.businesses = businesses
        self.inserted = []

    def table(self, name):
        return BusinessResolverTable(self, name)


class BusinessResolverTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def select(self, _columns):
        return self

    def execute(self):
        return type("Response", (), {"data": self.client.businesses})()

    def insert(self, row):
        self.client.inserted.append(row)
        self.client.businesses = [{"business_id": "created-business"}]
        return self


def test_default_business_resolution_reuses_sole_business():
    client = BusinessResolverClient([{"business_id": "existing-business"}])
    assert get_or_create_default_business(client) == "existing-business"
    assert client.inserted == []


def test_default_business_resolution_creates_only_when_empty():
    client = BusinessResolverClient([])
    assert get_or_create_default_business(client) == "created-business"
    assert client.inserted == [{"business_name": "My Business", "industry": "Retail", "currency": "INR"}]


def test_default_business_resolution_rejects_multiple_businesses():
    client = BusinessResolverClient([{"business_id": "one"}, {"business_id": "two"}])
    with pytest.raises(BusinessContextError, match="Multiple businesses exist"):
        get_or_create_default_business(client)


def test_tesseract_command_comes_from_environment(monkeypatch, tmp_path):
    fake_pytesseract = Mock()
    fake_pytesseract.get_tesseract_version.return_value = "5.0"
    monkeypatch.setattr(image_parser, "pytesseract", fake_pytesseract)
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"fake executable")
    monkeypatch.setenv("TESSERACT_CMD", str(executable))
    image_parser._configure_tesseract()
    assert fake_pytesseract.pytesseract.tesseract_cmd == str(executable)


class FakeTable:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls

    def insert(self, row):
        self.calls.append((self.name, row))
        return self

    def execute(self):
        rows = {
            "source_file": [{"file_id": "file-1"}],
            "financial_evidence": [{"evidence_id": "evidence-1"}],
            "evidence_details": [{}],
            "provenance": [{}],
        }
        return type("Response", (), {"data": rows[self.name]})()


class FakeSupabaseClient:
    def __init__(self):
        self.calls = []
        self.storage = FakeStorage()

    def table(self, name):
        return FakeTable(name, self.calls)


class FakeStorage:
    def list_buckets(self):
        return [{"name": "documents"}]

    def from_(self, _bucket):
        return self

    def upload(self, _path, _file, _options):
        return {"Key": _path}


def test_persists_image_evidence_in_schema_order(tmp_path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"test image")
    evidence = Evidence(
        file_name=path.name,
        extracted_text="TOTAL 10.00",
        confidence_score=64.5,
        processing_status="success",
    )
    client = FakeSupabaseClient()

    ids = persist_image_evidence(path, evidence, "business-1", client=client)

    assert ids == {"file_id": "file-1", "evidence_id": "evidence-1"}
    assert [name for name, _ in client.calls] == [
        "source_file",
        "financial_evidence",
        "evidence_details",
        "provenance",
    ]
    assert client.calls[1][1]["confidence_score"] == 0.645


def test_persists_image_evidence_maps_provenance_field_name(tmp_path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"test image")
    evidence = Evidence(
        file_name=path.name,
        extracted_text="TOTAL 10.00",
        confidence_score=64.5,
        processing_status="success",
        provenance=[
            EvidenceProvenance(
                field_name="amount",
                value="10.00",
                source_text="TOTAL 10.00",
                line_number=1,
                page_number=1,
                extraction_method="regex_total_label",
                confidence=None,
            )
        ],
    )
    client = FakeSupabaseClient()

    ids = persist_image_evidence(path, evidence, "business-1", client=client)

    assert ids == {"file_id": "file-1", "evidence_id": "evidence-1"}
    provenance_calls = [row for name, row in client.calls if name == "provenance"]
    assert len(provenance_calls) == 1
    rows = provenance_calls[0]
    assert len(rows) == 1
    assert rows[0]["field_name"] == "amount"
    assert rows[0]["source_text"] == "TOTAL 10.00"
    assert rows[0]["page_number"] == 1
    assert rows[0]["extraction_method"] == "regex_total_label"
    assert rows[0]["confidence_score"] is None
    assert rows[0]["location"] == {
        "field_name": "amount",
        "value": "10.00",
        "line_number": 1,
    }

