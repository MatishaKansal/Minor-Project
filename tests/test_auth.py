from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError

import backend.audit.router as audit_router
from backend.api import app
from backend import auth
from backend.audit.router import (
    EngagementCreate,
    EngagementStatusUpdate,
    FindingDecision,
    create_engagement,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_args):
        return self

    def eq(self, *args):
        self.filters.append(args)
        return self

    def limit(self, *_args):
        return self

    def insert(self, payload):
        self.inserted = payload
        return self

    def execute(self):
        if hasattr(self, "inserted"):
            self.rows.append(self.inserted)
            if self.rows and self.rows[0].get("entity_name"):
                return SimpleNamespace(data=[{**self.inserted, "engagement_id": "engagement-1"}])
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self.rows)


class FakeDatabase:
    def __init__(self, profiles=None):
        self.profiles = profiles or []

    def table(self, name):
        if name == "profiles":
            return FakeQuery(self.profiles)
        raise AssertionError(f"Unexpected table: {name}")


def _user(role="auditor", user_id=None):
    return auth.AuthenticatedUser(
        user_id=user_id or str(uuid4()),
        email="auditor@example.test",
        role=role,
        business_id=str(uuid4()),
    )


def _credentials(token="verified-token"):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_missing_bearer_token_is_unauthorized():
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(None)
    assert error.value.status_code == 401


@pytest.mark.parametrize("status", [400, 401, 403])
def test_invalid_or_expired_supabase_token_is_unauthorized(monkeypatch, status):
    class FakeSupabaseAuth:
        def get_user(self, _token):
            raise AuthApiError("token invalid or expired", status, "invalid_token")

    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=FakeSupabaseAuth()))
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(_credentials())
    assert error.value.status_code == 401


def test_missing_profile_is_forbidden(monkeypatch):
    class FakeSupabaseAuth:
        def get_user(self, _token):
            return SimpleNamespace(user=SimpleNamespace(id=str(uuid4()), email="user@example.test"))

    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=FakeSupabaseAuth()))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: FakeDatabase())

    with pytest.raises(HTTPException) as error:
        auth.get_current_user(_credentials())
    assert error.value.status_code == 403


def test_verified_identity_and_database_role_define_request_user(monkeypatch):
    verified_id = str(uuid4())
    business_id = str(uuid4())

    class FakeSupabaseAuth:
        def get_user(self, token):
            assert token == "verified-token"
            return SimpleNamespace(user=SimpleNamespace(id=verified_id, email="verified@example.test"))

    profiles = [{
        "user_id": verified_id,
        "role": "reviewer",
        "business_id": business_id,
        "email": "profile@example.test",
    }]
    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=FakeSupabaseAuth()))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: FakeDatabase(profiles))

    user = auth.get_current_user(_credentials())
    assert user.user_id == verified_id
    assert user.role == "reviewer"
    assert user.business_id == business_id
    assert user.email == "profile@example.test"


def test_role_dependency_rejects_non_reviewer():
    reviewer_only = auth.require_roles("reviewer")
    with pytest.raises(HTTPException) as error:
        reviewer_only(user=_user(role="auditor"))
    assert error.value.status_code == 403


def test_auditor_cannot_approve_engagement_or_close_finding(monkeypatch):
    monkeypatch.setattr(audit_router, "_client_and_business", lambda _user: pytest.fail("database must not be accessed"))
    auditor = _user(role="auditor")

    with pytest.raises(HTTPException) as completion_error:
        audit_router.update_engagement_status(
            uuid4(),
            EngagementStatusUpdate(status="Completed", comment="Ready for approval"),
            auditor,
        )
    assert completion_error.value.status_code == 403

    with pytest.raises(HTTPException) as decision_error:
        audit_router.decide_finding(
            uuid4(),
            FindingDecision(status="Closed", comment="Close finding"),
            auditor,
        )
    assert decision_error.value.status_code == 403


def test_audit_and_query_endpoints_require_a_supabase_session():
    client = TestClient(app)

    assert client.get("/api/audit/summary").status_code == 401
    assert client.post("/api/query", data={"question": "Find the invoice total"}).status_code == 401


def test_reviewer_cannot_create_engagement():
    actor = _user(role="reviewer")
    previous_override = app.dependency_overrides.get(auth.get_current_user)
    app.dependency_overrides[auth.get_current_user] = lambda: actor
    client = TestClient(app)
    try:
        response = client.post("/api/audit/engagements", json={
            "entity_name": "Example Ltd",
            "period_start": "2025-04-01",
            "period_end": "2026-03-31",
            "financial_year": "2025-26",
            "audit_type": "Financial statement audit",
            "audit_scope": "Revenue and expenses",
            "materiality_threshold": "1000",
            "reporting_currency": "INR",
            "assigned_auditor": "Audit team",
        })
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(auth.get_current_user, None)
        else:
            app.dependency_overrides[auth.get_current_user] = previous_override

    assert response.status_code == 403


def test_read_only_viewer_cannot_upload_evidence_to_query():
    actor = _user(role="read_only_viewer")
    previous_override = app.dependency_overrides.get(auth.get_current_user)
    app.dependency_overrides[auth.get_current_user] = lambda: actor
    client = TestClient(app)
    try:
        response = client.post(
            "/api/query",
            data={"question": "Find the invoice total"},
            files={"files": ("invoice.pdf", b"test evidence", "application/pdf")},
        )
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(auth.get_current_user, None)
        else:
            app.dependency_overrides[auth.get_current_user] = previous_override

    assert response.status_code == 403


def test_engagement_creation_attributes_authenticated_actor(monkeypatch):
    actor = _user(role="auditor")
    inserted_engagements = []
    events = []

    class ActionQuery:
        def __init__(self, table):
            self.table = table
            self.payload = None

        def insert(self, payload):
            self.payload = payload
            return self

        def execute(self):
            if self.table == "audit_engagement":
                inserted_engagements.append(self.payload)
                return SimpleNamespace(data=[{**self.payload, "engagement_id": "engagement-1"}])
            events.append(self.payload)
            return SimpleNamespace(data=[])

    class Client:
        def table(self, table):
            return ActionQuery(table)

    monkeypatch.setattr(audit_router, "_client_and_business", lambda user: (Client(), user.business_id))
    payload = EngagementCreate(
        entity_name="Example Ltd",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        financial_year="2025-26",
        audit_type="Financial statement audit",
        audit_scope="Revenue and expenses",
        materiality_threshold=Decimal("1000"),
        reporting_currency="INR",
        assigned_auditor="Audit team",
    )

    result = create_engagement(payload, actor)

    assert result["engagement"]["created_by_user_id"] == actor.user_id
    assert inserted_engagements[0]["created_by_user_id"] == actor.user_id
    assert events[0]["actor_user_id"] == actor.user_id
