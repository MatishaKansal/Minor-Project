"""Email Ingestion Connector for Small Business Financial Auditing System.

Supports:
- Option A: Production IMAP/SSL connections (Gmail, Outlook 365, Yahoo, Custom IMAP)
- Option B: Instant Demo Mailbox Simulation (for verification without credentials)
- Strict Frontend-driven Authorization Gate (blocks sync if unauthorized)
- Multimodal Attachment & Financial Email Body Extraction & Supabase Ingestion
- Automated Cascade Decision and Agentic Dispute Proposal Generation
"""

from __future__ import annotations

import email
from email.header import decode_header
import imaplib
import logging
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import time
from typing import Any, Dict, List, Optional
from datetime import datetime

from pydantic import BaseModel, Field

from backend.actions.agent_orchestrator import evaluate_agentic_actions
from backend.cascade.service import evidence_to_cascade_dict
from backend.services.ingestion_service import ALL_SUPPORTED_EXTENSIONS, ingest_file

LOGGER = logging.getLogger(__name__)

# Standard IMAP configurations
PROVIDER_DEFAULTS = {
    "gmail": {"host": "imap.gmail.com", "port": 993},
    "outlook": {"host": "outlook.office365.com", "port": 993},
    "yahoo": {"host": "imap.mail.yahoo.com", "port": 993},
    "custom": {"host": "", "port": 993},
    "demo": {"host": "demo-mailbox.internal", "port": 993},
}


class ConnectorAuthorizePayload(BaseModel):
    mode: str = "demo"  # "demo" or "live"
    provider: str = "demo"  # "demo", "gmail", "outlook", "yahoo", "custom"
    email_address: str = "demo-accounting@smallbiz.local"
    password: Optional[str] = None  # App password for live IMAP (kept in-memory only)
    imap_host: Optional[str] = None
    imap_port: int = 993
    folder: str = "INBOX"


class IngestedEmailItem(BaseModel):
    email_id: str
    sender: str
    recipient: str
    subject: str
    date: str
    attachments: List[str] = Field(default_factory=list)
    status: str = "processed"
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    chunks_indexed: int = 0
    anomalies_found: List[str] = Field(default_factory=list)


