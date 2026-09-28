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
from postgrest.exceptions import APIError
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
        {"business_name": "My Business", "industry": "Retail", "currency": "INR"}
    ).execute()
    if not created.data or not created.data[0].get("business_id"):
        raise BusinessContextError("Could not create the default business in Supabase")
    business_id = created.data[0]["business_id"]
    LOGGER.info("Created and resolved default business: %s", business_id)
    return business_id


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
    return round(max(0.0, min(100.0, confidence_score)) / 100, 4)


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


def _insert_evidence_records(
    supabase: Client,
    file_id: str,
    business_id: str,
    evidence: Any,
) -> dict[str, str]:
    confidence = _database_confidence(evidence.confidence_score)
    financial_row = {
        "file_id": file_id,
        "business_id": business_id,
        "confidence_score": confidence,
        "date": _database_date(evidence.date),
        "amount": evidence.amount,
        "currency": evidence.currency,
        "party_name": evidence.party_name,
        "reference_number": evidence.invoice_number,
        "description": evidence.description,
    }
    try:
        financial_response = supabase.table("financial_evidence").insert(financial_row).execute()
    except APIError as exc:
        error_payload = exc.args[0] if exc.args else {}
        error_text = str(error_payload)
        missing_business_column = (
            (isinstance(error_payload, dict) and error_payload.get("code") == "PGRST204")
            or "PGRST204" in error_text
        ) and "business_id" in error_text and "financial_evidence" in error_text
        if not missing_business_column:
            raise
        LOGGER.warning(
            "Deployed financial_evidence table has no business_id column; "
            "using source_file.business_id as the ownership link."
        )
        financial_row.pop("business_id")
        financial_response = supabase.table("financial_evidence").insert(financial_row).execute()
    if not financial_response.data:
        raise RuntimeError("Supabase did not return the inserted financial_evidence row")
    evidence_id = financial_response.data[0]["evidence_id"]

    details = {
        "source_type": evidence.source_type,
        "language": evidence.language,
        "extracted_text": evidence.extracted_text,
        "processing_status": evidence.processing_status,
        "error": evidence.error,
    }
    if hasattr(evidence, "summary") and evidence.summary:
        details["summary"] = evidence.summary.model_dump() if hasattr(evidence.summary, "model_dump") else evidence.summary
    if hasattr(evidence, "transactions") and evidence.transactions:
        details["transactions"] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in evidence.transactions
        ]
    supabase.table("evidence_details").insert(
        {"evidence_id": evidence_id, "details": details}
    ).execute()
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

        provenance_rows.append(
            {
                "evidence_id": evidence_id,
                "file_id": file_id,
                "field_name": field_name,
                "page_number": page_number,
                "source_text": source_text,
                "location": {
                    "field_name": field_name,
                    "value": value,
                    "line_number": line_number,
                },
                "extraction_method": extraction_method,
                "confidence_score": _database_confidence(confidence_score),
            }
        )
    if not provenance_rows:
        provenance_rows = [
            {
                "evidence_id": evidence_id,
                "file_id": file_id,
                "page_number": getattr(evidence, "page_number", 1),
                "source_text": getattr(evidence, "extracted_text", ""),
                "extraction_method": "TEXT" if evidence.source_type in {"text", "txt"} else ("PDF_TEXT" if evidence.source_type == "pdf" else "OCR"),
                "confidence_score": confidence,
            }
        ]
    supabase.table("provenance").insert(provenance_rows).execute()
    LOGGER.info("Persisted evidence: file_id=%s evidence_id=%s", file_id, evidence_id)
    return {"file_id": file_id, "evidence_id": evidence_id}


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
    bucket = get_storage_bucket(supabase)
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
        "file_type": file_type,
        "mime_type": mimetypes.guess_type(path.name)[0],
        "storage_path": storage_path or f"{bucket}/{object_path}",
        "file_hash": _file_hash(path),
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_evidence_records(supabase, file_id, business_id, evidence)


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
        "file_type": file_type,
        "mime_type": "text/plain",
        "storage_path": f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_evidence_records(supabase, file_id, business_id, evidence)


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
    """Persist bank statement CSV, summary, transactions, and provenance into Supabase."""
    supabase = client or get_supabase_client()
    business_id = business_id.strip() if business_id else get_or_create_default_business(supabase)
    bucket = get_storage_bucket(supabase)

    try:
        path = Path(csv_text_or_path)
        if path.is_file():
            file_bytes = path.read_bytes()
            safe_name = path.name
        else:
            file_bytes = str(csv_text_or_path).encode("utf-8")
            safe_name = source_name if source_name.endswith((".csv", ".tsv", ".txt")) else f"{source_name}.csv"
    except Exception:
        file_bytes = str(csv_text_or_path).encode("utf-8")
        safe_name = source_name if source_name.endswith((".csv", ".tsv", ".txt")) else f"{source_name}.csv"

    object_path = f"{business_id}/{uuid4()}-{safe_name}"
    supabase.storage.from_(bucket).upload(
        object_path,
        file_bytes,
        {"content-type": "text/csv; charset=utf-8", "upsert": "false"},
    )
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    source_row = {
        "business_id": business_id,
        "file_name": safe_name,
        "file_type": file_type,
        "mime_type": "text/csv",
        "storage_path": f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_evidence_records(supabase, file_id, business_id, evidence)


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
        "file_type": file_type,
        "mime_type": mime_type,
        "storage_path": f"{bucket}/{object_path}",
        "file_hash": file_hash,
        "processing_status": "processed" if evidence.processing_status == "success" else "failed",
    }
    source_response = supabase.table("source_file").insert(source_row).execute()
    if not source_response.data:
        raise RuntimeError("Supabase did not return the inserted source_file row")
    file_id = source_response.data[0]["file_id"]

    return _insert_evidence_records(supabase, file_id, business_id, evidence)
