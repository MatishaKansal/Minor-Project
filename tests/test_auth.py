from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from supabase_auth.errors import AuthApiError

import backend.audit.router as audit_router
from backend.api import app
from backend import auth
from backend.audit.router import EngagementCreate, create_engagement
import backend.api as api
import backend.auth_router as auth_router
import backend.database.supabase_repository as repository
from backend.auth_router import ProfileRoleUpdate


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
    def __init__(self, profiles=None, business_status="active"):
        self.profiles = profiles or []
        self.business_status = business_status

    def table(self, name):
        if name == "profiles":
            return FakeQuery(self.profiles)
        if name == "business":
            return FakeQuery([{"status": self.business_status}])
        raise AssertionError(f"Unexpected table: {name}")


def _user(role="administrator", user_id=None, business_id=None):
    return auth.AuthenticatedUser(
        user_id=user_id or str(uuid4()),
        email="auditor@example.test",
        role=role,
        business_id=business_id or str(uuid4()),
    )


def _credentials(token="verified-token"):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_missing_bearer_token_is_unauthorized():
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(None)
    assert error.value.status_code == 401


def test_username_login_returns_supabase_session_without_exposing_email(monkeypatch):
    class LoginClient:
        def sign_in_with_password(self, credentials):
            assert credentials == {"email": "internal@example.test", "password": "password123"}
            return SimpleNamespace(session=SimpleNamespace(access_token="access", refresh_token="refresh"))

    monkeypatch.setattr(auth_router, "get_supabase_client", lambda: SimpleNamespace(
        table=lambda _name: FakeQuery([{"email": "internal@example.test"}])
    ))
    monkeypatch.setattr(auth_router, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=LoginClient()))
    response = TestClient(app).post("/api/auth/login", json={"username": "Acme", "password": "password123"})
    assert response.status_code == 200
    assert response.json() == {"access_token": "access", "refresh_token": "refresh"}
    assert response.headers["cache-control"] == "no-store"


def test_unknown_username_and_invalid_password_are_indistinguishable(monkeypatch):
    class LoginClient:
        def sign_in_with_password(self, _credentials):
            raise AuthApiError("Invalid login credentials", 400, "invalid_credentials")

    monkeypatch.setattr(auth_router, "get_supabase_client", lambda: SimpleNamespace(table=lambda _name: FakeQuery([])))
    monkeypatch.setattr(auth_router, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=LoginClient()))
    response = TestClient(app).post("/api/auth/login", json={"username": "missing", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password."


def test_only_superadmin_can_set_platform_username(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user())
    response = TestClient(app).put("/api/auth/username", json={"username": "platform"})
    assert response.status_code == 403


def test_login_validation_does_not_echo_password():
    response = TestClient(app).post("/api/auth/login", json={"password": "do-not-echo-this-password"})
    assert response.status_code == 422
    assert "do-not-echo-this-password" not in response.text


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
        "role": "administrator",
        "business_id": business_id,
        "email": "profile@example.test",
    }]
    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=FakeSupabaseAuth()))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: FakeDatabase(profiles))

    user = auth.get_current_user(_credentials())
    assert user.user_id == verified_id
    assert user.role == "administrator"
    assert user.business_id == business_id
    assert user.email == "profile@example.test"


def test_platform_admin_does_not_need_a_company_assignment(monkeypatch):
    user_id = str(uuid4())
    fake_auth = SimpleNamespace(get_user=lambda _token: SimpleNamespace(user=SimpleNamespace(id=user_id, email="platform@example.test")))
    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=fake_auth))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: FakeDatabase([{
        "user_id": user_id, "role": "superadmin", "business_id": None,
    }]))
    user = auth.get_current_user(_credentials())
    assert user.role == "superadmin"
    assert user.business_id is None


def test_role_dependency_rejects_administrator():
    superadmin_only = auth.require_roles("superadmin")
    with pytest.raises(HTTPException) as error:
        superadmin_only(user=_user(role="administrator"))
    assert error.value.status_code == 403


def test_audit_and_query_endpoints_require_a_supabase_session():
    client = TestClient(app)

    assert client.get("/api/audit/summary").status_code == 401
    assert client.post("/api/query", data={"question": "Find the invoice total"}).status_code == 401


@pytest.mark.parametrize("path", [
    "/api/connectors/email/authorize",
    "/api/connectors/email/revoke",
    "/api/connectors/pos/authorize",
    "/api/connectors/pos/revoke",
])
def test_connector_authorization_requires_company_admin(monkeypatch, path):
    client = TestClient(app)
    assert client.post(path, json={}).status_code == 401
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    assert client.post(path, json={}).status_code == 403


def test_company_connector_is_isolated_by_business(monkeypatch):
    first = _user(role="administrator")
    second = _user(role="administrator")
    actor = [first]
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: actor[0])
    monkeypatch.setattr(api, "EMAIL_CONNECTORS", {})
    client = TestClient(app)

    response = client.post("/api/connectors/email/authorize", json={"mode": "demo"})
    assert response.status_code == 200
    assert client.get("/api/connectors/email/status").json()["is_authorized"] is True
    actor[0] = second
    assert client.get("/api/connectors/email/status").json()["is_authorized"] is False
    assert client.post("/api/connectors/email/revoke").status_code == 200
    actor[0] = first
    assert client.get("/api/connectors/email/status").json()["is_authorized"] is True


def test_admin_profile_management_requires_superadmin(monkeypatch):
    client = TestClient(app)
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user())
    assert client.get("/api/admin/profiles").status_code == 404
    assert client.get("/api/admin/businesses").status_code == 404
    with pytest.raises(ValueError):
        ProfileRoleUpdate(role="auditor", business_id=uuid4())


