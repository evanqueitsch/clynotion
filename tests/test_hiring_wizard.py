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
        "response_deadline": "12/18/2026",
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


def _invite(hire_id: str) -> None:
    marked = client.post(f"/hiring/hires/{hire_id}/qb-invite", headers=_alice(), json={"sent": True})
    assert marked.status_code == 200, marked.text


def test_calendar_day_follows_new_york_not_utc() -> None:
    from datetime import date

    from app.hiring.mailer import long_start_date, practice_calendar_day, short_start_date

    # 23:30 America/New_York (UTC-5) is 04:30 UTC the next morning.
    instant = "2026-11-19T04:30:00+00:00"
    assert practice_calendar_day(instant) == date(2026, 11, 18)
    assert short_start_date(instant) == "11/18/2026"
    assert long_start_date(instant) == "Wednesday, November 18, 2026"
    # 7:41pm Eastern on Nov 18 is 00:41 UTC on Nov 19.
    assert practice_calendar_day("2026-11-19T00:41:00+00:00") == date(2026, 11, 18)
    assert practice_calendar_day("2026-12-08") == date(2026, 12, 8)
    assert short_start_date("2026-12-08T00:00:00Z") == "12/08/2026"
    assert short_start_date("2026-12-08") == "12/08/2026"
    assert long_start_date("2026-12-08") == "Tuesday, December 8, 2026"
    assert long_start_date("2026-12-08T00:00:00+00:00") == "Tuesday, December 8, 2026"
    assert long_start_date("12/08/2026") == "Tuesday, December 8, 2026"


def test_sent_label_uses_the_eastern_day_without_changing_the_stored_time() -> None:
    from app.hiring.engine import _document_sends

    hire = {
        "handoff_at": "2026-11-19T00:41:00+00:00",
        "packet_form_keys": ["h4c_hipaa_nda"],
        "documents_sent": [
            {"form_key": "h4c_packet", "sent_at": "2026-11-19T04:30:00+00:00", "sent_by": "alice@example.com"}
        ],
    }
    rows = {item["form_key"]: item for item in _document_sends(hire)}
    assert rows["h4c_hipaa_nda"]["sent_at"] == "2026-11-19T00:41:00+00:00"
    assert rows["h4c_hipaa_nda"]["sent_on"] == "2026-11-18"
    assert rows["h4c_packet"]["sent_at"] == "2026-11-19T04:30:00+00:00"
    assert rows["h4c_packet"]["sent_on"] == "2026-11-18"


def test_packet_defaults_follow_schedule_and_role() -> None:
    full = {row["form_key"]: row for row in packet_catalog("full-time", True, "Clinician")}
    assert full["h4c_qsehra"]["available"] is False
    assert full["h4c_qsehra"]["note"] == "not in the library yet"
    assert full["h4c_employment_agreement"]["note"] == "not in the library yet"
    assert full["h4c_confidentiality"]["available"] is True
    assert full["h4c_confidentiality"]["title"] == "Confidentiality, non-competition and non-solicitation"
    assert full["h4c_handbook"]["available"] is False
    assert full["h4c_supervision"]["available"] is True
    assert full["h4c_employment_agreement"]["available"] is False
    assert full["h4c_qsehra"]["note"] == "not in the library yet"
    assert full["h4c_simple_ira"]["note"] == "not in the library yet"
    assert full["pa_standard_application"]["available"] is True
    assert full["h4c_pa_clearances"]["available"] is True
    assert "pa_clearances_info" not in full
    assert "irs_w4" not in full
    assert "h4c_welcome_template" not in full
    licensed = packet_catalog("full-time", True, "Clinician", "LCSW")
    assert all(row["form_key"] != "h4c_supervision" for row in licensed)
    part = packet_catalog("part-time", False, "Coordinator")
    keys = [row["form_key"] for row in part]
    assert "h4c_confidentiality" in keys
    assert "h4c_supervision" not in keys
    assert "h4c_qsehra" not in keys
    assert "h4c_simple_ira" not in keys
    assert "h4c_pa_clearances" not in keys
    assert "pa_standard_application" not in keys


def test_handoff_records_the_packet_and_does_not_send(monkeypatch) -> None:
    hire_id = _hire(monkeypatch)
    empty = client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": []})
    assert empty.status_code == 400
    missing = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["h4c_employment_agreement"]},
    )
    assert missing.status_code == 400
    handed = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["h4c_hipaa_nda"]},
    )
    assert handed.status_code == 200, handed.text
    body = handed.json()
    assert body["stage"] == "payroll"
    assert body["owner"] == "billing"
    assert body["handoff_at"]
    assert body["packet_form_keys"] == ["h4c_hipaa_nda"]
    assert body["staff_email_status"] == "suppressed"
    assert body["candidate_email_status"] == "suppressed"
    todo = next(item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet")
    assert todo.href == f"/#hiring?hire={hire_id}"
    assert todo.owner_user_id == "billing"
    again = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["h4c_hipaa_nda"]},
    )
    assert again.status_code == 200, again.text
    assert again.json()["notice"] == "already handed off"
    assert again.json()["packet_form_keys"] == ["h4c_hipaa_nda"]
    assert len([item for item in due_engine.list_open("practice-a") if item.source_ref == hire_id + ":packet"]) == 1
    history = client.get("/hiring/bootstrap", headers=_alice()).json()["history"]
    summaries = [item["summary"] for item in history if item.get("entity_id") == hire_id]
    assert summaries.count("Email not sent (setting off)") == 2
    assert "riley.sample@example.com" not in json.dumps(summaries)
    assert "$45.00" not in json.dumps(summaries)
    packed = client.post(
        f"/hiring/hires/{hire_id}/packet.zip",
        headers=_alice(),
        json={"form_keys": ["h4c_hipaa_nda", "h4c_packet"]},
    )
    assert packed.status_code == 200, packed.text
    names = zipfile.ZipFile(io.BytesIO(packed.content)).namelist()
    assert len(names) == 2
    assert all(name.endswith(".pdf") for name in names)
    assert not any("welcome" in name for name in names)


