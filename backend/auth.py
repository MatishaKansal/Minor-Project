"""Supabase access-token verification and application role dependencies."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase_auth.errors import AuthApiError, AuthError

from backend.database.supabase_repository import (
    SupabaseConfigurationError,
    get_supabase_client,
)

LOGGER = logging.getLogger(__name__)
PROJECT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

AuditRole = Literal["administrator", "auditor", "reviewer", "read_only_viewer"]
BEARER = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    role: AuditRole
    business_id: str


def _get_supabase_auth_client() -> Client:
    load_dotenv(dotenv_path=PROJECT_ENV_FILE)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY")
    if not url or not key:
        raise HTTPException(
            status_code=503,
            detail="Supabase Auth is not configured. Set SUPABASE_URL and SUPABASE_ANON_KEY on the backend.",
        )
    return create_client(url, key)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(BEARER),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="A Supabase access token is required.")

    try:
        verified = _get_supabase_auth_client().auth.get_user(credentials.credentials)
    except AuthApiError as exc:
        if exc.status in {400, 401, 403}:
            raise HTTPException(status_code=401, detail="The Supabase access token is invalid or expired.") from exc
        LOGGER.warning("Supabase Auth could not verify an access token (status %s).", exc.status)
        raise HTTPException(status_code=503, detail="Supabase Auth is temporarily unavailable.") from exc
    except AuthError as exc:
        LOGGER.warning("Supabase Auth rejected an access token (%s).", exc.name)
        raise HTTPException(status_code=401, detail="The Supabase access token is invalid or expired.") from exc
    except SupabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    auth_user = getattr(verified, "user", None)
    user_id = str(getattr(auth_user, "id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Supabase Auth did not return a verified user.")

    try:
        service_client = get_supabase_client()
        profiles = (
            service_client.table("profiles")
            .select("user_id,role,business_id,email")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except (APIError, SupabaseConfigurationError) as exc:
        LOGGER.exception("Could not load the authenticated user's audit profile.")
        raise HTTPException(status_code=503, detail="The user's audit profile could not be loaded.") from exc

    if not profiles:
        raise HTTPException(status_code=403, detail="This account does not have an audit profile. Ask an administrator to provision it.")
    profile = profiles[0]
    role = profile.get("role")
    if role not in {"administrator", "auditor", "reviewer", "read_only_viewer"}:
        LOGGER.error("User %s has an unsupported audit role.", user_id)
        raise HTTPException(status_code=403, detail="This account does not have a valid audit role.")
    business_id = profile.get("business_id")
    if not business_id:
        raise HTTPException(status_code=403, detail="This account is not assigned to a business.")

    return AuthenticatedUser(
        user_id=user_id,
        email=profile.get("email") or getattr(auth_user, "email", None),
        role=role,
        business_id=str(business_id),
    )


def require_roles(*roles: AuditRole) -> Callable[..., AuthenticatedUser]:
    def role_dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your audit role does not permit this action.")
        return user

    return role_dependency


def get_authenticated_database_client() -> Client:
    """Return the backend service client only after an endpoint has authorized its user."""
    try:
        return get_supabase_client()
    except SupabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
