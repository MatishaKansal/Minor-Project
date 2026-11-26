from uuid import uuid4
from fastapi.testclient import TestClient
import pytest

from backend.api import app
from backend.auth import AuthenticatedUser, get_current_user


@pytest.fixture
def test_user():
    return AuthenticatedUser(
        user_id=str(uuid4()),
        email="test_admin@ledgerline.local",
        role="administrator",
        business_id="d4b76546-6725-4361-86db-bb95ff79ab71",
        username="test_admin",
    )


@pytest.fixture
def client(test_user):
    app.dependency_overrides[get_current_user] = lambda: test_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_notebook_crud_lifecycle(client):
    nb_id = f"test-nb-{uuid4().hex[:8]}"

    # 1. Create a notebook
    create_payload = {
        "id": nb_id,
        "title": "Vendor Dispute Audit Notebook",
        "messages": [
            {"role": "user", "text": "Are there duplicate invoices for Apex Mart?"},
            {"role": "assistant", "answer": "Found 2 matching invoices with identical amounts."},
        ],
        "messageCount": 2,
    }
    res = client.post("/api/notebooks", json=create_payload)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "success"

    # 2. List notebooks and verify presence
    list_res = client.get("/api/notebooks")
    assert list_res.status_code == 200, list_res.text
    notebooks = list_res.json()
    assert any(nb["id"] == nb_id for nb in notebooks)

    # 3. Retrieve the full notebook
    get_res = client.get(f"/api/notebooks/{nb_id}")
    assert get_res.status_code == 200, get_res.text
    data = get_res.json()
    assert data["id"] == nb_id
    assert data["title"] == "Vendor Dispute Audit Notebook"
    assert len(data["messages"]) == 2

    # 4. Patch/Rename notebook
    patch_res = client.patch(f"/api/notebooks/{nb_id}", json={"title": "Renamed Vendor Audit"})
    assert patch_res.status_code == 200, patch_res.text

    # 5. Delete notebook
    del_res = client.delete(f"/api/notebooks/{nb_id}")
    assert del_res.status_code == 200, del_res.text

    # 6. Verify deletion
    verify_res = client.get(f"/api/notebooks/{nb_id}")
    assert verify_res.status_code == 404
