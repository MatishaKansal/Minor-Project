from pathlib import Path

from openpyxl import Workbook

from backend.parsers.excel_parser import parse_excel_file
from backend.services.ingestion_service import detect_file_type, ingest_file


def _write_workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_excel_invoice_workbook_extracts_gstin_and_rows(tmp_path: Path):
    path = tmp_path / "invoices.xlsx"
    _write_workbook(
        path,
        [
            "Invoice_ID", "Invoice_Date", "Seller_Name", "Seller_GSTIN",
            "Buyer_Name", "Buyer_GSTIN", "Item_Description", "Grand_Total",
        ],
        [[
            "INV-100", "2026-10-01", "ACME Supplies", "09AADCR5842H1Z6",
            "Example Retail", "07AACCM4684P1ZP", "Office paper", 125.5,
        ]],
    )

    result = parse_excel_file(path)

    assert result.processing_status == "success"
    assert result.source_type == "excel"
    assert result.invoice_number == "INV-100"
    assert result.seller_gstin == "09AADCR5842H1Z6"
    assert result.buyer_gstin == "07AACCM4684P1ZP"
    assert result.amount == "125.50"
    assert result.items[0].description == "Office paper"


def test_excel_workbook_is_supported_by_ingestion_service(tmp_path: Path):
    path = tmp_path / "invoice.xlsm"
    _write_workbook(path, ["Invoice_ID", "Seller_Name"], [["INV-1", "ACME"]])

    assert detect_file_type(path) == "excel"
    assert ingest_file(path).processing_status == "success"


def test_excel_index_workbook_is_not_rejected(tmp_path: Path):
    path = tmp_path / "index.xlsx"
    _write_workbook(
        path,
        ["File_Name", "Invoice_Category", "Data_Type"],
        [["invoice.xlsx", "GST Tax Invoice", "Synthetic invoice-only data"]],
    )

    result = parse_excel_file(path)

    assert result.processing_status == "success"
    assert result.items[0].description == "invoice.xlsx"
