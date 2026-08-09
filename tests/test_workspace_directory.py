"""Google Workspace directory → practice roster include flow."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.clinicians import clinician_store
from app.main import app


@pytest.fixture(autouse=True)
def _clean_roster(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ATTUNE_AUTH", "dev")
    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "mock")
    monkeypatch.setenv("ATTUNE_CLINICIAN_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_CLINICIAN_DATA_PATH", str(tmp_path / "clinicians.enc"))
    monkeypatch.setenv("ATTUNE_WORKSPACE_TOKEN_PATH", str(tmp_path / "workspace_tokens.enc"))
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "example.com")
    from app.crypto import generate_key

    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    clinician_store.reset_and_clear_disk()
    yield
    clinician_store.reset_and_clear_disk()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_workspace_status_mock_connected(client: TestClient) -> None:
    r = client.get("/workspace/status", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["connected"] is True


def test_list_and_include_workspace_users(client: TestClient) -> None:
    users = client.get("/workspace/users", headers=_auth())
    assert users.status_code == 200
    rows = users.json()
    assert len(rows) >= 2
    member = rows[0]
    included = client.post(
        "/workspace/include",
        headers=_auth(),
        json={
            "clear_seed_roster": True,
            "members": [
                {
                    "google_id": member["google_id"],
                    "email": member["email"],
                    "display_name": member["display_name"],
                    "default_role": "supervisor",
                }
            ],
        },
    )
    assert included.status_code == 200, included.text
    body = included.json()
    assert len(body) == 1
    assert body[0]["source"] == "workspace"
    assert body[0]["email"] == member["email"]

    roster = client.get("/clinicians", headers=_auth())
    assert roster.status_code == 200
    emails = {c["email"] for c in roster.json()}
    assert member["email"] in emails
    # Seed clinicians cleared for practice-a
    assert all(c["source"] == "workspace" for c in roster.json())


def test_include_admin_role(client: TestClient) -> None:
    users = client.get("/workspace/users", headers=_auth()).json()
    member = users[0]
    included = client.post(
        "/workspace/include",
        headers=_auth(),
        json={
            "clear_seed_roster": True,
            "members": [
                {
                    "google_id": member["google_id"],
                    "email": member["email"],
                    "display_name": member["display_name"],
                    "default_role": "admin",
                }
            ],
        },
    )
    assert included.status_code == 200, included.text
    assert included.json()[0]["default_role"] == "admin"
    listed = client.get("/workspace/users", headers=_auth()).json()
    hit = next(u for u in listed if u["google_id"] == member["google_id"])
    assert hit["already_included"] is True
    assert hit["default_role"] == "admin"


def test_exclude_workspace_member(client: TestClient) -> None:
    users = client.get("/workspace/users", headers=_auth()).json()
    member = users[0]
    client.post(
        "/workspace/include",
        headers=_auth(),
        json={
            "clear_seed_roster": True,
            "members": [
                {
                    "google_id": member["google_id"],
                    "email": member["email"],
                    "display_name": member["display_name"],
                    "default_role": "supervisee",
                }
            ],
        },
    )
    cid = f"gw:{member['google_id']}"
    ex = client.post(f"/workspace/exclude/{cid}", headers=_auth())
    assert ex.status_code == 200
    assert ex.json()["included"] is False
    roster = client.get("/clinicians", headers=_auth()).json()
    assert cid not in {c["clinician_id"] for c in roster}
