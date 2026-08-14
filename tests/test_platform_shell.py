"""Clynotion platform shell — practice home and tenant registry."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.main import app
from app.platform.practices import practice_store


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


def test_home_returns_practice_and_supervision_tool(client: TestClient) -> None:
    r = client.get("/home", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"] == "clynotion"
    assert body["practice"]["practice_id"] == "practice-a"
    assert body["practice"]["display_name"]
    assert any(t["id"] == "supervision" for t in body["tools"])


def test_practice_scoped(client: TestClient) -> None:
    a = client.get("/practice", headers=_auth("alice")).json()
    b = client.get("/practice", headers=_auth("bob")).json()
    assert a["practice_id"] == "practice-a"
    assert b["practice_id"] == "practice-b"


def test_legacy_attune_home_still_works(client: TestClient) -> None:
    r = client.get("/attune/home", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["product"] == "clynotion"


def test_health_reports_clynotion_shell(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["product"] == "clynotion"
    assert body["shell"] == "clynotion"
    assert body["tool"] == "supervision"
    assert body["version"] == "0.8.1"