def test_override_redirects_both_messages_without_pay(monkeypatch) -> None:
    sent: list[dict] = []

    def spy(practice_id, to, subject, text, attachments=(), html="", reply_to=""):
        sent.append(
            {"to": to, "subject": subject, "text": text, "attachments": attachments, "html": html, "reply_to": reply_to}
        )
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
    _invite(hire_id)
    handed = client.post(
        f"/hiring/hires/{hire_id}/handoff",
        headers=_alice(),
        json={"form_keys": ["h4c_hipaa_nda"]},
    )
    assert handed.status_code == 200, handed.text
    assert {item["to"] for item in sent} == {"qa.ops@example.com"}
    staff = next(item for item in sent if item["subject"].startswith("New Hire Ready"))
    assert staff["attachments"] == ()
    assert staff["reply_to"] == "operations@heart4change.org"
    assert "Jordan Example" in staff["text"]
    assert "Hi Casey," in staff["text"]
    assert "Full-time" in staff["text"]
    assert f"/#hiring?hire={hire_id}" in staff["html"]
    assert "$45" not in staff["text"] and "$45" not in staff["html"]
    assert "$55" not in staff["text"] and "$55" not in staff["html"]
    assert "$250" not in staff["text"]
    assert "jordan.example@" not in staff["text"]
    assert "QuickBooks" not in staff["text"]
    candidate = next(item for item in sent if item is not staff)
    assert "within 90 days" in candidate["text"]
    assert candidate["attachments"]
    assert all(name.endswith(".pdf") for name, _mime, _data in candidate["attachments"])
    assert "$45" not in candidate["text"] and "$45" not in candidate["html"]
    client.put(
        "/hiring/settings",
        headers=_alice(),
        json={"staff_handoff_emails_enabled": False, "candidate_emails_enabled": False, "wizard_email_override": ""},
    )


def test_quickbooks_invite_is_separate_from_handoff(monkeypatch) -> None:
    far_id = _hire(
        monkeypatch,
        personal_email="far.sample@example.com",
        display_name="Far Sample",
        candidate_full_name="Far Sample",
        candidate_first_name="Far",
        start_date="06/01/2027",
        response_deadline="06/01/2027",
        direct_client_access=False,
    )
    near_id = _hire(
        monkeypatch,
        personal_email="near.sample@example.com",
        display_name="Near Sample",
        candidate_full_name="Near Sample",
        candidate_first_name="Near",
        start_date="11/18/2026",
        response_deadline="12/01/2026",
        direct_client_access=False,
    )
    access_store.set_assignment("practice-a", "casey.example@example.com", "billing")
    hires = {item["id"]: item for item in client.get("/hiring/bootstrap", headers=_alice()).json()["hires"]}
    assert hires[far_id]["qb_invite_ready"] is False
    assert hires[far_id]["qb_invite_available_on"] == "2027-03-03"
    assert hires[near_id]["qb_invite_ready"] is True
    too_soon = client.post(f"/hiring/hires/{far_id}/qb-invite", headers=_alice(), json={"sent": True})
    assert too_soon.status_code == 409
    assert "90 days" in too_soon.json()["detail"]
    handed = client.post(f"/hiring/hires/{far_id}/handoff", headers=_alice(), json={"form_keys": ["h4c_hipaa_nda"]})
    assert handed.status_code == 200, handed.text
    assert handed.json()["owner"] == "billing"
    assert not handed.json()["qb_invite_at"]
    waiting = client.post(
        f"/hiring/hires/{near_id}/activate",
        headers=_alice(),
        json={"work_email": "near.sample@example.com"},
    )
    assert waiting.status_code == 409
    assert waiting.json()["detail"] == "Waiting on Admin: QuickBooks invite"
    home = client.get("/home", headers=_alice()).json()
    titles = [item["title"] for band in ("overdue", "this_week") for item in home["bands"][band]]
    assert "Near Sample: send the QuickBooks Workforce invite" in titles
    assert "Far Sample: send the QuickBooks Workforce invite" not in titles
    billing_home = client.get("/home", headers=_bill()).json()
    billing_titles = [item["title"] for band in ("overdue", "this_week") for item in billing_home["bands"][band]]
    assert all("QuickBooks" not in title for title in billing_titles)
    billing_items = client.get("/billing", headers=_bill()).json()["action_items"]
    assert all("QuickBooks" not in item["title"] for item in billing_items)
    assert client.post(f"/hiring/hires/{near_id}/qb-invite", headers=_bill(), json={"sent": True}).status_code == 403
    marked = client.post(f"/hiring/hires/{near_id}/qb-invite", headers=_alice(), json={"sent": True})
    assert marked.status_code == 200, marked.text
    cleared = client.get("/home", headers=_alice()).json()
    cleared_titles = [item["title"] for band in ("overdue", "this_week") for item in cleared["bands"][band]]
    assert "Near Sample: send the QuickBooks Workforce invite" not in cleared_titles
    page = open("app/static/hiring.js", encoding="utf-8").read()
    assert page.index('if (data.viewer === "billing")') < page.index("renderQuickBooksTask(card, hire);")
    assert "Waiting on Admin: QuickBooks invite" in page
    client.post(f"/hiring/hires/{far_id}/delete", headers=_alice(), json={"confirm": "Far Sample"})
    client.post(f"/hiring/hires/{near_id}/delete", headers=_alice(), json={"confirm": "Near Sample"})


