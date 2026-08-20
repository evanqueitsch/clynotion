"""HR onboarding & compliance — admin gate, LSW steps, I-9 / expiry, no forbidden fields."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import issue_token, user_store
from app.eligibility.service import eligibility_store
from app.grow.intake import intake_store
from app.hr.onboarding import onboarding_store
from app.ingest.sp_csv import ingest_store
from app.main import app
from app.platform.due import due_engine
from app.platform.practices import practice_store
from app.comply.credentials import credential_store
from app.store import store


@pytest.fixture(autouse=True)
def _reset():
    practice_store.reset()
    due_engine.reset()
    ingest_store.reset()
    intake_store.reset()
    eligibility_store.reset()
    credential_store.reset()
    onboarding_store.reset()
    store.clear()
    yield
    onboarding_store.reset()
    due_engine.reset()
    store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str = "alice") -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_hr_roster_seeded_for_admin(client: TestClient) -> None:
    r = client.get("/hr/roster", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    names = {row["display_name"] for row in r.json()}
    assert {"Alex Mistovich", "Nathan Sterry", "Kayleigh"} <= names


def test_hr_forbidden_for_other_practice_user(client: TestClient) -> None:
    r = client.get("/hr/roster", headers=_auth("bob"))
    assert r.status_code == 403


def test_lsw_shows_supervision_step_lcsw_na(client: TestClient) -> None:
    roster = client.get("/hr/roster", headers=_auth("alice")).json()
    nathan = next(r for r in roster if r["display_name"] == "Nathan Sterry")
    alex = next(r for r in roster if r["display_name"] == "Alex Mistovich")
    n = client.get(f"/hr/employees/{nathan['employee_id']}", headers=_auth("alice")).json()
    a = client.get(f"/hr/employees/{alex['employee_id']}", headers=_auth("alice")).json()
    assert n["onboarding"]["steps"]["supervisionAgreement"] == "NotStarted"
    assert a["onboarding"]["steps"]["supervisionAgreement"] == "NA"
    # Supervision log rejected for LCSW
    bad = client.post(
        f"/hr/employees/{alex['employee_id']}/supervision",
        headers=_auth("alice"),
        json={"duration_minutes": 60, "date": "2026-08-18"},
    )
    assert bad.status_code == 422


def test_percent_complete_and_forbidden_ssn(client: TestClient) -> None:
    created = client.post(
        "/hr/employees",
        headers=_auth("alice"),
        json={
            "display_name": "Test Hire",
            "license_type": "LPC",
            "start_date": date.today().isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    eid = created.json()["employee"]["employee_id"]
    assert created.json()["onboarding"]["percent_complete"] == 0.0
    # Mark all steps complete
    steps = {
        k: "Complete"
        for k in created.json()["onboarding"]["steps"]
        if k != "supervisionAgreement"
    }
    steps["supervisionAgreement"] = "NA"
    patched = client.patch(
        f"/hr/employees/{eid}/onboarding",
        headers=_auth("alice"),
        json={"steps": steps},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["onboarding"]["overall_status"] == "Complete"
    assert patched.json()["onboarding"]["percent_complete"] == 100.0
    # Forbidden field
    bad = client.patch(
        f"/hr/employees/{eid}",
        headers=_auth("alice"),
        json={"ssn": "123-45-6789"},
    )
    assert bad.status_code == 422


def test_i9_overdue_and_clearance_expiry_due(client: TestClient) -> None:
    past = (date.today() - timedelta(days=14)).isoformat()
    created = client.post(
        "/hr/employees",
        headers=_auth("alice"),
        json={
            "display_name": "Late I9",
            "license_type": "admin",
            "start_date": past,
        },
    )
    eid = created.json()["employee"]["employee_id"]
    detail = client.get(f"/hr/employees/{eid}", headers=_auth("alice")).json()
    assert detail["onboarding"]["i9_section2_overdue"] is True
    home = client.get("/home", headers=_auth("alice")).json()
    titles = [o["title"] for o in home["bands"]["overdue"] + home["bands"]["this_week"]]
    assert any("I-9 Section 2 overdue" in t and "Late I9" in t for t in titles)

    issue = (date.today() - timedelta(days=30)).isoformat()
    client.post(
        f"/hr/employees/{eid}/compliance",
        headers=_auth("alice"),
        json={"type": "Act34", "issue_date": issue},
    )
    dash = client.get("/hr/dashboard", headers=_auth("alice")).json()
    # freshly issued +60 months should NOT be in 60-day window
    assert not any(
        r.get("display_name") == "Late I9" and r.get("label") == "Act34"
        for r in dash["expiring_soon"]
    )
    # Near-term expiry
    soon = (date.today() + timedelta(days=10)).isoformat()
    client.patch(
        f"/hr/employees/{eid}",
        headers=_auth("alice"),
        json={"license_expiry": soon},
    )
    dash2 = client.get("/hr/dashboard", headers=_auth("alice")).json()
    assert any(
        r.get("display_name") == "Late I9" and "License" in r.get("label", "")
        for r in dash2["expiring_soon"]
    )


def test_lsw_supervision_hours(client: TestClient) -> None:
    roster = client.get("/hr/roster", headers=_auth("alice")).json()
    nathan = next(r for r in roster if r["display_name"] == "Nathan Sterry")
    r = client.post(
        f"/hr/employees/{nathan['employee_id']}/supervision",
        headers=_auth("alice"),
        json={"duration_minutes": 90, "date": "2026-08-18", "format": "InPerson"},
    )
    assert r.status_code == 200, r.text
    hours = r.json()["supervision"]
    assert hours["minutes_to_date"] == 90
    assert hours["hours_to_date"] == 1.5


def test_catalog_onboarding_live(client: TestClient) -> None:
    units = client.get("/catalog", headers=_auth("alice")).json()
    hr = next(u for u in units if u["name"] == "HR")
    tools = {t["id"]: t for t in hr["tools"]}
    assert tools["onboarding_compliance"]["status"] == "live"
    assert tools["onboarding_compliance"]["href"] == "/#onboarding"


def test_health_version_014(client: TestClient) -> None:
    assert client.get("/health").json()["version"] == "0.14.0"
