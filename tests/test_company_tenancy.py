from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError
from supabase_auth.errors import AuthApiError

import backend.company_router as company_router_module
from backend import auth
from backend.api import app
from backend.database import supabase_repository as repository


def _user(role="administrator", business_id=None):
    return auth.AuthenticatedUser(
        user_id=str(uuid4()), email="u@example.test", role=role, business_id=business_id or str(uuid4())
    )


class Table:
    def __init__(self, rows=None, count=0):
        self.rows, self.count, self.filters, self.payload = rows or [], count, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, *a):
        self.filters.append(a)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def execute(self):
        if self.payload is not None:
            return SimpleNamespace(data=[{**self.payload, "business_id": "b"}] if self.rows else [])
        return SimpleNamespace(data=self.rows, count=self.count)


class Db:
    def __init__(self, tables, rpc_calls=None):
        self.tables, self.rpc_calls = tables, rpc_calls if rpc_calls is not None else []

    def table(self, name):
        return self.tables.get(name, Table())

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return Table()


def test_suspended_company_blocks_its_users(monkeypatch):
    uid, bid = str(uuid4()), str(uuid4())
    fake_auth = SimpleNamespace(get_user=lambda _t: SimpleNamespace(user=SimpleNamespace(id=uid, email="a@b.co")))
    db = Db({
        "profiles": Table([{"user_id": uid, "role": "administrator", "business_id": bid}]),
        "business": Table([{"status": "suspended"}]),
    })
    monkeypatch.setattr(auth, "_get_supabase_auth_client", lambda: SimpleNamespace(auth=fake_auth))
    monkeypatch.setattr(auth, "get_supabase_client", lambda: db)
    from fastapi.security import HTTPAuthorizationCredentials

    with pytest.raises(HTTPException) as error:
        auth.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials="t"))
    assert error.value.status_code == 403


@pytest.mark.parametrize("path,method", [
    ("/api/admin/companies", "get"),
    ("/api/company/overview", "get"),
])
def test_company_endpoints_require_auth_and_role(monkeypatch, path, method):
    client = TestClient(app)
    assert getattr(client, method)(path).status_code == 401
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("administrator"))
    expected = 403 if path.startswith("/api/admin") else 200
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", lambda: Db({}))
    assert getattr(client, method)(path).status_code == expected


def test_company_admin_cannot_change_company_status(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("administrator"))
    response = TestClient(app).patch(f"/api/admin/companies/{uuid4()}/status", json={"status": "suspended"})
    assert response.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/companies"),
    ("get", "/api/admin/platform/activity"),
    ("get", "/api/admin/platform/health"),
    ("post", f"/api/admin/companies/{uuid4()}/administrator"),
])
def test_company_admin_cannot_access_platform_monitoring(monkeypatch, method, path):
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user())
    client = TestClient(app)
    response = client.post(path, json={"username": "admin", "password": "password123"}) if method == "post" else client.get(path)
    assert response.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("get", "/api/audit/summary"),
    ("get", "/api/company/overview"),
    ("get", "/api/connectors/email/status"),
    ("post", "/api/actions/run-patrol"),
    ("post", "/api/connectors/pos/revoke"),
])
def test_superadmin_cannot_access_company_workspace(monkeypatch, method, path):
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    assert getattr(TestClient(app), method)(path).status_code == 403


def test_company_list_reports_missing_migration(monkeypatch):
    class BrokenDb:
        def rpc(self, *_args):
            raise APIError({"code": "PGRST202", "message": "function not found"})

    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", BrokenDb)
    response = TestClient(app).get("/api/admin/companies")
    assert response.status_code == 503
    assert "migrations" in response.json()["detail"]


def test_company_list_returns_actual_rpc_data(monkeypatch):
    rows = [{"business_id": str(uuid4()), "business_name": "Acme", "document_count": 4}]
    db = Db({})
    db.rpc = lambda name, params: SimpleNamespace(execute=lambda: SimpleNamespace(data=rows))
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", lambda: db)
    response = TestClient(app).get("/api/admin/companies")
    assert response.status_code == 200
    assert response.json() == {"companies": rows}


def test_missing_migration_does_not_create_auth_account(monkeypatch):
    class BrokenDb:
        def rpc(self, *_args):
            raise APIError({"code": "PGRST202", "message": "function not found"})

    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", BrokenDb)
    response = TestClient(app).post("/api/admin/companies", json={
        "company_name": "Acme", "gstin": "27AAPCA1234F1Z5", "username": "acme", "password": "password123",
    })
    assert response.status_code == 503


def test_company_with_administrator_cannot_be_provisioned_again(monkeypatch):
    class BrokenDb:
        def rpc(self, *_args):
            raise APIError({"code": "P0001", "message": "This company already has a Company Admin"})

    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", BrokenDb)
    response = TestClient(app).post(f"/api/admin/companies/{uuid4()}/administrator", json={"username": "acme", "password": "password123"})
    assert response.status_code == 409


def test_superadmin_can_suspend_company(monkeypatch):
    actor = _user("superadmin")
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: actor)
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", lambda: Db({"business": Table([{}])}))
    response = TestClient(app).patch(f"/api/admin/companies/{actor.business_id}/status", json={"status": "suspended"})
    assert response.status_code == 200


def test_superadmin_registers_company_and_creates_account(monkeypatch):
    calls, business_id = [], str(uuid4())
    class Client:
        def rpc(self, name, params):
            calls.append((name, params))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=business_id))
    db = Client()
    monkeypatch.setattr(company_router_module, "_provision_admin", lambda client, bid, password: calls.append(("provision", bid, password)))
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", lambda: db)
    response = TestClient(app).post("/api/admin/companies", json={
        "company_name": "Acme Ltd", "gstin": "27AAPCA1234F1Z5", "username": "Acme", "password": "password123",
    })
    assert response.status_code == 201
    assert calls[0][0] == "reserve_new_company"
    assert calls[0][1]["p_username"] == "acme"
    assert calls[1] == ("provision", business_id, "password123")


