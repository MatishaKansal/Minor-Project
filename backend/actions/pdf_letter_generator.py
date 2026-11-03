"""Formal Audit & Tax Discrepancy Letter PDF Generator using ReportLab."""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LOGGER = logging.getLogger(__name__)


def generate_audit_letter_pdf(
    query: str,
    audit_answer: str,
    evidence: Dict[str, Any],
    stopped_tier: str = "layer_3",
    output_stream: Optional[io.BytesIO] = None,
) -> bytes:
    """
    Generate a formatted formal PDF audit letter from the cascade decision.
    """
    buffer = output_stream or io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=45,
        leftMargin=45,
        topMargin=45,
        bottomMargin=45,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1e293b"),
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#64748b"),
    )
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=10,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
    )
    callout_style = ParagraphStyle(
        "Callout",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#475569"),
    )

    invoice = evidence.get("invoice", {})
    biz = evidence.get("business", {})
    business_name = biz.get("business_name") or "Enterprise Client"
    gstin = biz.get("gstin") or "N/A"
    inv_num = invoice.get("invoice_number") or "N/A"
    inv_total = invoice.get("invoice_total") or "N/A"
    seller = invoice.get("seller_name") or invoice.get("party_name") or "Vendor"

    story = []

    # Header Banner
    story.append(Paragraph("AUDIT DISCREPANCY & RECONCILIATION NOTICE", title_style))
    story.append(Paragraph(f"Issued by {business_name} | GSTIN: {gstin} | Generated: {datetime.now().strftime('%B %d, %Y')}", subtitle_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#3b82f6"), spaceAfter=15))

    # Reference Metadata Table
    meta_data = [
        [Paragraph("<b>Target Invoice:</b>", body_style), Paragraph(str(inv_num), body_style),
         Paragraph("<b>Vendor / Counterparty:</b>", body_style), Paragraph(str(seller), body_style)],
        [Paragraph("<b>Reported Total:</b>", body_style), Paragraph(f"INR {inv_total}", body_style),
         Paragraph("<b>Decision Tier:</b>", body_style), Paragraph(f"{stopped_tier.upper()}", body_style)],
    ]
    t_meta = Table(meta_data, colWidths=[110, 150, 120, 140])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 14))

    # Query Section
    story.append(Paragraph("1. Audit Subject & Scope", section_heading))
    story.append(Paragraph(f"<b>Inquiry:</b> {query}", body_style))
    story.append(Spacer(1, 10))

    # Findings Section
    story.append(Paragraph("2. Authoritative Findings & Derivations", section_heading))
    clean_lines = [line.strip() for line in audit_answer.split("\n") if line.strip()]
    for line in clean_lines:
        story.append(Paragraph(line.replace("**", "<b>").replace("*", ""), body_style))
        story.append(Spacer(1, 3))

    story.append(Spacer(1, 10))

    # Citations / Provenance Section
    prov_items = evidence.get("provenance", [])
    if prov_items:
        story.append(Paragraph("3. Supporting Verification Provenance", section_heading))
        prov_rows = [["ID", "Field", "Extracted Source Snippet"]]
        for p in prov_items[:6]:
            prov_rows.append([
                Paragraph(str(p.get("provenance_id", "prov")), body_style),
                Paragraph(str(p.get("field_name", "")), body_style),
                Paragraph(str(p.get("source_text", p.get("field_value", "")))[:60], body_style),
            ])
        t_prov = Table(prov_rows, colWidths=[90, 110, 320])
        t_prov.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ]))
        story.append(t_prov)
        story.append(Spacer(1, 12))

    # Sign-off / Disclaimer
    story.append(Spacer(1, 10))
    story.append(Paragraph("This document was prepared by the automated 4-tier LLM-Cascade financial verification agent with deterministic and contextual verification gates. Please retain for internal tax and audit compliance.", callout_style))
    story.append(Spacer(1, 15))
    story.append(Paragraph("Authorized Signatory: __________________________________", body_style))

    doc.build(story)
    return buffer.getvalue()
