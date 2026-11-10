from backend.audit.engine import evaluate_population


def _invoice(invoice_id="invoice-1", **overrides):
    return {
        "invoice_id": invoice_id,
        "invoice_number": "INV-1",
        "seller_name": "Vendor A",
        "invoice_date": "2025-05-01",
        "subtotal": "100.00",
        "cgst_total": "9.00",
        "sgst_total": "9.00",
        "igst_total": "0.00",
        "cess_total": "0.00",
        "round_off": "0.00",
        "invoice_total": "118.00",
        **overrides,
    }


def _results(invoices, items=None, start="2025-04-01", end="2026-03-31", materiality="100"):
    return evaluate_population(invoices, items or {}, start, end, materiality)


def test_valid_invoice_passes_arithmetic_and_period_but_marks_unavailable_evidence():
    results = _results([_invoice()])
    outcomes = {result["rule_id"]: result["outcome"] for result in results}

    assert outcomes["AR-03"] == "PASS"
    assert outcomes["AR-15"] == "PASS"
    assert outcomes["AR-04"] == "INSUFFICIENT EVIDENCE"
    assert len(outcomes) == 16


def test_duplicate_invoice_is_flagged_for_each_record_without_claiming_fraud():
    results = _results([_invoice(), _invoice(invoice_id="invoice-2")])

    assert [row["outcome"] for row in results if row["rule_id"] == "AR-01"] == ["EXCEPTION", "EXCEPTION"]
    assert all("fraud" not in row["explanation"].lower() for row in results)
    assert next(row for row in results if row["rule_id"] == "AR-01")["severity"] == "High"


def test_invoice_arithmetic_discrepancy_is_reported():
    results = _results([_invoice(invoice_total="120.00")])
    arithmetic = next(row for row in results if row["rule_id"] == "AR-03")

    assert arithmetic["outcome"] == "EXCEPTION"
    assert arithmetic["discrepancy"] == "2.00"
    assert arithmetic["financial_exposure"] == "2.00"
    assert arithmetic["severity"] == "Low"


def test_tax_calculation_requires_complete_line_evidence():
    invoice = _invoice()
    items = {"invoice-1": [{
        "taxable_value": "100.00",
        "gst_rate": "18",
        "cgst_amount": "9.00",
        "sgst_amount": "9.00",
        "igst_amount": "0.00",
        "cess_amount": "0.00",
    }]}
    results = _results([invoice], items)

    assert next(row for row in results if row["rule_id"] == "AR-12")["outcome"] == "PASS"
    assert next(row for row in _results([invoice]) if row["rule_id"] == "AR-12")["outcome"] == "INSUFFICIENT EVIDENCE"


def test_out_of_period_invoice_requires_cutoff_exception_review():
    result = next(row for row in _results([_invoice(invoice_date="2024-12-31")]) if row["rule_id"] == "AR-15")

    assert result["outcome"] == "EXCEPTION"
    assert "cutoff review" in result["explanation"]


def test_non_finite_invoice_amount_is_insufficient_evidence():
    results = _results([_invoice(invoice_total="NaN")])

    assert next(row for row in results if row["rule_id"] == "AR-03")["outcome"] == "INSUFFICIENT EVIDENCE"
