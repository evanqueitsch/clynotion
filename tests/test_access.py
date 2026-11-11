"""Role grants are data. No role sees nothing. Melissa is not an Admin."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.access.resolve import is_owner
from app.main import app
from app.access.store import access_store
from app.auth import User, issue_token, user_store
from app.hr.time_entry import set_clock
from tests.test_time_entry import (
    FRI_1130,
    WEEK,
    _alice,
    _enter,
    _jordan,
    _melissa,
    _roster,
    _save_settings,
    _settings,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def test_home_page_is_not_cached(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "no-store" in response.headers.get("cache-control", "")
    assert 'id="btn-top-roles"' in response.text


def test_operations_mailbox_is_admin() -> None:
    from app.access.resolve import assigned_role_id, is_owner, permissions_for

    user = User("google:ops", "Evan Queitsch", "practice-hfc", "operations@heart4change.org")
    assert is_owner(user)
    assert "capability:manage_roles" in permissions_for(user)
    assert assigned_role_id(user) == "admin"


def test_roles_screen_is_linked_from_home_chrome() -> None:
    text = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'id="btn-top-roles" class="primary"' in text
    assert "hidden" not in text.split('id="btn-top-roles"', 1)[1].split(">", 1)[0]
    assert "Roles &amp; access" in text
    assert 'id="home-roles-card" class="tool-card"' in text
    assert '"/#roles"' in text


def test_home_catalog_follows_role_and_marks_unbuilt_modules(client: TestClient) -> None:
    from app.platform.catalog import CATALOG_ITEMS

    admin = client.get("/home", headers=_alice()).json()
    assert admin["full_catalog"] is True
    seen = {tool["id"]: tool for unit in admin["units"] for tool in unit["tools"]}
    catalog_tools = [
        item
        for item in CATALOG_ITEMS
        if item.get("kind") == "tool" and item.get("status") != "retired"
    ]
    assert set(seen) == {item["id"] for item in catalog_tools}
    planned = [item["id"] for item in catalog_tools if item.get("status") == "planned"]
    assert planned
    for tool_id in planned:
        assert seen[tool_id]["status"] == "planned"
        assert seen[tool_id]["coming_soon"] is True
        assert seen[tool_id]["href"] == ""
    page = client.get("/").text
    assert "Coming Soon" in page
    assert ">Payroll week<" in page
    assert "Weekly hours admin" not in page

    _roster()
    jordan = _jordan()
    staff = client.get("/home", headers=_auth(jordan)).json()
    assert staff["full_catalog"] is False
    assert [tool["id"] for unit in staff["units"] for tool in unit["tools"]] == ["time_entry"]
    assert all(not tool.get("coming_soon") for unit in staff["units"] for tool in unit["tools"])

    access_store.update_role_permissions(
        "practice-a", "clinician", ["tool:time_entry", "tool:intake_log"]
    )
    granted = client.get("/home", headers=_auth(jordan)).json()
    assert {tool["id"] for unit in granted["units"] for tool in unit["tools"]} == {"time_entry", "intake_log"}
    assert client.get("/intake", headers=_auth(jordan)).status_code == 200

    access_store.update_role_permissions("practice-a", "clinician", ["tool:time_entry"])
    revoked = client.get("/home", headers=_auth(jordan)).json()
    assert [tool["id"] for unit in revoked["units"] for tool in unit["tools"]] == ["time_entry"]
    assert client.get("/intake", headers=_auth(jordan)).status_code == 403

    access_store.update_role_permissions(
        "practice-a", "clinician", ["tool:time_entry", "tool:marketing_workbench"]
    )
    hidden = client.get("/home", headers=_auth(jordan)).json()
    assert [tool["id"] for unit in hidden["units"] for tool in unit["tools"]] == ["time_entry"]


def test_seeded_roles_grant_only_time_entry_and_admin_is_separate(client: TestClient) -> None:
    roles = client.get("/access/roles", headers=_alice()).json()["roles"]
    by_id = {row["id"]: row for row in roles}
    for role_id in ("intake", "clinician", "clinical_supervisor"):
        assert by_id[role_id]["permissions"] == ["tool:time_entry"]
    billing = by_id["billing"]["permissions"]
    assert "tool:time_entry" in billing
    for permission in (
        "tool:credentialing",
        "capability:credentialing_manage",
        "capability:credentialing_payers",
        "capability:credentialing_sensitive",
        "tool:billing",
    ):
        assert permission in billing
    assert "capability:quickbooks_money" not in billing
    assert "capability:payroll_quickbooks_push" not in billing
    assert "capability:manage_roles" in by_id["admin"]["permissions"]
    assert "capability:payroll_correct" not in by_id["admin"]["permissions"]
    assert "capability:quickbooks_money" not in by_id["admin"]["permissions"]
    assert "capability:payroll_quickbooks_push" not in by_id["admin"]["permissions"]
    assert "tool:credentialing" in by_id["admin"]["permissions"]
    assert "tool:onboarding_compliance" not in by_id["admin"]["permissions"]
    assert "tool:time_entry" in by_id["admin"]["permissions"]


def test_no_role_sees_no_product_and_cannot_call_apis(client: TestClient) -> None:
    person = User("user-new", "New Hire", "practice-a", "new@heart4change.org")
    home = client.get("/home", headers=_auth(person))
    assert home.status_code == 200
    assert home.json()["tools"] == []
    assert "Contact Evan" in (home.json()["note"] or "")
    me = client.get("/access/me", headers=_auth(person)).json()
    assert me["landing"] == "none"
    assert me["permissions"] == []
    assert client.get("/hr/time/me", headers=_auth(person)).status_code == 403
    assert client.get("/clinicians", headers=_auth(person)).status_code == 403
    assert client.get("/hr/roster", headers=_auth(person)).status_code == 403


def test_time_entry_role_still_requires_the_hours_checklist(client: TestClient) -> None:
    _roster()
    set_clock(datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc))
    _save_settings(client, participant_emails=["sam@heart4change.org"])
    blocked = _enter(client, _jordan(), 2, 0)
    assert blocked.status_code == 403
    allowed = client.post(
        "/hr/time/entries",
        headers=_auth(User("user-sam", "Sam Rivera", "practice-a", "sam@heart4change.org")),
        json={"hours": 2, "minutes": 0, "week_ending": WEEK},
    )
    assert allowed.status_code == 200, allowed.text
    assert client.get("/clinicians", headers=_auth(_jordan())).status_code == 403
    assert client.get("/hr/time/week?week_ending=" + WEEK, headers=_auth(_jordan())).status_code == 403
    home = client.get("/home", headers=_auth(_jordan())).json()
    assert home["note"] == "Time entry"
    assert [tool["id"] for tool in home["tools"]] == ["time_entry"]
    assert home["bands"]["overdue"] == []


def test_melissa_keeps_payroll_without_admin_and_admin_cannot_correct(client: TestClient) -> None:
    _roster()
    set_clock(datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc))
    _save_settings(client, participant_emails=["jordan@heart4change.org"])
    submission = _enter(client, _jordan(), 3, 0).json()["submission_id"]
    set_clock(FRI_1130)
    client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice())
    admin_fix = client.patch(
        f"/hr/time/entries/{submission}",
        headers=_alice(),
        json={"hours": 1, "minutes": 0},
    )
    assert admin_fix.status_code == 403
    assert client.get("/hr/time/settings", headers=_auth(_melissa())).status_code == 403
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_auth(_melissa()))
    assert week.status_code == 200, week.text
    fixed = client.patch(
        f"/hr/time/entries/{submission}",
        headers=_auth(_melissa()),
        json={"hours": 4, "minutes": 0},
    )
    assert fixed.status_code == 200, fixed.text
    me = client.get("/access/me", headers=_auth(_melissa())).json()
    assert me["can_correct_payroll"] is True
    assert me["can_manage_roles"] is False
    admin = client.get("/access/me", headers=_alice()).json()
    assert admin["can_manage_roles"] is True
    assert admin["can_correct_payroll"] is False


def test_operations_mailbox_is_owner_and_the_shared_hr_mailbox_is_not(client: TestClient) -> None:
    owner = User("user-ops", "Evan Queitsch", "practice-hfc", "operations@heart4change.org")
    shared = User("user-hr", "Human Resources", "practice-hfc", "hr@heart4change.org")
    assert is_owner(owner)
    assert is_owner(shared) is False
    assert client.get("/access/me", headers=_auth(owner)).json()["can_manage_roles"] is True
    shared_me = client.get("/access/me", headers=_auth(shared)).json()
    assert shared_me["landing"] == "none"
    assert shared_me["can_manage_roles"] is False
    assert access_store.role_id_for("practice-hfc", "operations@heart4change.org") == "admin"
    assert access_store.role_id_for("practice-hfc", "hr@heart4change.org") == ""


def test_assignment_json_persists_and_plain_text_does_not(client: TestClient, tmp_path, monkeypatch) -> None:
    from app.access.store import AccessStore
    from app.crypto import generate_key

    monkeypatch.setenv("ATTUNE_ACCESS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_ACCESS_DATA_PATH", str(tmp_path / "roles.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    access_store._memory = None
    rejected = client.put(
        "/access/assignments",
        headers={**_alice(), "Content-Type": "text/plain"},
        content=b'{"email":"intake@heart4change.org","role_id":"intake"}',
    )
    assert rejected.status_code == 422
    saved = client.put(
        "/access/assignments",
        headers=_alice(),
        json={"email": "intake@heart4change.org", "role_id": "intake"},
    )
    assert saved.status_code == 200, saved.text
    listed = client.get("/access/assignments", headers=_alice()).json()["assignments"]
    assert {"email": "intake@heart4change.org", "role_id": "intake"} in listed
    reloaded = AccessStore()
    assert reloaded.role_id_for("practice-a", "intake@heart4change.org") == "intake"
    cleared = client.put(
        "/access/assignments",
        headers=_alice(),
        json={"email": "intake@heart4change.org", "role_id": ""},
    )
    assert cleared.status_code == 200
    assert reloaded.role_id_for("practice-a", "intake@heart4change.org") == "" or AccessStore().role_id_for(
        "practice-a", "intake@heart4change.org"
    ) == ""


def test_admin_cannot_remove_their_own_admin_role_or_the_last_admin(client: TestClient) -> None:
    ops = User("user-ops", "Evan Queitsch", "practice-hfc", "operations@heart4change.org")
    assert client.get("/access/roles", headers=_auth(ops)).status_code == 200
    own = client.put(
        "/access/assignments",
        headers=_auth(ops),
        json={"email": "operations@heart4change.org", "role_id": ""},
    )
    assert own.status_code == 422
    assert "own Admin" in own.json()["detail"]
    with pytest.raises(ValueError, match="at least one Admin"):
        access_store.set_assignment(
            "practice-hfc",
            "operations@heart4change.org",
            "",
            actor_email="hr@heart4change.org",
        )
    assert access_store.role_id_for("practice-hfc", "operations@heart4change.org") == "admin"


def test_owner_email_is_break_glass_and_assignments_stay_in_practice(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setenv("ATTUNE_HR_ADMIN_EMAILS", "evan@heart4change.org")
    evan = User("user-evan", "Evan", "practice-a", "evan@heart4change.org")
    assert is_owner(evan)
    assert client.get("/access/roles", headers=_auth(evan)).status_code == 200
    saved = client.put(
        "/access/assignments",
        headers=_auth(evan),
        json={"email": "jordan@heart4change.org", "role_id": "billing"},
    )
    assert saved.status_code == 200
    other = User("user-admin-b", "Admin B", "practice-b", "adminb@example.com")
    access_store.set_assignment("practice-b", "adminb@example.com", "admin")
    theirs = client.get("/access/assignments", headers=_auth(other)).json()["assignments"]
    assert all(row["email"] != "jordan@heart4change.org" for row in theirs)
    changed = client.put(
        "/access/roles/clinician",
        headers=_auth(evan),
        json={"permissions": ["tool:time_entry", "tool:intake_log", "capability:payroll_correct"]},
    )
    assert changed.status_code == 200
    assert "capability:payroll_correct" not in changed.json()["permissions"]
    assert "tool:intake_log" in changed.json()["permissions"]
    locked = client.put(
        "/access/roles/admin",
        headers=_auth(evan),
        json={"permissions": ["tool:time_entry"]},
    )
    assert locked.status_code == 422
