"""Clynotion platform shell — home, due engine, comply seeds, SP ingest."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.ingest.sp_csv import ingest_store
from app.main import app
from app.platform.due import due_engine
from app.platform.practices import practice_store
from app.store import store

FIXTURE_DOC = Path(__file__).resolve().parents[1] / "docs/fixtures/sp_documentation_sample.csv"


@pytest.fixture(autouse=True)
def _reset_platform():
    practice_store.reset()
    due_engine.reset()
    ingest_store.reset()
    store.clear()
    yield
    practice_store.reset()
    due_engine.reset()
    ingest_store.reset()
    store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_home_returns_tools_pulse_and_seeded_comply(client: TestClient) -> None:
    r = client.get("/home", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product"] == "clynotion"
    tool_ids = {t["id"] for t in body["tools"]}
    assert {"supervision", "comply", "ingest"} <= tool_ids
    assert "pulse" in body
    assert body["pulse"]["open_obligations"] >= 1
    # OPS-2 seeds + stale documentation ingest land on Home
    titles = [o["title"] for o in body["bands"]["overdue"] + body["bands"]["this_week"]]
    assert any(t.startswith("o5:") for t in titles) or any(
        "documentation" in t.lower() for t in titles
    )


def test_complete_obligation_one_click(client: TestClient) -> None:
    client.post("/comply/seed", headers=_auth("alice"))
    home = client.get("/home", headers=_auth("alice")).json()
    items = [
        o
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
        if o["source"] == "ops2" and o["source_ref"] == "o5"
    ]
    assert items, "expected seeded o5 clock on Home"
    oid = items[0]["obligation_id"]
    done = client.post(f"/due/{oid}/complete", headers=_auth("alice"))
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    home2 = client.get("/home", headers=_auth("alice")).json()
    open_ids = {
        o["obligation_id"]
        for o in home2["bands"]["overdue"] + home2["bands"]["this_week"]
    }
    assert oid not in open_ids
    # Completed OPS-2 clocks must not be re-seeded open on the next Home load.
    assert not any(
        o["source"] == "ops2" and o["source_ref"] == "o5"
        for o in home2["bands"]["overdue"] + home2["bands"]["this_week"]
    )


def test_comply_catalog(client: TestClient) -> None:
    r = client.get("/comply/catalog", headers=_auth("alice"))
    assert r.status_code == 200
    assert len(r.json()) == 12


def test_documentation_ingest_idempotent_and_unsigned_due(
    client: TestClient,
) -> None:
    csv_text = FIXTURE_DOC.read_text(encoding="utf-8")
    first = client.post(
        "/ingest/upload",
        headers=_auth("alice"),
        json={"report_type": "documentation", "csv_text": csv_text},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "ok"
    assert body["row_count"] == 4
    assert body["unsigned_aging_count"] == 2

    second = client.post(
        "/ingest/upload",
        headers=_auth("alice"),
        json={"report_type": "documentation", "csv_text": csv_text},
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "duplicate"
    assert second.json()["upload_id"] == body["upload_id"]

    home = client.get("/home", headers=_auth("alice")).json()
    assert any(
        "Unsigned notes aging" in o["title"]
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
    )
    assert home["pulse"]["unsigned_aging_rows"] == 2


def test_demographics_ingest_rejected(client: TestClient) -> None:
    r = client.post(
        "/ingest/upload",
        headers=_auth("alice"),
        json={"report_type": "demographics", "csv_text": "a,b\n1,2"},
    )
    assert r.status_code == 422


def test_unfinalized_supervision_draft_appears_on_home(
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
    match = next(
        o
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
        if o["source_ref"] == sid
    )
    assert match["title"] == "Finalize supervision draft"
    assert "stuck" not in match["title"].lower()


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
    sid = draft.json()["session_id"]
    fin = client.post(f"/sessions/{sid}/finalize", headers=_auth("alice"), json={})
    assert fin.status_code == 200, fin.text
    home = client.get("/home", headers=_auth("alice")).json()
    assert not any(
        o["source_ref"] == sid
        for o in home["bands"]["overdue"] + home["bands"]["this_week"]
    )


def test_due_items_are_practice_scoped(client: TestClient) -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    due_engine.upsert(
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


def test_health_version(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["version"] == "0.10.0"
    assert body["product"] == "clynotion"


def test_legacy_attune_home_still_works(client: TestClient) -> None:
    r = client.get("/attune/home", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["product"] == "clynotion"
