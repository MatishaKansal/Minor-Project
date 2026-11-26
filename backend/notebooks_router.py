"""FastAPI router for persisting and retrieving audit notebooks and conversation history."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from backend.auth import AuthenticatedUser, get_authenticated_database_client, require_roles

LOGGER = logging.getLogger(__name__)

notebooks_router = APIRouter(
    prefix="/api/notebooks",
    tags=["chat notebooks"],
    dependencies=[Depends(require_roles("administrator", "superadmin"))],
)


class NotebookSavePayload(BaseModel):
    id: str = Field(..., description="Unique client or server notebook identifier")
    title: str = Field(..., min_length=1, max_length=200)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    messageCount: Optional[int] = None
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class NotebookPatchPayload(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    messages: Optional[List[Dict[str, Any]]] = None


def _resolve_business_id(user: AuthenticatedUser) -> str:
    if user.business_id:
        return user.business_id
    if user.role == "administrator":
        return user.company_id()
    # For superadmins not bound to a business, fallback to user_id
    return user.user_id


@notebooks_router.get("")
def list_notebooks(
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> List[Dict[str, Any]]:
    """List all saved notebooks for the active company workspace."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)

    try:
        query = (
            client.table("chat_notebooks")
            .select("notebook_id, business_id, user_id, title, message_count, messages, created_at, updated_at")
            .eq("business_id", business_id)
            .order("updated_at", desc=True)
            .limit(100)
        )
        response = query.execute()
        rows = response.data or []

        return [
            {
                "id": row["notebook_id"],
                "title": row["title"],
                "messageCount": row.get("message_count") or len(row.get("messages") or []),
                "messages": row.get("messages") or [],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]
    except APIError as exc:
        LOGGER.exception("Failed to query chat notebooks for business %s", business_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch notebooks from database: {exc.message}",
        ) from exc


@notebooks_router.get("/{notebook_id}")
def get_notebook(
    notebook_id: str,
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> Dict[str, Any]:
    """Retrieve full messages and state for a single notebook."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)

    try:
        response = (
            client.table("chat_notebooks")
            .select("notebook_id, business_id, user_id, title, message_count, messages, created_at, updated_at")
            .eq("notebook_id", notebook_id)
            .eq("business_id", business_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Notebook '{notebook_id}' was not found.",
            )

        row = rows[0]
        return {
            "id": row["notebook_id"],
            "title": row["title"],
            "messages": row.get("messages") or [],
            "messageCount": row.get("message_count") or len(row.get("messages") or []),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
    except APIError as exc:
        LOGGER.exception("Failed to retrieve notebook %s", notebook_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error reading notebook: {exc.message}",
        ) from exc


@notebooks_router.post("")
def save_or_update_notebook(
    payload: NotebookSavePayload,
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> Dict[str, Any]:
    """Upsert an entire notebook (title, messages, timestamps) into the database."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)
    now_iso = datetime.now(timezone.utc).isoformat()
    msg_count = payload.messageCount if payload.messageCount is not None else len(payload.messages)

    row = {
        "notebook_id": payload.id,
        "business_id": business_id,
        "user_id": user.user_id,
        "title": payload.title,
        "messages": payload.messages,
        "message_count": msg_count,
        "updated_at": payload.updatedAt or now_iso,
    }
    if payload.createdAt:
        row["created_at"] = payload.createdAt

    try:
        client.table("chat_notebooks").upsert(row).execute()
        return {
            "status": "success",
            "notebook": {
                "id": payload.id,
                "title": payload.title,
                "messageCount": msg_count,
                "updatedAt": row["updated_at"],
            },
        }
    except APIError as exc:
        LOGGER.exception("Failed to upsert notebook %s", payload.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error saving notebook: {exc.message}",
        ) from exc


@notebooks_router.patch("/{notebook_id}")
def update_notebook(
    notebook_id: str,
    payload: NotebookPatchPayload,
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> Dict[str, Any]:
    """Partially update notebook title or messages."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)
    now_iso = datetime.now(timezone.utc).isoformat()

    updates: Dict[str, Any] = {"updated_at": now_iso}
    if payload.title is not None:
        updates["title"] = payload.title
    if payload.messages is not None:
        updates["messages"] = payload.messages
        updates["message_count"] = len(payload.messages)

    try:
        res = (
            client.table("chat_notebooks")
            .update(updates)
            .eq("notebook_id", notebook_id)
            .eq("business_id", business_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Notebook '{notebook_id}' was not found.",
            )
        return {"status": "success", "updated": res.data[0]}
    except APIError as exc:
        LOGGER.exception("Failed to update notebook %s", notebook_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error updating notebook: {exc.message}",
        ) from exc


@notebooks_router.delete("/{notebook_id}")
def delete_notebook(
    notebook_id: str,
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> Dict[str, Any]:
    """Delete a specific notebook from database."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)

    try:
        client.table("chat_notebooks").delete().eq("notebook_id", notebook_id).eq("business_id", business_id).execute()
        return {"status": "deleted", "id": notebook_id}
    except APIError as exc:
        LOGGER.exception("Failed to delete notebook %s", notebook_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error deleting notebook: {exc.message}",
        ) from exc


@notebooks_router.delete("")
def clear_all_notebooks(
    user: AuthenticatedUser = Depends(require_roles("administrator", "superadmin")),
) -> Dict[str, Any]:
    """Clear all notebooks belonging to the current business tenant."""
    client = get_authenticated_database_client()
    business_id = _resolve_business_id(user)

    try:
        client.table("chat_notebooks").delete().eq("business_id", business_id).execute()
        return {"status": "cleared", "business_id": business_id}
    except APIError as exc:
        LOGGER.exception("Failed to clear notebooks for business %s", business_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error clearing notebooks: {exc.message}",
        ) from exc
