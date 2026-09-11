"""Supabase persistence for parsed image evidence."""

import hashlib
import logging
import mimetypes
import os
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
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return value


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
        raise FileNotFoundError(f"Image file does not exist: {path}")
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
                "confidence_score": confidence_score,
            }
        )
    if not provenance_rows:
        provenance_rows = [
            {
                "evidence_id": evidence_id,
                "file_id": file_id,
                "page_number": evidence.page_number,
                "source_text": evidence.extracted_text,
                "extraction_method": "PDF_TEXT" if evidence.source_type == "pdf" else "OCR",
                "confidence_score": confidence,
            }
        ]
    supabase.table("provenance").insert(provenance_rows).execute()
    LOGGER.info("Persisted evidence: file_id=%s evidence_id=%s", file_id, evidence_id)
    return {"file_id": file_id, "evidence_id": evidence_id}


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