def test_library_pdfs_round_trip_form_fields() -> None:
    from pypdf import PdfReader, PdfWriter

    base = "app/hiring/assets/forms"
    expected = {
        "h4c_employee_info.pdf": {"employee_signature", "signature_date", "attach_resume"},
        "h4c_hipaa.pdf": {"employee_signature", "printed_name"},
        "h4c_packet.pdf": {"employee_signature", "hipaa_signature", "before_day_forms"},
        "h4c_pa_clearances.pdf": {"employee_name", "date_received"},
        "h4c_supervision.pdf": {
            "supervisee_name",
            "credential_lsw",
            "credential_sw",
            "supervisee_email",
            "effective_date",
            "supervisor_signature",
            "supervisor_date",
            "supervisee_signature",
            "supervisee_date",
        },
        "h4c_confidentiality.pdf": {
            "employee_name",
            "sign_day",
            "sign_month",
            "sign_year",
            "employee_signature",
            "printed_name",
            "company_by",
            "company_title",
        },
    }
    for name, needed in expected.items():
        reader = PdfReader(f"{base}/{name}")
        fields = reader.get_fields() or {}
        assert needed <= set(fields), name
    standard = PdfReader(f"{base}/pa_standard_application.pdf")
    assert len(standard.pages) == 10
    assert len(standard.get_fields() or {}) > 50
    reader = PdfReader(f"{base}/h4c_pa_clearances.pdf")
    writer = PdfWriter()
    writer.append(reader)
    writer.update_page_form_field_values(
        writer.pages[0],
        {"employee_name": "Riley Sample", "date_received": "12/08/2026"},
        auto_regenerate=False,
    )
    import io

    buffer = io.BytesIO()
    writer.write(buffer)
    again = PdfReader(io.BytesIO(buffer.getvalue())).get_fields() or {}
    assert str(again["employee_name"].get("/V")) == "Riley Sample"
    assert str(again["date_received"].get("/V")) == "12/08/2026"
    supervision = PdfReader(f"{base}/h4c_supervision.pdf")
    writer = PdfWriter()
    writer.append(supervision)
    writer.update_page_form_field_values(
        writer.pages[0],
        {
            "supervisee_name": "Riley Sample",
            "supervisee_email": "riley",
            "credential_lsw": "/Yes",
        },
        auto_regenerate=False,
    )
    writer.update_page_form_field_values(
        writer.pages[1],
        {"effective_date": "12/08/2026", "supervisor_signature": "Melissa Smith", "supervisee_signature": "Riley Sample"},
        auto_regenerate=False,
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    filled = PdfReader(io.BytesIO(buffer.getvalue())).get_fields() or {}
    assert str(filled["supervisee_name"].get("/V")) == "Riley Sample"
    assert str(filled["effective_date"].get("/V")) == "12/08/2026"
    assert str(filled["credential_lsw"].get("/V")) == "/Yes"
    agreement = PdfReader(f"{base}/h4c_confidentiality.pdf")
    writer = PdfWriter()
    writer.append(agreement)
    writer.update_page_form_field_values(writer.pages[0], {"employee_name": "Riley Sample"}, auto_regenerate=False)
    writer.update_page_form_field_values(
        writer.pages[2],
        {"sign_day": "8", "sign_month": "December", "sign_year": "2026", "company_by": "Melissa Smith", "company_title": "Owner"},
        auto_regenerate=False,
    )
    buffer = io.BytesIO()
    writer.write(buffer)
    filled = PdfReader(io.BytesIO(buffer.getvalue())).get_fields() or {}
    assert str(filled["employee_name"].get("/V")) == "Riley Sample"
    assert str(filled["sign_year"].get("/V")) == "2026"
    assert str(filled["company_title"].get("/V")) == "Owner"
    assert "OPPORTUN1TY" not in agreement.pages[2].extract_text()


def test_additional_documents_go_to_the_override_and_the_personnel_folder(monkeypatch) -> None:
    from app.hiring.personnel_drive import clear_personnel_drive_for_tests, set_personnel_drive_for_tests

    class FolderDrive:
        def __init__(self) -> None:
            self.uploads: list[tuple[str, str, str]] = []
            self.n = 0

        def ensure_folder(self, folder_id: str, name: str) -> tuple[str, str]:
            self.n += 1
            new_id = folder_id or f"fld-{self.n}"
            return new_id, "https://drive.example/" + new_id

        def ensure_child(self, parent_id: str, name: str) -> str:
            self.n += 1
            return f"child-{name}-{self.n}"

        def upload(self, folder_id: str, filename: str, data: bytes, mime: str) -> str:
            self.uploads.append((folder_id, filename, mime))
            self.n += 1
            return f"file-{self.n}"

        def rename(self, file_id: str, name: str) -> None:
            return None

        def move(self, file_id: str, folder_id: str, name: str = "") -> None:
            return None

        def trash(self, file_id: str) -> None:
            return None

    sent: list[dict] = []

    def spy(practice_id, to, subject, text, attachments=(), html="", reply_to=""):
        sent.append(
            {"to": to, "subject": subject, "text": text, "attachments": attachments, "html": html, "reply_to": reply_to}
        )
        return "sent"

    drive = FolderDrive()
    set_personnel_drive_for_tests(drive)
    try:
        hire_id = _hire(
            monkeypatch,
            personal_email="riley.sample@example.com",
            display_name="Riley Sample",
            candidate_full_name="Riley Sample",
            candidate_first_name="Riley",
            position_title="Coordinator",
            direct_client_access=False,
        )
        early = client.post(
            f"/hiring/hires/{hire_id}/additional-documents/preview",
            headers=_alice(),
            json={"form_keys": ["h4c_packet"]},
        )
        assert early.status_code == 409
        handed = client.post(
            f"/hiring/hires/{hire_id}/handoff",
            headers=_alice(),
            json={"form_keys": ["h4c_hipaa_nda", "h4c_employee_info"]},
        )
        assert handed.status_code == 200, handed.text
        sends = {item["form_key"]: item["sent_at"] for item in handed.json()["document_sends"]}
        assert set(sends) == {"h4c_hipaa_nda", "h4c_employee_info"}
        held = client.post(
            f"/hiring/hires/{hire_id}/additional-documents",
            headers=_alice(),
            json={"form_keys": ["h4c_packet"]},
        )
        assert held.status_code == 200, held.text
        held_body = held.json()
        assert held_body["emailed"] is False
        assert held_body["additional_email"] == "suppressed"
        assert held_body["notice"] == "Not emailed: candidate emails are turned off"
        assert held_body["documents"] == ["New hire packet"]
        assert held_body["packet_form_keys"] == ["h4c_hipaa_nda", "h4c_employee_info"]
        assert "h4c_packet" not in {item["form_key"] for item in held_body["document_sends"]}
        client.put(
            "/hiring/settings",
            headers=_alice(),
            json={"candidate_emails_enabled": True, "wizard_email_override": "operations@heart4change.org"},
        )
        monkeypatch.setattr("app.hiring.engine.deliver", spy)
        preview = client.post(
            f"/hiring/hires/{hire_id}/additional-documents/preview",
            headers=_alice(),
            json={"form_keys": ["h4c_packet", "pa_clgs_32_6"]},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["subject"] == "Additional Onboarding Documents — Heart for Change"
        assert "Dear Riley," in preview.json()["text"]
        assert "fillable PDF" in preview.json()["text"]
        assert preview.json()["from"] == "operations@heart4change.org"
        assert "New hire packet" in preview.json()["html"]
        access_store.set_assignment("practice-a", "casey.example@example.com", "billing")
        assert client.post(
            f"/hiring/hires/{hire_id}/additional-documents",
            headers=_bill(),
            json={"form_keys": ["h4c_packet"]},
        ).status_code == 403
        sent.clear()
        before = len(drive.uploads)
        mailed = client.post(
            f"/hiring/hires/{hire_id}/additional-documents",
            headers=_alice(),
            json={"form_keys": ["h4c_packet", "pa_clgs_32_6"]},
        )
        assert mailed.status_code == 200, mailed.text
        body = mailed.json()
        assert body["packet_form_keys"] == ["h4c_hipaa_nda", "h4c_employee_info", "h4c_packet", "pa_clgs_32_6"]
        assert body["emailed"] is True
        assert body["emailed_via"] == "override"
        assert body["emailed_to"] == "operations@heart4change.org"
        assert body["documents"] == ["New hire packet", "PA Residency Certification CLGS-32-6"]
        assert body["notice"] == (
            "Sent 2 documents to test recipient operations@heart4change.org: "
            "New hire packet, PA Residency Certification CLGS-32-6"
        )
        assert body["additional_email"] == "sent"
        later = {item["form_key"]: item for item in body["document_sends"]}
        assert later["h4c_packet"]["sent_by"] == "alice@example.com"
        assert later["h4c_packet"]["sent_at"] != sends["h4c_hipaa_nda"]
        assert body["drive_copy"] == "copied"
        assert len(sent) == 1
        message = sent[0]
        assert message["to"] == "operations@heart4change.org"
        assert message["reply_to"] == "operations@heart4change.org"
        assert message["subject"] == "Additional Onboarding Documents — Heart for Change"
        assert [name for name, _mime, _data in message["attachments"]] == ["h4c_packet.pdf", "pa_clgs_32_6.pdf"]
        assert "Dear Riley," in message["text"]
        added = [item for item in drive.uploads[before:] if item[1] in {"h4c_packet.pdf", "pa_clgs_32_6.pdf"}]
        assert len(added) == 2
        page = open("app/static/hiring.js", encoding="utf-8").read()
        assert "Send additional documents" in page
        assert "additional-banner" in page
        assert "reopenAdditional" in page
        assert "scrollIntoView" in page
        assert "practiceDay" in page
        assert 'refreshHire(saved)' in page
        assert "Sending…" in page
        assert 'if (!hire.handoff_at) return;' in page
        client.put(
            "/hiring/settings",
            headers=_alice(),
            json={"candidate_emails_enabled": False, "wizard_email_override": ""},
        )
    finally:
        clear_personnel_drive_for_tests()


def test_billing_sees_only_the_handoff_and_cannot_open_pay_or_i9(monkeypatch) -> None:
    hire_id = _hire(monkeypatch, personal_email="jordan.example@example.com", display_name="Jordan Example", candidate_full_name="Jordan Example", candidate_first_name="Jordan")
    access_store.set_assignment("practice-a", "casey.example@example.com", "billing")
    hidden = client.get("/hiring/bootstrap", headers=_bill())
    assert hidden.status_code == 200
    assert hidden.json()["hires"] == []
    _invite(hire_id)
    client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["h4c_hipaa_nda"]})
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
    _invite(recalled_setup)
    client.post(f"/hiring/hires/{recalled_setup}/handoff", headers=_alice(), json={"form_keys": ["h4c_hipaa_nda"]})
    recalled = client.post(f"/hiring/hires/{recalled_setup}/recall", headers=_alice())
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["owner"] == "admin"
    assert recalled.json()["qb_invite_at"]
    assert recalled.json()["qb_invite_by"] == "alice@example.com"
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
    assert "function refreshHire" in page
    assert "function renderHireCard" in page
    assert "refreshHire(saved, message || \"Saved.\")" in page
    assert "handed off" in page
    assert "Offer deleted." in page
    assert "Hire deleted." in page
    assert 'getElementById("hire-" + hire.id)' in page
    assert "Handing off…" in page
    assert 'copy.textContent = "Copied"' in page
    assert "Candidate email is on" in page
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
    _invite(hire_id)
    handed = client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["h4c_hipaa_nda"]})
    assert handed.status_code == 200, handed.text
    home = client.get("/home", headers=_alice()).json()
    titles = [item["title"] for band in ("overdue", "this_week") for item in home["bands"][band]]
    assert "Riley Sample: file the PA new-hire report and schedule the I-9" in titles
    overdue = [item["title"] for item in home["bands"]["overdue"]]
    assert "Riley Sample: file the PA new-hire report and schedule the I-9" not in overdue
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
    report = client.post(
        f"/hiring/hires/{hire_id}/items/pa_new_hire_report",
        headers=_alice(),
        json={"status": "done", "dates": {"pa_new_hire_filed_on": "2026-12-02"}},
    )
    assert report.status_code == 200, report.text
    item = next(row for row in report.json()["items"] if row["template_key"] == "pa_new_hire_report")
    assert item["status"] == "done"
    assert all(row["template_key"] != "payroll_w4_dd" for row in report.json()["items"])
    recalled = client.post(f"/hiring/hires/{hire_id}/recall", headers=_alice())
    assert recalled.status_code == 200
    assert all(item.source_ref != hire_id + ":packet" for item in due_engine.list_open("practice-a"))
    again = client.post(f"/hiring/hires/{hire_id}/handoff", headers=_alice(), json={"form_keys": ["h4c_hipaa_nda"]})
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


