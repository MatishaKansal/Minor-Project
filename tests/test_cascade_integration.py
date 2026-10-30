"""Test the integration between multimodal extracted Evidence and LLM Cascade Decision Maker."""

from backend.cascade.service import CascadeDecisionMaker, evidence_to_cascade_dict
from backend.schemas.evidence import Evidence, EvidenceProvenance, InvoiceItem


def test_evidence_to_cascade_dict_mapping():
    evidence = Evidence(
        file_name="vendor_invoice_101.pdf",
        processing_status="success",
        invoice_number="INV-2026-101",
        date="2026-10-15",
        subtotal="50000.00",
        cgst_total="4500.00",
        sgst_total="4500.00",
        invoice_total="59000.00",
        currency="INR",
        items=[
            InvoiceItem(
                line_number=1,
                description="Server Infrastructure Consulting",
                taxable_value="50000.00",
                gst_rate="18.00",
                cgst_amount="4500.00",
                sgst_amount="4500.00",
                line_total="59000.00",
            )
        ],
        provenance=[
            EvidenceProvenance(
                field_name="invoice_total",
                value="59000.00",
                source_text="Invoice Total: ₹59,000.00",
                page_number=1,
                extraction_method="pymupdf",
            )
        ],
    )

    chunks = [
        {"chunk_id": "chunk_doc_1", "content": "Payment of 59000 INR was received via NEFT", "similarity": 0.85}
    ]

    cascade_dict = evidence_to_cascade_dict([evidence], retrieved_chunks=chunks)

    assert cascade_dict["invoice"]["invoice_number"] == "INV-2026-101"
    assert cascade_dict["invoice"]["invoice_total"] == 59000.0
    assert len(cascade_dict["items"]) == 1
    assert cascade_dict["items"][0]["description"] == "Server Infrastructure Consulting"
    assert len(cascade_dict["provenance"]) == 1
    assert len(cascade_dict["document_chunks"]) == 1
    assert cascade_dict["document_chunks"][0]["chunk_id"] == "chunk_doc_1"


def test_cascade_decision_maker_execution():
    decision_maker = CascadeDecisionMaker()
    
    evidence_dict = {
        "business": {"business_name": "Test Org", "gstin": "27AAPCA1234F1Z5"},
        "invoice": {
            "invoice_number": "INV-TEST",
            "subtotal": 1000.0,
            "cgst_total": 90.0,
            "sgst_total": 90.0,
            "invoice_total": 1180.0,
            "currency": "INR",
        },
        "items": [{"description": "Service A", "taxable_value": 1000.0, "line_total": 1180.0}],
        "provenance": [{"provenance_id": "prov_1", "field_name": "invoice_total", "field_value": "1180.0"}],
        "document_chunks": [],
    }

    result = decision_maker.execute_decision(
        query="Verify if tax was added to invoice total",
        evidence=evidence_dict,
        force_mock=True,
    )

    assert "answer" in result
    assert result["stopped_tier"] in ["layer_1", "layer_2", "layer_3", "layer_4"]
    assert len(result["tier_history"]) > 0
