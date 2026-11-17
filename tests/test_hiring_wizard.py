"""Cross-role onboarding wizard. Synthetic people only. Mail never leaves the process."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.access.store import access_store
from app.auth import User, issue_token
from app.clinicians import clinician_store
from app.hiring.checklist import supervision_required
from app.hiring.forms_catalog import packet_catalog
from app.hiring.personnel_drive import clear_personnel_drive_for_tests, set_personnel_drive_for_tests
from app.main import app
from app.platform.due import due_engine

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_due_todos():
    due_engine.reset()
    yield
    due_engine.reset()


def _alice() -> dict[str, str]:
    user = User("user-alice", "alice", "practice-a", "alice@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _bill() -> dict[str, str]:
    user = User("user-bill", "bill", "practice-a", "casey.example@example.com")
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _offer(**extra: object) -> dict:
    body = {
        "display_name": "Riley Sample",
        "personal_email": "riley.sample@example.com",
        "employment_type": "full-time",
        "candidate_full_name": "Riley Sample",
        "candidate_first_name": "Riley",
        "supervisor_name": "Alex Supervisor",
        "hourly_rate": "$45.00",
        "licensed_rate": "$55.00",
        "caseload_range": "25-30",
        "qsehra_monthly_amount": "$250",
        "qsehra_wait_days": "60",
        "offer_date": "11/17/2026",
        "start_date": "11/17/2026",
        "response_deadline": "11/17/2026",
        "direct_client_access": True,
        "position_title": "Clinician",
    }
    body.update(extra)
    return body


def _hire(monkeypatch, **extra: object) -> str:
    monkeypatch.setattr("app.hiring.engine.deliver", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sent")))
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(**extra))
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    assert sent.status_code == 200, sent.text
    accepted = client.post(
        f"/hiring/offers/{offer_id}/accept",
        headers=_alice(),
        json={"typed_name": extra.get("display_name") or "Riley Sample"},
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["hire"]["id"]


def test_packet_defaults_follow_schedule_and_role() -> None:
    full = {row["form_key"]: row for row in packet_catalog("full-time", True, "Clinician")}
    assert full["h4c_qsehra"]["available"] is False
    assert full["h4c_qsehra"]["note"] == "not in the library yet"
    assert full["h4c_employment_agreement"]["note"] == "not in the library yet"
    assert full["h4c_confidentiality"]["available"] is False
    assert full["h4c_handbook"]["available"] is False
    assert full["h4c_supervision_agreement"]["available"] is False
    assert full["pa_standard_application"]["available"] is False
    assert full["pa_clearances_info"]["available"] is False
    assert full["irs_w4"]["default"] is True
    part = packet_catalog("part-time", False, "Coordinator")
    keys = [row["form_key"] for row in part]
    assert "h4c_qsehra" not in keys
    assert "h4c_simple_ira" not in keys
    assert "pa_clearances_info" not in keys
    assert "pa_standard_application" not in keys


def test_handoff_records_the_packet_and_does_not_send(monkeypatch) -> None:
    hire_id = _hire(monkeypatch)
    missing = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["h4c_employment_agreement"]},
    )
    assert missing.status_code == 400
    handed = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["irs_w4", "h4c_hipaa_nda"]},
    )
    assert handed.status_code == 200, handed.text
    body = handed.json()
    assert body["stage"] == "payroll"
    assert body["owner"] == "billing"
    assert body["handoff_at"]
    assert body["packet_form_keys"] == ["irs_w4", "h4c_hipaa_nda"]
    assert body["staff_email_status"] == "suppressed"
    assert body["candidate_email_status"] == "suppressed"
    todo = next(item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet")
    assert todo.href == f"/#hiring?hire={hire_id}"
    assert todo.owner_user_id == "billing"
    again = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["irs_w4"]},
    )
    assert again.status_code == 200, again.text
    assert again.json()["notice"] == "already handed off"
    assert again.json()["packet_form_keys"] == ["irs_w4", "h4c_hipaa_nda"]
    assert len([item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet"]) == 1
    history = client.get("/hiring/bootstrap", headers=_alice()).json()["history"]
    summaries = [item["summary"] for item in history if item.get("entity_id") == hire_id]
    assert summaries.count("Email not sent (setting off)") == 2
    assert "riley.sample@example.com" not in json.dumps(summaries)
    assert "$45.00" not in json.dumps(summaries)
    packed = client.post(
        f"/hiring/hires/{hire_id}/packet.zip",
        headers=_alice(),
        json={"form_keys": ["irs_w4", "h4c_welcome_template"]},
    )
    assert packed.status_code == 200, packed.text
    names = zipfile.ZipFile(io.BytesIO(packed.content)).namelist()
    assert any(name.endswith(".pdf") for name in names)
    assert any(name.endswith(".docx") for name in names)


def test_override_redirects_both_messages_without_pay(monkeypatch) -> None:
    sent: list[dict] = []

    def spy(practice_id, to, subject, text, attachments=()):
        sent.append({"to": to, "subject": subject, "text": text, "attachments": attachments})
        return "sent"

    monkeypatch.setattr("app.hiring.engine.deliver", spy)
    clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="casey-example",
        email="casey.example@example.com",
        display_name="Casey Example",
    )
    access_store.set_assignment("practice-a", "casey.example@example.com", "billing")
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(personal_email="jordan.example@example.com", display_name="Jordan Example", candidate_full_name="Jordan Example", candidate_first_name="Jordan"))
    offer_id = created.json()["id"]
    assert client.get("/hiring/bootstrap", headers=_alice()).json()["billing_label"] == "Casey"
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    accepted = client.post(f"/hiring/offers/{offer_id}/accept", headers=_alice(), json={"typed_name": "Jordan Example"})
    hire_id = accepted.json()["hire"]["id"]
    client.put(
        "/hiring/settings",
        headers=_alice(),
        json={
            "staff_handoff_emails_enabled": True,
            "candidate_emails_enabled": True,
            "wizard_email_override": "qa.ops@example.com",
        },
    )
    handed = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["irs_w4"]},
    )
    assert handed.status_code == 200, handed.text
    assert {item["to"] for item in sent} == {"qa.ops@example.com"}
    staff = next(item for item in sent if item["subject"].startswith("New hire ready"))
    assert staff["attachments"] == ()
    assert "Jordan Example" in staff["text"]
    assert "full-time" in staff["text"]
    assert f"/#hiring?hire={hire_id}" in staff["text"]
    assert "$45" not in staff["text"]
    assert "$55" not in staff["text"]
    assert "$250" not in staff["text"]
    assert "jordan.example@" not in staff["text"]
    candidate = next(item for item in sent if item is not staff)
    assert candidate["attachments"]
    assert all(name.endswith(".pdf") for name, _mime, _data in candidate["attachments"])
    assert "$45" not in candidate["text"]
    client.put(
        "/hiring/settings",
        headers=_alice(),
        json={"staff_handoff_emails_enabled": False, "candidate_emails_enabled": False, "wizard_email_override": ""},
    )


def test_billing_sees_only_the_handoff_and_cannot_open_pay_or_i9(monkeypatch) -> None:
    hire_id = _hire(monkeypatch, personal_email="jordan.example@example.com", display_name="Jordan Example", candidate_full_name="Jordan Example", candidate_first_name="Jordan")
    access_store.set_assignment("practice-a", "casey.example@example.com", "billing")
    hidden = client.get("/hiring/bootstrap", headers=_bill())
    assert hidden.status_code == 200
    assert hidden.json()["hires"] == []
    client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["irs_w4"]})
    visible = client.get("/hiring/bootstrap", headers=_bill())
    assert visible.status_code == 200, visible.text
    payload = visible.text
    assert "hourly_rate" not in payload
    assert "qsehra" not in payload
    assert "licensed_rate" not in payload
    assert visible.json()["offers"] == []
    assert visible.json()["hires"][0]["id"] == hire_id
    offer_id = visible.json()["hires"][0]["offer_id"]
    assert client.get(f"/hiring/offers/{offer_id}/pdf", headers=_bill()).status_code == 403
    assert client.post("/hiring/offers", headers=_bill(), json=_offer()).status_code == 403
    assert client.post(f"/hiring/offers/{offer_id}/send", headers=_bill(), json={"channel": "outside"}).status_code == 403
    assert client.post(f"/hiring/offers/{offer_id}/accept", headers=_bill(), json={"typed_name": "Jordan Example"}).status_code == 403
    assert client.post(f"/hiring/hires/{hire_id}/delete", headers=_bill(), json={"confirm": "Jordan Example"}).status_code == 403
    uploaded = client.post(
        f"/hiring/hires/{hire_id}/items/i9_scan_uploaded/file",
        headers=_alice(),
        files={"upload": ("i9.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert client.get(f"/hiring/documents/{uploaded.json()['document_id']}", headers=_bill()).status_code == 403
    dated = client.post(
        f"/hiring/hires/{hire_id}/items/clearances",
        headers=_bill(),
        json={"status": "done", "dates": {"clearances_received_on": "2026-11-17"}},
    )
    assert dated.status_code == 200, dated.text
    blocked = client.post(
        f"/hiring/hires/{hire_id}/items/i9_scan_uploaded",
        headers=_bill(),
        json={"status": "done"},
    )
    assert blocked.status_code == 403
    activated = client.post(
        f"/hiring/hires/{hire_id}/activate",
        headers=_bill(),
        json={"work_email": "jordan.work@example.com"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert "hourly_rate" not in activated.text
    recalled_setup = _hire(monkeypatch, personal_email="riley.sample@example.com")
    client.post(f"/hiring/hires/{recalled_setup}/handoff", headers=_alice(), json={"form_keys": ["irs_w4"]})
    recalled = client.post(f"/hiring/hires/{recalled_setup}/recall", headers=_alice())
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["owner"] == "admin"
    assert recalled.json()["stage"] == "accepted"
    assert recalled.json()["staff_email_status"] == "suppressed"
    ids = [item["id"] for item in client.get("/hiring/bootstrap", headers=_bill()).json()["hires"]]
    assert recalled_setup not in ids
    home = client.get("/home", headers=_bill()).json()
    tools = [tool["id"] for unit in home["units"] for tool in unit["tools"]]
    assert "hiring_onboarding" in tools
    assert access_store.role_id_for("practice-a", "casey.example@example.com") == "billing"


def test_blank_credentials_skip_license_items_and_same_day_dues_are_not_red() -> None:
    from datetime import date, timedelta

    from app.hiring.checklist import add_business_days, build_items, clearance_state

    created = date(2026, 11, 17)
    items = build_items(
        hire_id="hire-1",
        employment_type="full-time",
        start=created,
        accepted=created,
        direct_client_access=True,
        needs_supervision=True,
        has_license=False,
        closed=set(),
        new_id=lambda prefix: prefix + "1",
    )
    by_key = {item["template_key"]: item for item in items}
    assert by_key["license_verification"]["status"] == "n/a"
    assert by_key["license_verification"]["note"] == "No license on file"
    assert by_key["npi_caqh"]["status"] == "n/a"
    floor = add_business_days(created, 1, set())
    assert by_key["welcome_sent"]["due_date"] >= floor.isoformat()
    assert by_key["clearances"]["due_date"] < created.isoformat()
    state = clearance_state(direct=True, status="open", due=created - timedelta(days=1), today=created, start=created, created=created)
    assert state == {"warning": False, "badge": "due now"}
    licensed = build_items(
        hire_id="hire-2",
        employment_type="full-time",
        start=created,
        accepted=created,
        direct_client_access=True,
        needs_supervision=False,
        has_license=True,
        closed=set(),
        new_id=lambda prefix: prefix + "2",
    )
    licensed_keys = {item["template_key"]: item for item in licensed}
    assert licensed_keys["license_verification"]["status"] == "open"
    assert licensed_keys["npi_caqh"]["note"] == ""


def test_revise_sent_offer_starts_a_new_draft_and_activation_is_not_manual(monkeypatch) -> None:
    hire_id = _hire(monkeypatch, candidate_credentials="LPC-Candidate")
    blocked = client.post(
        f"/hiring/hires/{hire_id}/items/activate",
        headers=_alice(),
        json={"status": "done"},
    )
    assert blocked.status_code == 409
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(personal_email="revise.sample@example.com", candidate_credentials="LSW"))
    offer_id = created.json()["id"]
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    revised = client.post(f"/hiring/offers/{offer_id}/revise", headers=_alice())
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "draft"
    assert revised.json()["current_version"] == 2
    page = open("app/static/hiring.js", encoding="utf-8").read()
    assert 'hire.stage === "payroll"' in page
    assert "downloadSelected.disabled = true" in page


def test_hiring_screen_updates_rows_without_a_full_reload() -> None:
    page = open("app/static/hiring.js", encoding="utf-8").read()
    assert "function paintChecklist" in page
    assert "paintChecklist(card, saved)" in page
    assert "Offer deleted." in page
    assert "linkedHire.stage" in page
    assert '— v" + offer.current_version' in page


def test_supervision_applies_until_a_full_license() -> None:
    assert supervision_required("") is True
    assert supervision_required("LSW") is True
    assert supervision_required("LPC-Candidate") is True
    assert supervision_required("LCSW") is False
    assert supervision_required("LPC") is False
    assert supervision_required("LMFT") is False
    assert supervision_required("Licensed psychologist") is False


def test_handoff_todo_shows_on_home_and_billing_and_recall_removes_it(monkeypatch) -> None:
    hire_id = _hire(
        monkeypatch,
        start_date="12/31/2026",
        response_deadline="12/17/2026",
        direct_client_access=False,
        candidate_credentials="",
    )
    handed = client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["irs_w4"]})
    assert handed.status_code == 200, handed.text
    home = client.get("/home", headers=_alice()).json()
    titles = [item["title"] for band in ("overdue", "this_week") for item in home["bands"][band]]
    assert "Riley Sample: W-4, direct deposit, and the PA new-hire report" in titles
    overdue = [item["title"] for item in home["bands"]["overdue"]]
    assert "Riley Sample: W-4, direct deposit, and the PA new-hire report" not in overdue
    match = next(item for band in ("overdue", "this_week") for item in home["bands"][band] if hire_id in item["href"])
    assert match["href"] == f"/#hiring?hire={hire_id}"
    assert match["owner_user_id"] == "billing"
    billing = client.get("/billing", headers=_alice()).json()
    assert any(hire_id in item["href"] for item in billing["action_items"])
    hire = next(item for item in client.get("/hiring/bootstrap", headers=_alice()).json()["hires"] if item["id"] == hire_id)
    agreement = next(item for item in hire["items"] if item["template_key"] == "supervision_agreement")
    assert agreement["status"] == "open"
    dated = client.post(
        f"/hiring/hires/{hire_id}/items/i9_section2",
        headers=_alice(),
        json={"status": "done", "dates": {"i9_completed_on": "2026-12-01"}},
    )
    assert dated.status_code == 200, dated.text
    section = next(item for item in dated.json()["items"] if item["template_key"] == "i9_section2")
    assert section["dates"]["i9_completed_on"] == "2026-12-01"
    assert section["dates"]["i9_section2_completed_on"] == "2026-12-01"
    payroll = client.post(
        f"/hiring/hires/{hire_id}/items/payroll_w4_dd",
        headers=_alice(),
        json={"status": "open", "dates": {"w4_received_on": "2026-12-02", "direct_deposit_received_on": "2026-12-03"}},
    )
    assert payroll.json()["items"]
    item = next(row for row in payroll.json()["items"] if row["template_key"] == "payroll_w4_dd")
    assert item["status"] == "done"
    recalled = client.post(f"/hiring/hires/{hire_id}/recall", headers=_alice())
    assert recalled.status_code == 200
    assert all(item.source_ref != hire_id + ":packet" for item in due_engine.list_open("practice-a"))
    again = client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["irs_w4"]})
    assert again.status_code == 200
    assert again.json().get("notice") != "already handed off"
    assert len([item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet"]) == 1
    client.post(f"/hiring/hires/{hire_id}/delete", headers=_alice(), json={"confirm": "Riley Sample"})
    assert all(item.source_ref != hire_id + ":packet" for item in due_engine.list_open("practice-a"))


def test_second_save_updates_and_a_duplicate_name_is_rejected() -> None:
    created = client.post("/hiring/offers", headers=_alice(), json=_offer(candidate_credentials="LCSW"))
    assert created.status_code == 200, created.text
    updated = client.put(f"/hiring/offers/{created.json()['id']}", headers=_alice(), json=_offer(candidate_credentials="LCSW", caseload_range="20-25"))
    assert updated.status_code == 200, updated.text
    assert updated.json()["id"] == created.json()["id"]
    duplicate = client.post("/hiring/offers", headers=_alice(), json=_offer(candidate_credentials="LCSW"))
    assert duplicate.status_code == 409
    hire_id = _accept(created.json()["id"])
    hire = next(item for item in client.get("/hiring/bootstrap", headers=_alice()).json()["hires"] if item["id"] == hire_id)
    agreement = next(item for item in hire["items"] if item["template_key"] == "supervision_agreement")
    assert agreement["status"] == "n/a"


def _accept(offer_id: str) -> str:
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    accepted = client.post(f"/hiring/offers/{offer_id}/accept", headers=_alice(), json={"typed_name": "Riley Sample"})
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["hire"]["id"]


def test_delete_hire_keeps_the_personnel_folder(monkeypatch) -> None:
    from tests.test_hiring_onboarding import RecordingDrive

    monkeypatch.setattr("app.hiring.engine.deliver", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sent")))
    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        created = client.post("/hiring/offers", headers=_alice(), json=_offer(personal_email="folder.sample@example.com"))
        offer_id = created.json()["id"]
        client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
        accepted = client.post(
            f"/hiring/offers/{offer_id}/accept-paper",
            headers=_alice(),
            data={"accepted_on": "11/17/2026"},
            files={"upload": ("signed.pdf", b"%PDF-1.4 synthetic", "application/pdf")},
        )
        assert accepted.status_code == 200, accepted.text
        hire_id = accepted.json()["hire"]["id"]
        parent = next(file_id for file_id, name in drive.folders.items() if name == "Sample, Riley")
        removed = client.post(f"/hiring/hires/{hire_id}/delete", headers=_alice(), json={"confirm": "Riley Sample"})
        assert removed.status_code == 200, removed.text
        assert parent not in drive.trashed
        offer = client.get(f"/hiring/offers/{offer_id}", headers=_alice()).json()
        assert offer["drive_status"] != "trashed"
        assert "Folder trashed" not in (offer["drive_note"] or "")
    finally:
        clear_personnel_drive_for_tests()
