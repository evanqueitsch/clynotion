"""Attune shell — practice home and tenant registry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.attune.practices import practice_store
from app.auth import issue_token, user_store
from app.main import app


@pytest.fixture(autouse=True)
def _reset_practices():
    practice_store.reset()
    yield
    practice_store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_attune_home_returns_practice_and_clynotion_tool(client: TestClient) -> None:
    r = client.get("/attune/home", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"] == "attune"
    assert body["practice"]["practice_id"] == "practice-a"
    assert body["practice"]["display_name"]
    assert any(t["id"] == "clynotion" for t in body["tools"])


def test_attune_practice_scoped(client: TestClient) -> None:
    a = client.get("/attune/practice", headers=_auth("alice")).json()
    b = client.get("/attune/practice", headers=_auth("bob")).json()
    assert a["practice_id"] == "practice-a"
    assert b["practice_id"] == "practice-b"


def test_health_reports_attune_shell(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["product"] == "attune"
    assert body["shell"] == "attune"
    assert body["version"] == "0.8.0"
