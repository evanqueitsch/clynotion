"""Practice Owner is a fixed role. Melissa gains it. Nobody else changes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.access.permissions import DENIALS_SUMMARY_NAME, permission_registry
from app.access.resolve import has_permission, permissions_for
from app.access.store import access_store, lookup_melissa_email, others_fingerprint
from app.auth import User, issue_token
from app.clinicians import clinician_store
from app.employees.store import employees_store
from app.hr.time_entry import MELISSA_EMAIL
from app.main import app
from app.platform.catalog import CATALOG_ITEMS

YES = [
    "tool:employee_view",
    "capability:hr_records_view",
    "tool:credentialing",
    "capability:payroll_dashboard",
    "tool:denials_summary",
]
DENY = [
    "capability:hr_records_manage",
    "capability:hr_sensitive",
    "capability:credentialing_manage",
    "capability:credentialing_payers",
    "capability:credentialing_sensitive",
    "tool:billing",
    "tool:hiring_onboarding",
    "capability:payroll_quickbooks_push",
    "capability:quickbooks_money",
    "tool:denials",
    "capability:phi_claims",
    "capability:manage_roles",
]
BILLING_EMAIL = "billing.holder@heart4change.org"
CLINICIAN_EMAIL = "clinician.one@heart4change.org"


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _user(email: str, practice_id: str = "practice-hfc") -> User:
    return User(f"user-{email}", email.split("@", 1)[0], practice_id, email)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _write_roles(path: Path, assignments: dict[str, str], clinician_permissions: list[str]) -> None:
    from app.crypto import encrypt_utf8

    payload = {
        "version": 1,
        "practices": {
            "practice-hfc": {
                "grants_version": 2,
                "roles": {
                    "billing": {
                        "name": "Billing",
                        "permissions": [
                            "tool:time_entry",
                            "tool:credentialing",
                            "capability:credentialing_manage",
                            "capability:credentialing_payers",
                            "capability:credentialing_sensitive",
                            "tool:billing",
                        ],
                    },
                    "clinician": {"name": "Clinician", "permissions": clinician_permissions},
                    "admin": {"name": "Admin", "permissions": ["tool:time_entry"]},
                },
                "assignments": assignments,
            }
        },
    }
    path.write_bytes(encrypt_utf8(json.dumps(payload)))


def _snapshot(emails: list[str]) -> dict[str, list[str]]:
    return {email: sorted(permissions_for(_user(email))) for email in emails}


def test_roster_lookup_prefers_the_known_mailbox_and_otherwise_one_melissa() -> None:
    clinician_store.upsert_workspace_user(
        "practice-hfc",
        google_id="melissa-known",
        email=MELISSA_EMAIL,
        display_name="Melissa",
    )
    clinician_store.upsert_workspace_user(
        "practice-hfc",
        google_id="melissa-other",
        email="melissa.other@heart4change.org",
        display_name="Melissa Other",
    )
    assert lookup_melissa_email("practice-hfc") == MELISSA_EMAIL
    assert lookup_melissa_email("practice-a") == ""


def test_practice_owner_is_exact_and_only_melissa_changes(tmp_path, monkeypatch) -> None:
    from app.crypto import decrypt_utf8, generate_key

    monkeypatch.setenv("ATTUNE_ACCESS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_ACCESS_DATA_PATH", str(tmp_path / "access_roles.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    cred = tmp_path / "credentialing.enc"
    cred.write_bytes(b"leave-this-file-alone")
    clinician_store.upsert_workspace_user(
        "practice-hfc",
        google_id="melissa-roster",
        email=MELISSA_EMAIL,
        display_name="Melissa",
    )
    assignments = {
        "operations@heart4change.org": "admin",
        BILLING_EMAIL: "billing",
        CLINICIAN_EMAIL: "clinician",
    }
    path = tmp_path / "access_roles.enc"
    _write_roles(path, assignments, ["tool:time_entry"])
    access_store._memory = None
    import app.access.store as store_module

    emails = [MELISSA_EMAIL, "operations@heart4change.org", BILLING_EMAIL, CLINICIAN_EMAIL, "new@heart4change.org"]
    monkeypatch.setattr(store_module, "GRANTS_VERSION", 2)
    before = _snapshot(emails)
    monkeypatch.setattr(store_module, "GRANTS_VERSION", 3)
    access_store._memory = None
    after = _snapshot(emails)

    assert access_store.permissions_for_role("practice-hfc", "practice_owner") == YES
    assert before[MELISSA_EMAIL] == sorted(
        {"tool:time_entry", "capability:payroll_dashboard", "capability:payroll_correct"}
    )
    assert set(before[MELISSA_EMAIL]).issubset(after[MELISSA_EMAIL])
    for email in emails:
        if email == MELISSA_EMAIL:
            continue
        assert after[email] == before[email]
    assert access_store.role_id_for("practice-hfc", MELISSA_EMAIL) == "practice_owner"
    assert access_store.role_id_for("practice-hfc", BILLING_EMAIL) == "billing"
    assert access_store.role_id_for("practice-hfc", CLINICIAN_EMAIL) == "clinician"
    assert access_store.role_id_for("practice-hfc", "operations@heart4change.org") == "admin"
    saved = json.loads(decrypt_utf8(path.read_bytes()))
    practice = saved["practices"]["practice-hfc"]
    record = practice["practice_owner_migration"]
    assert record["before_permissions"] == before[MELISSA_EMAIL]
    assert record["other_assignments_sha256"] == others_fingerprint(practice["assignments"], MELISSA_EMAIL)
    assert record["admin_count"] == 1
    assert record["billing_count"] == 1
    assert "account_permissions" not in practice
    assert cred.read_bytes() == b"leave-this-file-alone"
    again = _snapshot(emails)
    assert again == after
    for permission in DENY:
        assert permission not in YES
        assert permission not in after[MELISSA_EMAIL]
    assert "capability:payroll_correct" in after[MELISSA_EMAIL]
    assert "capability:payroll_correct" not in YES


def test_previous_role_permissions_stay_on_the_mailbox_not_the_role(tmp_path, monkeypatch) -> None:
    from app.crypto import decrypt_utf8, generate_key

    monkeypatch.setenv("ATTUNE_ACCESS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_ACCESS_DATA_PATH", str(tmp_path / "access_roles.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    clinician_store.upsert_workspace_user(
        "practice-hfc",
        google_id="melissa-roster",
        email=MELISSA_EMAIL,
        display_name="Melissa",
    )
    _write_roles(
        tmp_path / "access_roles.enc",
        {
            "operations@heart4change.org": "admin",
            BILLING_EMAIL: "billing",
            MELISSA_EMAIL: "clinician",
        },
        ["tool:time_entry", "tool:intake_log", "capability:hr_records_manage", "capability:manage_roles"],
    )
    access_store._memory = None
    import app.access.store as store_module

    monkeypatch.setattr(store_module, "GRANTS_VERSION", 2)
    before = sorted(permissions_for(_user(MELISSA_EMAIL)))
    monkeypatch.setattr(store_module, "GRANTS_VERSION", 3)
    access_store._memory = None
    after = sorted(permissions_for(_user(MELISSA_EMAIL)))
    assert set(before) - {"capability:manage_roles"} <= set(after)
    role = access_store.permissions_for_role("practice-hfc", "practice_owner")
    assert role == YES
    assert "capability:hr_records_manage" not in role
    assert "capability:manage_roles" not in role
    assert "tool:intake_log" not in role
    saved = json.loads(decrypt_utf8((tmp_path / "access_roles.enc").read_bytes()))
    account = saved["practices"]["practice-hfc"]["account_permissions"][MELISSA_EMAIL]
    assert account == ["tool:intake_log", "capability:hr_records_manage"]
    assert "capability:manage_roles" not in after
    assert access_store.role_id_for("practice-hfc", BILLING_EMAIL) == "billing"


def test_denial_summaries_are_a_permission_without_a_home_card(client: TestClient) -> None:
    assert all(item["id"] != "denials_summary" for item in CATALOG_ITEMS)
    row = next(item for item in permission_registry() if item["id"] == "tool:denials_summary")
    assert row["name"] == DENIALS_SUMMARY_NAME
    assert row["grantable"] is True
    assert row["sensitive"] is True
    page = client.get("/").text
    assert "These permissions are marked sensitive. Only grant them on purpose." in page
    ops = _user("operations@heart4change.org")
    roles = client.get("/access/roles", headers=_auth(ops)).json()
    practice_owner = next(item for item in roles["roles"] if item["id"] == "practice_owner")
    assert practice_owner["name"] == "Practice Owner"
    assert practice_owner["locked"] is False
    assert practice_owner["permissions"] == YES
    admin = next(item for item in roles["roles"] if item["id"] == "admin")
    assert "tool:denials_summary" not in admin["permissions"]
    assert "capability:payroll_correct" not in admin["permissions"]
    home = client.get("/home", headers=_auth(ops)).json()
    ids = [tool["id"] for unit in home["units"] for tool in unit["tools"]]
    assert "denials_summary" not in ids
    melissa = _user(MELISSA_EMAIL)
    hers = client.get("/home", headers=_auth(melissa)).json()
    her_ids = [tool["id"] for unit in hers["units"] for tool in unit["tools"]]
    assert "denials_summary" not in her_ids
    assert "hiring_onboarding" not in her_ids
    assert all(not tool.get("coming_soon") for unit in hers["units"] for tool in unit["tools"])
    time_entry = next(tool for unit in hers["units"] for tool in unit["tools"] if tool["id"] == "time_entry")
    assert "time-admin" in time_entry["href"]
    employees_store.set_rollout("practice-hfc", True, "test@example.com")
    opened = client.get("/home", headers=_auth(melissa)).json()
    opened_ids = [tool["id"] for unit in opened["units"] for tool in unit["tools"]]
    assert "employee_view" in opened_ids
    assert "credentialing" in opened_ids
    employees_store.set_rollout("practice-hfc", False, "test@example.com")
    access_store.ensure("practice-hfc")
    assert employees_store.rollout_enabled("practice-hfc") is False


def test_admin_can_check_melissa_without_signing_in_as_her(client: TestClient) -> None:
    ops = _user("operations@heart4change.org")
    melissa = _user(MELISSA_EMAIL)
    denied = client.get("/access/effective", params={"email": MELISSA_EMAIL}, headers=_auth(melissa))
    assert denied.status_code == 403
    checked = client.get("/access/effective", params={"email": MELISSA_EMAIL}, headers=_auth(ops))
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["email"] == MELISSA_EMAIL
    assert body["role_id"] == "practice_owner"
    assert body["permissions"] == sorted(permissions_for(melissa))
    for permission in YES:
        assert has_permission(melissa, permission) is True
        assert permission in body["permissions"]
    for permission in DENY:
        assert has_permission(melissa, permission) is False
    assert has_permission(melissa, "capability:payroll_correct") is True
    assert has_permission(melissa, "capability:payroll_dashboard") is True
    week = client.get("/hr/time/week?week_ending=2026-03-13", headers=_auth(melissa))
    assert week.status_code != 403
    assert client.get("/access/roles", headers=_auth(melissa)).status_code == 403
    grid = client.get("/credentialing/grid", headers=_auth(melissa))
    assert grid.status_code == 200, grid.text
    assert grid.json()["can_sensitive"] is False
    assert grid.json()["can_manage"] is False
    assert grid.json()["can_payers"] is False
    reveal = client.post(
        "/credentialing/reveal",
        headers=_auth(melissa),
        json={"subject_id": "missing", "field": "npi"},
    )
    assert reveal.status_code == 403
    assert client.get("/billing", headers=_auth(melissa)).status_code == 403
