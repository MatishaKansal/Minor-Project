"""Supabase access-token verification and application role dependencies."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Awaitable, Callable, Literal

from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
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

AuditRole = Literal["superadmin", "administrator"]
BEARER = HTTPBearer(auto_error=False)


class SensitiveInputRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

        return handler


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    role: AuditRole
    business_id: str | None
    username: str | None = None

    def company_id(self) -> str:
        if self.role != "administrator" or not self.business_id:
            raise HTTPException(status_code=403, detail="A Company Admin account is required.")
        return self.business_id


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
    selected_business: Annotated[str | None, Header(alias="X-Business-ID")] = None,
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
            .select("user_id,role,business_id,email,username")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
            .data
            or []
        )
    except (APIError, SupabaseConfigurationError) as exc:
        LOGGER.exception("Could not load the authenticated user's audit profile.")
        raise HTTPException(status_code=503, detail="The user's audit profile could not be loaded.") from exc

    if not isinstance(profiles, list):
        raise HTTPException(status_code=503, detail="The user's audit profile could not be loaded.")
    if not profiles:
        raise HTTPException(status_code=403, detail="This account does not have an audit profile. Ask a superadmin to provision it.")
    profile = profiles[0]
    if not isinstance(profile, dict):
        raise HTTPException(status_code=503, detail="The user's audit profile could not be loaded.")
    role_value = profile.get("role")
    role: AuditRole
    if role_value == "superadmin":
        role = "superadmin"
    elif role_value == "administrator":
        role = "administrator"
    else:
        LOGGER.error("User %s has an unsupported audit role.", user_id)
        raise HTTPException(status_code=403, detail="This account does not have a valid audit role.")
    business_id = profile.get("business_id")
    if business_id is not None and not isinstance(business_id, str):
        raise HTTPException(status_code=503, detail="The user's audit profile is invalid.")
    if not business_id and role == "administrator":
        raise HTTPException(status_code=403, detail="This account is not assigned to a business.")

    if role != "superadmin":
        try:
            own_business = (
                service_client.table("business")
                .select("status")
                .eq("business_id", str(business_id))
                .limit(1)
                .execute()
                .data
                or []
            )
        except APIError as exc:
            LOGGER.exception("Could not verify the company status.")
            raise HTTPException(status_code=503, detail="The company status could not be verified.") from exc
        if not isinstance(own_business, list) or not own_business or not isinstance(own_business[0], dict) or own_business[0].get("status") != "active":
            raise HTTPException(status_code=403, detail="This company is suspended or unavailable.")

    if selected_business and selected_business != str(business_id):
        raise HTTPException(status_code=403, detail="The requested business is not assigned to this account.")

    email = profile.get("email") or getattr(auth_user, "email", None)
    username = profile.get("username")
    if (email is not None and not isinstance(email, str)) or (username is not None and not isinstance(username, str)):
        raise HTTPException(status_code=503, detail="The user's audit profile is invalid.")
    return AuthenticatedUser(
        user_id=user_id,
        email=email,
        role=role,
        business_id=str(business_id) if business_id else None,
        username=username,
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
