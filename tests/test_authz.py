"""AuthZ: practice-scoped session access returns 404 on mismatch (not 403)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit import audit_log
from app.auth import issue_token, user_store
from app.main import app
from app.store import store

SAMPLE = Path("sample_supervision_transcript.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean():
    audit_log.clear()
    store.clear()
    yield
    audit_log.clear()
    store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token_for(username: str) -> str:
    _, user = user_store._users[username]
    return issue_token(user)


def _auth(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token_for(username)}"}


def test_practice_b_gets_404_on_practice_a_session(client: TestClient) -> None:
    # Alice (practice-a) creates a session
    r = client.post(
        "/sessions/draft/json",
        headers=_auth("alice"),
        json={"transcript": SAMPLE},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    # Bob (practice-b) must get 404 — not 403 — on every session route
    routes = [
        ("GET", f"/sessions/{sid}"),
        ("GET", f"/sessions/{sid}/note"),
        ("POST", f"/sessions/{sid}/draft"),
        ("POST", f"/sessions/{sid}/finalize"),
    ]
    for method, path in routes:
        if method == "GET":
            resp = client.get(path, headers=_auth("bob"))
        else:
            resp = client.post(path, headers=_auth("bob"), json={})
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code} {resp.text}"
        assert resp.status_code != 403

    # Alice can still read her own session
    ok = client.get(f"/sessions/{sid}", headers=_auth("alice"))
    assert ok.status_code == 200
    assert ok.json()["practice_id"] == "practice-a"


def test_unauthenticated_session_routes_401(client: TestClient) -> None:
    r = client.post("/sessions/draft/json", json={"transcript": SAMPLE})
    assert r.status_code == 401