def test_wizard_email_test_sends_only_to_the_override_and_creates_nothing(monkeypatch) -> None:
    sent: list[dict] = []

    def spy(practice_id, to, subject, text, attachments=(), html="", reply_to=""):
        sent.append(
            {"to": to, "subject": subject, "text": text, "attachments": attachments, "html": html, "reply_to": reply_to}
        )
        return "sent"

    monkeypatch.setattr("app.hiring.engine.deliver", spy)
    before = client.get("/hiring/bootstrap", headers=_alice()).json()
    refused = client.post("/hiring/wizard-email-test", headers=_alice())
    assert refused.status_code == 400
    client.put("/hiring/settings", headers=_alice(), json={"wizard_email_override": "qa.ops@example.com"})
    assert client.post("/hiring/wizard-email-test", headers=_alice()).status_code == 400
    client.put(
        "/hiring/settings",
        headers=_alice(),
        json={"wizard_email_override": "operations@heart4change.org"},
    )
    sent_ok = client.post("/hiring/wizard-email-test", headers=_alice())
    assert sent_ok.status_code == 200, sent_ok.text
    assert {item["to"] for item in sent} == {"operations@heart4change.org"}
    assert all(item["subject"].startswith("[TEST] ") for item in sent)
    assert all(item["reply_to"] == "operations@heart4change.org" for item in sent)
    staff = next(item for item in sent if "Payroll Setup" in item["subject"])
    assert staff["attachments"] == ()
    assert "Riley Sample" in staff["text"]
    assert "Hello," in staff["text"]
    assert "Hello there," not in staff["text"]
    assert "Tuesday, December 8, 2026" in staff["html"]
    assert "12/08/2026" in staff["subject"]
    assert "Open in Clynotion" in staff["html"]
    assert staff["html"].count("max-width:600px") == 1
    assert "#hiring?hire=" not in staff["html"]
    assert "<img" not in staff["html"].lower()
    candidate = next(item for item in sent if item is not staff)
    assert candidate["attachments"]
    assert all(name.endswith(".pdf") for name, _mime, _data in candidate["attachments"])
    assert "Dear Riley," in candidate["text"]
    assert "Pennsylvania clearances" in candidate["text"]
    assert "200 Willow Valley Sq, Suite 210, Lancaster, PA" in candidate["html"]
    assert "<img" not in candidate["html"].lower()
    assert "src=" not in candidate["html"].lower()
    after = client.get("/hiring/bootstrap", headers=_alice()).json()
    assert [item["id"] for item in after["offers"]] == [item["id"] for item in before["offers"]]
    assert [item["id"] for item in after["hires"]] == [item["id"] for item in before["hires"]]
    assert after["settings"]["staff_handoff_emails_enabled"] is False
    assert after["settings"]["candidate_emails_enabled"] is False
    client.put("/hiring/settings", headers=_alice(), json={"wizard_email_override": ""})


