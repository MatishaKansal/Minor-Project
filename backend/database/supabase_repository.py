"""Supabase persistence for parsed image evidence."""

import hashlib
import logging
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from supabase import Client, create_client

LOGGER = logging.getLogger(__name__)
PROJECT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class SupabaseConfigurationError(RuntimeError):
    """Raised when the backend Supabase configuration is incomplete."""


class BusinessContextError(RuntimeError):
    """Raised when an active business cannot be resolved safely."""


class StorageConfigurationError(RuntimeError):
    """Raised when no unambiguous Supabase Storage bucket is available."""


def get_supabase_client() -> Client:
    load_dotenv(dotenv_path=PROJECT_ENV_FILE)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url:
        raise SupabaseConfigurationError(
            f"SUPABASE_URL is missing. Add it to {PROJECT_ENV_FILE}."
        )
    if not key:
        raise SupabaseConfigurationError(
            "SUPABASE_SERVICE_ROLE_KEY is missing. Add the backend-only service-role "
            f"key to {PROJECT_ENV_FILE}; do not use the publishable key for server writes."
        )
    return create_client(url, key)


def get_or_create_default_business(client: Client | None = None) -> str:
    """Resolve the sole business, create the demo business when empty, or fail safely."""
    supabase = client or get_supabase_client()
    response = supabase.table("business").select("business_id").execute()
    businesses = response.data or []
    if len(businesses) == 1:
        business_id = businesses[0]["business_id"]
        LOGGER.info("Resolved active business: %s", business_id)
        return business_id
    if len(businesses) > 1:
        raise BusinessContextError(
            "Multiple businesses exist. Configure an active business context instead "
            "of choosing an arbitrary business."
        )

    created = supabase.table("business").insert(
        {"business_name": "My Business", "default_currency": "INR"}
    ).execute()
    if not created.data or not created.data[0].get("business_id"):
        raise BusinessContextError("Could not create the default business in Supabase")
    business_id = created.data[0]["business_id"]
    LOGGER.info("Created and resolved default business: %s", business_id)
    return business_id


def _normalise_gstin(value: str | None) -> str | None:
    """Return a compact GSTIN only when it has the expected 15-character shape."""
    if not value:
        return None
    compact = re.sub(r"[^0-9A-Z]", "", value.upper())
    if re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", compact):
        return compact
    return None


def _sync_business_profile(supabase: Client, business_id: str, evidence: Any) -> None:
    """Fill missing business identity fields from the document's seller details."""
    seller_gstin = _normalise_gstin(getattr(evidence, "seller_gstin", None))
    seller_name = getattr(evidence, "seller_name", None) or getattr(evidence, "party_name", None)
    seller_pan = getattr(evidence, "seller_pan", None)
    seller_address = getattr(evidence, "seller_address", None)
    currency = getattr(evidence, "currency", None)

    profile: dict[str, Any] = {}
    if seller_name:
        profile["business_name"] = str(seller_name).strip()
    if seller_gstin:
        profile["gstin"] = seller_gstin
        profile["state_code"] = seller_gstin[:2]
        seller_pan = seller_pan or seller_gstin[2:12]
    if seller_pan:
        profile["pan"] = str(seller_pan).strip().upper()
    if isinstance(seller_address, dict) and seller_address:
        profile["address"] = seller_address
    if currency:
        profile["default_currency"] = str(currency).strip().upper()

    if not profile:
        LOGGER.info("No seller identity fields available to update business %s", business_id)
        return

    # Update only fields present in the parsed evidence. Database errors are
    # intentionally allowed to propagate so ingestion cannot report false success.
    supabase.table("business").update(profile).eq("business_id", business_id).execute()
    LOGGER.info("Synchronized business profile fields for %s: %s", business_id, sorted(profile))


