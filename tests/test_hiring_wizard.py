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
from app.hiring.forms_catalog import packet_catalog
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