def test_folder_normalization_renames_by_id_without_changing_the_offer() -> None:
    from tests.test_hiring_onboarding import RecordingDrive
    from app.hiring.engine import hiring

    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        created = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(
                display_name="MICHAEL A. SAMPLE",
                candidate_full_name="MICHAEL A. SAMPLE",
                candidate_first_name="MICHAEL",
            ),
        )
        assert created.status_code == 200, created.text
        offer_id = created.json()["id"]
        practice = hiring._memory["practices"]["practice-a"]
        practice.pop("personnel_normalize", None)
        offer = practice["offers"][offer_id]
        offer["drive_folder_name"] = "SAMPLE, MICHAEL A."
        offer["drive_status"] = "error"
        before_status = offer["status"]
        before_name = practice["candidates"][offer["candidate_id"]]["display_name"]
        result = hiring.normalize_personnel_folders()
        assert result["folders"] == 1
        assert drive.folders[offer["drive_folder_id"]] == "Sample, Michael"
        assert offer["status"] == before_status
        assert practice["candidates"][offer["candidate_id"]]["display_name"] == before_name
        assert offer["drive_status"] == "connected"
        assert offer["drive_folder_name"] == "Sample, Michael"
        public = client.get(f"/hiring/offers/{offer_id}", headers=_alice()).json()
        assert public["drive_note"] == ""
        assert public["status"] == before_status
    finally:
        clear_personnel_drive_for_tests()
        hiring._memory["practices"]["practice-a"].pop("personnel_normalize", None)


