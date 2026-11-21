"""Authenticated profile and superadmin access management endpoints."""

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from httpx import HTTPError
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field, SecretStr, field_validator
from supabase_auth.errors import AuthApiError, AuthError

from backend.auth import (
    AuthenticatedUser,
    AuditRole,
    SensitiveInputRoute,
    _get_supabase_auth_client,
    get_authenticated_database_client,
    get_current_user,
    require_roles,
)
from backend.database.supabase_repository import SupabaseConfigurationError, get_supabase_client

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["authentication"], route_class=SensitiveInputRoute)
admin_router = APIRouter(
    prefix="/api/admin",
    tags=["user administration"],
    dependencies=[Depends(require_roles("superadmin"))],
)


class ProfileRoleUpdate(BaseModel):
    role: AuditRole
    business_id: UUID


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: SecretStr


class UsernameUpdate(BaseModel):
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]*$")

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


@router.post("/login")
def login(payload: LoginRequest) -> JSONResponse:
    identifier = payload.username.strip().lower()
    if "@" in identifier:
        email = identifier
    else:
        try:
            rows = get_supabase_client().table("profiles").select("email").eq("username", identifier).limit(1).execute().data
        except (APIError, SupabaseConfigurationError, HTTPError) as exc:
            LOGGER.exception("Could not resolve login username.")
            raise HTTPException(status_code=503, detail="Sign in is temporarily unavailable.") from exc
        if not isinstance(rows, list):
            raise HTTPException(status_code=503, detail="The account lookup returned an invalid response.")
        row = rows[0] if rows else None
        if row is not None and not isinstance(row, dict):
            raise HTTPException(status_code=503, detail="The account lookup returned an invalid response.")
        address = row.get("email") if isinstance(row, dict) else None
        if address is not None and not isinstance(address, str):
            raise HTTPException(status_code=503, detail="The account lookup returned an invalid response.")
        if row is not None and not address:
            raise HTTPException(status_code=503, detail="The account is not configured for sign in.")
        email = address or f"{uuid4().hex}@accounts.ledgerline.invalid"
    try:
        result = _get_supabase_auth_client().auth.sign_in_with_password({
            "email": email, "password": payload.password.get_secret_value(),
        })
    except AuthApiError as exc:
        if exc.status == 429:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.") from exc
        if exc.status in {400, 401, 403}:
            raise HTTPException(status_code=401, detail="Invalid username or password.") from exc
        LOGGER.warning("Supabase sign-in failed (status %s, code %s).", exc.status, exc.code)
        raise HTTPException(status_code=503, detail="Sign in is temporarily unavailable.") from exc
    except (AuthError, HTTPError) as exc:
        LOGGER.warning("Supabase sign-in failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Sign in is temporarily unavailable.") from exc
    if not result.session:
        raise HTTPException(status_code=503, detail="Supabase did not return a session.")
    return JSONResponse(
        {"access_token": result.session.access_token, "refresh_token": result.session.refresh_token},
        headers={"Cache-Control": "no-store"},
    )


@router.put("/username")
def update_username(payload: UsernameUpdate, user: AuthenticatedUser = Depends(require_roles("superadmin"))) -> dict[str, str]:
    try:
        get_authenticated_database_client().rpc("set_superadmin_username", {
            "p_user_id": user.user_id, "p_username": payload.username,
        }).execute()
    except APIError as exc:
        if exc.code in {"P0001", "23505"}:
            raise HTTPException(status_code=409, detail="Username is already in use or the profile is unavailable.") from exc
        LOGGER.exception("Could not set Super Admin username.")
        raise HTTPException(status_code=503, detail="Could not set username. Check the backend logs.") from exc
    return {"username": payload.username}


@router.get("/me")
def get_my_profile(user: AuthenticatedUser = Depends(get_current_user)) -> dict[str, Any]:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role,
        "business_id": user.business_id,
        "username": user.username,
    }


@admin_router.get("/profiles")
def list_profiles(user: AuthenticatedUser = Depends(require_roles("superadmin"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    rows = (
        client.table("profiles")
        .select("user_id,email,role,business_id,created_at,updated_at")
        .order("created_at")
        .execute()
        .data
        or []
    )
    return {"profiles": rows}


@admin_router.get("/businesses")
def list_businesses(user: AuthenticatedUser = Depends(require_roles("superadmin"))) -> dict[str, Any]:
    client = get_authenticated_database_client()
    rows = client.table("business").select("business_id,business_name").order("business_name").execute().data or []
    return {"businesses": rows}


@admin_router.patch("/profiles/{user_id}")
def update_profile_role(
    user_id: UUID,
    payload: ProfileRoleUpdate,
    actor: AuthenticatedUser = Depends(require_roles("superadmin")),
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
    if profile_id == actor.user_id and payload.role != "superadmin":
        raise HTTPException(status_code=409, detail="Superadmins cannot remove their own superadmin access.")
    business_id = str(payload.business_id)
    businesses = client.table("business").select("business_id").eq("business_id", business_id).limit(1).execute().data or []
    if not businesses:
        raise HTTPException(status_code=404, detail="Business was not found.")
    try:
        updated = client.rpc("update_profile_access", {
            "p_actor_user_id": actor.user_id,
            "p_user_id": profile_id,
            "p_role": payload.role,
            "p_business_id": business_id,
        }).execute().data
    except APIError as exc:
        if "profiles_one_administrator_per_business" in str(exc) or exc.code == "23505":
            raise HTTPException(status_code=409, detail="This company already has a Company Admin.") from exc
        raise
    if not updated:
        raise HTTPException(status_code=502, detail="Supabase did not return the updated profile.")
    return {"profile": updated}