def get_storage_bucket(client: Client) -> str:
    configured = os.getenv("SUPABASE_STORAGE_BUCKET")
    if configured:
        return configured
    buckets = client.storage.list_buckets()
    bucket_names = [
        bucket.get("name") if isinstance(bucket, dict) else getattr(bucket, "name", None)
        for bucket in buckets
    ]
    bucket_names = [name for name in bucket_names if name]
    if len(bucket_names) == 1:
        return bucket_names[0]
    if not bucket_names:
        raise StorageConfigurationError(
            "No Supabase Storage bucket exists. Create a bucket and set "
            "SUPABASE_STORAGE_BUCKET, or configure exactly one existing bucket."
        )
    raise StorageConfigurationError(
        "Multiple Supabase Storage buckets exist. Set SUPABASE_STORAGE_BUCKET "
        "to select the document bucket explicitly."
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_confidence(confidence_score: float | None) -> float | None:
    if confidence_score is None:
        return None
    return round(max(0.0, min(100.0, confidence_score)), 2)


def _database_date(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    # Strip ordinals like 1st, 2nd, 3rd, 19th
    cleaned_no_ordinal = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", cleaned, flags=re.IGNORECASE)
    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%d/%m/%y",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
    )
    for pattern in formats:
        try:
            return datetime.strptime(cleaned_no_ordinal, pattern).date().isoformat()
        except ValueError:
            continue
    # Month + Day without year: default to current year
    for pattern in ("%B %d", "%b %d", "%d %B", "%d %b"):
        try:
            dt = datetime.strptime(cleaned_no_ordinal, pattern)
            return dt.replace(year=datetime.now().year).date().isoformat()
        except ValueError:
            continue

    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
    if match:
        return match.group(0)
    return None


def _insert_invoice_records(
    supabase: Client,
    file_id: str,
    business_id: str,
    evidence: Any,
) -> dict[str, str]:
    """Insert invoice header, extraction run, item rows, and field provenance."""
    invoice_date = _database_date(getattr(evidence, "invoice_date", None) or evidence.date)
    invoice_total = getattr(evidence, "invoice_total", None) or evidence.amount
    invoice_row = {
        "business_id": business_id,
        "file_id": file_id,
        "invoice_number": evidence.invoice_number,
        "invoice_date": invoice_date,
        "due_date": _database_date(getattr(evidence, "due_date", None)),
        "invoice_type": getattr(evidence, "invoice_type", None),
        "seller_name": getattr(evidence, "seller_name", None) or evidence.party_name,
        "seller_gstin": getattr(evidence, "seller_gstin", None),
        "seller_pan": getattr(evidence, "seller_pan", None),
        "seller_address": getattr(evidence, "seller_address", None),
        "buyer_name": getattr(evidence, "buyer_name", None),
        "buyer_gstin": getattr(evidence, "buyer_gstin", None),
        "buyer_pan": getattr(evidence, "buyer_pan", None),
        "buyer_address": getattr(evidence, "buyer_address", None),
        "place_of_supply": getattr(evidence, "place_of_supply", None),
        "reverse_charge": getattr(evidence, "reverse_charge", None),
        "subtotal": getattr(evidence, "subtotal", None),
        "discount_amount": getattr(evidence, "discount_amount", None),
        "cgst_total": getattr(evidence, "cgst_total", None),
        "sgst_total": getattr(evidence, "sgst_total", None),
        "igst_total": getattr(evidence, "igst_total", None),
        "cess_total": getattr(evidence, "cess_total", None),
        "round_off": getattr(evidence, "round_off", None),
        "invoice_total": invoice_total,
        "currency": getattr(evidence, "currency", None) or "INR",
        "payment_terms": getattr(evidence, "payment_terms", None),
        "notes": getattr(evidence, "notes", None),
    }
    existing_invoices = (
        supabase.table("invoice")
        .select("invoice_id")
        .eq("file_id", file_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing_invoices:
        invoice_id = existing_invoices[0]["invoice_id"]
        supabase.table("invoice").update(invoice_row).eq("invoice_id", invoice_id).execute()
    else:
        invoice_response = supabase.table("invoice").insert(invoice_row).execute()
        if not invoice_response.data:
            raise RuntimeError("Supabase did not return the inserted invoice row")
        invoice_id = invoice_response.data[0]["invoice_id"]

    extraction_method = getattr(evidence, "source_type", "unknown")
    extraction_row = {
        "invoice_id": invoice_id,
        "extraction_status": getattr(evidence, "processing_status", "success"),
        "extraction_method": extraction_method,
        "overall_confidence": _database_confidence(getattr(evidence, "confidence_score", None)),
        "raw_text": getattr(evidence, "extracted_text", ""),
        "error_message": getattr(evidence, "error", None),
    }
    existing_extractions = (
        supabase.table("invoice_extraction")
        .select("extraction_id")
        .eq("invoice_id", invoice_id)
        .order("processed_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing_extractions:
        extraction_id = existing_extractions[0]["extraction_id"]
        supabase.table("invoice_extraction").update(extraction_row).eq(
            "extraction_id", extraction_id
        ).execute()
    else:
        extraction_response = supabase.table("invoice_extraction").insert(extraction_row).execute()
        extraction_id = extraction_response.data[0]["extraction_id"] if extraction_response.data else None

    item_rows = []
    for index, item in enumerate(getattr(evidence, "items", []) or [], start=1):
        item_data = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        allowed = {
            key: item_data[key]
            for key in (
                "description", "hsn_sac_code", "quantity", "unit", "unit_price",
                "discount_amount", "taxable_value", "gst_rate", "cgst_amount",
                "sgst_amount", "igst_amount", "cess_amount", "line_total",
            )
            if key in item_data
        }
        item_rows.append({
            "invoice_id": invoice_id,
            "line_number": item_data.get("line_number") or index,
            **allowed,
        })
    existing_items = (
        supabase.table("invoice_item")
        .select("item_id")
        .eq("invoice_id", invoice_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if item_rows and not existing_items:
        supabase.table("invoice_item").insert(item_rows).execute()

    payment_rows = []
    for payment in getattr(evidence, "payments", []) or []:
        payment_data = payment.model_dump(exclude_none=True) if hasattr(payment, "model_dump") else dict(payment)
        payment_rows.append({
            "invoice_id": invoice_id,
            "payment_date": _database_date(payment_data.get("payment_date")),
            "amount": payment_data.get("amount"),
            "payment_mode": payment_data.get("payment_mode"),
            "reference_number": payment_data.get("reference_number"),
        })
    existing_payments = (
        supabase.table("invoice_payment")
        .select("payment_id")
        .eq("invoice_id", invoice_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if payment_rows and not existing_payments:
        supabase.table("invoice_payment").insert(payment_rows).execute()

    provenance_rows = []
    for item in evidence.provenance:
        if isinstance(item, dict):
            field_name = item.get("field_name")
            value = item.get("value")
            source_text = item.get("source_text", "")
            line_number = item.get("line_number")
            page_number = item.get("page_number", 1)
            extraction_method = item.get("extraction_method", "OCR")
            confidence_score = item.get("confidence")
        else:
            field_name = getattr(item, "field_name", None)
            value = getattr(item, "value", None)
            source_text = getattr(item, "source_text", "")
            line_number = getattr(item, "line_number", None)
            page_number = getattr(item, "page_number", 1)
            extraction_method = getattr(item, "extraction_method", "OCR")
            confidence_score = getattr(item, "confidence", None)

        if field_name:
            provenance_rows.append({
                "invoice_id": invoice_id,
                "extraction_id": extraction_id,
                "field_name": field_name,
                "field_value": str(value) if value is not None else None,
                "page_number": page_number or 1,
                "source_text": source_text,
                # The current invoice_field_provenance table stores the method
                # inside its JSON location column; it has no top-level
                # extraction_method column.
                "location": {"line_number": line_number, "extraction_method": extraction_method},
                "confidence": _database_confidence(confidence_score),
            })
    existing_provenance = (
        supabase.table("invoice_field_provenance")
        .select("provenance_id")
        .eq("invoice_id", invoice_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if provenance_rows and not existing_provenance:
        supabase.table("invoice_field_provenance").insert(provenance_rows).execute()
    LOGGER.info("Persisted invoice: file_id=%s invoice_id=%s", file_id, invoice_id)
    return {"file_id": file_id, "invoice_id": invoice_id, "evidence_id": invoice_id}


def persist_evidence(
    file_path: str | os.PathLike[str],
    evidence: Any,
    business_id: str | None = None,
    file_type: str = "receipt",
    storage_path: str | None = None,
    client: Client | None = None,
) -> dict[str, str]:
    """Insert a parsed document, its financial evidence, and provenance rows."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Document file does not exist: {path}")
    supabase = client or get_supabase_client()
    business_id = business_id.strip() if business_id else get_or_create_default_business(supabase)
    _sync_business_profile(supabase, business_id, evidence)
    bucket = get_storage_bucket(supabase)
    file_hash = _file_hash(path)
    existing_files = (
        supabase.table("source_file")
        .select("file_id")
        .eq("business_id", business_id)
        .eq("file_hash", file_hash)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing_files:
        file_id = existing_files[0]["file_id"]
        LOGGER.info("Reusing existing source_file for identical content: file_id=%s", file_id)
        return _insert_invoice_records(supabase, file_id, business_id, evidence)

    object_path = f"{business_id}/{uuid4()}-{path.name}"
    with path.open("rb") as source_file:
        supabase.storage.from_(bucket).upload(
            object_path,
            source_file,
            {"content-type": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "upsert": "false"},
        )
    source_row = {
        "business_id": business_id,
        "file_name": path.name,
        "mime_type": mimetypes.guess_type(path.name)[0],
        "storage_path": storage_path or f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "page_count": getattr(evidence, "page_count", None),
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_invoice_records(supabase, file_id, business_id, evidence)


def persist_text_evidence(
    text: str,
    evidence: Any,
    source_name: str = "chat_input.txt",
    business_id: str | None = None,
    file_type: str = "receipt",
    client: Client | None = None,
) -> dict[str, str]:
    """Insert raw text/chat message into Supabase storage, source_file, and evidence tables."""
    supabase = client or get_supabase_client()
    business_id = business_id.strip() if business_id else get_or_create_default_business(supabase)
    _sync_business_profile(supabase, business_id, evidence)
    bucket = get_storage_bucket(supabase)
    safe_name = source_name if source_name.endswith(".txt") else f"{source_name}.txt"
    object_path = f"{business_id}/{uuid4()}-{safe_name}"

    text_bytes = text.encode("utf-8")
    supabase.storage.from_(bucket).upload(
        object_path,
        text_bytes,
        {"content-type": "text/plain; charset=utf-8", "upsert": "false"},
    )
    file_hash = hashlib.sha256(text_bytes).hexdigest()

    source_row = {
        "business_id": business_id,
        "file_name": safe_name,
        "mime_type": "text/plain",
        "storage_path": f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "page_count": getattr(evidence, "page_count", None),
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_invoice_records(supabase, file_id, business_id, evidence)


def persist_image_evidence(
    file_path: str | os.PathLike[str],
    evidence: Any,
    business_id: str | None = None,
    file_type: str = "receipt",
    storage_path: str | None = None,
    client: Client | None = None,
) -> dict[str, str]:
    """Backward-compatible image persistence entry point."""
    return persist_evidence(file_path, evidence, business_id, file_type, storage_path, client)


def persist_bank_statement_evidence(
    csv_text_or_path: str | os.PathLike[str],
    evidence: Any,
    source_name: str = "bank_statement.csv",
    business_id: str | None = None,
    file_type: str = "bank_statement",
    client: Client | None = None,
) -> dict[str, str]:
    """Reject bank statement persistence because the configured schema is invoice-only."""
    raise ValueError(
        "The current database schema stores invoices only. Bank CSV parsing remains "
        "available, but persistence requires dedicated bank statement and transaction tables."
    )


def persist_voice_evidence(
    audio_path_or_bytes: str | os.PathLike[str] | bytes,
    evidence: Any,
    source_name: str = "voice_note.wav",
    business_id: str | None = None,
    file_type: str = "voice",
    client: Client | None = None,
) -> dict[str, str]:
    """Persist audio voice recording file, transcription, and extracted financial fields into Supabase."""
    supabase = client or get_supabase_client()
    business_id = business_id.strip() if business_id else get_or_create_default_business(supabase)
    _sync_business_profile(supabase, business_id, evidence)
    bucket = get_storage_bucket(supabase)

    if isinstance(audio_path_or_bytes, (str, os.PathLike)) and Path(audio_path_or_bytes).is_file():
        path = Path(audio_path_or_bytes)
        file_bytes = path.read_bytes()
        safe_name = path.name
    elif isinstance(audio_path_or_bytes, bytes):
        file_bytes = audio_path_or_bytes
        safe_name = source_name
    else:
        path = Path(str(audio_path_or_bytes))
        file_bytes = path.read_bytes() if path.is_file() else b""
        safe_name = path.name if path.name else source_name

    object_path = f"{business_id}/{uuid4()}-{safe_name}"
    guessed_type, _ = mimetypes.guess_type(safe_name)
    mime_type = guessed_type or "audio/wav"

    supabase.storage.from_(bucket).upload(
        object_path,
        file_bytes,
        {"content-type": mime_type, "upsert": "false"},
    )
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    source_row = {
        "business_id": business_id,
        "file_name": safe_name,
        "mime_type": mime_type,
        "storage_path": f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "page_count": getattr(evidence, "page_count", None),
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_invoice_records(supabase, file_id, business_id, evidence)
