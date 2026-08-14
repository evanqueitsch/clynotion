"""Per-clinician credential clocks (six clocks → due engine)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.clinicians import clinician_store, install_test_fixtures
from app.comply.credentials import credential_store
from app.main import app
from app.platform.due import due_engine
from app.platform.practices import practice_store


@pytest.fixture(autouse=True)
def _reset():
    practice_store.reset()
    due_engine.reset()
    credential_store.reset()
    clinician_store.reset()
    install_test_fixtures()
    yield
    practice_store.reset()
    due_engine.reset()
    credential_store.reset()
    clinician_store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_credential_matrix_six_clocks_per_clinician(client: TestClient) -> None:
    r = client.get("/comply/credentials", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    clocks = rows[0]["clocks"]
    assert len(clocks) == 6
    ids = {c["clock_id"] for c in clocks}
    assert "license_renewal" in ids
    assert "clinician_exclusion_screening" in ids
    assert all(c["overdue"] for c in clocks)  # no baseline yet


def test_complete_credential_clock_pushes_due_forward(client: TestClient) -> None:
    matrix = client.get("/comply/credentials", headers=_auth("alice")).json()
    clin_id = matrix[0]["clinician_id"]
    name = matrix[0]["display_name"]
    done = client.post(
        f"/comply/credentials/{clin_id}/license_renewal/complete",
        headers=_auth("alice"),
    )
    assert done.status_code == 200, done.text
    assert done.json()["last_completed"]
    home = client.get("/home", headers=_auth("alice")).json()
    open_titles = [
        o["title"] for o in home["bands"]["overdue"] + home["bands"]["this_week"]
    ]
    # Just completed → due in ~730d, should not be on Home bands
    assert not any(
        "License renewal" in t and name in t for t in open_titles
    )


def test_unknown_baseline_appears_on_home_overdue(client: TestClient) -> None:
    home = client.get("/home", headers=_auth("alice")).json()
    cred = [
        o
        for o in home["bands"]["overdue"]
        if o["source"] == "credential_clocks"
    ]
    assert len(cred) >= 6  # at least one clinician × six clocks


def test_crosswalk_includes_credential_clocks(client: TestClient) -> None:
    rows = client.get("/catalog/crosswalk", headers=_auth("alice")).json()
    assert any(r["id"] == "caqh_reattestation" for r in rows)
    assert any(r["kind"] == "credential_clock" for r in rows)


def test_health_version_013(client: TestClient) -> None:
    assert client.get("/health").json()["version"] == "0.13.0"
