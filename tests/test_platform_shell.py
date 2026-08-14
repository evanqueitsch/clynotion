"""Clynotion platform shell — practice home, due engine, tenant registry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.main import app
from app.platform.due import due_engine
from app.platform.practices import practice_store
from app.store import store


@pytest.fixture(autouse=True)
def _reset_platform():
    practice_store.reset()
    due_engine.reset()
    store.clear()
    yield
    practice_store.reset()
    due_engine.reset()
    store.clear()


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
    assert "bands" in body
    assert body["bands"]["overdue"] == []
    assert body["bands"]["this_week"] == []


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
    assert body["version"] == "0.9.0"


def test_unfinalized_supervision_draft_appears_on_home_overdue(
    client: TestClient,
) -> None:
    draft = client.post(
        "/sessions/draft/json",
        headers=_auth("alice"),
        json={
            "transcript": (
                "Speaker 0: Let's review the case.\n"
                "Speaker 1: I felt stuck with the client.\n"
                "Speaker 0: Try reflecting feelings next session."
            ),
            "present": [],
            "capture_mode": "session_surface",
        },
    )
    assert draft.status_code == 200, draft.text
    sid = draft.json()["session_id"]

    home = client.get("/home", headers=_auth("alice")).json()
    overdue = home["bands"]["overdue"]
    assert len(overdue) >= 1
    match = next(o for o in overdue if o["source_ref"] == sid)
    assert match["title"] == "Finalize supervision draft"
    assert match["domain"] == "people"
    assert match["source"] == "supervision"
    assert match["owner_user_id"] == "user-alice"
    assert "guidance" not in match["title"].lower()
    # titles must not contain note/transcript content
    assert "stuck" not in match["title"].lower()

    due = client.get("/due", headers=_auth("alice")).json()
    assert any(o["source_ref"] == sid for o in due["overdue"])


def test_finalize_clears_supervision_obligation(client: TestClient) -> None:
    draft = client.post(
        "/sessions/draft/json",
        headers=_auth("alice"),
        json={
            "transcript": (
                "Speaker 0: Supervisor here.\n"
                "Speaker 1: Supervisee reflecting.\n"
                "Speaker 0: Guidance for next week."
            ),
            "present": [],
            "capture_mode": "session_surface",
        },
    )
    assert draft.status_code == 200, draft.text
    sid = draft.json()["session_id"]
    # single-speaker drafts finalize without a map
    fin = client.post(
        f"/sessions/{sid}/finalize",
        headers=_auth("alice"),
        json={},
    )
    assert fin.status_code == 200, fin.text

    home = client.get("/home", headers=_auth("alice")).json()
    assert not any(o["source_ref"] == sid for o in home["bands"]["overdue"])
    assert not any(o["source_ref"] == sid for o in home["bands"]["this_week"])


def test_due_bands_split_this_week(client: TestClient) -> None:
    from app.platform.due import due_engine as engine

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    engine.upsert(
        practice_id="practice-a",
        domain="comply",
        source="manual",
        title="Renew license",
        owner_user_id="user-alice",
        due_at=future,
        href="/",
        source_ref="lic-1",
    )
    bands = client.get("/due", headers=_auth("alice")).json()
    assert any(o["title"] == "Renew license" for o in bands["this_week"])
    assert not any(o["title"] == "Renew license" for o in bands["overdue"])


def test_due_items_are_practice_scoped(client: TestClient) -> None:
    from app.platform.due import due_engine as engine

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    engine.upsert(
        practice_id="practice-a",
        domain="people",
        source="manual",
        title="Alice only item",
        owner_user_id="user-alice",
        due_at=past,
        href="/",
        source_ref="a-1",
    )
    a = client.get("/due", headers=_auth("alice")).json()
    b = client.get("/due", headers=_auth("bob")).json()
    assert any(o["title"] == "Alice only item" for o in a["overdue"])
    assert not any(o["title"] == "Alice only item" for o in b["overdue"])
    assert not any(o["title"] == "Alice only item" for o in b["this_week"])
