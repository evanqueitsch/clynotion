"""Human history lines. Raw change objects stay stored for the audit trail."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.credentialing.store import credentialing_store
from app.history_text import format_history_line
from app.main import app
from tests.test_time_entry import _alice

SECRET = "0000000999"
PHONE = "555-0199"


def test_formatter_turns_raw_changes_into_a_masked_sentence() -> None:
    raw = {
        "at": "2026-11-12T17:51:00+00:00",
        "by": "operations@example.com",
        "source": "manual",
        "changes": {
            "effective_on": {"old": "", "new": "2026-12-15"},
            "has_expiration": {"old": "", "new": False},
            "npi": {"old": "", "new": SECRET},
            "personal_phone": {"old": PHONE, "new": ""},
        },
    }
    line = format_history_line(raw)
    assert line.startswith("11/12/2026, 12:51 PM ET — operations@example.com changed ")
    assert "Effective on from (empty) to 12/15/2026" in line
    assert "Credentials expire from (empty) to no" in line
    assert "NPI from (empty) to a saved value" not in line
    assert "NPI" in line
    assert SECRET not in line
    assert PHONE not in line
    assert "effective_on" not in line
    assert "{" not in line
    assert raw["changes"]["npi"]["new"] == SECRET
    assert raw["changes"]["effective_on"]["new"] == "2026-12-15"
    noisy = {
        "at": "2026-11-12T17:51:00+00:00",
        "by": "qa@example.com",
        "source": "manual",
        "changes": {
            "hire_date": {"old": "", "new": ""},
            "effective_on": {"old": "", "new": "2026-12-15"},
        },
    }
    skipped = format_history_line(noisy)
    assert "Hire date" not in skipped
    assert "Effective on from (empty) to 12/15/2026" in skipped
    assert noisy["changes"]["hire_date"] == {"old": "", "new": ""}


def test_enrollment_history_keeps_raw_changes_and_adds_a_human_line() -> None:
    credentialing_store.reset()
    client = TestClient(app)
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa@example.com",
    )
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "QA Test Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa@example.com",
    )
    saved = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "",
            "effective_on": "12/15/2026",
            "has_expiration": False,
            "note": "",
        },
    )
    assert saved.status_code == 200, saved.text
    event = saved.json()["history"][-1]
    assert event["source"] == "manual"
    assert event["changes"]["effective_on"]["new"] == "2026-12-15"
    assert "Effective on" in event["line"]
    assert "12/15/2026" in event["line"]
    assert "effective_on" not in event["line"]
    assert "has_expiration" not in event["line"]
    assert "{" not in event["line"]
    stored = credentialing_store._memory["practices"]["practice-a"]["enrollments"]
    raw = next(iter(stored.values()))["history"][-1]
    assert "line" not in raw
    assert raw["changes"]["effective_on"]["new"] == "2026-12-15"


def test_subject_history_api_returns_raw_secrets_and_a_masked_line() -> None:
    credentialing_store.reset()
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa@example.com",
    )
    practice = credentialing_store._memory["practices"]["practice-a"]["subjects"][subject["id"]]
    practice["history"].append(
        {
            "at": "2026-11-12T17:51:00+00:00",
            "by": "qa@example.com",
            "source": "manual",
            "changes": {"npi": {"old": "", "new": SECRET}, "license_number": {"old": "set", "new": "changed"}},
        }
    )
    public = credentialing_store.public_subject("practice-a", subject["id"])
    assert public is not None
    event = public["history"][-1]
    assert event["changes"]["npi"]["new"] == SECRET
    assert SECRET not in event["line"]
    assert "NPI" in event["line"]
    assert "License number" in event["line"]
    assert "••••••" in event["line"]
    stored = practice["history"][-1]
    assert stored["changes"]["npi"]["new"] == SECRET
    assert "line" not in stored
