"""OPS-3 intake log + OPS-5 eligibility stubs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.eligibility.service import eligibility_store
from app.grow.intake import intake_store
from app.main import app
from app.platform.due import due_engine
from app.platform.practices import practice_store


@pytest.fixture(autouse=True)
def _reset():
    practice_store.reset()
    due_engine.reset()
    intake_store.reset()
    eligibility_store.reset()
    yield
    practice_store.reset()
    due_engine.reset()
    intake_store.reset()
    eligibility_store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_intake_issues_sequential_ids_and_rejects_names(client: TestClient) -> None:
    bad = client.post(
        "/intake",
        headers=_auth("alice"),
        json={"case_code": "Jane Doe", "channel": "phone", "triage": "routine"},
    )
    assert bad.status_code == 422

    a = client.post(
        "/intake",
        headers=_auth("alice"),
        json={"case_code": "HFC-1001", "channel": "phone", "triage": "routine"},
    )
    assert a.status_code == 200, a.text
    assert a.json()["intake_id"] == "IN-0001"
    b = client.post(
        "/intake",
        headers=_auth("alice"),
        json={"case_code": "HFC-1002", "channel": "website", "triage": "urgent"},
    )
    assert b.json()["intake_id"] == "IN-0002"


def test_intake_missed_access_writes_due(client: TestClient) -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    r = client.post(
        "/intake",
        headers=_auth("alice"),
        json={
            "case_code": "HFC-2001",
            "channel": "phone",
            "triage": "routine",
            "request_at": past,
        },
    )
    assert r.status_code == 200, r.text
    iid = r.json()["intake_id"]
    home = client.get("/home", headers=_auth("alice")).json()
    assert any(
        o["source"] == "ops3" and iid in o["title"]
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
    )


def test_intake_schedule_clears_due(client: TestClient) -> None:
    r = client.post(
        "/intake",
        headers=_auth("alice"),
        json={"case_code": "HFC-3001", "channel": "phone", "triage": "routine"},
    )
    iid = r.json()["intake_id"]
    patch = client.patch(
        f"/intake/{iid}",
        headers=_auth("alice"),
        json={
            "date_offered": datetime.now(timezone.utc).isoformat(),
            "date_scheduled": datetime.now(timezone.utc).isoformat(),
            "outcome": "scheduled",
        },
    )
    assert patch.status_code == 200, patch.text
    home = client.get("/home", headers=_auth("alice")).json()
    assert not any(
        o["source"] == "ops3" and o["source_ref"] == f"access:{iid}"
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
    )


def test_eligibility_mock_append_only(client: TestClient) -> None:
    first = client.post(
        "/eligibility/check",
        headers=_auth("alice"),
        json={
            "case_code": "HFC-4001",
            "service_date": "2026-08-14",
            "method": "mock",
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["outcome"] == "eligible"
    second = client.post(
        "/eligibility/check",
        headers=_auth("alice"),
        json={
            "case_code": "HFC-4001X",
            "service_date": "2026-08-14",
            "method": "mock",
        },
    )
    assert second.json()["outcome"] == "ineligible"
    rows = client.get("/eligibility", headers=_auth("alice")).json()
    assert len(rows) == 2


def test_eligibility_rejects_client_name(client: TestClient) -> None:
    r = client.post(
        "/eligibility/check",
        headers=_auth("alice"),
        json={
            "case_code": "Alice Smith",
            "service_date": "2026-08-14",
            "method": "mock",
        },
    )
    assert r.status_code == 422


def test_health_version_011(client: TestClient) -> None:
    assert client.get("/health").json()["version"] == "0.11.0"
