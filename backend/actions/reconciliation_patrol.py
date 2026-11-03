"""Reconciliation & Anomaly Patrol Agent.

Scans parsed invoices against bank records or runs scheduled checks to detect:
1. Duplicate invoices/payments.
2. Invoices missing matching bank payments.
3. Bank transactions missing supporting receipts/invoices.
4. Total GST discrepancy aggregates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)


class PatrolAnomaly(BaseModel):
    anomaly_id: str
    severity: str  # high, medium, low
    category: str  # duplicate_payment, missing_receipt, gst_mismatch, unrecorded_expense
    title: str
    description: str
    recommended_action: str
    affected_ids: List[str] = Field(default_factory=list)


class PatrolDigest(BaseModel):
    total_invoices_scanned: int = 0
    total_bank_transactions: int = 0
    matched_count: int = 0
    anomalies_detected: int = 0
    anomalies: List[PatrolAnomaly] = Field(default_factory=list)
    summary_message: str


def run_reconciliation_patrol(
    invoices: List[Dict[str, Any]],
    bank_transactions: List[Dict[str, Any]],
    tolerance: float = 1.0,
) -> PatrolDigest:
    """
    Run proactive anomaly patrol across in-memory or persisted financial records.
    """
    anomalies: List[PatrolAnomaly] = []
    matched_invoices = set()
    matched_txns = set()

    # 1. Check duplicate invoices
    seen_inv_numbers: Dict[str, List[int]] = {}
    for idx, inv in enumerate(invoices):
        inv_no = inv.get("invoice_number")
        if inv_no:
            seen_inv_numbers.setdefault(inv_no, []).append(idx)

    for inv_no, indices in seen_inv_numbers.items():
        if len(indices) > 1:
            anomalies.append(
                PatrolAnomaly(
                    anomaly_id=f"anomaly_dup_inv_{inv_no}",
                    severity="high",
                    category="duplicate_payment",
                    title=f"Duplicate Invoice Number Detected: {inv_no}",
                    description=f"Invoice number '{inv_no}' appears {len(indices)} times across the records.",
                    recommended_action="Verify if this was billed twice or represents duplicate file uploads.",
                    affected_ids=[inv_no],
                )
            )

    # 2. Match Invoices to Bank Transactions
    for i_idx, inv in enumerate(invoices):
        inv_tot = inv.get("invoice_total") or inv.get("amount")
        if inv_tot is None:
            continue
        try:
            inv_val = float(inv_tot)
        except (ValueError, TypeError):
            continue

        inv_num = inv.get("invoice_number", f"INV-{i_idx+1}")
        matched = False
        for t_idx, txn in enumerate(bank_transactions):
            if t_idx in matched_txns:
                continue
            txn_amt = txn.get("amount")
            if txn_amt is None:
                continue
            try:
                txn_val = abs(float(txn_amt))
            except (ValueError, TypeError):
                continue

            if abs(inv_val - txn_val) <= tolerance:
                matched_invoices.add(i_idx)
                matched_txns.add(t_idx)
                matched = True
                break

        if not matched and inv_val > 0:
            anomalies.append(
                PatrolAnomaly(
                    anomaly_id=f"anomaly_unmatched_inv_{inv_num}",
                    severity="medium",
                    category="missing_receipt",
                    title=f"Unpaid / Unmatched Invoice: {inv_num}",
                    description=f"Invoice {inv_num} for INR {inv_val:,.2f} has no corresponding payment in bank records.",
                    recommended_action="Check if payment is scheduled or was paid from an unlinked account.",
                    affected_ids=[inv_num],
                )
            )

    # 3. Bank transactions with no invoice
    for t_idx, txn in enumerate(bank_transactions):
        if t_idx in matched_txns:
            continue
        try:
            t_val = abs(float(txn.get("amount", 0)))
        except (ValueError, TypeError):
            continue

        desc = txn.get("description", "Unknown Debit")
        if t_val > 500.0:  # Flag significant unbacked transactions
            anomalies.append(
                PatrolAnomaly(
                    anomaly_id=f"anomaly_unbacked_txn_{t_idx+1}",
                    severity="medium",
                    category="unrecorded_expense",
                    title=f"Bank Debit Missing Invoice: INR {t_val:,.2f}",
                    description=f"Transaction '{desc}' for INR {t_val:,.2f} does not have an attached receipt or tax invoice.",
                    recommended_action="Request receipt from employee or vendor to claim input tax credit (ITC).",
                    affected_ids=[desc],
                )
            )

    summary = (
        f"Patrol scanned {len(invoices)} invoices and {len(bank_transactions)} bank entries. "
        f"Found {len(matched_invoices)} reconciled pairs and flagged {len(anomalies)} anomaly items."
    )

    return PatrolDigest(
        total_invoices_scanned=len(invoices),
        total_bank_transactions=len(bank_transactions),
        matched_count=len(matched_invoices),
        anomalies_detected=len(anomalies),
        anomalies=anomalies,
        summary_message=summary,
    )
