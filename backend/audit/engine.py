"""Deterministic, evidence-aware procedures for the currently supported invoice data."""

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


PROCEDURES = {
    "AR-01": "Duplicate invoice detection",
    "AR-02": "Duplicate payment detection",
    "AR-03": "Invoice arithmetic verification",
    "AR-04": "Invoice and PO amount verification",
    "AR-05": "Invoice and GRN quantity verification",
    "AR-06": "Overpayment detection",
    "AR-07": "Partial payment verification",
    "AR-08": "Bank debit without supporting invoice",
    "AR-09": "Invoice without purchase authorization",
    "AR-10": "Ledger entry without supporting evidence",
    "AR-11": "Ledger and source document mismatch",
    "AR-12": "GST/tax calculation verification",
    "AR-13": "Missing mandatory supporting documents",
    "AR-14": "Unmatched bank transactions",
    "AR-15": "Unusual transaction dates and cutoff issues",
    "AR-16": "Vendor and payment-reference inconsistencies",
}

_TOLERANCE = Decimal("0.02")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _result(
    rule_id: str,
    outcome: str,
    explanation: str,
    *,
    expected: Any = None,
    actual: Any = None,
    discrepancy: Decimal | None = None,
    exposure: Decimal | None = None,
    severity: str = "Unrated",
    severity_basis: str = "No monetary exposure could be calculated.",
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "procedure": PROCEDURES[rule_id],
        "outcome": outcome,
        "explanation": explanation,
        "expected_value": expected,
        "actual_value": actual,
        "discrepancy": str(discrepancy) if discrepancy is not None else None,
        "financial_exposure": str(exposure) if exposure is not None else None,
        "severity": severity,
        "severity_basis": severity_basis,
    }


def _classify_severity(exposure: Decimal | None, materiality: Decimal | None) -> tuple[str, str]:
    if exposure is None or materiality is None or materiality <= 0:
        return "Unrated", "Exposure or the engagement materiality threshold is unavailable."
    if exposure >= materiality:
        return "High", f"Exposure {exposure} meets or exceeds engagement materiality {materiality}."
    return "Low", f"Exposure {exposure} is below engagement materiality {materiality}."


