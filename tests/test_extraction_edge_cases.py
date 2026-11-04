"""Edge-case regression tests for financial_field_extractor."""

import pytest

from backend.services.financial_field_extractor import (
    extract_financial_fields,
    _extract_document_number,
    to_document_lines,
)


# ─── Invoice Number Edge Cases ────────────────────────────────────────────────

def test_invoice_number_on_next_line_label_separated():
    """PDF layout where label and value are on separate lines (e.g. real invoice.pdf)."""
    text = "Invoice Number\n123\nDate: 2024-03-25\nAmount Due\n$19.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "123"


def test_invoice_number_not_captured_as_word_number():
    """'Number' keyword should never be returned as the invoice number."""
    text = "Invoice Number\n123"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "123"
    assert fields["invoice_number"] != "Number"


def test_invoice_number_inline():
    """Standard inline: 'Invoice No: INV-001'."""
    text = "Invoice No: INV-001\nTotal: USD 100.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "INV-001"


def test_invoice_number_with_hash():
    """Hash-style reference: 'Invoice #ORD-2026-99'."""
    text = "Invoice #ORD-2026-99\nTotal: USD 55.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "ORD-2026-99"


def test_invoice_number_digit_only():
    """Pure numeric invoice number on same line."""
    text = "Invoice No: 4567\nTotal: RM 88.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "4567"


def test_receipt_number_label_next_line():
    """Receipt Number label on its own line, value on the next."""
    text = "Receipt Number\nREC-20240925\nTotal: SGD 35.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "REC-20240925"


def test_ref_number_inline():
    """Reference number patterns."""
    text = "Ref: TXN-88881\nAmount: USD 200.00"
    fields = extract_financial_fields(text)
    assert fields["invoice_number"] == "TXN-88881"


# ─── Amount on Next Line Edge Cases ──────────────────────────────────────────

def test_amount_due_label_value_on_separate_lines():
    """'Amount Due' on its own line, '$19.00' on the next — like real invoice.pdf."""
    text = "Subtotal\n$19.00\nTax Rate\n0%\nAmount Due\n$19.00"
    fields = extract_financial_fields(text)
    assert fields["amount"] == "19.00"


def test_total_label_on_own_line_amount_follows():
    """TOTAL label on its own line with amount on next."""
    text = "TOTAL\nRM 48.00\nCASH\nRM 50.00"
    fields = extract_financial_fields(text)
    assert fields["amount"] == "48.00"


# ─── Date Edge Cases ──────────────────────────────────────────────────────────

def test_date_month_name_day_year():
    """'March 25, 2024' format — common in PDF invoices."""
    text = "Issue Date\nMarch 25, 2024\nAmount Due\n$19.00"
    fields = extract_financial_fields(text)
    assert fields["date"] == "March 25, 2024"


def test_date_dd_mon_yyyy():
    """'25 March 2024' format."""
    text = "Date: 25 March 2024\nTotal: USD 100.00"
    fields = extract_financial_fields(text)
    assert fields["date"] == "25 March 2024"


def test_date_iso_format():
    """ISO 8601 date."""
    text = "Date: 2026-09-25\nTotal: USD 50.00"
    fields = extract_financial_fields(text)
    assert fields["date"] == "2026-09-25"


def test_date_slashes():
    """DD/MM/YYYY format."""
    text = "Date: 25/09/2026\nTotal: USD 50.00"
    fields = extract_financial_fields(text)
    assert fields["date"] == "25/09/2026"


def test_issue_date_label_separated():
    """'Issue Date' label on own line, date value on next line."""
    text = "Invoice Number\n123\nIssue Date\nMarch 25, 2024\nAmount Due\n$19.00"
    fields = extract_financial_fields(text)
    assert fields["date"] == "March 25, 2024"


# ─── Party Name Edge Cases ────────────────────────────────────────────────────

def test_party_name_llc_no_label():
    """'Example, LLC' style — comma-separated suffix, no explicit vendor label."""
    text = "Invoice\nInvoice Number\n123\nIssue Date\nMarch 25, 2024\nExample, LLC\n123 Fake Street\nTotal: $19.00"
    fields = extract_financial_fields(text)
    assert fields["party_name"] == "Example, LLC"


def test_party_name_with_explicit_vendor_label():
    """Vendor: label should always win."""
    text = "Vendor: ACME Supplies\nInvoice No: INV-001\nTotal: USD 125.00"
    fields = extract_financial_fields(text)
    assert fields["party_name"] == "ACME Supplies"


def test_party_name_pvt_ltd():
    """Indian company style."""
    text = "Raj Traders Pvt Ltd\n123 MG Road\nDate: 2026-09-01\nTotal: INR 5000"
    fields = extract_financial_fields(text)
    assert fields["party_name"] == "Raj Traders Pvt Ltd"


def test_party_name_not_address_line():
    """Address line like '123 Fake Street' must not be extracted as party name."""
    text = "Example, LLC\n123 Fake Street\nNew York City, NY 10012\nTotal: $19.00"
    fields = extract_financial_fields(text)
    # Party name should be the company, not the street address
    assert fields["party_name"] == "Example, LLC"
    assert fields["party_name"] != "123 Fake Street"


# ─── Full Real Invoice Simulation ─────────────────────────────────────────────

def test_full_invoice_pdf_simulation():
    """End-to-end extraction matching the real data/input/pdf/invoice.pdf content."""
    # This is the exact line-by-line text extracted by PyMuPDF from invoice.pdf
    text = (
        "Invoice\n"
        "Invoice Number\n"
        "123\n"
        "Issue Date\n"
        "March 25, 2024\n"
        "Due Date\n"
        "March 25, 2024\n"
        "Status\n"
        "PAID\n"
        "Example, LLC\n"
        "123 Fake Street\n"
        "New York City, NY 10012\n"
        "(555) 867-5309\n"
        "support@example.com\n"
        "Bill To\n"
        "Customer\n"
        "Address\n"
        "City, State Zipcode\n"
        "customer@example.org\n"
        "Item\n"
        "Unit Cost\n"
        "Quantity\n"
        "Amount\n"
        "Subscription\n"
        "$19.00\n"
        "1\n"
        "$19.00\n"
        "Subtotal\n"
        "$19.00\n"
        "Tax Rate\n"
        "0%\n"
        "Amount Due\n"
        "$19.00\n"
        "For questions, contact us anytime at support@example.com."
    )
    fields = extract_financial_fields(text)

    assert fields["invoice_number"] == "123", f"Expected '123', got {repr(fields['invoice_number'])}"
    assert fields["date"] == "March 25, 2024", f"Expected 'March 25, 2024', got {repr(fields['date'])}"
    assert fields["amount"] == "19.00", f"Expected '19.00', got {repr(fields['amount'])}"
    assert fields["currency"] == "USD", f"Expected 'USD', got {repr(fields['currency'])}"
    assert fields["party_name"] == "Example, LLC", f"Expected 'Example, LLC', got {repr(fields['party_name'])}"