def test_drive_status_aligns_without_calling_drive_or_changing_the_offer() -> None:
    from tests.test_hiring_onboarding import RecordingDrive
    from app.hiring.engine import hiring

    class ExplodingDrive:
        def rename(self, file_id: str, name: str) -> None:
            raise AssertionError("Drive should not be called")

    drive = RecordingDrive()
    set_personnel_drive_for_tests(drive)
    try:
        created = client.post(
            "/hiring/offers",
            headers=_alice(),
            json=_offer(
                display_name="MICHAEL A. SAMPLE",
                candidate_full_name="MICHAEL A. SAMPLE",
                candidate_first_name="MICHAEL",
            ),
        )
        assert created.status_code == 200, created.text
        offer_id = created.json()["id"]
        practice = hiring._memory["practices"]["practice-a"]
        offer = practice["offers"][offer_id]
        offer["status"] = "sent"
        offer["drive_status"] = "error"
        offer["drive_folder_id"] = offer.get("drive_folder_id") or "fld-existing"
        offer["drive_folder_name"] = "SAMPLE, MICHAEL A."
        offer["drive_pdf_file_id"] = "pdf-1"
        offer["drive_pdf_name"] = "SAMPLE_MICHAEL_A_offer-letter.pdf"
        offer["resume_id"] = "res-1"
        offer["drive_resume_file_id"] = "resume-1"
        offer["drive_resume_name"] = "SAMPLE_MICHAEL_A_resume.pdf"
        practice.setdefault("resumes", {})["res-1"] = {"id": "res-1", "mime": "application/pdf", "offer_id": offer_id}
        practice["personnel_normalize"] = {"done": True, "folders": 1, "files": 2}
        set_personnel_drive_for_tests(ExplodingDrive())
        result = hiring.align_drive_status()
        assert result["fields"] == ["drive_folder_name", "drive_pdf_name", "drive_resume_name", "drive_status"]
        assert offer["drive_status"] == "connected"
        assert offer["drive_folder_name"] == "Sample, Michael"
        assert offer["drive_pdf_name"] == "Sample_Michael_offer-letter.pdf"
        assert offer["status"] == "sent"
        assert practice["candidates"][offer["candidate_id"]]["display_name"] == "MICHAEL A. SAMPLE"
        assert len(drive.folders) == 1
        public = client.get(f"/hiring/offers/{offer_id}", headers=_alice()).json()
        assert public["drive_note"] == ""
        assert public["status"] == "sent"
        again = hiring.align_drive_status()
        assert again["offers"] == 0
    finally:
        clear_personnel_drive_for_tests()
        hiring._memory["practices"]["practice-a"].pop("personnel_normalize", None)