class EmailConnector:
    """Manages email mailbox authorization and attachment synchronization."""

    def __init__(self) -> None:
        self.is_authorized: bool = False
        self.mode: str = "demo"
        self.provider: str = "demo"
        self.email_address: Optional[str] = None
        self.imap_host: Optional[str] = None
        self.imap_port: int = 993
        self.folder: str = "INBOX"
        self._password: Optional[str] = None  # In-memory only, never persisted
        self.last_synced_at: Optional[str] = None
        self.synced_count: int = 0
        self.recent_items: List[Dict[str, Any]] = []
        self._demo_cursor: int = 0

    def get_status(self) -> Dict[str, Any]:
        """Returns connector authorization status and masked metadata."""
        return {
            "is_authorized": self.is_authorized,
            "mode": self.mode,
            "provider": self.provider,
            "email_address": self.email_address,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "last_synced_at": self.last_synced_at,
            "synced_count": self.synced_count,
            "recent_items": self.recent_items[-5:] if self.recent_items else [],
        }

    def authorize(self, payload: ConnectorAuthorizePayload) -> Dict[str, Any]:
        """Authorizes the connector via the frontend UI.
        
        Validates live IMAP credentials if mode == 'live', or sets up demo mailbox if mode == 'demo'.
        """
        mode = payload.mode.lower()
        if mode == "demo":
            self.is_authorized = True
            self.mode = "demo"
            self.provider = "demo"
            self.email_address = payload.email_address or "demo-accounting@smallbiz.local"
            self.imap_host = "demo-mailbox.internal"
            self.imap_port = 993
            self._password = None
            LOGGER.info("Email Connector authorized in DEMO mode for %s", self.email_address)
            return {
                "success": True,
                "message": "Demo Mailbox successfully authorized. Ready to sync sample small-business invoices.",
                "status": self.get_status(),
            }

        # Live IMAP validation
        provider = payload.provider.lower()
        host = payload.imap_host or PROVIDER_DEFAULTS.get(provider, {}).get("host")
        port = payload.imap_port or PROVIDER_DEFAULTS.get(provider, {}).get("port", 993)
        username = payload.email_address.strip() if payload.email_address else ""
        password = payload.password.strip() if payload.password else ""

        if not host:
            raise ValueError(f"IMAP host required for provider '{provider}'.")
        if not username or not password:
            raise ValueError("Email address and App Password are required for live IMAP connection.")

        # Test IMAP SSL login
        try:
            ssl_context = ssl.create_default_context()
            with imaplib.IMAP4_SSL(host, port, ssl_context=ssl_context) as imap_client:
                imap_client.login(username, password)
                # Verify mailbox selection
                typ, _ = imap_client.select(payload.folder or "INBOX", readonly=True)
                if typ != "OK":
                    raise ConnectionError(f"Could not open folder '{payload.folder}'.")
                imap_client.close()
                imap_client.logout()
        except Exception as exc:
            LOGGER.error("Live IMAP connection test failed for %s@%s: %s", username, host, exc)
            self.is_authorized = False
            raise ConnectionError(
                f"IMAP Authentication failed for {username}: {str(exc)}. "
                "Ensure IMAP is enabled and an App Password is used."
            ) from exc

        self.is_authorized = True
        self.mode = "live"
        self.provider = provider
        self.email_address = username
        self.imap_host = host
        self.imap_port = port
        self.folder = payload.folder or "INBOX"
        self._password = password  # Stored in-memory session only

        LOGGER.info("Email Connector authorized in LIVE mode for %s on %s:%s", username, host, port)
        return {
            "success": True,
            "message": f"Successfully authorized live IMAP connection for {username}.",
            "status": self.get_status(),
        }

    def revoke(self) -> Dict[str, Any]:
        """Revokes mailbox authorization and clears stored session credentials."""
        self.is_authorized = False
        self.email_address = None
        self._password = None
        LOGGER.info("Email Connector authorization revoked.")
        return {
            "success": True,
            "message": "Email Connector access has been revoked.",
            "status": self.get_status(),
        }

    def sync(
        self,
        business_id: Optional[str] = None,
        max_items: int = 2,
    ) -> Dict[str, Any]:
        """Polls and syncs emails and attachments.
        
        Strictly enforces the authorization gate.
        """
        if not self.is_authorized:
            raise PermissionError(
                "Email Connector is NOT authorized. "
                "The business owner must explicitly authorize email access via the frontend."
            )

        sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.mode == "demo":
            results = self._sync_demo_mailbox(business_id=business_id, count=max_items)
        else:
            results = self._sync_live_mailbox(business_id=business_id, max_items=max_items)

        self.last_synced_at = sync_time
        self.synced_count += len(results["items"])
        self.recent_items.extend(results["items"])

        return {
            "success": True,
            "timestamp": sync_time,
            "mode": self.mode,
            "email_account": self.email_address,
            "emails_scanned": results["emails_scanned"],
            "invoices_ingested": len(results["items"]),
            "items": results["items"],
            "proposals": results["proposals"],
            "message": results["message"],
        }

    def _sync_demo_mailbox(
        self,
        business_id: Optional[str] = None,
        count: int = 2,
    ) -> Dict[str, Any]:
        """Simulates fetching vendor invoice emails from a demo business inbox."""
        base_dir = Path(__file__).resolve().parent.parent.parent
        pdf_dir = base_dir / "dataset" / "pdf"
        txt_dir = base_dir / "dataset" / "text"

        # Catalog of rich small-business sample vendor email invoices
        demo_catalog = [
            {
                "email_id": "MSG-DEMO-901",
                "sender": "billing@redbullindia.com",
                "recipient": self.email_address or "accounts@smallbiz.local",
                "subject": "Tax Invoice for Energy Drinks Consignment - Red Bull India Pvt Ltd",
                "date": "2026-10-10",
                "file_path": pdf_dir / "RedBullIndiaPvt.Ltd_4203510047.pdf",
                "vendor_hint": "Red Bull India Pvt Ltd",
            },
            {
                "email_id": "MSG-DEMO-902",
                "sender": "invoicing@gujralenterprises.in",
                "recipient": self.email_address or "accounts@smallbiz.local",
                "subject": "Monthly Supply Invoice #4203304681 - Gujral Enterprises",
                "date": "2026-10-12",
                "file_path": pdf_dir / "GujralEnterprises_4203304681 (1).pdf",
                "vendor_hint": "Gujral Enterprises",
            },
            {
                "email_id": "MSG-DEMO-903",
                "sender": "billing@shc-marketing.com",
                "recipient": self.email_address or "accounts@smallbiz.local",
                "subject": "Marketing Services Invoice #4203304680 - S.H.C Marketing",
                "date": "2026-10-15",
                "file_path": pdf_dir / "SH.C.Marketing_4203304680 (1).pdf",
                "vendor_hint": "S.H.C Marketing",
            },
            {
                "email_id": "MSG-DEMO-904",
                "sender": "procurement@smallbiz.com",
                "recipient": self.email_address or "finance@northstarconsulting.com",
                "subject": "Supplier Payment Request - Invoice #8004",
                "date": "2026-09-14",
                "file_path": txt_dir / "financial_email_004.txt",
                "vendor_hint": "Northstar Consulting",
            },
            {
                "email_id": "MSG-DEMO-905",
                "sender": "billing@acmesupplies.com",
                "recipient": self.email_address or "accounts@smallbiz.com",
                "subject": "Payment Reminder & Invoice Statement #8001",
                "date": "2026-09-09",
                "file_path": txt_dir / "financial_email_001.txt",
                "vendor_hint": "Acme Supplies",
            },
        ]

        items_to_process = []
        for i in range(count):
            idx = (self._demo_cursor + i) % len(demo_catalog)
            items_to_process.append(demo_catalog[idx])
        self._demo_cursor = (self._demo_cursor + count) % len(demo_catalog)

        processed_items: List[Dict[str, Any]] = []
        all_proposals: List[Dict[str, Any]] = []
        ingested_evidence_list = []

        for entry in items_to_process:
            file_path = entry["file_path"]
            if not file_path.exists():
                LOGGER.warning("Demo file not found: %s", file_path)
                continue

            # Ingest file into Supabase & vector chunks
            try:
                evidence = ingest_file(
                    file_path=file_path,
                    persist_to_database=True,
                    business_id=business_id,
                )
                ingested_evidence_list.append(evidence)

                inv_num = getattr(evidence, "invoice_number", None) or entry["email_id"]
                tot_amt = getattr(evidence, "invoice_total", None) or getattr(evidence, "amount", None)
                vendor = getattr(evidence, "seller_name", None) or getattr(evidence, "party_name", None) or entry["vendor_hint"]

                processed_items.append({
                    "email_id": entry["email_id"],
                    "sender": entry["sender"],
                    "recipient": entry["recipient"],
                    "subject": entry["subject"],
                    "date": entry["date"],
                    "attachments": [file_path.name],
                    "status": "ingested_and_indexed",
                    "invoice_number": inv_num,
                    "vendor_name": vendor,
                    "amount": tot_amt,
                    "database_file_id": evidence.database_file_id or "",
                })
            except Exception as exc:
                LOGGER.exception("Error ingesting demo mailbox file %s: %s", file_path.name, exc)

        # Synthesize Agentic Action Proposals for newly synced invoices
        if ingested_evidence_list:
            try:
                cascade_evidence = evidence_to_cascade_dict(
                    evidence_list=ingested_evidence_list,
                    retrieved_chunks=[],
                )
                audit_decision = {
                    "answer": f"Audit of ingested mailbox invoices for {len(processed_items)} vendor bill(s). Checking for rate variances and line item tax compliance.",
                    "stopped_tier": "layer_2",
                }
                proposals = evaluate_agentic_actions(
                    query="Verify ingested vendor invoice details and calculate discrepancy",
                    audit_decision=audit_decision,
                    evidence=cascade_evidence,
                )
                all_proposals = [p.model_dump() for p in proposals]
            except Exception as exc:
                LOGGER.warning("Could not evaluate action proposals on mailbox sync: %s", exc)

        return {
            "emails_scanned": len(items_to_process),
            "items": processed_items,
            "proposals": all_proposals,
            "message": f"Successfully pulled and indexed {len(processed_items)} invoice document(s) from Demo Mailbox.",
        }

    def _sync_live_mailbox(
        self,
        business_id: Optional[str] = None,
        max_items: int = 5,
    ) -> Dict[str, Any]:
        """Connects via IMAP4_SSL, searches for invoice emails, extracts attachments, and ingests them."""
        if not self.imap_host or not self.email_address or not self._password:
            raise ValueError("Live IMAP session credentials missing. Re-authorize via dashboard.")

        processed_items: List[Dict[str, Any]] = []
        ingested_evidence_list = []
        emails_scanned = 0

        try:
            ssl_context = ssl.create_default_context()
            with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=ssl_context) as client:
                client.login(self.email_address, self._password)
                client.select(self.folder, readonly=False)

                # Search for unread emails first, then all recent emails
                typ, msg_ids = client.search(None, "UNSEEN")
                ids = msg_ids[0].split() if typ == "OK" and msg_ids[0] else []
                if not ids:
                    typ, msg_ids = client.search(None, "ALL")
                    ids = msg_ids[0].split() if typ == "OK" and msg_ids[0] else []

                # Take the most recent up to max_items
                recent_ids = ids[-max_items:] if ids else []
                emails_scanned = len(recent_ids)

                for msg_id in reversed(recent_ids):
                    res, data = client.fetch(msg_id, "(RFC822)")
                    if res != "OK" or not data or not data[0]:
                        continue

                    raw_email = data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # Extract Subject
                    subject = self._decode_header_str(msg.get("Subject", "No Subject"))
                    sender = self._decode_header_str(msg.get("From", "Unknown Sender"))
                    date_str = msg.get("Date", "")

                    extracted_attachments: List[str] = []
                    email_body_text = ""

                    # Process multipart / parts
                    with tempfile.TemporaryDirectory(prefix="email-ingest-") as tmpdir:
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))

                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                try:
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        email_body_text += payload.decode(part.get_content_charset() or "utf-8", errors="replace") + "\n"
                                except Exception:
                                    pass

                            # Check for attachments
                            filename = part.get_filename()
                            if filename:
                                filename = self._decode_header_str(filename)
                                suffix = Path(filename).suffix.lower()
                                if suffix in ALL_SUPPORTED_EXTENSIONS:
                                    attachment_data = part.get_payload(decode=True)
                                    if attachment_data:
                                        att_path = Path(tmpdir) / filename
                                        att_path.write_bytes(attachment_data)
                                        evidence = ingest_file(
                                            file_path=att_path,
                                            persist_to_database=True,
                                            business_id=business_id,
                                        )
                                        ingested_evidence_list.append(evidence)
                                        extracted_attachments.append(filename)

                        # If no attachments found, but email body has financial contents, ingest body as .txt
                        if not extracted_attachments and email_body_text.strip():
                            keywords = ["invoice", "receipt", "payment", "bill", "tax", "gst", "amount", "due"]
                            if any(k in email_body_text.lower() or k in subject.lower() for k in keywords):
                                safe_subject = "".join(c for c in subject if c.isalnum() or c in " _-")[:30].strip() or "email_invoice"
                                txt_filename = f"{safe_subject}_{msg_id.decode() if isinstance(msg_id, bytes) else msg_id}.txt"
                                txt_path = Path(tmpdir) / txt_filename
                                txt_path.write_text(
                                    f"From: {sender}\nSubject: {subject}\nDate: {date_str}\n\n{email_body_text}",
                                    encoding="utf-8"
                                )
                                evidence = ingest_file(
                                    file_path=txt_path,
                                    persist_to_database=True,
                                    business_id=business_id,
                                )
                                ingested_evidence_list.append(evidence)
                                extracted_attachments.append(txt_filename)

                    if extracted_attachments:
                        processed_items.append({
                            "email_id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                            "sender": sender,
                            "recipient": self.email_address,
                            "subject": subject,
                            "date": date_str,
                            "attachments": extracted_attachments,
                            "status": "ingested_and_indexed",
                        })

                client.close()
                client.logout()

        except Exception as exc:
            LOGGER.exception("Live IMAP mailbox sync failed: %s", exc)
            raise ConnectionError(f"IMAP mailbox synchronization failed: {exc}") from exc

        # Synthesize Agentic Action Proposals for live synced files
        all_proposals: List[Dict[str, Any]] = []
        if ingested_evidence_list:
            try:
                cascade_evidence = evidence_to_cascade_dict(
                    evidence_list=ingested_evidence_list,
                    retrieved_chunks=[],
                )
                audit_decision = {
                    "answer": f"Audit of {len(processed_items)} live mailbox email(s). Extracted {len(ingested_evidence_list)} document(s).",
                    "stopped_tier": "layer_2",
                }
                proposals = evaluate_agentic_actions(
                    query="Verify live email invoice details and tax breakdown",
                    audit_decision=audit_decision,
                    evidence=cascade_evidence,
                )
                all_proposals = [p.model_dump() for p in proposals]
            except Exception as exc:
                LOGGER.warning("Could not evaluate action proposals for live sync: %s", exc)

        return {
            "emails_scanned": emails_scanned,
            "items": processed_items,
            "proposals": all_proposals,
            "message": f"Scanned {emails_scanned} email(s) from live IMAP server. Successfully extracted and indexed {len(processed_items)} document(s).",
        }

    @staticmethod
    def _decode_header_str(header_value: str) -> str:
        """Decodes MIME encoded header strings like =?utf-8?b?...?="""
        decoded_parts = []
        for text, encoding in decode_header(header_value):
            if isinstance(text, bytes):
                decoded_parts.append(text.decode(encoding or "utf-8", errors="replace"))
            else:
                decoded_parts.append(str(text))
        return " ".join(decoded_parts)


# Global singleton instance
EMAIL_CONNECTOR = EmailConnector()
