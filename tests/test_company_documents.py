from types import SimpleNamespace
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend import auth
from backend.api import app
from backend import company_router
from backend.rag import indexer


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.values, self.operation = [], None, "select"
        self.start, self.end = 0, None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters.append(lambda row: str(row.get(field)) == str(value))
        return self

    def in_(self, field, values):
        self.filters.append(lambda row: row.get(field) in values)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self.start, self.end = start, end + 1
        return self

    def limit(self, count):
        self.end = count
        return self

    def insert(self, values):
        self.operation, self.values = "insert", values
        return self

    def update(self, values):
        self.operation, self.values = "update", values
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table, [])
        if self.operation == "insert":
            row = {**self.values, "file_id": str(uuid4())}
            rows.append(row)
            return SimpleNamespace(data=[row])
        selected = [row for row in rows if all(check(row) for check in self.filters)]
        if self.operation == "update":
            for row in selected:
                row.update(self.values)
        return SimpleNamespace(data=selected[self.start:self.end], count=len(selected))


class Database:
    def __init__(self):
        self.rows, self.objects = {}, {}
        self.storage = self

    def table(self, name):
        return Query(self, name)

    def from_(self, _bucket):
        return self

    def upload(self, path, content, _options):
        self.objects[path] = content

    def download(self, path):
        return self.objects[path]


def setup_client(monkeypatch):
    company = str(uuid4())
    actor = auth.AuthenticatedUser(user_id=str(uuid4()), email="u@example.test", role="administrator", business_id=company)
    db = Database()
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: actor)
    monkeypatch.setattr(company_router, "get_authenticated_database_client", lambda: db)
    monkeypatch.setattr(company_router, "get_storage_bucket", lambda _db: "documents")
    return TestClient(app), db, actor


def test_uploads_process_independently_and_cannot_cross_companies(monkeypatch):
    client, db, actor = setup_client(monkeypatch)
    calls = []
    def unexpected_search(*_args, **_kwargs):
        raise AssertionError("Document upload must not run document search or indexing")
    monkeypatch.setattr(indexer, "index_company_documents_on_question", unexpected_search)

    def parse(path, persist_to_database, business_id):
        calls.append((path.name, persist_to_database, business_id))
        if path.name == "bad.txt":
            return SimpleNamespace(processing_status="failed", error="Unreadable document")
        return SimpleNamespace(processing_status="success", source_type="text", page_count=1)

    monkeypatch.setattr(company_router, "ingest_file", parse)
    monkeypatch.setattr(company_router, "_insert_invoice_records", lambda _db, file_id, business_id, _evidence: {"invoice_id": file_id})
    ids = []
    for name in ("one.txt", "bad.txt", "three.txt"):
        response = client.post("/api/company/documents", files={"file": (name, b"example", "text/plain")})
        assert response.status_code == 201
        ids.append(response.json()["document"]["file_id"])
    assert calls == []
    results = [client.post(f"/api/company/documents/{file_id}/process") for file_id in ids]
    assert [response.status_code for response in results] == [200, 422, 200]
    assert [row["processing_status"] for row in db.rows["source_file"]] == ["processed", "failed", "processed"]
    assert all(not persist and company == actor.business_id for _, persist, company in calls)
    assert client.post(f"/api/company/documents/{ids[0]}/process").status_code == 409
    monkeypatch.setattr(company_router, "ingest_file", lambda *_args, **_kwargs: SimpleNamespace(processing_status="success", source_type="text", page_count=1))
    assert client.post(f"/api/company/documents/{ids[1]}/process").status_code == 200
    assert db.rows["source_file"][1]["processing_status"] == "processed"
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: replace(actor, business_id=str(uuid4())))
    assert client.get(f"/api/company/documents/{ids[0]}").status_code == 404
    assert client.post(f"/api/company/documents/{ids[1]}/process").status_code == 404


def test_review_flags_only_same_company_and_requires_both_fields(monkeypatch):
    client, db, actor = setup_client(monkeypatch)
    own_file, other_file, foreign_file = str(uuid4()), str(uuid4()), str(uuid4())
    invoice_id = str(uuid4())
    db.rows["source_file"] = [{"business_id": actor.business_id, "file_id": own_file, "file_name": "invoice.pdf"}]
    db.rows["invoice"] = [
        {"invoice_id": invoice_id, "business_id": actor.business_id, "file_id": own_file, "seller_name": "Acme Ltd", "invoice_number": "INV-001"},
        {"invoice_id": str(uuid4()), "business_id": actor.business_id, "file_id": other_file, "seller_name": "acme  ltd", "invoice_number": "inv 001"},
        {"invoice_id": str(uuid4()), "business_id": str(uuid4()), "file_id": foreign_file, "seller_name": "Acme Ltd", "invoice_number": "INV-001"},
        {"invoice_id": str(uuid4()), "business_id": actor.business_id, "file_id": str(uuid4()), "seller_name": None, "invoice_number": "INV-001"},
    ]
    db.rows["invoice_extraction"] = [{"invoice_id": invoice_id, "raw_text": "Invoice date: 01/02/2025"}]
    db.rows["invoice_field_provenance"] = [{"invoice_id": invoice_id, "field_name": "invoice_date", "source_text": "Invoice date: 01/02/2025"}]
    response = client.get(f"/api/company/documents/{own_file}/review")
    assert response.status_code == 200
    result = response.json()
    assert [row["file_id"] for row in result["suspected_duplicates"]] == [other_file]
    assert result["invoices"][0]["provenance"][0]["field_name"] == "invoice_date"
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: replace(actor, business_id=str(uuid4())))
    assert client.get(f"/api/company/documents/{own_file}/review").status_code == 404


def test_superadmin_cannot_upload_or_review_documents(monkeypatch):
    client, _db, actor = setup_client(monkeypatch)
    monkeypatch.setitem(app.dependency_overrides, auth.get_current_user, lambda: replace(actor, role="superadmin"))
    assert client.post("/api/company/documents", files={"file": ("file.txt", b"x", "text/plain")}).status_code == 403
    assert client.get(f"/api/company/documents/{uuid4()}/review").status_code == 403


def test_search_indexes_only_unindexed_documents_from_the_company(monkeypatch):
    db = Database()
    company, other = str(uuid4()), str(uuid4())
    own, indexed, foreign = str(uuid4()), str(uuid4()), str(uuid4())
    invoice = str(uuid4())
    db.rows["source_file"] = [
        {"business_id": company, "file_id": own, "file_name": "own.pdf", "processing_status": "processed"},
        {"business_id": company, "file_id": indexed, "file_name": "indexed.pdf", "processing_status": "processed"},
        {"business_id": other, "file_id": foreign, "file_name": "foreign.pdf", "processing_status": "processed"},
    ]
    db.rows["document_chunks"] = [{"business_id": company, "source_file_id": indexed}]
    db.rows["invoice"] = [{"business_id": company, "file_id": own, "invoice_id": invoice}]
    db.rows["invoice_extraction"] = [{"invoice_id": invoice, "raw_text": "invoice text"}]
    calls = []
    monkeypatch.setattr(indexer, "index_document", lambda **kwargs: calls.append(kwargs) or 1)
    assert indexer.index_company_documents_on_question(company, db) == []
    assert [(call["business_id"], call["source_file_id"], call["text"]) for call in calls] == [(company, own, "invoice text")]