def test_wizard_steps_follow_direct_client_access_and_escape_html() -> None:
    from email import message_from_bytes, policy
    import base64

    from app.hiring.mailer import compose_wizard, sample_wizard_values
    from app.hr.gmail_oauth import _raw_message
    from app.hr.time_mail import OutboundMail

    values = sample_wizard_values(link="https://clynotion.com/#hiring", supervisor="Jordan Example", packet_list="Form I-9")
    _subject, office, _html = compose_wizard("candidate", {}, values, direct=False)
    assert "Pennsylvania clearances" not in office
    _subject, direct, html = compose_wizard("candidate", {}, values, direct=True)
    assert "Pennsylvania clearances" in direct
    assert "1. Review the attached new-hire packet (Form I-9)" in direct
    _subject, staff_office, _html = compose_wizard("staff", {}, values, direct=False)
    assert "Confirm exclusion screening is complete." in staff_office
    assert "PA clearances and exclusion" not in staff_office
    _subject, staff_direct, staff_html = compose_wizard(
        "staff", {}, {**values, "name": "Riley <Sample>"}, direct=True
    )
    assert "Confirm PA clearances and exclusion screening" in staff_direct
    assert "Riley &lt;Sample&gt;" in staff_html
    assert "<img" not in staff_html.lower()
    assert "src=" not in staff_html.lower()
    message = OutboundMail(
        to="operations@heart4change.org",
        subject="[TEST] sample",
        text=staff_direct,
        html=staff_html,
        reply_to="operations@heart4change.org",
        attachments=(("packet.pdf", "application/pdf", b"%PDF-1.4"),),
    )
    parsed = message_from_bytes(
        base64.urlsafe_b64decode(_raw_message(message, "operations@heart4change.org") + "==="),
        policy=policy.default,
    )
    assert parsed["reply-to"] == "operations@heart4change.org"
    kinds = {part.get_content_type() for part in parsed.walk()}
    assert {"text/plain", "text/html", "application/pdf"} <= kinds


def test_wizard_preview_renders_riley_without_sending_or_storing(monkeypatch) -> None:
    def spy(*_args, **_kwargs):
        raise AssertionError("preview must not send")

    monkeypatch.setattr("app.hiring.engine.deliver", spy)
    before = client.get("/hiring/bootstrap", headers=_alice()).json()
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "candidate"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["subject"].startswith("Welcome to Heart for Change")
    assert "Riley" in body["html"]
    assert "max-width:600px" in body["html"]
    assert "[TEST]" not in body["subject"]
    staff = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert staff.status_code == 200, staff.text
    assert "Open in Clynotion" in staff.json()["html"]
    assert "</html>" in staff.json()["html"]
    after = client.get("/hiring/bootstrap", headers=_alice()).json()
    assert [item["id"] for item in after["offers"]] == [item["id"] for item in before["offers"]]
    assert after["settings"]["staff_handoff_emails_enabled"] is False
    assert after["settings"]["candidate_emails_enabled"] is False
    assert after["settings"]["wizard_email_override"] == ""


def test_staff_greeting_uses_the_first_billing_name_and_otherwise_says_hello() -> None:
    clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="christine-example",
        email="christine.example@example.com",
        display_name="Christine Example",
    )
    clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="morgan-example",
        email="morgan.example@example.com",
        display_name="Morgan Example",
    )
    access_store.set_assignment("practice-a", "morgan.example@example.com", "billing")
    access_store.set_assignment("practice-a", "christine.example@example.com", "billing")
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert preview.status_code == 200, preview.text
    assert "Hi Christine," in preview.json()["html"]
    assert "Hello there," not in preview.json()["html"]
    access_store.set_assignment("practice-a", "alex.example@example.com", "billing")
    clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="alex-example",
        email="alex.example@example.com",
        display_name="Alex Example",
    )
    first = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert "Hi Alex," in first.json()["html"]
    access_store.set_assignment("practice-a", "christine.example@example.com", "")
    access_store.set_assignment("practice-a", "morgan.example@example.com", "")
    access_store.set_assignment("practice-a", "alex.example@example.com", "")
    none = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert "Hello," in none.json()["text"]
    assert "Hi ," not in none.json()["text"]
    assert "there," not in none.json()["text"]


