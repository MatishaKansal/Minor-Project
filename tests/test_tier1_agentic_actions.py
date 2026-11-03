"""Tests for Tier 1 Agentic Actions: Dispute Email Drafter, PDF Letter Generator, and Anomaly Patrol."""

import io
from backend.actions.agent_orchestrator import evaluate_agentic_actions
from backend.actions.email_drafter import draft_dispute_email
from backend.actions.pdf_letter_generator import generate_audit_letter_pdf
from backend.actions.reconciliation_patrol import run_reconciliation_patrol


def test_email_drafter():
    evidence = {
        "business": {"business_name": "Apex Tech Solutions", "gstin": "27AAPCA1234F1Z5"},
        "invoice": {
            "invoice_number": "INV-2026-0891",
            "invoice_total": 14160.0,
            "subtotal": 12000.0,
            "seller_name": "Cloud Providers Pvt Ltd",
        },
        "provenance": [{"provenance_id": "prov_total_1", "field_name": "invoice_total", "source_text": "14160.00"}],
    }

    draft = draft_dispute_email(
        query="Why is there a discrepancy of 2160?",
        audit_answer="Discrepancy of 2160 is due to 18% GST (CGST 1080 + SGST 1080).",
        evidence=evidence,
        stopped_tier="layer_2",
    )

    assert draft is not None
    assert draft.recipient_name == "Cloud Providers Pvt Ltd"
    assert "INV-2026-0891" in draft.subject
    assert "Discrepancy of 2160" in draft.body_text
    assert draft.status == "draft"


def test_pdf_letter_generator():
    evidence = {
        "business": {"business_name": "Apex Tech Solutions", "gstin": "27AAPCA1234F1Z5"},
        "invoice": {
            "invoice_number": "INV-2026-0891",
            "invoice_total": 14160.0,
            "subtotal": 12000.0,
            "seller_name": "Cloud Providers Pvt Ltd",
        },
        "provenance": [{"provenance_id": "prov_1", "field_name": "invoice_total", "source_text": "14160.00"}],
    }

    pdf_bytes = generate_audit_letter_pdf(
        query="Why is there a discrepancy of 2160?",
        audit_answer="GST tax calculation requires verification.",
        evidence=evidence,
        stopped_tier="layer_2",
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")


def test_reconciliation_patrol():
    invoices = [
        {"invoice_number": "INV-001", "invoice_total": "5000.00"},
        {"invoice_number": "INV-001", "invoice_total": "5000.00"},  # Duplicate
        {"invoice_number": "INV-002", "invoice_total": "12000.00"}, # Unmatched
    ]
    bank_txns = [
        {"amount": "5000.00", "description": "Vendor Transfer INV-001"},
        {"amount": "800.00", "description": "Unbacked Office Lunch Debit"},
    ]

    digest = run_reconciliation_patrol(invoices=invoices, bank_transactions=bank_txns)

    assert digest.total_invoices_scanned == 3
    assert digest.total_bank_transactions == 2
    assert digest.anomalies_detected >= 2

    categories = [a.category for a in digest.anomalies]
    assert "duplicate_payment" in categories
    assert "missing_receipt" in categories


def test_agent_orchestrator_proposals():
    evidence = {
        "business": {"business_name": "Test Firm", "gstin": "07AAAAA0000A1Z5"},
        "invoice": {
            "invoice_number": "INV-99",
            "invoice_total": 999.0,
            "seller_name": "Test Vendor",
        },
        "invoices": [{"invoice_number": "INV-99", "invoice_total": 999.0}],
        "provenance": [],
        "payments": [],
    }
    decision = {
        "answer": "Invoice has an unexpected difference or discrepancy.",
        "stopped_tier": "layer_2",
    }

    proposals = evaluate_agentic_actions(
        query="Check this discrepancy",
        audit_decision=decision,
        evidence=evidence,
    )

    assert len(proposals) >= 2
    types = [p.action_type for p in proposals]
    assert "draft_email" in types
    assert "generate_pdf" in types
