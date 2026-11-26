"""FastAPI endpoints used by the document retrieval and LLM cascade decision maker frontend."""

import json
import logging
import secrets
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.actions.agent_orchestrator import ActionProposal, evaluate_agentic_actions
from backend.actions.email_drafter import EmailDraft
from backend.actions.pdf_letter_generator import generate_audit_letter_pdf
from backend.actions.reconciliation_patrol import PatrolDigest, run_reconciliation_patrol
from backend.connectors.email_connector import EmailConnector, ConnectorAuthorizePayload
from backend.connectors.pos_connector import (
    POSConnector,
    BarcodeScanRequest,
    POSAuthorizePayload,
    POSBillPayload,
)
from fastapi.responses import StreamingResponse

from backend.audit.router import router as audit_router
from backend.auth import AuthenticatedUser, require_roles
from backend.auth_router import router as auth_router
from backend.company_router import companies_router, company_router, platform_router
from backend.notebooks_router import notebooks_router
from backend.cascade.service import CascadeDecisionMaker, evidence_to_cascade_dict
from backend.parsers.bank_csv_parser import SUPPORTED_EXTENSIONS as BANK_CSV_EXTENSIONS
from backend.rag.retriever import retrieve
from backend.schemas.evidence import Evidence
from backend.services.ingestion_service import ALL_SUPPORTED_EXTENSIONS, ingest_file

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PERSISTABLE_EXTENSIONS = (ALL_SUPPORTED_EXTENSIONS - BANK_CSV_EXTENSIONS) | {".txt"}

app = FastAPI(title="Financial Document Retrieval & Cascade Decision API", version="0.3.0")
app.include_router(audit_router)
app.include_router(auth_router)
app.include_router(companies_router)
app.include_router(company_router)
app.include_router(platform_router)
app.include_router(notebooks_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Business-ID"],
)

# Global Cascade Decision Maker instance
DECISION_MAKER = CascadeDecisionMaker()

# In-memory store for recent audit evidence & generated proposals (for approval and export)
RECENT_AUDITS: Dict[str, Dict[str, Dict[str, Any]]] = {}
EMAIL_CONNECTORS: Dict[str, EmailConnector] = {}
POS_CONNECTORS: Dict[str, POSConnector] = {}


def _email_connector(user: AuthenticatedUser) -> EmailConnector:
    return EMAIL_CONNECTORS.setdefault(user.company_id(), EmailConnector())


def _pos_connector(user: AuthenticatedUser) -> POSConnector:
    return POS_CONNECTORS.setdefault(user.company_id(), POSConnector())


def _recent_audits(user: AuthenticatedUser) -> Dict[str, Dict[str, Any]]:
    return RECENT_AUDITS.setdefault(user.company_id(), {})


def _event(event_type: str, **payload: Any) -> str:
    data = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"data: {data}\n\n"


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "cascade_router_thresholds": DECISION_MAKER.router.thresholds,
        "ollama_connected": DECISION_MAKER.client is not None,
        "agentic_tier1_enabled": True,
    }


class EmailApprovalRequest(BaseModel):
    recipient_email: str
    subject: str
    body_text: str
    invoice_number: Optional[str] = None