def evaluate_population(
    invoices: list[dict[str, Any]],
    items_by_invoice: dict[str, list[dict[str, Any]]],
    period_start: str,
    period_end: str,
    materiality_threshold: Any = None,
) -> list[dict[str, Any]]:
    """Evaluate every rule for each invoice; unsupported data is never treated as a pass."""
    materiality = _decimal(materiality_threshold)
    duplicate_keys: dict[tuple[str, str], list[int]] = {}
    for index, invoice in enumerate(invoices):
        number = str(invoice.get("invoice_number") or "").strip().casefold()
        vendor = str(invoice.get("seller_name") or "").strip().casefold()
        if number and vendor:
            duplicate_keys.setdefault((vendor, number), []).append(index)

    results: list[dict[str, Any]] = []
    for index, invoice in enumerate(invoices):
        invoice_id = str(invoice.get("invoice_id") or "")
        row_results: list[dict[str, Any]] = []

        number = str(invoice.get("invoice_number") or "").strip()
        vendor = str(invoice.get("seller_name") or "").strip()
        duplicate_group = duplicate_keys.get((vendor.casefold(), number.casefold()), []) if number and vendor else []
        if not number or not vendor:
            row_results.append(_result("AR-01", "INSUFFICIENT EVIDENCE", "Invoice number or vendor identity is missing; duplicate testing cannot be performed."))
        elif len(duplicate_group) > 1:
            exposure = _decimal(invoice.get("invoice_total"))
            severity, basis = _classify_severity(exposure, materiality)
            row_results.append(_result("AR-01", "EXCEPTION", f"Invoice number {number} appears {len(duplicate_group)} times for vendor {vendor} in this engagement population.", actual={"invoice_number": number, "vendor": vendor, "population_occurrences": len(duplicate_group), "invoice_total": str(exposure) if exposure is not None else None}, exposure=exposure, severity=severity, severity_basis=basis))
        else:
            row_results.append(_result("AR-01", "PASS", "Invoice number and vendor are unique within the linked engagement population."))

        subtotal = _decimal(invoice.get("subtotal"))
        taxes = [_decimal(invoice.get(field)) for field in ("cgst_total", "sgst_total", "igst_total", "cess_total")]
        total = _decimal(invoice.get("invoice_total"))
        round_off = _decimal(invoice.get("round_off")) or Decimal(0)
        if subtotal is None or total is None or any(value is None for value in taxes):
            row_results.append(_result("AR-03", "INSUFFICIENT EVIDENCE", "Subtotal, invoice total, or one or more tax components are missing or not numeric."))
        else:
            expected_total = subtotal + sum(taxes, Decimal(0)) + round_off
            difference = total - expected_total
            if abs(difference) > _TOLERANCE:
                severity, basis = _classify_severity(abs(difference), materiality)
                row_results.append(_result("AR-03", "EXCEPTION", "The extracted invoice total does not equal subtotal plus tax components and round-off.", expected=str(expected_total), actual=str(total), discrepancy=difference, exposure=abs(difference), severity=severity, severity_basis=basis))
            else:
                row_results.append(_result("AR-03", "PASS", "Invoice total agrees with subtotal, tax components, and round-off within 0.02.", expected=str(expected_total), actual=str(total)))

        invoice_items = items_by_invoice.get(invoice_id, [])
        tax_checks: list[tuple[Decimal, Decimal, Decimal]] = []
        for item in invoice_items:
            taxable = _decimal(item.get("taxable_value"))
            rate = _decimal(item.get("gst_rate"))
            item_tax_values = [_decimal(item.get(field)) for field in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount")]
            if taxable is not None and rate is not None and all(value is not None for value in item_tax_values):
                tax_checks.append((taxable, rate, sum(item_tax_values, Decimal(0))))
        if not invoice_items or len(tax_checks) != len(invoice_items):
            row_results.append(_result("AR-12", "INSUFFICIENT EVIDENCE", "Taxable value, GST rate, or tax components are missing for at least one invoice line."))
        else:
            differences = [(taxable * rate / Decimal(100) - actual) for taxable, rate, actual in tax_checks]
            discrepancy = sum(differences, Decimal(0))
            if any(abs(value) > _TOLERANCE for value in differences):
                exposure = sum((abs(value) for value in differences), Decimal(0))
                severity, basis = _classify_severity(exposure, materiality)
                row_results.append(_result("AR-12", "EXCEPTION", "One or more line-item tax amounts do not agree with taxable value multiplied by the stated GST rate.", expected=str(sum((base * rate / Decimal(100) for base, rate, _ in tax_checks), Decimal(0))), actual=str(sum((actual for _, _, actual in tax_checks), Decimal(0))), discrepancy=discrepancy, exposure=exposure, severity=severity, severity_basis=basis))
            else:
                row_results.append(_result("AR-12", "PASS", "Line-item tax amounts agree with the stated taxable values and GST rates within 0.02.", discrepancy=discrepancy))

        for rule_id in ("AR-02", "AR-04", "AR-05", "AR-06", "AR-07", "AR-08", "AR-09", "AR-10", "AR-11", "AR-13", "AR-14", "AR-16"):
            row_results.append(_result(rule_id, "INSUFFICIENT EVIDENCE", "This procedure requires payment, PO, GRN, bank, ledger, or engagement-specific document requirements that are not available in the current data model."))

        raw_date = invoice.get("invoice_date")
        if not raw_date:
            row_results.append(_result("AR-15", "INSUFFICIENT EVIDENCE", "Invoice date is missing; period cutoff testing cannot be performed."))
        else:
            try:
                transaction_date = date.fromisoformat(str(raw_date)[:10])
                start = date.fromisoformat(period_start)
                end = date.fromisoformat(period_end)
            except ValueError:
                row_results.append(_result("AR-15", "INSUFFICIENT EVIDENCE", "Invoice date or engagement period is not a valid ISO date."))
            else:
                if transaction_date < start or transaction_date > end:
                    row_results.append(_result("AR-15", "EXCEPTION", "Invoice date falls outside the engagement period and requires auditor cutoff review.", expected={"period_start": period_start, "period_end": period_end}, actual=str(transaction_date), severity="Medium"))
                else:
                    row_results.append(_result("AR-15", "PASS", "Invoice date falls within the engagement period."))

        results.extend({"invoice_id": invoice_id, **result} for result in row_results)
    return results
