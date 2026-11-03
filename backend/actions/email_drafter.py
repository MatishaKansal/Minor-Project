"""Vendor Dispute & Reconciliation Email Drafter Agent."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

LOGGER = logging.getLogger(__name__)


class EmailDraft(BaseModel):
    action_type: str = "vendor_dispute_email"
    recipient_email: str
    recipient_name: str
    sender_name: str = "Finance & Accounts Department"
    subject: str
    body_text: str
    discrepancy_amount: Optional[float] = None
    invoice_number: Optional[str] = None
    citations: List[str] = Field(default_factory=list)
    status: str = "draft"  # draft, approved, sent


def draft_dispute_email(
    query: str,
    audit_answer: str,
    evidence: Dict[str, Any],
    stopped_tier: str = "layer_3",
) -> Optional[EmailDraft]:
    """
    Synthesize an audit dispute email draft from the cascade's answer and evidence.
    """
    invoice = evidence.get("invoice", {})
    invoices = evidence.get("invoices", [])
    if not invoice and invoices:
        invoice = invoices[0]

    biz = evidence.get("business", {})
    business_name = biz.get("business_name") or "Our Enterprise"

    invoice_number = (
        invoice.get("invoice_number")
        or "INV-REFERENCE"
    )
    seller_name = (
        invoice.get("seller_name")
        or invoice.get("party_name")
        or "Accounts Team"
    )
    seller_email = f"billing@{seller_name.lower().replace(' ', '').replace('/', '').replace('.', '')[:12]}.com"

    invoice_total = invoice.get("invoice_total")
    subtotal = invoice.get("subtotal")

    # Extract provenance IDs if present
    citations = [
        p.get("provenance_id") or p.get("field_name")
        for p in evidence.get("provenance", [])
    ][:4]

    subject = f"Urgent: Reconciliation Inquiry for Invoice {invoice_number} - {business_name}"

    body_lines = [
        f"Dear {seller_name} Accounts Team,",
        "",
        f"We are writing from the finance and audit department at {business_name} regarding invoice {invoice_number}.",
        "",
        "During our automated multi-tier financial audit review, our system identified a reconciliation discrepancy between the billed amount and supporting evidence.",
        "",
        "--- Audit Findings Summary ---",
    ]

    # Clean and include the audit answer excerpt
    clean_findings = audit_answer.strip()
    if clean_findings:
        body_lines.append(clean_findings)
    else:
        body_lines.append(f"Invoice Total: {invoice_total} | Subtotal: {subtotal}")

    body_lines.extend([
        "------------------------------",
        "",
        "Could you please review the attached invoice breakdown and provide an updated statement or credit note at your earliest convenience?",
        "",
        f"Referenced Provenance Citations: {', '.join(citations) if citations else 'Automated OCR extraction'}",
        "",
        "Sincerely,",
        f"{business_name} Finance & Audit Team",
    ])

    return EmailDraft(
        recipient_email=seller_email,
        recipient_name=seller_name,
        sender_name=f"{business_name} Finance Team",
        subject=subject,
        body_text="\n".join(body_lines),
        invoice_number=invoice_number,
        discrepancy_amount=float(invoice_total) if isinstance(invoice_total, (int, float)) else None,
        citations=citations,
        status="draft",
    )
