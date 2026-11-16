"""Deploy 1: admin offers, manual statuses, response period, and role gates."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.access.guard import required_permission
from app.access.permissions import OFFER_MANAGE, ONBOARDING_SENSITIVE, admin_permissions
from app.access.store import access_store
from app.auth import User, issue_token
from app.employees.store import employees_store
from app.main import app

client = TestClient(app)


def _auth(username: str, email: str, practice: str = "practice-a") -> dict[str, str]:
    user = User("user-" + username, username, practice, email)
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _alice() -> dict[str, str]:
    return _auth("alice", "alice@example.com")


def _offer(deadline: str = "", licensed: str = "") -> dict[str, str]:
    body = {
        "display_name": "Test Candidate QA",
        "personal_email": "qa.candidate@example.com",
        "employment_type": "full-time",
        "candidate_full_name": "Test Candidate QA",
        "candidate_first_name": "Test",
        "supervisor_name": "Alex Supervisor",
        "hourly_rate": "$45.00",
        "caseload_range": "25-30",
        "qsehra_monthly_amount": "$250",
        "qsehra_wait_days": "60",
        "offer_date": "11/16/2026",
        "start_date": "01/05/2027",
        "response_deadline": deadline,
        "licensed_rate": licensed,
    }
    return body


def test_guard_and_admin_seed() -> None:
    assert required_permission("/hiring/offers") == "tool:hiring_onboarding"
    assert OFFER_MANAGE in admin_permissions()
    assert ONBOARDING_SENSITIVE in admin_permissions()
    assert "capability:hiring_payroll" not in access_store.permissions_for_role("practice-a", "billing")
    assert "capability:offer_manage" not in access_store.permissions_for_role("practice-a", "practice_owner")


def test_response_period_sticks_and_new_offer_uses_it() -> None:
    saved = client.put("/hiring/settings", headers=_alice(), json={"offer_response_days": 14})
    assert saved.status_code == 200, saved.text
    assert saved.json()["offer_response_days"] == 14
    again = client.get("/hiring/bootstrap", headers=_alice())
    assert again.json()["settings"]["offer_response_days"] == 14
    created = client.post("/hiring/offers", headers=_alice(), json=_offer())
    assert created.status_code == 200, created.text
    assert created.json()["response_deadline"] == "2026-11-30"
    assert created.json()["deadline_overridden"] is False
    restored = client.put("/hiring/settings", headers=_alice(), json={"offer_response_days": 30})
    assert restored.json()["offer_response_days"] == 30
    reloaded = client.get("/hiring/bootstrap", headers=_alice())
    assert reloaded.json()["settings"]["offer_response_days"] == 30


def test_create_edit_reload_and_manual_statuses() -> None:
    created = client.post("/hiring/offers", headers=_alice(), json=_offer("12/16/2026", "$55.00"))
    assert created.status_code == 200, created.text
    offer_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["fields"]["licensed_rate"] == "$55.00"
    assert created.json()["direct_client_access"] is True
    edited = client.put(
        f"/hiring/offers/{offer_id}",
        headers=_alice(),
        json=_offer("12/20/2026", "$55.00") | {"hourly_rate": "$46.00"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["fields"]["hourly_rate"] == "$46.00"
    assert edited.json()["response_deadline"] == "2026-12-20"
    loaded = client.get(f"/hiring/offers/{offer_id}", headers=_alice())
    assert loaded.json()["fields"]["hourly_rate"] == "$46.00"
    missing = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026") | {"hourly_rate": ""},
    )
    assert missing.status_code == 400
    part = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026")
        | {
            "personal_email": "qa.part@example.com",
            "candidate_full_name": "Riley Sample",
            "display_name": "Riley Sample",
            "employment_type": "part-time",
            "qsehra_monthly_amount": "",
            "qsehra_wait_days": "",
            "caseload_range": "12-18",
            "licensed_rate": "",
        },
    )
    assert part.status_code == 200, part.text
    pdf = client.get(f"/hiring/offers/{offer_id}/pdf", headers=_alice())
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    assert client.get("/hiring/bootstrap", headers=_alice()).json()["mail_queue"] == []
    accepted = client.post(
        f"/hiring/offers/{offer_id}/accept",
        headers=_alice(),
        json={"typed_name": "Test Candidate QA"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["offer"]["status"] == "accepted"
    assert accepted.json()["offer"]["acceptance"]["pdf_sha256"]
    assert accepted.json()["offer"]["acceptance"]["method"] == "paper"
    declined = client.post(
        f"/hiring/offers/{part.json()['id']}/send",
        headers=_alice(),
        json={"channel": "outside"},
    )
    assert declined.status_code == 200, declined.text
    no = client.post(
        f"/hiring/offers/{part.json()['id']}/decline",
        headers=_alice(),
        json={"reason": "schedule", "note": ""},
    )
    assert no.status_code == 200, no.text
    assert no.json()["status"] == "declined"
    again = client.post(
        f"/hiring/offers/{part.json()['id']}/accept",
        headers=_alice(),
        json={"typed_name": "Riley Sample"},
    )
    assert again.status_code == 409


def test_other_roles_cannot_see_offers() -> None:
    access_store.set_assignment("practice-a", "bill@example.com", "billing")
    access_store.set_assignment("practice-a", "own@example.com", "practice_owner")
    access_store.set_assignment("practice-a", "staff@example.com", "clinician")
    for email, role in (
        ("bill@example.com", "billing"),
        ("own@example.com", "practice_owner"),
        ("staff@example.com", "clinician"),
    ):
        headers = _auth(role, email)
        assert client.get("/hiring/bootstrap", headers=headers).status_code == 403
        assert client.post("/hiring/offers", headers=headers, json=_offer("12/16/2026")).status_code == 403
    home = client.get("/home", headers=_auth("own", "own@example.com")).json()
    ids = [tool["id"] for unit in home["units"] for tool in unit["tools"]]
    assert "hiring_onboarding" not in ids
    assert employees_store.rollout_enabled("practice-a") is False


def test_non_admin_with_the_tool_is_still_dark() -> None:
    access_store.update_role_permissions(
        "practice-a",
        "clinician",
        ["tool:time_entry", "tool:hiring_onboarding", "capability:offer_manage"],
    )
    access_store.set_assignment("practice-a", "staff@example.com", "clinician")
    denied = client.get("/hiring/bootstrap", headers=_auth("staff", "staff@example.com"))
    assert denied.status_code == 403
    admin_home = client.get("/home", headers=_alice()).json()
    ids = [tool["id"] for unit in admin_home["units"] for tool in unit["tools"]]
    assert "hiring_onboarding" in ids


def test_blank_form_uses_live_defaults_not_sample_values() -> None:
    from pathlib import Path

    from app.hiring.fill import supervisor_name_default

    page = Path("app/static/hiring.js").read_text(encoding="utf-8")
    for banned in (
        "Test Candidate QA",
        "Alex Supervisor",
        "qa.candidate@example.com",
        "$45.00",
        "25-30",
        "$250",
    ):
        assert banned not in page
    assert "offer_supervisor_name_default" in page
    assert "offer_response_days" in page
    settings = client.get("/hiring/bootstrap", headers=_alice()).json()["settings"]
    assert settings["offer_supervisor_title_default"] == "Director"
    assert settings["offer_supervisor_name_default"] == supervisor_name_default()
    assert settings["offer_response_days"] == 30


def test_part_time_can_clear_direct_client_access() -> None:
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026")
        | {
            "personal_email": "qa.part@example.com",
            "candidate_full_name": "Riley Sample",
            "display_name": "Riley Sample",
            "employment_type": "part-time",
            "qsehra_monthly_amount": "",
            "qsehra_wait_days": "",
            "caseload_range": "12-18",
            "direct_client_access": False,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["direct_client_access"] is False
    assert created.json()["employment_type"] == "part-time"
    loaded = client.get(f"/hiring/offers/{created.json()['id']}", headers=_alice())
    assert loaded.json()["direct_client_access"] is False


def test_withdraw_keeps_history_and_delete_needs_the_typed_name() -> None:
    from pathlib import Path

    created = client.post("/hiring/offers", headers=_alice(), json=_offer("12/16/2026", "$55.00"))
    offer_id = created.json()["id"]
    sent = client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    assert sent.status_code == 200, sent.text
    withdrawn = client.post(
        f"/hiring/offers/{offer_id}/withdraw",
        headers=_alice(),
        json={"reason": "Position filled"},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["status"] == "withdrawn"
    listed = client.get("/hiring/bootstrap", headers=_alice()).json()
    assert any(item["id"] == offer_id and item["status"] == "withdrawn" for item in listed["offers"])
    assert any(item["action"] == "withdrawn" and item["entity_id"] == offer_id for item in listed["history"])
    wrong = client.post(
        f"/hiring/offers/{offer_id}/delete",
        headers=_alice(),
        json={"confirm": "someone else"},
    )
    assert wrong.status_code == 400
    removed = client.post(
        f"/hiring/offers/{offer_id}/delete",
        headers=_alice(),
        json={"confirm": "Test Candidate QA"},
    )
    assert removed.status_code == 200, removed.text
    after = client.get("/hiring/bootstrap", headers=_alice()).json()
    assert all(item["id"] != offer_id for item in after["offers"])
    assert any(item["action"] == "deleted" and item["entity_id"] == offer_id for item in after["history"])
    assert client.get(f"/hiring/offers/{offer_id}/pdf", headers=_alice()).status_code == 404
    script = Path("app/static/hiring.js").read_text(encoding="utf-8")
    assert 'cache: "no-store"' in script
    assert 'message: "Saved."' in script
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert 'replace(/^#\\/?/, "#")' in html
    assert "syncTopChrome" in html
    assert "field-error" in html


def test_plain_money_is_stored_in_letter_format() -> None:
    from pathlib import Path

    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026")
        | {
            "personal_email": "qa.money@example.com",
            "display_name": "Money Example",
            "candidate_full_name": "Money Example",
            "hourly_rate": "40",
            "licensed_rate": "1,250",
            "qsehra_monthly_amount": "100",
            "caseload_range": "25–30",
        },
    )
    assert created.status_code == 200, created.text
    fields = created.json()["fields"]
    assert fields["hourly_rate"] == "$40.00"
    assert fields["licensed_rate"] == "$1,250.00"
    assert fields["qsehra_monthly_amount"] == "$100"
    assert fields["caseload_range"] == "25–30"
    bad = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026") | {"hourly_rate": "forty"},
    )
    assert bad.status_code == 400
    detail = bad.json()["detail"]
    assert detail == {"message": "Hourly rate must be a dollar amount.", "field": "hourly_rate"}
    script = Path("app/static/hiring.js").read_text(encoding="utf-8")
    assert "markField" in script


def test_delete_accepted_offer_removes_it() -> None:
    created = client.post(
        "/hiring/offers",
        headers=_alice(),
        json=_offer("12/16/2026") | {"personal_email": "qa.accepted@example.com", "display_name": "Accepted Candidate QA", "candidate_full_name": "Accepted Candidate QA"},
    )
    offer_id = created.json()["id"]
    client.post(f"/hiring/offers/{offer_id}/send", headers=_alice(), json={"channel": "outside"})
    accepted = client.post(
        f"/hiring/offers/{offer_id}/accept",
        headers=_alice(),
        json={"typed_name": "Accepted Candidate QA"},
    )
    assert accepted.status_code == 200, accepted.text
    denied = client.post(
        f"/hiring/offers/{offer_id}/delete",
        headers=_auth("staff", "staff@example.com"),
        json={"confirm": "Accepted Candidate QA"},
    )
    assert denied.status_code == 403
    removed = client.post(
        f"/hiring/offers/{offer_id}/delete",
        headers=_alice(),
        json={"confirm": "Accepted Candidate QA"},
    )
    assert removed.status_code == 200, removed.text
    assert client.get(f"/hiring/offers/{offer_id}", headers=_alice()).status_code == 404

