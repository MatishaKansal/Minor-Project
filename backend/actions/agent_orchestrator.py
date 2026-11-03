"""Agent Action Orchestrator for Small Business Financial Assistant.

Evaluates cascade audit determinations, selects appropriate agentic tools,
and generates actionable proposals (Dispute Email Draft, Formal PDF Letter, Patrol Digest).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.actions.email_drafter import EmailDraft, draft_dispute_email
from backend.actions.pdf_letter_generator import generate_audit_letter_pdf
from backend.actions.reconciliation_patrol import PatrolDigest, run_reconciliation_patrol

LOGGER = logging.getLogger(__name__)


class ActionProposal(BaseModel):
    action_id: str
    action_type: str  # draft_email, generate_pdf, reconciliation_patrol
    title: str
    description: str
    payload: Dict[str, Any]
    status: str = "pending_approval"  # pending_approval, executed, dismissed


class AgenticExecutionResult(BaseModel):
    audit_answer: str
    stopped_tier: str
    stopped_model: str
    proposals: List[ActionProposal] = Field(default_factory=list)


def evaluate_agentic_actions(
    query: str,
    audit_decision: Dict[str, Any],
    evidence: Dict[str, Any],
) -> List[ActionProposal]:
    """
    Examines the cascade result and evidence to determine which agentic tasks should be proposed.
    """
    proposals: List[ActionProposal] = []
    answer = audit_decision.get("answer", "")
    stopped_tier = audit_decision.get("stopped_tier", "layer_4")

    # Check if discrepancy, tax mismatch, or audit issue exists
    has_discrepancy = any(
        kw in answer.lower() or kw in query.lower()
        for kw in ["discrepan", "mismatch", "incorrect", "unmatched", "cannot be determined", "not sum", "difference"]
    )
    has_invoice = bool(evidence.get("invoice") or evidence.get("invoices"))

    # 1. Propose Vendor Dispute Email Draft
    if has_invoice and (has_discrepancy or "email" in query.lower() or "dispute" in query.lower()):
        email_draft = draft_dispute_email(
            query=query,
            audit_answer=answer,
            evidence=evidence,
            stopped_tier=stopped_tier,
        )
        if email_draft:
            proposals.append(
                ActionProposal(
                    action_id=f"action_email_{email_draft.invoice_number}",
                    action_type="draft_email",
                    title=f"Draft Vendor Discrepancy Email ({email_draft.recipient_name})",
                    description=f"Auto-generated dispute email to {email_draft.recipient_email} citing line item provenance and audit findings.",
                    payload=email_draft.model_dump(),
                )
            )

    # 2. Propose Formal PDF Audit Letter
    if has_invoice:
        inv_no = (evidence.get("invoice") or {}).get("invoice_number", "AuditNotice")
        proposals.append(
            ActionProposal(
                action_id=f"action_pdf_{inv_no}",
                action_type="generate_pdf",
                title="Generate Formal Audit Notice PDF",
                description="Export a verified, print-ready PDF audit notice complete with GSTIN, provenance table, and signature line.",
                payload={
                    "download_url": f"/api/actions/generate-letter-pdf?invoice_number={inv_no}",
                    "invoice_number": inv_no,
                },
            )
        )

    # 3. Propose Reconciliation Patrol if multiple records exist
    invoices = evidence.get("invoices", [])
    payments = evidence.get("payments", [])
    if len(invoices) > 1 or len(payments) > 0 or "patrol" in query.lower() or "reconcil" in query.lower():
        patrol_res = run_reconciliation_patrol(invoices=invoices, bank_transactions=payments)
        proposals.append(
            ActionProposal(
                action_id="action_patrol_digest",
                action_type="reconciliation_patrol",
                title="Cross-Document Anomaly Patrol Digest",
                description=patrol_res.summary_message,
                payload=patrol_res.model_dump(),
            )
        )

    return proposals
