"""Authenticated profile and administrator-only access management endpoints."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import (
    AuthenticatedUser,
    AuditRole,
    get_authenticated_database_client,
    get_current_user,
    require_roles,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
admin_router = APIRouter(
    prefix="/api/admin",
    tags=["user administration"],
    dependencies=[Depends(require_roles("administrator"))],
)


class ProfileRoleUpdate(BaseModel):
    role: AuditRole


@router.get("/me")
def get_my_profile(user: AuthenticatedUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "business_id": user.business_id,
    }


@admin_router.get("/profiles")
def list_profiles(user: AuthenticatedUser = Depends(require_roles("administrator"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    rows = (
        client.table("profiles")
        .select("user_id,email,role,business_id,created_at,updated_at")
        .or_(f"business_id.eq.{user.business_id},business_id.is.null")
        .order("created_at")
        .execute()
        .data
        or []
    )
    return {"profiles": rows}


@admin_router.patch("/profiles/{user_id}")
def update_profile_role(
    user_id: UUID,
    payload: ProfileRoleUpdate,
    actor: AuthenticatedUser = Depends(require_roles("administrator")),
) -> dict[str, Any]:
    client = get_authenticated_database_client()
    profile_id = str(user_id)
    targets = (
        client.table("profiles")
        .select("user_id,role,business_id")
        .eq("user_id", profile_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not targets:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    target = targets[0]
    if target.get("business_id") not in {None, actor.business_id}:
        raise HTTPException(status_code=404, detail="Profile was not found.")
    previous_role = target["role"]
    if profile_id == actor.user_id and payload.role != "administrator":
        raise HTTPException(status_code=409, detail="Administrators cannot remove their own administrator access.")
    if previous_role == "administrator" and payload.role != "administrator":
        administrators = (
            client.table("profiles")
            .select("user_id")
            .eq("business_id", actor.business_id)
            .eq("role", "administrator")
            .execute()
            .data
            or []
        )
        if len(administrators) <= 1:
            raise HTTPException(status_code=409, detail="Assign another administrator before changing the last administrator's role.")
    updated = (
        client.table("profiles")
        .update({"role": payload.role, "business_id": actor.business_id})
        .eq("user_id", profile_id)
        .execute()
        .data
        or []
    )
    client.table("audit_profile_history").insert({
        "user_id": profile_id,
        "business_id": actor.business_id,
        "actor_user_id": actor.user_id,
        "action": "role_updated",
        "previous_role": previous_role,
        "new_role": payload.role,
    }).execute()
    return {"profile": updated[0] if updated else {**target, "role": payload.role, "business_id": actor.business_id}}
