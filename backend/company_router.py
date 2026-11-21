"""Platform company management and company overview."""

import hashlib
import logging
import mimetypes
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from httpx import HTTPError
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field, SecretStr, field_validator
from supabase_auth.errors import AuthApiError, AuthError
from storage3.utils import StorageException

from backend.auth import (
    AuthenticatedUser,
    SensitiveInputRoute,
    get_authenticated_database_client,
    require_roles,
)
from backend.database.supabase_repository import _insert_invoice_records, get_storage_bucket
from backend.parsers.bank_csv_parser import SUPPORTED_EXTENSIONS as BANK_CSV_EXTENSIONS
from backend.parsers.text_parser import SUPPORTED_EXTENSIONS as TEXT_EXTENSIONS
from backend.services.ingestion_service import ALL_SUPPORTED_EXTENSIONS, ingest_file

LOGGER = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_EXTENSIONS = (ALL_SUPPORTED_EXTENSIONS - BANK_CSV_EXTENSIONS) | TEXT_EXTENSIONS

companies_router = APIRouter(
    prefix="/api/admin/companies",
    tags=["company administration"],
    dependencies=[Depends(require_roles("superadmin"))],
    route_class=SensitiveInputRoute,
)
company_router = APIRouter(prefix="/api/company", tags=["company"])
platform_router = APIRouter(
    prefix="/api/admin/platform",
    tags=["platform monitoring"],
    dependencies=[Depends(require_roles("superadmin"))],
)

class CompanyStatusUpdate(BaseModel):
    status: str = Field(pattern="^(active|suspended)$")


class AdminCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    password: SecretStr

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class CompanyRegistration(AdminCredentials):
    company_name: str = Field(min_length=2, max_length=120)
    gstin: str = Field(pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

    @field_validator("gstin", mode="before")
    @classmethod
    def normalize_gstin(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


class CompanyGstin(BaseModel):
    gstin: str = Field(pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

    @field_validator("gstin", mode="before")
    @classmethod
    def normalize_gstin(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value


def _count(client: Any, table: str, business_id: str) -> int:
    response = client.table(table).select("*", count="exact").eq("business_id", business_id).limit(1).execute()
    if response.count is None:
        raise HTTPException(status_code=502, detail=f"Could not count {table} records.")
    return int(response.count)


def _database_error(exc: APIError) -> HTTPException:
    LOGGER.exception("Platform database query failed.")
    if exc.code in {"42703", "PGRST204", "PGRST202"}:
        return HTTPException(status_code=503, detail="The platform database is not up to date. Apply the Supabase migrations and reload the API.")
    return HTTPException(status_code=502, detail="The platform database could not complete the request. Check the backend logs.")


def _registration_error(exc: APIError) -> HTTPException:
    if exc.code == "P0001":
        return HTTPException(status_code=409, detail=exc.message)
    if exc.code == "23505":
        if "business_registration_gstin_unique" in str(exc):
            return HTTPException(status_code=409, detail="A company with this GSTIN is already registered.")
        if "profiles_one_administrator_per_business" in str(exc):
            return HTTPException(status_code=409, detail="This company already has a Company Admin.")
        if "username" in str(exc):
            return HTTPException(status_code=409, detail="Username is already in use.")
    return _database_error(exc)


def _validate_password(password: SecretStr) -> str:
    value = password.get_secret_value()
    if len(value) < 8 or len(value) > 128:
        raise HTTPException(status_code=422, detail="Password must be between 8 and 128 characters.")
    return value


def _provision_admin(client: Any, business_id: str, password: str) -> None:
    attempt_id = str(uuid4())
    try:
        reservation = client.rpc("claim_company_account", {
            "p_business_id": business_id, "p_attempt_id": attempt_id,
        }).execute().data
    except APIError as exc:
        raise _registration_error(exc) from exc
    except HTTPError as exc:
        LOGGER.exception("Could not confirm the account creation claim.")
        raise HTTPException(status_code=503, detail="Account setup could not be confirmed. Refresh the company details before retrying.") from exc
    if not isinstance(reservation, dict) or not reservation.get("user_id") or not reservation.get("username"):
        raise HTTPException(status_code=502, detail="Invalid account reservation; contact the platform administrator.")
    user_id = reservation["user_id"]
    email = f'{reservation["username"]}@accounts.ledgerline.invalid'
    try:
        try:
            user = client.auth.admin.get_user_by_id(user_id).user
        except AuthApiError as exc:
            if exc.status != 404 or exc.code != "user_not_found":
                raise
            user = None
        if user is None:
            user = client.auth.admin.create_user({
                "id": user_id, "email": email, "password": password, "email_confirm": True,
                "app_metadata": {"ledgerline_business_id": business_id},
            }).user
        else:
            metadata = getattr(user, "app_metadata", None) or {}
            if str(user.id) != user_id or user.email != email or metadata.get("ledgerline_business_id") != business_id:
                raise HTTPException(status_code=409, detail="The reserved Auth account does not belong to this company.")
            user = client.auth.admin.update_user_by_id(user_id, {"password": password}).user
        metadata = getattr(user, "app_metadata", None) or {}
        if not user or str(user.id) != user_id or user.email != email or metadata.get("ledgerline_business_id") != business_id:
            raise HTTPException(status_code=502, detail="Supabase returned an unexpected account.")
        client.rpc("finish_company_account", {"p_business_id": business_id, "p_attempt_id": attempt_id}).execute()
    except (AuthError, APIError, HTTPError, HTTPException) as exc:
        LOGGER.exception("Company administrator account creation failed.")
        try:
            client.rpc("fail_company_account", {"p_business_id": business_id, "p_attempt_id": attempt_id}).execute()
        except (APIError, HTTPError):
            LOGGER.exception("Could not release the company account creation claim.")
            raise HTTPException(status_code=503, detail="Account setup needs manual recovery. The company was preserved; check backend logs.") from exc
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, APIError):
            raise _registration_error(exc) from exc
        if isinstance(exc, AuthApiError) and exc.code in {"email_exists", "user_already_exists"}:
            raise HTTPException(status_code=409, detail="This username is already used by an Auth account. The company was preserved; choose another username on its details page.") from exc
        raise HTTPException(status_code=502, detail="Account creation failed. The company was preserved; retry from its details page. Check backend logs.") from exc


@companies_router.get("")
def list_companies() -> dict[str, Any]:
    client = get_authenticated_database_client()
    try:
        result = client.rpc("platform_companies", {}).execute().data
    except APIError as exc:
        raise _database_error(exc) from exc
    if not isinstance(result, list):
        raise HTTPException(status_code=502, detail="The platform database returned an invalid company list.")
    return {"companies": result}


@companies_router.post("", status_code=201)
def create_company(payload: CompanyRegistration) -> dict[str, str]:
    name = payload.company_name.strip()
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Company name must contain at least two characters.")
    password = _validate_password(payload.password)
    client = get_authenticated_database_client()
    try:
        business_id = client.rpc("reserve_new_company", {
            "p_name": name, "p_gstin": payload.gstin, "p_username": payload.username, "p_user_id": str(uuid4()),
        }).execute().data
    except APIError as exc:
        raise _registration_error(exc) from exc
    except HTTPError as exc:
        LOGGER.exception("Could not confirm company registration.")
        raise HTTPException(status_code=503, detail="Registration could not be confirmed. Refresh the company list before retrying.") from exc
    if not business_id:
        raise HTTPException(status_code=502, detail="The database did not return the registered company.")
    _provision_admin(client, str(business_id), password)
    return {"message": "Company and Company Admin created.", "business_id": str(business_id)}


@companies_router.post("/{business_id}/administrator", status_code=201)
def create_company_admin(business_id: UUID, payload: AdminCredentials) -> dict[str, str]:
    password = _validate_password(payload.password)
    client = get_authenticated_database_client()
    try:
        client.rpc("reserve_existing_company", {
            "p_business_id": str(business_id), "p_username": payload.username, "p_user_id": str(uuid4()),
        }).execute()
    except APIError as exc:
        raise _registration_error(exc) from exc
    except HTTPError as exc:
        LOGGER.exception("Could not confirm the account reservation.")
        raise HTTPException(status_code=503, detail="Account setup could not be confirmed. Refresh the company details before retrying.") from exc
    _provision_admin(client, str(business_id), password)
    return {"message": "Company Admin account created."}


@companies_router.put("/{business_id}/registration")
def verify_company_registration(business_id: UUID, payload: CompanyGstin) -> dict[str, str]:
    try:
        get_authenticated_database_client().rpc("verify_company_gstin", {
            "p_business_id": str(business_id), "p_gstin": payload.gstin,
        }).execute()
    except APIError as exc:
        raise _registration_error(exc) from exc
    except HTTPError as exc:
        LOGGER.exception("Could not confirm GSTIN verification.")
        raise HTTPException(status_code=503, detail="GSTIN verification could not be confirmed. Refresh the company details.") from exc
    return {"message": "Company GSTIN verified."}


@platform_router.get("/activity")
def platform_activity(business_id: UUID | None = None) -> dict[str, Any]:
    client = get_authenticated_database_client()
    try:
        activity = client.rpc("platform_activity", {"p_business_id": str(business_id) if business_id else None}).execute().data
    except APIError as exc:
        raise _database_error(exc) from exc
    if not isinstance(activity, list):
        raise HTTPException(status_code=502, detail="The platform database returned invalid activity.")
    return {"activity": activity}


@platform_router.get("/health")
def platform_health() -> dict[str, Any]:
    client = get_authenticated_database_client()
    try:
        client.table("business").select("business_id,status").limit(1).execute()
    except APIError as exc:
        raise _database_error(exc) from exc
    return {"database": "available"}


@companies_router.patch("/{business_id}/status")
def update_company_status(
    business_id: UUID,
    payload: CompanyStatusUpdate,
) -> dict[str, Any]:
    client = get_authenticated_database_client()
    try:
        updated = (
            client.table("business").update({"status": payload.status}).eq("business_id", str(business_id)).execute().data or []
        )
    except APIError as exc:
        raise _database_error(exc) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Company was not found.")
    return {"company": updated[0]}


@company_router.get("/overview")
def company_overview(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    business_id = user.company_id()
    business = client.table("business").select("business_id,business_name,status").eq("business_id", business_id).limit(1).execute().data or []
    engagements = (
        client.table("audit_engagement").select("engagement_id,entity_name,status,created_at")
        .eq("business_id", business_id).order("created_at", desc=True).limit(5).execute().data or []
    )
    return {
        "company": business[0] if business else {"business_id": business_id},
        "documents": _count(client, "source_file", business_id),
        "invoices": _count(client, "invoice", business_id),
        "engagements": _count(client, "audit_engagement", business_id),
        "recent_engagements": engagements,
    }


@company_router.get("/documents")
def company_documents(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    business_id = user.company_id()
    try:
        rows = (
            client.table("source_file")
            .select("file_id,file_name,processing_status,uploaded_at")
            .eq("business_id", business_id)
            .order("uploaded_at", desc=True)
            .limit(100)
            .execute().data
        )
    except APIError as exc:
        raise _database_error(exc) from exc
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="The database returned an invalid document list.")
    return {"documents": rows, "total": _count(client, "source_file", business_id)}


def _company_document(client: Any, business_id: str, file_id: UUID) -> dict[str, Any]:
    try:
        rows = (
            client.table("source_file").select("file_id,file_name,mime_type,storage_path,processing_status")
            .eq("business_id", business_id).eq("file_id", str(file_id)).limit(1).execute().data
        )
    except APIError as exc:
        raise _database_error(exc) from exc
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="The database returned invalid document details.")
    if not rows:
        raise HTTPException(status_code=404, detail="Document was not found.")
    document = rows[0]
    if not isinstance(document, dict):
        raise HTTPException(status_code=502, detail="The document details are invalid.")
    return document


def _document_content(client: Any, document: dict[str, Any], business_id: str, file_id: UUID) -> bytes:
    storage_path = document.get("storage_path")
    if not isinstance(storage_path, str):
        raise HTTPException(status_code=502, detail="The document storage location is unavailable.")
    bucket, separator, object_path = storage_path.partition("/")
    if not separator or not bucket or not object_path.startswith(f"{business_id}/") or ".." in PurePosixPath(object_path).parts:
        LOGGER.error("Document %s has an unsafe storage location.", file_id)
        raise HTTPException(status_code=409, detail="The document storage location cannot be verified.")
    try:
        content = client.storage.from_(bucket).download(object_path)
    except (StorageException, HTTPError) as exc:
        LOGGER.exception("Could not download company document %s.", file_id)
        raise HTTPException(status_code=502, detail="The document could not be downloaded.") from exc
    return content


@company_router.post("/documents", status_code=201)
def upload_company_document(
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_roles("administrator")),
) -> dict[str, Any]:
    name = Path((file.filename or "").replace("\\", "/")).name
    if not name or Path(name).suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=415, detail="This document type is not supported.")
    content = file.file.read(MAX_DOCUMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The document is empty.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="The document exceeds the 20 MB limit.")
    client = get_authenticated_database_client()
    business_id = user.company_id()
    bucket = get_storage_bucket(client)
    object_path = f"{business_id}/{uuid4()}-{name}"
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    try:
        client.storage.from_(bucket).upload(object_path, content, {"content-type": mime_type, "upsert": "false"})
        response = client.table("source_file").insert({
            "business_id": business_id, "file_name": name, "mime_type": mime_type,
            "storage_path": f"{bucket}/{object_path}", "file_hash": hashlib.sha256(content).hexdigest(),
            "processing_status": "pending",
        }).execute()
    except (StorageException, APIError, HTTPError) as exc:
        LOGGER.exception("Could not store document for company %s.", business_id)
        raise HTTPException(status_code=502, detail="The upload could not be saved. Check the backend logs before retrying.") from exc
    if not response.data or not isinstance(response.data[0], dict) or not response.data[0].get("file_id"):
        raise HTTPException(status_code=502, detail="The database did not confirm the document upload.")
    return {"document": response.data[0]}


@company_router.post("/documents/{file_id}/process")
def process_company_document(file_id: UUID, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    business_id = user.company_id()
    document = _company_document(client, business_id, file_id)
    try:
        claimed = (
            client.table("source_file").update({"processing_status": "processing"})
            .eq("business_id", business_id).eq("file_id", str(file_id))
            .in_("processing_status", ["pending", "failed"]).execute().data
        )
    except APIError as exc:
        raise _database_error(exc) from exc
    if not claimed:
        raise HTTPException(status_code=409, detail="This document is already being processed or has been processed.")
    try:
        content = _document_content(client, document, business_id, file_id)
        name = document.get("file_name")
        if not isinstance(name, str) or Path(name).name != name or Path(name).suffix.lower() not in DOCUMENT_EXTENSIONS:
            raise HTTPException(status_code=409, detail="The stored document name is invalid.")
        with tempfile.TemporaryDirectory(prefix="ledgerline-document-") as directory:
            path = Path(directory) / name
            path.write_bytes(content)
            evidence = ingest_file(path, persist_to_database=False, business_id=business_id)
        if evidence.processing_status != "success":
            raise HTTPException(status_code=422, detail=evidence.error or "The document could not be processed.")
        if evidence.source_type == "bank_csv":
            raise HTTPException(status_code=422, detail="Bank statement extraction cannot yet be saved in this workspace.")
        result = _insert_invoice_records(client, str(file_id), business_id, evidence)
        updated = client.table("source_file").update({"processing_status": "processed", "page_count": evidence.page_count}).eq(
            "business_id", business_id
        ).eq("file_id", str(file_id)).eq("processing_status", "processing").execute().data
        if not updated:
            raise RuntimeError("The processed status was not confirmed by the database.")
        return {"file_id": str(file_id), "invoice_id": result["invoice_id"], "processing_status": "processed"}
    except Exception as exc:
        LOGGER.exception("Document processing failed for %s.", file_id)
        try:
            failed = client.table("source_file").update({"processing_status": "failed"}).eq(
                "business_id", business_id
            ).eq("file_id", str(file_id)).eq("processing_status", "processing").execute().data
            if not failed:
                raise RuntimeError("The failed status was not confirmed by the database.")
        except (APIError, HTTPError, RuntimeError) as status_exc:
            LOGGER.exception("Could not record processing failure for %s.", file_id)
            raise HTTPException(status_code=503, detail="Processing failed and its status could not be saved. Contact the platform administrator before retrying.") from status_exc
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail="Document processing failed. Check the backend logs and retry.") from exc


def _duplicate_key(invoice: dict[str, Any]) -> tuple[str, str] | None:
    supplier = invoice.get("seller_name")
    number = invoice.get("invoice_number")
    if not isinstance(supplier, str) or not isinstance(number, str):
        return None
    supplier_key = re.sub(r"\s+", " ", supplier.strip().casefold())
    number_key = "".join(character for character in number.casefold() if character.isalnum())
    return (supplier_key, number_key) if supplier_key and number_key else None


@company_router.get("/documents/{file_id}/review")
def review_company_document(file_id: UUID, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    business_id = user.company_id()
    document = _company_document(client, business_id, file_id)
    try:
        rows = client.table("invoice").select("*").eq("business_id", business_id).eq("file_id", str(file_id)).execute().data
        if not isinstance(rows, list):
            raise HTTPException(status_code=502, detail="The database returned invalid extracted records.")
        invoices = []
        for invoice in rows:
            if not isinstance(invoice, dict) or not invoice.get("invoice_id"):
                raise HTTPException(status_code=502, detail="The database returned an invalid extracted record.")
            invoice_id = invoice["invoice_id"]
            extractions = client.table("invoice_extraction").select("raw_text,processed_at").eq(
                "invoice_id", invoice_id
            ).order("processed_at", desc=True).limit(1).execute().data
            provenance = client.table("invoice_field_provenance").select(
                "field_name,field_value,source_text,page_number,location,confidence"
            ).eq("invoice_id", invoice_id).execute().data
            invoices.append({"record": invoice, "extraction": extractions[0] if extractions else None, "provenance": provenance})

        duplicates = []
        keys = {_duplicate_key(item["record"]) for item in invoices} - {None}
        if keys:
            start = 0
            while True:
                candidates = client.table("invoice").select(
                    "invoice_id,file_id,invoice_number,seller_name"
                ).eq("business_id", business_id).order("invoice_id").range(start, start + 499).execute().data
                if not isinstance(candidates, list):
                    raise HTTPException(status_code=502, detail="The database returned invalid duplicate candidates.")
                for row in candidates:
                    if not isinstance(row, dict):
                        raise HTTPException(status_code=502, detail="The database returned an invalid duplicate candidate.")
                    if str(row.get("file_id")) != str(file_id) and _duplicate_key(row) in keys:
                        duplicates.append(row)
                if len(candidates) < 500:
                    break
                start += 500
    except APIError as exc:
        raise _database_error(exc) from exc
    return {"document": {"file_id": str(file_id), "file_name": document["file_name"]}, "invoices": invoices, "suspected_duplicates": duplicates}


@company_router.get("/documents/{file_id}")
def download_company_document(file_id: UUID, user: AuthenticatedUser = Depends(require_roles("administrator"))) -> Response:
    client = get_authenticated_database_client()
    business_id = user.company_id()
    document = _company_document(client, business_id, file_id)
    content = _document_content(client, document, business_id, file_id)
    filename = document.get("file_name") or "document"
    mime_type = document.get("mime_type") or "application/octet-stream"
    if not isinstance(filename, str) or not isinstance(mime_type, str):
        raise HTTPException(status_code=502, detail="The document details are invalid.")
    return Response(content, media_type=mime_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "no-store",
    })