def test_business_selection_rejects_cross_company_access_for_both_roles(monkeypatch):
    own_business = str(uuid4())
    other_business = str(uuid4())
    profile = {"user_id": str(uuid4()), "role": "administrator", "business_id": own_business}

    class FakeSupabaseAuth:
        def get_user(self, _token):
            return SimpleNamespace(user=SimpleNamespace(id=profile["user_id"], email="user@example.test"))

    class BusinessDatabase(FakeDatabase):
        def table(self, name):
            if name == "business":
                class BusinessQuery(FakeQuery):
                    def execute(self):
                        if ("business_id", own_business) in self.filters:
                            return SimpleNamespace(data=[{"status": "active"}])
                        if ("business_id", other_business) not in self.filters:
                            return SimpleNamespace(data=[])
                        return super().execute()
                return BusinessQuery([{"business_id": other_business}])
            return super().table(name)

    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=FakeSupabaseAuth()))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: BusinessDatabase([profile]))
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(_credentials(), other_business)
    assert error.value.status_code == 403
    profile["role"] = "superadmin"
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(_credentials(), other_business)
    assert error.value.status_code == 403
    response = TestClient(app).get("/api/auth/me", headers={
        "Authorization": "Bearer verified-token", "X-Business-ID": other_business,
    })
    assert response.status_code == 403
    with pytest.raises(HTTPException) as error:
        auth.get_current_user(_credentials(), str(uuid4()))
    assert error.value.status_code == 403


@pytest.mark.parametrize("role", ["superadmin", "administrator"])
def test_only_company_admin_can_upload_in_active_business(monkeypatch, role):
    actor = _user(role=role)
    calls = []
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: actor)
    monkeypatch.setattr(api, "ingest_file", lambda _path, **kwargs: (
        calls.append(kwargs) or SimpleNamespace(processing_status="success", database_file_id=None)
    ))
    monkeypatch.setattr(repository, "get_supabase_client", lambda: object())
    monkeypatch.setattr(api, "retrieve", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "evidence_to_cascade_dict", lambda **kwargs: {})
    monkeypatch.setattr(api, "DECISION_MAKER", SimpleNamespace(execute_decision=lambda **kwargs: {"answer": "done"}))
    monkeypatch.setattr(api, "evaluate_agentic_actions", lambda **kwargs: [])
    monkeypatch.setattr(api, "RECENT_AUDITS", {})
    response = TestClient(app).post(
        "/api/query", data={"question": "Review this"}, files={"files": ("test.txt", b"evidence", "text/plain")}
    )
    if role == "superadmin":
        assert response.status_code == 403
        assert not calls
    else:
        assert response.status_code == 200
        assert '"answer": "done"' in response.text
        assert calls[0]["business_id"] == actor.business_id


def test_webhook_key_binds_bill_to_its_business(monkeypatch):
    calls = []
    business_id = str(uuid4())
    connector = SimpleNamespace(
        is_authorized=True, api_key="test-key",
        process_incoming_bill=lambda bill, business_id: calls.append(business_id) or {"success": True},
    )
    monkeypatch.setattr(api, "POS_CONNECTORS", {business_id: connector})
    payload = {
        "bill_number": "B-1", "items": [], "subtotal": 1, "total_cgst": 0,
        "total_sgst": 0, "total_tax": 0, "grand_total": 1,
    }
    client = TestClient(app)
    assert client.post("/api/connectors/pos/webhook", json=payload).status_code == 401
    assert client.post("/api/connectors/pos/webhook", json=payload, headers={"X-POS-API-KEY": "wrong"}).status_code == 401
    assert client.post("/api/connectors/pos/webhook", json=payload, headers={"X-POS-API-KEY": "test-key"}).status_code == 200
    assert calls == [business_id]


def test_profile_assignment_uses_atomic_database_operation(monkeypatch):
    actor = _user(role="superadmin")
    target_id = uuid4()
    business_id = uuid4()
    calls = []

    class Client:
        def table(self, name):
            return FakeQuery([{"user_id": str(target_id), "role": "administrator"}] if name == "profiles"
                             else [{"business_id": str(business_id)}])

        def rpc(self, name, params):
            calls.append((name, params))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data={
                "user_id": str(target_id), "role": params["p_role"], "business_id": params["p_business_id"],
            }))

    monkeypatch.setattr(auth_router, "get_authenticated_database_client", Client)
    result = auth_router.update_profile_role(target_id, ProfileRoleUpdate(role="superadmin", business_id=business_id), actor)
    assert result["profile"]["business_id"] == str(business_id)
    assert calls == [("update_profile_access", {
        "p_actor_user_id": actor.user_id, "p_user_id": str(target_id),
        "p_role": "superadmin", "p_business_id": str(business_id),
    })]

    with pytest.raises(HTTPException) as error:
        auth_router.update_profile_role(UUID(actor.user_id), ProfileRoleUpdate(role="administrator", business_id=business_id), actor)
    assert error.value.status_code == 409


@pytest.mark.parametrize("role", ["superadmin", "administrator"])
def test_only_company_admin_can_advance_audit_work(monkeypatch, role):
    class EmptyClient:
        def table(self, _name):
            return FakeQuery([])

    monkeypatch.setattr(audit_router, "_client_and_business", lambda user: (EmptyClient(), user.business_id))
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user(role=role))
    response = TestClient(app).patch(f"/api/audit/engagements/{uuid4()}/status", json={"status": "Completed", "comment": "Ready"})
    assert response.status_code == (403 if role == "superadmin" else 404)


def test_engagement_creation_attributes_authenticated_actor(monkeypatch):
    actor = _user(role="administrator")
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