def test_provisioning_fails_safely_and_allows_retry():
    business_id, user_id = str(uuid4()), str(uuid4())
    calls = []
    class Client:
        def rpc(self, name, params):
            calls.append(name)
            data = {"user_id": user_id, "username": "acme"} if name == "claim_company_account" else None
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))
    db = Client()
    def missing(_id):
        raise AuthApiError("not found", 404, "user_not_found")
    def failed(_attributes):
        raise AuthApiError("unavailable", 503, "unexpected_failure")
    db.auth = SimpleNamespace(admin=SimpleNamespace(get_user_by_id=missing, create_user=failed))
    with pytest.raises(HTTPException) as error:
        company_router_module._provision_admin(db, business_id, "password123")
    assert error.value.status_code == 502
    assert "preserved" in error.value.detail
    assert calls == ["claim_company_account", "fail_company_account"]


def test_provisioning_rejects_unrelated_existing_auth_account():
    business_id, user_id = str(uuid4()), str(uuid4())
    calls = []
    class Client:
        def rpc(self, name, params):
            calls.append(name)
            return SimpleNamespace(execute=lambda: SimpleNamespace(data={"user_id": user_id, "username": "acme"}))
    db = Client()
    db.auth = SimpleNamespace(admin=SimpleNamespace(get_user_by_id=lambda _id: SimpleNamespace(
        user=SimpleNamespace(id=user_id, email="other@example.test", app_metadata={})
    )))
    with pytest.raises(HTTPException) as error:
        company_router_module._provision_admin(db, business_id, "password123")
    assert error.value.status_code == 409
    assert calls == ["claim_company_account", "fail_company_account"]


def test_company_registration_requires_platform_role_and_valid_input(monkeypatch):
    client = TestClient(app)
    ok = {"company_name": "Acme", "gstin": "27AAPCA1234F1Z5", "username": "acme", "password": "password123"}
    assert client.post("/api/admin/companies", json=ok).status_code == 401
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user())
    assert client.post("/api/admin/companies", json=ok).status_code == 403
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    assert client.post("/api/admin/companies", json={**ok, "username": "bad username"}).status_code == 422
    assert client.post("/api/auth/signup-company", json=ok).status_code == 404
    missing_gstin = {key: value for key, value in ok.items() if key != "gstin"}
    response = client.post("/api/admin/companies", json=missing_gstin)
    assert response.status_code == 422
    assert "password123" not in response.text


def test_document_seller_does_not_rename_company():
    updates = []

    class Client:
        def table(self, _name):
            return SimpleNamespace(update=lambda p: updates.append(p) or SimpleNamespace(eq=lambda *a: SimpleNamespace(execute=lambda: None)))

    evidence = SimpleNamespace(seller_name="Vendor Pvt", party_name="Vendor Pvt", seller_gstin=None,
                               seller_pan=None, seller_address=None, currency="INR")
    repository._sync_business_profile(Client(), str(uuid4()), evidence)
    assert all("business_name" not in update for update in updates)


def test_company_documents_are_scoped_and_counted(monkeypatch):
    user = _user()
    table = Table([{"file_id": str(uuid4()), "file_name": "invoice.pdf"}], count=101)
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: user)
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", lambda: Db({"source_file": table}))
    response = TestClient(app).get("/api/company/documents")
    assert response.status_code == 200
    assert response.json()["total"] == 101
    assert ("business_id", user.business_id) in table.filters


def test_document_download_enforces_company_and_storage_scope(monkeypatch):
    user, other_business, file_id = _user(), str(uuid4()), str(uuid4())
    downloads = []
    records = [{
        "business_id": user.business_id, "file_id": file_id, "file_name": "invoice.pdf",
        "mime_type": "application/pdf", "storage_path": f"documents/{user.business_id}/invoice.pdf",
    }, {
        "business_id": other_business, "file_id": str(uuid4()), "file_name": "private.pdf",
        "storage_path": f"documents/{other_business}/private.pdf",
    }]

    class ScopedTable(Table):
        def execute(self):
            return SimpleNamespace(data=[row for row in records if all(row.get(k) == v for k, v in self.filters)])

    class Client:
        storage = SimpleNamespace(from_=lambda bucket: SimpleNamespace(
            download=lambda path: downloads.append((bucket, path)) or b"pdf content"
        ))

        def table(self, _name):
            return ScopedTable()

    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: user)
    monkeypatch.setattr(company_router_module, "get_authenticated_database_client", Client)
    client = TestClient(app)
    response = client.get(f"/api/company/documents/{file_id}")
    assert response.status_code == 200
    assert response.content == b"pdf content"
    assert response.headers["cache-control"] == "no-store"
    assert downloads == [("documents", f"{user.business_id}/invoice.pdf")]
    assert client.get(f"/api/company/documents/{records[1]['file_id']}").status_code == 404
    records[0]["storage_path"] = f"documents/{user.business_id}/../{other_business}/private.pdf"
    assert client.get(f"/api/company/documents/{file_id}").status_code == 409
    records[0]["storage_path"] = f"documents/{other_business}/private.pdf"
    assert client.get(f"/api/company/documents/{file_id}").status_code == 409
    assert len(downloads) == 1
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: _user("superadmin"))
    assert client.get(f"/api/company/documents/{file_id}").status_code == 403


