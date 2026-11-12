"""Month/day/year dates and the enrollment expiration choice."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.credentialing.digest import digest_preview
from app.credentialing.reminders import sync_reminders
from app.credentialing.store import credentialing_store
from app.dates import DATE_HINT, parse_calendar_date
from app.main import app
from app.platform.due import due_engine
from tests.test_time_entry import _alice


def test_parser_accepts_the_formats_the_field_can_show() -> None:
    assert parse_calendar_date("11/09/2026") == "2026-11-09"
    assert parse_calendar_date("2026-11-09") == "2026-11-09"
    assert parse_calendar_date("11/9/26") == "2026-11-09"
    assert parse_calendar_date("11.09.2026") == "2026-11-09"
    assert parse_calendar_date("11.9.26") == "2026-11-09"
    assert parse_calendar_date("9/11/2026") == "2026-09-11"
    assert parse_calendar_date("") == ""
    assert parse_calendar_date("   ") == ""
    assert "YYYY-MM-DD" not in DATE_HINT
    with pytest.raises(ValueError) as bad:
        parse_calendar_date("02/31/2026")
    assert str(bad.value) == DATE_HINT
    assert "11/09/2026" in str(bad.value)
    with pytest.raises(ValueError):
        parse_calendar_date("13/09/2026")
    with pytest.raises(ValueError):
        parse_calendar_date("not-a-date")


def test_display_value_round_trips_and_clear_persists_empty() -> None:
    credentialing_store.reset()
    client = TestClient(app)
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "QA Test Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa",
    )
    saved = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "11/09/2026",
            "effective_on": "11/9/26",
            "has_expiration": False,
            "note": "",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["submitted_on"] == "2026-11-09"
    assert body["effective_on"] == "2026-11-09"
    assert body["has_expiration"] is False
    again = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "11/09/2026",
            "has_expiration": False,
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["submitted_on"] == "2026-11-09"
    cleared = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "",
            "effective_on": "",
            "has_expiration": False,
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["submitted_on"] == ""
    assert cleared.json()["effective_on"] == ""
    invalid = client.put(
        "/credentialing/enrollments",
        headers=_alice(),
        json={
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "pending",
            "submitted_on": "02/31/2026",
            "has_expiration": False,
        },
    )
    assert invalid.status_code == 422
    detail = invalid.json()["detail"]
    assert detail == DATE_HINT
    assert "YYYY-MM-DD" not in detail
    assert "%Y" not in detail


def test_credentials_expire_requires_dates_and_no_expiration_clears_them() -> None:
    credentialing_store.reset()
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "QA Test Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa",
    )
    with pytest.raises(ValueError) as exc:
        credentialing_store.save_enrollment(
            "practice-a",
            {
                "subject_id": subject["id"],
                "payer_id": payer["id"],
                "status": "in_network",
                "has_expiration": True,
                "expires_on": "",
                "note": "QA note",
            },
            actor="qa",
        )
    assert "11/09/2026" in str(exc.value)
    saved = credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "in_network",
            "has_expiration": True,
            "expires_on": "11/09/2026",
            "next_follow_up": "",
            "note": "QA note",
        },
        actor="qa",
    )
    assert saved["expires_on"] == "2026-11-09"
    assert saved["next_follow_up"] == "2026-08-11"
    assert saved["has_expiration"] is True
    cleared = credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "in_network",
            "has_expiration": False,
            "expires_on": "11/09/2026",
            "next_follow_up": "08/11/2026",
            "note": "QA note",
        },
        actor="qa",
    )
    assert cleared["has_expiration"] is False
    assert cleared["expires_on"] == ""
    assert cleared["next_follow_up"] == ""


def test_existing_expiration_date_is_not_rewritten_until_save() -> None:
    due_engine.reset()
    credentialing_store.reset()
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    payer = credentialing_store.save_payer(
        "practice-a",
        {"name": "QA Test Payer", "type": "commercial", "accepts_licenses": ["LCSW"]},
        actor="qa",
    )
    credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject["id"],
            "payer_id": payer["id"],
            "status": "in_network",
            "effective_on": "2024-01-01",
            "note": "QA note",
        },
        actor="qa",
    )
    _payload, row = credentialing_store._load_practice("practice-a")
    enrollment = row["enrollments"][f"{subject['id']}|{payer['id']}"]
    enrollment.pop("has_expiration", None)
    enrollment["expires_on"] = "2026-11-09"
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("recredential_90") == 1
    assert "has_expiration" not in enrollment
    enrollment["expires_on"] = ""
    counts = sync_reminders("practice-a", "qa-user")
    assert "recredential_90" not in counts
    assert "expiration_follow_up" not in counts
    assert "has_expiration" not in enrollment


def test_pending_follow_up_stays_when_credentials_do_not_expire() -> None:
    due_engine.reset()
    credentialing_store.reset()
    from tests.test_billing_phase2 import _seed_pair

    subject_id, payer_id = _seed_pair(
        status="pending",
        submitted="2026-10-30",
        has_expiration=False,
    )
    counts = sync_reminders("practice-a", "qa-user")
    assert counts.get("follow_up") == 1
    assert "recredential_90" not in counts
    cell = credentialing_store.save_enrollment(
        "practice-a",
        {
            "subject_id": subject_id,
            "payer_id": payer_id,
            "status": "pending",
            "submitted_on": "2026-10-30",
            "has_expiration": False,
            "note": "QA note",
        },
        actor="qa",
    )
    assert cell["application_follow_up"] == "2026-11-13"
    assert cell["follow_up_on"] == "2026-11-13"


def test_digest_and_home_skip_expiration_without_a_date() -> None:
    due_engine.reset()
    credentialing_store.reset()
    from tests.test_billing_phase2 import _day, _seed_pair

    _seed_pair(status="in_network", effective=_day(-400))
    sync_reminders("practice-a", "qa-user")
    assert "Recredential" not in digest_preview("practice-a")
    _seed_pair(status="in_network", has_expiration=True, enrollment_expires=_day(20))
    sync_reminders("practice-a", "qa-user")
    preview = digest_preview("practice-a")
    assert "Recredential" in preview
    client = TestClient(app)
    home = client.get("/home", headers=_alice())
    assert home.status_code == 200, home.text
    titles = [item["title"] for item in home.json()["bands"]["overdue"]]
    assert any(title.startswith("Recredential") for title in titles)


def test_payer_default_does_not_rewrite_enrollments() -> None:
    credentialing_store.reset()
    payer = credentialing_store.save_payer(
        "practice-a",
        {
            "name": "QA Test Payer",
            "type": "commercial",
            "accepts_licenses": ["LCSW"],
            "credentials_expire": False,
        },
        actor="qa",
    )
    subject = credentialing_store.save_subject(
        "practice-a",
        {"name": "QA Test Clinician", "kind": "clinician", "license_type": "LCSW"},
        actor="qa",
    )
    assert payer["credentials_expire"] is False
    _payload, row = credentialing_store._load_practice("practice-a")
    assert f"{subject['id']}|{payer['id']}" not in row["enrollments"]
    client = TestClient(app)
    grid = client.get("/credentialing/grid", headers=_alice()).json()
    cell = next(
        item
        for item in grid["cells"]
        if item["subject_id"] == subject["id"] and item["payer_id"] == payer["id"]
    )
    assert cell["enrollment_saved"] is False
    assert cell["has_expiration"] is None
    shown = next(item for item in grid["payers"] if item["id"] == payer["id"])
    assert shown["credentials_expire"] is False


def test_page_date_helper_matches_the_field() -> None:
    script = Path("app/static/credentialing.js").read_text(encoding="utf-8")
    start = script.index("function normalizeCalendarDate")
    end = script.index("function readDate")
    source = script[start:end] + """
const cases = [
  ["11/09/2026", "2026-11-09"],
  ["2026-11-09", "2026-11-09"],
  ["11/9/26", "2026-11-09"],
  ["11.09.2026", "2026-11-09"],
  ["", ""],
  ["   ", ""],
];
for (const [raw, expected] of cases) {
  const got = normalizeCalendarDate(raw);
  if (got !== expected) {
    console.error("normalize", raw, got);
    process.exit(2);
  }
}
if (formatUsDate("2026-11-09") !== "11/09/2026") process.exit(3);
if (formatUsDate("") !== "") process.exit(4);
if (normalizeCalendarDate("02/31/2026") !== null) process.exit(5);
if (normalizeCalendarDate("9/11/2026") !== "2026-09-11") process.exit(6);
"""
    completed = subprocess.run(["node", "-e", source], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr or completed.stdout