def test_empty_billing_profile_uses_the_mailbox_name(monkeypatch) -> None:
    """Production shape: the Billing role is assigned and the profile name is empty."""
    person = clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="christine-empty",
        email="christine.example@example.com",
        display_name="christine.example@example.com",
    )
    person.display_name = ""
    access_store.set_assignment("practice-a", "christine.example@example.com", "billing")
    monkeypatch.setattr("app.workspace_directory.directory_names_readonly", lambda _practice: {})
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert "Hi Christine," in body["html"]
    assert "Hello there," not in body["html"]
    assert body["greeting_source"] == "email_local"


def test_directory_name_is_used_when_the_profile_name_is_empty(monkeypatch) -> None:
    clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="christine-blank",
        email="christine.example@example.com",
        display_name="christine.example@example.com",
    ).display_name = ""
    access_store.set_assignment("practice-a", "christine.example@example.com", "billing")

    def names(_practice: str) -> dict[str, str]:
        return {"christine.example@example.com": "Christine Example"}

    monkeypatch.setattr("app.workspace_directory.directory_names_readonly", names)
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert preview.status_code == 200, preview.text
    assert "Hi Christine," in preview.json()["html"]
    assert preview.json()["greeting_source"] == "directory"


def test_a_role_mailbox_falls_back_to_hello(monkeypatch) -> None:
    monkeypatch.setattr("app.workspace_directory.directory_names_readonly", lambda _practice: {})
    person = clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="billing-role",
        email="billing@example.com",
        display_name="billing@example.com",
    )
    person.display_name = ""
    access_store.set_assignment("practice-a", "billing@example.com", "billing")
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert preview.status_code == 200, preview.text
    assert "Hello," in preview.json()["text"]
    assert "Hi Billing," not in preview.json()["html"]
    assert preview.json()["greeting_source"] == "none"
    from app.hiring.engine import _billing_greeting

    for local in ("operations", "admin", "info", "office", "hr", "payroll", "support", "noreply", "contact", "team"):
        email = local + "@example.com"
        other = clinician_store.upsert_workspace_user(
            "practice-a",
            google_id=local + "-role",
            email=email,
            display_name=email,
        )
        other.display_name = ""
        access_store.set_assignment("practice-a", "billing@example.com", "")
        access_store.set_assignment("practice-a", email, "billing")
        first, source = _billing_greeting("practice-a", {"staff_greeting_name": ""})
        assert first == "", local
        assert source == "none", local


def test_saved_billing_contact_name_wins_over_the_mailbox(monkeypatch) -> None:
    monkeypatch.setattr("app.workspace_directory.directory_names_readonly", lambda _practice: {})
    person = clinician_store.upsert_workspace_user(
        "practice-a",
        google_id="billing-role-saved",
        email="billing@example.com",
        display_name="billing@example.com",
    )
    person.display_name = ""
    access_store.set_assignment("practice-a", "billing@example.com", "billing")
    before = client.get("/hiring/bootstrap", headers=_alice()).json()
    assert before["settings"]["staff_greeting_name"] == ""
    assert before["settings"]["staff_handoff_emails_enabled"] is False
    saved = client.put(
        "/hiring/settings",
        headers=_alice(),
        json={"staff_greeting_name": "Christine"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["staff_greeting_name"] == "Christine"
    assert saved.json()["staff_handoff_emails_enabled"] is False
    assert saved.json()["candidate_emails_enabled"] is False
    preview = client.post("/hiring/wizard-email-preview", headers=_alice(), json={"kind": "staff"})
    assert "Hi Christine," in preview.json()["html"]
    assert preview.json()["greeting_source"] == "setting"


def test_saved_w4_steps_are_rewritten_without_replacing_other_lines() -> None:
    from app.hiring.mailer import OLD_CANDIDATE_INVITE_STEP, OLD_STAFF_SETUP_STEP, migrate_wizard_steps

    candidate = "\n".join(
        [
            "Review the attached new-hire packet, including the W-4, and complete each form.",
            "Bring your completed forms with you.",
            OLD_CANDIDATE_INVITE_STEP,
            "Keep this custom closing step.",
        ]
    )
    migrated, changed = migrate_wizard_steps("candidate", candidate)
    assert changed == [
        "Review the attached new-hire packet, including the W-4, and complete each form.",
        OLD_CANDIDATE_INVITE_STEP,
    ]
    assert "within 90 days" in migrated
    assert "do_not_reply@intuit.com" in migrated
    assert "Keep this custom closing step." in migrated
    assert "{packet_list}" in migrated
    staff, staff_changed = migrate_wizard_steps("staff", OLD_STAFF_SETUP_STEP + "\nFile the Pennsylvania new-hire report.")
    assert staff_changed == [OLD_STAFF_SETUP_STEP]
    assert staff.startswith("File the Pennsylvania new-hire report.")
    assert "QuickBooks" not in staff
    dropped, removed = migrate_wizard_steps(
        "staff",
        "Confirm the hire completed QuickBooks Workforce self-onboarding (W-4 and direct deposit).\nFile the Pennsylvania new-hire report.",
    )
    assert removed == ["Confirm the hire completed QuickBooks Workforce self-onboarding (W-4 and direct deposit)."]
    assert dropped.startswith("File the Pennsylvania new-hire report.")
    assert "QuickBooks" not in dropped