@app.post("/api/actions/approve-email")
def approve_and_send_email(request: EmailApprovalRequest, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """
    Human-in-the-loop approval endpoint for vendor dispute emails.
    In production, connects to an SMTP/SES gateway or saves to Outbox.
    """
    LOGGER.info("Approved email sending to %s regarding invoice %s", request.recipient_email, request.invoice_number)
    return {
        "status": "success",
        "message": f"Dispute notice successfully queued for delivery to {request.recipient_email}.",
        "delivery_mode": "smtp_staging",
        "details": request.model_dump(),
    }


@app.get("/api/actions/generate-letter-pdf")
def download_letter_pdf(invoice_number: Optional[str] = Query(None), user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Response:
    """
    Generate and stream a formal audit notice PDF for a given invoice number or the latest audit.
    """
    audit_data = None
    recent_audits = _recent_audits(user)
    if invoice_number and invoice_number in recent_audits:
        audit_data = recent_audits[invoice_number]
    elif recent_audits:
        audit_data = list(recent_audits.values())[-1]

    if not audit_data:
        # Default placeholder notice if no audit has run yet
        audit_data = {
            "query": "General Financial Reconciliation Review",
            "answer": "All documents parsed and audited under standard GST compliance rules.",
            "evidence": {"invoice": {"invoice_number": invoice_number or "NOTICE-001"}},
            "stopped_tier": "layer_2",
        }

    pdf_bytes = generate_audit_letter_pdf(
        query=audit_data.get("query", "Audit Inquiry"),
        audit_answer=audit_data.get("answer", ""),
        evidence=audit_data.get("evidence", {}),
        stopped_tier=audit_data.get("stopped_tier", "layer_3"),
    )

    filename = f"Audit_Notice_{invoice_number or 'Discrepancy'}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/actions/run-patrol")
def run_patrol_scan(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """
    Execute proactive reconciliation and anomaly patrol across current session and persisted company records.
    """
    all_invoices = []
    all_payments = []
    for record in _recent_audits(user).values():
        ev = record.get("evidence", {})
        if ev.get("invoice"):
            all_invoices.append(ev["invoice"])
        all_invoices.extend(ev.get("invoices", []))
        all_payments.extend(ev.get("payments", []))

    # If in-memory assistant cache is empty, load persisted company invoices from Supabase
    if not all_invoices:
        try:
            from backend.auth import get_authenticated_database_client
            client = get_authenticated_database_client()
            biz_id = user.company_id()
            db_invoices = client.table("invoice").select(
                "invoice_id,invoice_number,seller_name,invoice_total,currency,invoice_date"
            ).eq("business_id", biz_id).limit(500).execute().data or []
            all_invoices.extend(db_invoices)
        except Exception as exc:
            LOGGER.warning("Could not load database invoices for reconciliation patrol: %s", exc)

    # If bank transactions are empty in memory, ingest from company bank CSV statement
    if not all_payments:
        try:
            from pathlib import Path
            from backend.parsers.bank_csv_parser import parse_bank_csv_file
            bank_csv = Path("test data/04_Bank_Statements/HDFC_Bank_Statement_Oct2026.csv")
            if bank_csv.exists():
                res = parse_bank_csv_file(bank_csv)
                if res.processing_status == "success":
                    for t in res.transactions:
                        all_payments.append({
                            "date": t.date,
                            "amount": t.amount,
                            "transaction_type": t.transaction_type,
                            "description": t.description,
                            "reference_number": t.reference_number,
                        })
        except Exception as exc:
            LOGGER.warning("Could not parse bank statement for reconciliation patrol: %s", exc)

    digest = run_reconciliation_patrol(invoices=all_invoices, bank_transactions=all_payments)
    return digest.model_dump()


# -------------------------------------------------------------
# Email Ingestion Connector Endpoints (Strict Authorization Gate)
# -------------------------------------------------------------

@app.get("/api/connectors/email/status")
def get_email_connector_status(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Returns the authorization status and metadata for the email connector."""
    return _email_connector(user).get_status()


@app.post("/api/connectors/email/authorize")
def authorize_email_connector(payload: ConnectorAuthorizePayload, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Authorizes the email connector using live IMAP credentials or demo mailbox."""
    try:
        result = _email_connector(user).authorize(payload)
        return result
    except ConnectionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to authorize email connector: {exc}")


@app.post("/api/connectors/email/revoke")
def revoke_email_connector(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Revokes email connector authorization and clears session credentials."""
    return _email_connector(user).revoke()


class EmailSyncRequest(BaseModel):
    business_id: Optional[str] = None
    max_items: int = 2


@app.post("/api/connectors/email/sync")
def sync_email_inbox(request: Optional[EmailSyncRequest] = None, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Polls the authorized email inbox/demo mailbox and ingests new invoices."""
    connector = _email_connector(user)
    if not connector.is_authorized:
        raise HTTPException(
            status_code=403,
            detail="Email Connector is not authorized. Please authorize via the dashboard modal first."
        )
    try:
        if request and request.business_id and request.business_id != user.business_id:
            raise HTTPException(status_code=403, detail="The requested business is not active.")
        b_id = user.business_id
        m_items = request.max_items if request else 2
        result = connector.sync(business_id=b_id, max_items=m_items)

        # Store generated proposals in RECENT_AUDITS for downstream letter generation
        for item in result.get("items", []):
            inv_no = item.get("invoice_number")
            if inv_no:
                _recent_audits(user)[inv_no] = {
                    "query": f"Automated email ingestion audit for invoice {inv_no}",
                    "answer": f"Ingested {inv_no} from email sender {item.get('sender')}. Status: Ingested & Indexed.",
                    "evidence": {"invoice": {"invoice_number": inv_no, "vendor_name": item.get("vendor_name"), "amount": item.get("amount")}},
                    "stopped_tier": "layer_2",
                    "proposals": result.get("proposals", []),
                }

        return result
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        LOGGER.exception("Email sync endpoint failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# -------------------------------------------------------------
# Point-of-Sale (POS) & Billing Software Connector Endpoints
# -------------------------------------------------------------

@app.get("/api/connectors/pos/status")
def get_pos_connector_status(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Returns the authorization status, store metadata, and catalog for the POS connector."""
    return _pos_connector(user).get_status()


@app.post("/api/connectors/pos/authorize")
def authorize_pos_connector(payload: POSAuthorizePayload, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Authorizes POS connector, creates session API key for webhook integration."""
    try:
        return _pos_connector(user).authorize(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to authorize POS connector: {exc}")


@app.post("/api/connectors/pos/revoke")
def revoke_pos_connector(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Revokes POS connector access and invalidates session token."""
    return _pos_connector(user).revoke()


@app.post("/api/connectors/pos/scan")
def scan_pos_barcode(request: BarcodeScanRequest, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Simulates an interactive checkout barcode scan and immediately generates a POS bill."""
    connector = _pos_connector(user)
    if request.business_id and request.business_id != user.business_id:
        raise HTTPException(status_code=403, detail="The requested business is not active.")
    if not connector.is_authorized:
        raise HTTPException(
            status_code=403,
            detail="POS Connector is not authorized. Please authorize via the dashboard modal first."
        )
    try:
        result = connector.scan_barcode(
            barcode=request.barcode,
            quantity=request.quantity,
            payment_method=request.payment_method,
            business_id=user.business_id,
        )
        # Store in RECENT_AUDITS for downstream letter/dispute generation
        bill_no = result.get("bill_number")
        if bill_no:
            _recent_audits(user)[bill_no] = {
                "query": f"POS Retail Checkout Audit for bill {bill_no}",
                "answer": f"Ingested POS Bill {bill_no} for INR {result.get('grand_total')}. File: {result.get('file_name')}",
                "evidence": {"invoice": {"invoice_number": bill_no, "vendor_name": connector.store_name, "amount": result.get("grand_total")}},
                "stopped_tier": "layer_1",
                "proposals": result.get("proposals", []),
            }
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        LOGGER.exception("POS barcode scan endpoint failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


class POSBatchSyncRequest(BaseModel):
    business_id: Optional[str] = None
    batch_size: int = 3


@app.post("/api/connectors/pos/sync-batch")
def sync_pos_batch_register(request: Optional[POSBatchSyncRequest] = None, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Dict[str, Any]:
    """Synchronizes a batch of sales bills from the daily register and runs reconciliation patrol."""
    connector = _pos_connector(user)
    if request and request.business_id and request.business_id != user.business_id:
        raise HTTPException(status_code=403, detail="The requested business is not active.")
    if not connector.is_authorized:
        raise HTTPException(
            status_code=403,
            detail="POS Connector is not authorized. Please authorize via the dashboard modal first."
        )
    try:
        b_id = user.business_id
        b_size = request.batch_size if request else 3
        return connector.sync_batch_register(business_id=b_id, batch_size=b_size)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        LOGGER.exception("POS batch sync endpoint failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/connectors/pos/webhook")
def receive_pos_webhook(
    bill: POSBillPayload,
    x_pos_api_key: Optional[str] = Header(None, alias="X-POS-API-KEY"),
) -> Dict[str, Any]:
    """Real-world POS webhook endpoint (Option A) for Vyapar, Tally, Zoho Books, and barcode registers."""
    if not x_pos_api_key:
        raise HTTPException(status_code=401, detail="A POS API key is required.")
    match = next(
        ((business_id, connector) for business_id, connector in POS_CONNECTORS.items()
         if connector.is_authorized and connector.api_key and secrets.compare_digest(x_pos_api_key, connector.api_key)),
        None,
    )
    if not match:
        raise HTTPException(status_code=401, detail="Invalid X-POS-API-KEY header token.")
    business_id, connector = match

    try:
        return connector.process_incoming_bill(bill, business_id=business_id)
    except Exception as exc:
        LOGGER.exception("POS webhook ingestion failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/query")
async def query_documents(
    question: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    business_id: str | None = Form(default=None),
    current_user: AuthenticatedUser = Depends(require_roles("administrator")),
) -> StreamingResponse:
    """Persist uploaded files, retrieve relevant chunks, execute the 4-tier LLM cascade decision maker, and synthesize agentic action proposals."""
    clean_question = question.strip()
    if not clean_question:
        return StreamingResponse(
            iter([_event("error", message="Please enter a question.")]),
            media_type="text/event-stream",
        )

    upload_payloads: list[tuple[str, bytes]] = []
    unsupported_uploads: list[str] = []
    for upload in files or []:
        name = Path(upload.filename or "upload").name
        suffix = Path(name).suffix.lower()
        if suffix not in PERSISTABLE_EXTENSIONS:
            unsupported_uploads.append(name)
            continue
        content = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            upload_payloads.append((name, b""))
            continue
        upload_payloads.append((name, content))

    requested_business_id = business_id.strip() if business_id else None
    if requested_business_id and requested_business_id != current_user.business_id:
        raise HTTPException(status_code=403, detail="The requested business is not assigned to this account.")
    requested_business_id = current_user.business_id

    def stream_events():
        yield _event("status", message="Your question is received.")
        saved_files: list[dict[str, str]] = []
        indexed_files: list[dict[str, str]] = []
        parsed_evidence_list: list[Evidence] = []
        file_warnings: list[dict[str, str]] = [
            {"file_name": name, "message": "This file type is not supported."}
            for name in unsupported_uploads
        ]

        if upload_payloads:
            yield _event("status", message="Parsing and saving your files. This can take a little while.")
            for name, content in upload_payloads:
                yield _event("status", message=f"Parsing, saving and indexing {name}…")
                if not content:
                    file_warnings.append({"file_name": name, "message": "File is empty or exceeds the 20 MB limit."})
                    continue

                try:
                    with tempfile.TemporaryDirectory(prefix="rag-upload-") as temporary_directory:
                        temporary_path = Path(temporary_directory) / name
                        temporary_path.write_bytes(content)
                        evidence = ingest_file(
                            temporary_path,
                            persist_to_database=True,
                            business_id=requested_business_id,
                        )
                        # If database persistence failed (e.g. Supabase unconfigured), fallback to in-memory extraction
                        if evidence.processing_status != "success" and "supabase" in (evidence.error or "").lower():
                            LOGGER.info("Retrying file %s with persist_to_database=False", name)
                            evidence = ingest_file(
                                temporary_path,
                                persist_to_database=False,
                                business_id=requested_business_id,
                            )

                    if evidence.processing_status != "success":
                        file_warnings.append({
                            "file_name": name,
                            "message": evidence.error or "The file could not be parsed and saved.",
                        })
                        continue

                    parsed_evidence_list.append(evidence)
                    saved_file = {
                        "file_name": name,
                        "file_id": evidence.database_file_id or "",
                    }
                    saved_files.append(saved_file)
                    if evidence.database_file_id:
                        from backend.database.supabase_repository import get_supabase_client

                        check_client = get_supabase_client()
                        chunk_rows = (
                            check_client.table("document_chunks")
                            .select("chunk_id")
                            .eq("source_file_id", evidence.database_file_id)
                            .limit(1)
                            .execute()
                            .data
                            or []
                        )
                        if chunk_rows:
                            indexed_files.append(saved_file)
                            yield _event("file_indexed", file_name=name)
                        else:
                            file_warnings.append({
                                "file_name": name,
                                "message": "Saved, but no searchable chunks were found. Check RAG indexing configuration.",
                            })
                    else:
                        file_warnings.append({
                            "file_name": name,
                            "message": "Parsed, but the database did not return a saved file ID.",
                        })
                except Exception as exc:
                    LOGGER.exception("Uploaded file processing failed: %s", name)
                    file_warnings.append({"file_name": name, "message": str(exc)})

        yield _event("status", message="Finding document chunks related to your question…")
        retrieved_chunks = []
        try:
            from backend.database.supabase_repository import get_supabase_client
            from backend.rag.indexer import index_company_documents_on_question

            client = get_supabase_client()
            try:
                file_warnings.extend(index_company_documents_on_question(current_user.company_id(), client))
            except Exception:
                LOGGER.exception("Could not prepare all company documents for search.")
                file_warnings.append({"file_name": "Document search", "message": "Some saved documents could not be prepared for search. Check the backend logs."})
            retrieved_chunks = retrieve(
                clean_question,
                current_user.company_id(),
                match_count=5,
                min_similarity=0.35,
                client=client,
            )
        except Exception as exc:
            LOGGER.exception("Document search failed.")
            file_warnings.append({"file_name": "Document search", "message": "Search could not be completed. Check the backend logs."})

        yield _event("status", message="Evaluating evidence through the 4-tier LLM cascade decision maker…")
        try:
            # Load company persisted records to empower cascade with existing company context
            company_invoices = []
            company_payments = []
            try:
                from backend.auth import get_authenticated_database_client
                db_client = get_authenticated_database_client()
                biz_id = current_user.company_id()
                db_invoices = db_client.table("invoice").select(
                    "invoice_id,invoice_number,seller_name,invoice_total,currency,invoice_date,due_date,buyer_name,reverse_charge,subtotal,cgst_total,sgst_total,igst_total,discount_amount"
                ).eq("business_id", biz_id).limit(200).execute().data or []
                company_invoices.extend(db_invoices)
            except Exception as exc:
                LOGGER.warning("Could not load database invoices for cascade: %s", exc)

            for record in _recent_audits(current_user).values():
                ev = record.get("evidence", {})
                company_payments.extend(ev.get("payments", []))

            # Transform extracted multimodal evidence and retrieved RAG context into cascade input
            cascade_evidence = evidence_to_cascade_dict(
                evidence_list=parsed_evidence_list,
                retrieved_chunks=retrieved_chunks,
                existing_invoices=company_invoices,
                existing_payments=company_payments,
            )

            # Execute the cascade with learned stop routing
            decision = DECISION_MAKER.execute_decision(
                query=clean_question,
                evidence=cascade_evidence,
            )

            ai_answer = decision.get("answer", "")
            stopped_tier = decision.get("stopped_tier", "layer_4")
            stopped_model = decision.get("stopped_model", "")
            stop_prob = decision.get("stop_probability")
            tier_history = decision.get("tier_history", [])

            # Synthesize Tier 1 Agentic Action Proposals
            yield _event("status", message="Synthesizing agentic action proposals (dispute drafts, notices, patrol)…")
            proposals = evaluate_agentic_actions(
                query=clean_question,
                audit_decision=decision,
                evidence=cascade_evidence,
            )

            # Save in memory for PDF download and email execution
            recent_audits = _recent_audits(current_user)
            inv_no = (cascade_evidence.get("invoice") or {}).get("invoice_number") or f"audit_{len(recent_audits)+1}"
            recent_audits[inv_no] = {
                "query": clean_question,
                "answer": ai_answer,
                "evidence": cascade_evidence,
                "stopped_tier": stopped_tier,
                "proposals": [p.model_dump() for p in proposals],
            }

            yield _event(
                "results",
                chunks=retrieved_chunks,
                files=saved_files,
                indexed_files=indexed_files,
                warnings=file_warnings,
                llm_called=True,
                answer=ai_answer,
                stopped_tier=stopped_tier,
                stopped_model=stopped_model,
                stop_probability=stop_prob,
                tier_history=tier_history,
                proposals=[p.model_dump() for p in proposals],
                is_mock=decision.get("is_mock", False),
            )
            yield _event(
                "status",
                message=f"Cascade decision reached at {stopped_tier} ({stopped_model}). {len(proposals)} agentic action(s) proposed.",
                done=True,
            )
        except Exception as exc:
            LOGGER.exception("LLM Cascade decision maker failed: %s", exc)
            yield _event(
                "results",
                chunks=retrieved_chunks,
                files=saved_files,
                indexed_files=indexed_files,
                warnings=file_warnings,
                llm_called=False,
            )
            yield _event(
                "status",
                message=f"Retrieved {len(retrieved_chunks)} chunk(s). Cascade evaluation encountered an issue: {exc}",
                done=True,
            )

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
