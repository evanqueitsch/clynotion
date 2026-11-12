"""Employee View dark build. Synthetic contact values only."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient

from app.access.guard import required_permission
from app.access.permissions import BILLING_HR_WHEN_ROLLOUT, billing_role_permissions
from app.access.store import access_store
from app.auth import User, issue_token
from app.credentialing.store import credentialing_store
from app.crypto import decrypt_utf8, generate_key
from app.employees.store import employees_store
from app.main import app

STREET = "1 QA Street"
PHONE = "(555)010-0100"
PERSONAL = "qa@example.com"
NPI = "1234567890"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _user(email: str, username: str) -> dict[str, str]:
    user = User("user-" + username, username, "practice-a", email)
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _contact(**overrides: str) -> dict[str, str]:
    body = {
        "preferred_name": "",
        "street": "",
        "city": "",
        "state": "",
        "zip": "",
        "personal_phone": "",
        "personal_email": "",
        "work_phone": "",
        "emergency_name": "",
        "emergency_phone": "",
    }
    body.update(overrides)
    return body


def _clinician(client: TestClient, name: str = "QA Test Clinician") -> str:
    saved = client.put(
        "/credentialing/subjects",
        headers=_alice(),
        json={"name": name, "kind": "clinician", "license_type": "LCSW"},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["id"]


def test_time_entry_guard_is_unchanged() -> None:
    assert required_permission("/hr/time") == "tool:time_entry"
    assert required_permission("/hr/time/week") == "capability:payroll_dashboard"
    assert required_permission("/employee/me") == "tool:employee_view"
    assert required_permission("/employee/directory") == "capability:hr_records_view"


def test_rollout_stays_off_and_hides_the_tool_from_staff(client: TestClient) -> None:
    me = client.get("/employee/me", headers=_alice())
    assert me.status_code == 200, me.text
    assert me.json()["rollout_enabled"] is False
    access_store.update_role_permissions(
        "practice-a",
        "clinician",
        ["tool:time_entry", "tool:employee_view", "capability:hr_records_view"],
    )
    access_store.set_assignment("practice-a", "viewer@example.com", "clinician")
    viewer = _user("viewer@example.com", "viewer")
    assert client.get("/employee/me", headers=viewer).status_code == 403
    home = client.get("/catalog", headers=viewer).json()
    ids = [tool["id"] for unit in home for tool in unit["tools"]]
    assert "employee_view" not in ids
    assert "capability:hr_records_view" not in billing_role_permissions()
    assert "capability:hr_records_manage" not in BILLING_HR_WHEN_ROLLOUT
    turned = client.put("/employee/rollout", headers=_alice(), json={"enabled": True})
    assert turned.status_code == 200
    assert client.get("/employee/me", headers=viewer).status_code == 200
    client.put("/employee/rollout", headers=_alice(), json={"enabled": False})
    assert client.get("/employee/me", headers=viewer).status_code == 403


def test_own_contact_saves_and_clears_without_printing_or_emailing(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    saved = client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(
            preferred_name="QA",
            street=STREET,
            city="QA City",
            state="PA",
            zip="00000",
            personal_phone=PHONE,
            personal_email=PERSONAL,
            work_phone="(555)010-0101",
            emergency_name="QA Contact",
            emergency_phone="(555)010-0102",
        ),
    )
    assert saved.status_code == 200, saved.text
    again = client.get("/employee/me", headers=_alice()).json()
    assert again["contact"]["street"] == STREET
    assert again["contact"]["personal_phone"] == PHONE
    assert PHONE not in json.dumps(again["history"])
    assert STREET not in json.dumps(again["history"])
    cleared = client.put("/employee/me/contact", headers=_alice(), json=_contact())
    assert cleared.status_code == 200, cleared.text
    empty = client.get("/employee/me", headers=_alice()).json()
    assert empty["contact"]["street"] == ""
    assert empty["contact"]["personal_phone"] == ""
    assert empty["contact"]["emergency_name"] == ""
    billing = client.get("/billing", headers=_alice()).json()
    blob = json.dumps(billing["employee_changes"])
    assert PHONE not in blob
    assert STREET not in blob
    assert PERSONAL not in blob
    preview = client.post("/billing/digest/preview", headers=_alice())
    assert preview.status_code == 200
    assert STREET not in preview.text
    assert PHONE not in preview.text
    assert PERSONAL not in preview.text
    captured = capsys.readouterr()
    assert STREET not in captured.out
    assert PHONE not in captured.err


def test_non_clinician_hides_credentialing_until_a_confirmed_link(client: TestClient) -> None:
    subject_id = _clinician(client, "alice")
    me = client.get("/employee/me", headers=_alice()).json()
    assert me["is_clinician"] is False
    assert "credentialing" not in me
    assert me["qualification_types"] == []
    assert me["suggested_subject"]["id"] == subject_id
    assert me["subject_id"] == ""
    blocked = client.post(
        "/employee/me/changes",
        headers=_alice(),
        json={"field": "license_type", "value": "LSW"},
    )
    assert blocked.status_code == 422
    linked = client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    assert linked.status_code == 200, linked.text
    assert linked.json()["is_clinician"] is True
    assert "credentialing" in linked.json()
    assert linked.json()["credentialing"]["npi_masked"] == ""


def test_specialties_and_trainings_save_immediately_and_show_on_billing(client: TestClient) -> None:
    subject_id = _clinician(client)
    linked = client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    assert linked.status_code == 200, linked.text
    specialties = client.put(
        "/employee/me/specialties",
        headers=_alice(),
        json={"names": ["Trauma", "Anxiety"]},
    )
    assert specialties.status_code == 200, specialties.text
    profile = credentialing_store.public_subject("practice-a", subject_id)
    assert profile is not None
    assert profile["specialties"] == ["Trauma", "Anxiety"]
    trainings = client.put(
        "/employee/me/trainings",
        headers=_alice(),
        json={"items": [{"name": "Boundaries", "completed_on": "2026-01-15"}]},
    )
    assert trainings.status_code == 200, trainings.text
    billing = client.get("/billing", headers=_alice()).json()
    summaries = [item["summary"] for item in billing["employee_changes"]]
    assert any("Trauma" in item for item in summaries)
    assert any(item == "Updated trainings: Boundaries" for item in summaries)
    cleared = client.put("/employee/me/specialties", headers=_alice(), json={"names": []})
    assert cleared.status_code == 200
    assert credentialing_store.public_subject("practice-a", subject_id)["specialties"] == []
    empty_trainings = client.put("/employee/me/trainings", headers=_alice(), json={"items": []})
    assert empty_trainings.json()["trainings"] == []


def test_license_change_waits_for_approval_and_return_needs_a_note(client: TestClient) -> None:
    subject_id = _clinician(client)
    payer = client.post(
        "/credentialing/payers",
        headers=_alice(),
        json={"name": "QA Strict Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
    )
    assert payer.status_code == 200, payer.text
    client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    requested = client.post(
        "/employee/me/changes",
        headers=_alice(),
        json={"field": "license_type", "value": "LSW"},
    )
    assert requested.status_code == 200, requested.text
    assert credentialing_store.public_subject("practice-a", subject_id)["license_type"] == "LCSW"
    pending = client.get("/billing", headers=_alice()).json()["employee_pending"]
    assert pending[0]["field"] == "license_type"
    assert pending[0]["field_label"] == "License type"
    assert pending[0]["proposed"] == "LSW"
    missing = client.post(
        f"/billing/employee-pending/{pending[0]['id']}/return",
        headers=_alice(),
        json={"note": " "},
    )
    assert missing.status_code == 422
    returned = client.post(
        f"/billing/employee-pending/{pending[0]['id']}/return",
        headers=_alice(),
        json={"note": "Need the license type confirmed."},
    )
    assert returned.status_code == 200, returned.text
    assert credentialing_store.public_subject("practice-a", subject_id)["license_type"] == "LCSW"
    again = client.post(
        "/employee/me/changes",
        headers=_alice(),
        json={"field": "license_type", "value": "LSW"},
    )
    assert again.status_code == 200
    pending_id = client.get("/billing", headers=_alice()).json()["employee_pending"][0]["id"]
    approved = client.post(f"/billing/employee-pending/{pending_id}/approve", headers=_alice())
    assert approved.status_code == 200, approved.text
    assert credentialing_store.public_subject("practice-a", subject_id)["license_type"] == "LSW"
    grid = client.get("/credentialing/grid", headers=_alice()).json()
    cell = next(item for item in grid["cells"] if item["subject_id"] == subject_id)
    assert "LSW" in cell["suggestion_reason"]


def test_secret_change_is_not_in_the_queue_or_the_audit(client: TestClient) -> None:
    subject_id = _clinician(client)
    client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    requested = client.post(
        "/employee/me/changes",
        headers=_alice(),
        json={"field": "npi", "value": NPI},
    )
    assert requested.status_code == 200, requested.text
    billing = client.get("/billing", headers=_alice())
    assert NPI not in billing.text
    pending_id = billing.json()["employee_pending"][0]["id"]
    revealed = client.post(f"/billing/employee-pending/{pending_id}/reveal", headers=_alice())
    assert revealed.status_code == 200
    assert revealed.json()["value"] == NPI
    audit = client.get("/employee/audit", headers=_alice()).json()
    assert NPI not in json.dumps(audit)


def test_directory_view_is_audited_without_values_and_export_redacts(
    client: TestClient,
) -> None:
    client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(street=STREET, personal_phone=PHONE, personal_email=PERSONAL),
    )
    directory = client.get("/employee/directory", headers=_alice())
    assert directory.status_code == 200, directory.text
    assert STREET not in directory.text
    opened = client.get("/employee/people/alice@example.com", headers=_alice())
    assert opened.status_code == 200
    audit = client.get("/employee/audit", headers=_alice()).json()["events"]
    assert any(item["action"] == "view_directory" and item["actor"] == "alice@example.com" for item in audit)
    assert STREET not in json.dumps(audit)
    assert PHONE not in json.dumps(audit)
    full = client.get("/employee/export.csv", headers=_alice())
    assert STREET in full.text
    access_store.update_role_permissions(
        "practice-a",
        "clinician",
        ["tool:time_entry", "tool:employee_view", "capability:hr_records_view"],
    )
    access_store.set_assignment("practice-a", "viewer@example.com", "clinician")
    client.put("/employee/rollout", headers=_alice(), json={"enabled": True})
    viewer = _user("viewer@example.com", "viewer")
    redacted = client.get("/employee/export.csv", headers=viewer)
    assert redacted.status_code == 200, redacted.text
    assert STREET not in redacted.text
    assert PHONE not in redacted.text
    assert PERSONAL not in redacted.text
    assert "withheld" in redacted.text


def test_hr_audit_and_credentialing_reveal_are_not_capped() -> None:
    for _ in range(501):
        employees_store.record_audit("practice-a", "alice@example.com", "view_directory", "directory", "")
    assert len(employees_store.audit_events("practice-a")) == 501
    credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "npi": NPI},
        actor="alice@example.com",
    )
    subject_id = credentialing_store.list_subject_names("practice-a")[0]["id"]
    for _ in range(501):
        credentialing_store.reveal(
            "practice-a",
            actor="alice@example.com",
            subject_id=subject_id,
            field="npi",
            action="reveal",
        )
    events = credentialing_store.audit_for("practice-a", subject_id)
    assert len(events) == 501
    assert NPI not in json.dumps(events)


def test_pending_over_three_business_days_is_overdue(client: TestClient) -> None:
    subject_id = _clinician(client)
    client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    client.post("/employee/me/changes", headers=_alice(), json={"field": "license_type", "value": "LPC"})
    practice = employees_store._memory["practices"]["practice-a"]
    practice["pending"][0]["at"] = "2026-10-01T12:00:00-04:00"
    pending = client.get("/billing", headers=_alice()).json()["employee_pending"]
    assert pending[0]["overdue"] is True


def test_idle_logoff_and_small_health_are_in_the_ui(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "IDLE_LIMIT_MS = 20 * 60 * 1000" in page.text
    assert "20 minutes of inactivity" in page.text
    health = client.get("/health")
    assert health.json()["version"] == "0.19.9"
    assert "deepgram" not in health.text.casefold()
    assert "phi_path" not in health.json()
    script = client.get("/static/employee.js")
    assert script.status_code == 200


def test_employees_file_can_use_its_own_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    own = generate_key()
    shared = generate_key()
    monkeypatch.setenv("ATTUNE_EMPLOYEES_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_EMPLOYEES_DATA_PATH", str(tmp_path / "employees.enc"))
    monkeypatch.setenv("ATTUNE_EMPLOYEES_ENCRYPTION_KEY", own)
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", shared)
    employees_store.reset()
    employees_store.save_contact(
        "practice-a",
        "alice@example.com",
        _contact(street=STREET),
        "alice@example.com",
    )
    blob = (tmp_path / "employees.enc").read_bytes()
    assert STREET.encode() not in blob
    assert STREET in Fernet(own.encode()).decrypt(blob).decode()
    with pytest.raises(InvalidToken):
        Fernet(shared.encode()).decrypt(blob)
    employees_store.reset()


def test_grants_version_2_does_not_edit_role_lists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.crypto import encrypt_utf8

    monkeypatch.setenv("ATTUNE_ACCESS_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_ACCESS_DATA_PATH", str(tmp_path / "access_roles.enc"))
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", generate_key())
    original = {
        "version": 1,
        "practices": {
            "practice-hfc": {
                "grants_version": 1,
                "roles": {
                    "billing": {
                        "name": "Billing",
                        "permissions": ["tool:time_entry", "tool:intake_log", "tool:billing"],
                    },
                    "clinician": {"name": "Clinician", "permissions": ["tool:time_entry"]},
                    "admin": {"name": "Admin", "permissions": ["tool:time_entry"]},
                },
                "assignments": {"clinician@heart4change.org": "clinician"},
            }
        },
    }
    path = tmp_path / "access_roles.enc"
    path.write_bytes(encrypt_utf8(json.dumps(original)))
    access_store._memory = None
    billing = access_store.permissions_for_role("practice-hfc", "billing")
    assert billing == ["tool:time_entry", "tool:intake_log", "tool:billing"]
    assert "capability:hr_records_view" not in billing
    assert access_store.role_id_for("practice-hfc", "clinician@heart4change.org") == "clinician"
    saved = json.loads(decrypt_utf8(path.read_bytes()))
    assert saved["practices"]["practice-hfc"]["grants_version"] == 2
    assert (tmp_path / "access_roles.enc.bak-grants-v2").is_file()
    backup = json.loads(decrypt_utf8((tmp_path / "access_roles.enc.bak-grants-v2").read_bytes()))
    assert backup["practices"]["practice-hfc"]["roles"]["billing"]["permissions"] == [
        "tool:time_entry",
        "tool:intake_log",
        "tool:billing",
    ]


def test_home_tile_is_live_for_admin_and_hidden_while_rollout_is_off(client: TestClient) -> None:
    home = client.get("/home", headers=_alice()).json()
    tool = next(
        item
        for unit in home["units"]
        if unit["id"] == "hr"
        for item in unit["tools"]
        if item["id"] == "employee_view"
    )
    assert tool["status"] == "live"
    assert tool["href"] == "/#employee"
    assert tool["coming_soon"] is False
    assert tool["open_label"] == "Open Employee View"
    access_store.update_role_permissions("practice-a", "clinician", ["tool:time_entry"])
    access_store.set_assignment("practice-a", "viewer@example.com", "clinician")
    staff = client.get("/home", headers=_user("viewer@example.com", "viewer")).json()
    ids = [item["id"] for unit in staff["units"] for item in unit["tools"]]
    assert "employee_view" not in ids
    page = client.get("/").text
    assert 'tool.id === "employee_view" && tool.status === "live"' in page


def test_multiple_trainings_save_remove_and_clear(client: TestClient) -> None:
    first = client.put(
        "/employee/me/trainings",
        headers=_alice(),
        json={"items": [
            {"name": "Boundaries", "completed_on": "2026-01-15"},
            {"name": "Ethics", "completed_on": "2026-02-01"},
        ]},
    )
    assert first.status_code == 200, first.text
    assert [item["name"] for item in first.json()["trainings"]] == ["Boundaries", "Ethics"]
    again = client.get("/employee/me", headers=_alice()).json()
    assert [item["name"] for item in again["trainings"]] == ["Boundaries", "Ethics"]
    removed = client.put(
        "/employee/me/trainings",
        headers=_alice(),
        json={"items": [{"name": "Boundaries", "completed_on": "2026-01-15"}]},
    )
    assert [item["name"] for item in removed.json()["trainings"]] == ["Boundaries"]
    cleared = client.put("/employee/me/trainings", headers=_alice(), json={"items": []})
    assert cleared.json()["trainings"] == []
    reloaded = client.get("/employee/me", headers=_alice()).json()
    assert reloaded["trainings"] == []
    summaries = [item["summary"] for item in client.get("/billing", headers=_alice()).json()["employee_changes"]]
    assert "Updated trainings: Boundaries, Ethics" in summaries
    assert "Cleared trainings" in summaries
    blob = "\n".join(summaries)
    assert PHONE not in blob
    assert STREET not in blob
    assert PERSONAL not in blob
    script = client.get("/static/employee.js?v=0.19.2").text
    assert "Add training" in script
    assert "Remove" in script
    assert "draft.clear" in script


def test_history_lines_are_readable_and_omit_contact_values(client: TestClient) -> None:
    saved = client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(street=STREET, personal_phone=PHONE, personal_email=PERSONAL, emergency_name="QA Contact"),
    )
    assert saved.status_code == 200, saved.text
    lines = [item["line"] for item in saved.json()["history"]]
    phone = next(line for line in lines if "Personal phone" in line)
    assert "alice@example.com" in phone
    assert "ET" in phone
    assert "updated Personal phone" in phone
    assert PHONE not in phone
    assert STREET not in "\n".join(lines)
    assert PERSONAL not in "\n".join(lines)
    assert "personal_phone" not in phone
    assert "emergency_name" not in "\n".join(lines)
    assert any("Emergency contact name" in line for line in lines)


def test_certifications_save_for_any_staff_and_do_not_change_credentialing(client: TestClient) -> None:
    saved = client.put(
        "/employee/me/certifications",
        headers=_alice(),
        json={"items": [{"name": "CPR/First Aid", "issued_on": "2026-01-01", "expires_on": ""}]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["is_clinician"] is False
    assert "credentialing" not in saved.json()
    assert saved.json()["certifications"][0]["name"] == "CPR/First Aid"
    subject_id = _clinician(client)
    profile = credentialing_store.public_subject("practice-a", subject_id)
    assert profile is not None
    assert not profile.get("certifications")
    client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    linked = client.get("/employee/me", headers=_alice()).json()
    assert linked["is_clinician"] is True
    assert linked["credentialing"]["name"] == "QA Test Clinician"
    assert linked["certifications"][0]["name"] == "CPR/First Aid"
    assert linked["credentialing"]["clock_labels"]["license_renewal"] == "License renewal"
    summaries = [item["summary"] for item in client.get("/billing", headers=_alice()).json()["employee_changes"]]
    assert "Updated certifications: CPR/First Aid" in summaries
    assert PHONE not in "\n".join(summaries)
    cleared = client.put("/employee/me/certifications", headers=_alice(), json={"items": []})
    assert cleared.json()["certifications"] == []
    assert client.get("/employee/me", headers=_alice()).json()["certifications"] == []


def test_directory_lists_workspace_roster_and_suggestion_waits_for_confirm(client: TestClient) -> None:
    subject_id = _clinician(client, "Jamie Clinician")
    directory = client.get("/employee/directory", headers=_alice()).json()["people"]
    names = [person["name"] for person in directory]
    assert names.count("Jamie Clinician") == 1
    jamie = next(person for person in directory if person["name"] == "Jamie Clinician")
    assert jamie["key"] == subject_id
    assert jamie["email"] == ""
    assert "alex.supervisor@example.com" in [person["email"] for person in directory]
    assert not any(person["email"] == "jamie.clinician@example.com" for person in directory)
    detail = client.get(f"/employee/people/{subject_id}", headers=_alice())
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Jamie Clinician"
    link = detail.json()["workspace_link"]
    assert link["suggested_email"] == "jamie.clinician@example.com"
    assert link["linked_email"] == ""
    assert "Workspace account" in client.get("/static/employee.js?v=0.19.2").text
    records = employees_store._memory["practices"]["practice-a"].get("records") or {}
    assert not (records.get("jamie.clinician@example.com") or {}).get("subject_id")
    confirmed = client.put(
        f"/employee/people/{subject_id}/workspace",
        headers=_alice(),
        json={"email": "jamie.clinician@example.com"},
    )
    assert confirmed.status_code == 200, confirmed.text
    linked = employees_store._memory["practices"]["practice-a"]["records"]["jamie.clinician@example.com"]
    assert linked["subject_id"] == subject_id
    viewed = client.get("/employee/people/jamie.clinician@example.com", headers=_alice())
    assert viewed.status_code == 200, viewed.text
    history = "\n".join(item["line"] for item in viewed.json()["history"])
    assert "Workspace account" in history
    assert STREET not in history
    cleared = client.put(
        f"/employee/people/{subject_id}/workspace",
        headers=_alice(),
        json={"email": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert employees_store._memory["practices"]["practice-a"]["records"]["jamie.clinician@example.com"]["subject_id"] == ""
    again = client.get("/employee/directory", headers=_alice()).json()["people"]
    assert [person["name"] for person in again].count("Jamie Clinician") == 1


def test_directory_merges_email_match_and_leaves_workspace_only_rows(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        employees_store,
        "_workspace_people",
        lambda practice_id: [
            {"email": "alex.example@example.com", "name": "A Different Label"},
            {"email": "evan.viewer@example.com", "name": "Evan Viewer"},
        ],
    )
    subject_id = _clinician(client, "Alex Example")
    directory = client.get("/employee/directory", headers=_alice()).json()["people"]
    assert [person["name"] for person in directory].count("Alex Example") == 1
    assert not any(person["name"] == "A Different Label" for person in directory)
    clinician = client.get(f"/employee/people/{subject_id}", headers=_alice())
    assert clinician.status_code == 200, clinician.text
    link = clinician.json()["workspace_link"]
    assert link["suggested_email"] == "alex.example@example.com"
    assert link["linked_email"] == ""
    records = (employees_store._memory or {}).get("practices", {}).get("practice-a", {}).get("records") or {}
    assert not (records.get("alex.example@example.com") or {}).get("subject_id")
    workspace = client.get("/employee/people/evan.viewer@example.com", headers=_alice())
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    assert body["workspace_account"] == "evan.viewer@example.com"
    assert body["name"] == "Evan Viewer"
    assert "workspace_link" not in body
    assert "candidates" in body
    page = client.get("/").text
    assert 'window.location.hash = "#employee"' in page
    assert 'window.location.hash = "#employees"' in page
    assert body["specialties"] == []
    assert body["trainings"] == []
    assert body["certifications"] == []
    assert "history" in body
    assert "credentialing" not in body
    assert body["show_credentialing"] is False


def test_non_clinician_file_includes_sections_and_clinician_keeps_credentialing(
    client: TestClient,
) -> None:
    specialties = client.put(
        "/employee/me/specialties",
        headers=_alice(),
        json={"names": ["Front desk"]},
    )
    assert specialties.status_code == 200, specialties.text
    trainings = client.put(
        "/employee/me/trainings",
        headers=_alice(),
        json={"items": [{"name": "Safety", "completed_on": "2026-03-01"}]},
    )
    assert trainings.status_code == 200, trainings.text
    certifications = client.put(
        "/employee/me/certifications",
        headers=_alice(),
        json={"items": [{"name": "Mandated reporter", "issued_on": "2026-02-01", "expires_on": ""}]},
    )
    assert certifications.status_code == 200, certifications.text
    me = client.get("/employee/me", headers=_alice()).json()
    assert me["is_clinician"] is False
    assert me["show_credentialing"] is False
    assert "credentialing" not in me
    assert me["specialties"] == ["Front desk"]
    assert me["trainings"][0]["name"] == "Safety"
    assert me["certifications"][0]["name"] == "Mandated reporter"
    assert employees_store.rollout_enabled("practice-a") is False
    person = client.get("/employee/people/alice@example.com", headers=_alice())
    assert person.status_code == 200, person.text
    opened = person.json()
    assert opened["specialties"] == ["Front desk"]
    assert opened["trainings"][0]["name"] == "Safety"
    assert opened["certifications"][0]["name"] == "Mandated reporter"
    assert "credentialing" not in opened
    assert opened["can_edit"] is True
    assert opened["history"]
    assert PHONE not in json.dumps(opened["history"])
    subject_id = _clinician(client)
    linked = client.put("/employee/me/link", headers=_alice(), json={"subject_id": subject_id})
    assert linked.status_code == 200, linked.text
    clinician = linked.json()
    assert clinician["is_clinician"] is True
    assert clinician["show_credentialing"] is True
    assert clinician["credentialing"]["license_type"] == "LCSW"
    assert clinician["change_fields"][0] == {"id": "license_type", "label": "License type"}
    again = client.get("/employee/people/alice@example.com", headers=_alice()).json()
    assert again["credentialing"]["name"] == "QA Test Clinician"
    assert again["certifications"][0]["name"] == "Mandated reporter"
    assert again["credentialing"]["clock_labels"]["caqh_reattestation"] == "CAQH re-attestation"


def test_hr_view_can_reveal_without_edit_and_manage_can_edit(client: TestClient) -> None:
    client.put(
        "/employee/me/contact",
        headers=_alice(),
        json=_contact(street=STREET, personal_phone=PHONE),
    )
    access_store.update_role_permissions(
        "practice-a",
        "clinician",
        ["tool:employee_view", "capability:hr_records_view", "capability:hr_sensitive"],
    )
    access_store.set_assignment("practice-a", "viewer@example.com", "clinician")
    client.put("/employee/rollout", headers=_alice(), json={"enabled": True})
    viewer = _user("viewer@example.com", "viewer")
    opened = client.get("/employee/people/alice@example.com", headers=viewer)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["can_edit"] is False
    assert body["can_reveal"] is True
    assert body["contact"]["street"] == STREET
    assert body["contact_revealed"] is True
    denied = client.put(
        "/employee/people/alice@example.com/trainings",
        headers=viewer,
        json={"items": [{"name": "Safety", "completed_on": ""}]},
    )
    assert denied.status_code == 403
    access_store.update_role_permissions(
        "practice-a",
        "clinician",
        [
            "tool:employee_view",
            "capability:hr_records_view",
            "capability:hr_sensitive",
            "capability:hr_records_manage",
        ],
    )
    managed = client.get("/employee/people/alice@example.com", headers=viewer).json()
    assert managed["can_edit"] is True
    saved = client.put(
        "/employee/people/alice@example.com/specialties",
        headers=viewer,
        json={"names": ["Scheduling"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["specialties"] == ["Scheduling"]
    assert saved.json()["can_edit"] is True
    client.put("/employee/rollout", headers=_alice(), json={"enabled": False})
    assert employees_store.rollout_enabled("practice-a") is False


def test_employee_file_ui_hides_history_and_keeps_sections(client: TestClient) -> None:
    page = client.get("/").text
    panel = page.split('id="employee-panel"', 1)[1].split('id="employees-panel"', 1)[0]
    assert "My employee file" in panel
    assert panel.index("employee-identity") < panel.index("employee-contact")
    assert panel.index("employee-contact") < panel.index("employee-specialties")
    assert panel.index("employee-specialties") < panel.index("employee-trainings")
    assert panel.index("employee-trainings") < panel.index("employee-certifications")
    assert panel.index("employee-certifications") < panel.index("employee-credentialing")
    assert panel.index("employee-credentialing") < panel.index("employee-history")
    assert "btn-employee-directory" in panel
    assert "Staff directory" in panel
    assert "employee.js?v=0.19.7" in page
    assert "credentialing.js?v=0.19.9" in page
    script = client.get("/static/employee.js").text
    assert "Change history" in script
    assert "JSON.stringify" not in script
    assert 'window.location.hash = "#employees"' in script
    credentialing = client.get("/static/credentialing.js").text
    assert "event.line" in credentialing
    assert "JSON.stringify(event.changes" not in credentialing
    assert "Change history" in credentialing
    billing = client.get("/static/billing.js").text
    assert "field_label" in billing
    assert "item.line" in billing
