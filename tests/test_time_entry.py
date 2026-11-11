"""Weekly hours: pay weeks, rounding, authz, cutoff, mail claim/retry."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.auth import User, issue_token, user_store
from app.clinicians import clinician_store
from app.hr.time_entry import (
    HOURS_HELPER_TEXT,
    MAX_WEEK_MINUTES,
    MELISSA_EMAIL,
    REMINDER_DUE_SUBJECT,
    REMINDER_FIRST_SUBJECT,
    TIME_ENTRY_URL,
    TimeStore,
    TimeStoreError,
    combine_hours_minutes,
    deadline_instant,
    normalize_lines,
    pay_week_ending,
    round_payable_minutes,
    set_clock,
    time_store,
)
from app.hr.time_mail import (
    GmailSender,
    MailConfigError,
    MailResult,
    OutboundMail,
    RecordingMailer,
    load_service_account,
    set_mailer_for_tests,
)
from app.hr.time_schedule import run_tick
from app.main import app

WEEK = "2026-03-13"  # workweek Monday Mar 9 – Friday Mar 13; payroll Thursday Mar 19
WED = datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc)  # Wed Mar 18 10:00 EDT, entry open
THU_0900 = datetime(2026, 3, 18, 13, 0, tzinfo=timezone.utc)  # Wed Mar 18 09:00 EDT reminder
FRI_0800 = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)  # Thu Mar 19 08:00 EDT reminder
FRI_1129 = datetime(2026, 3, 19, 15, 29, tzinfo=timezone.utc)
FRI_1130 = datetime(2026, 3, 19, 15, 30, tzinfo=timezone.utc)  # Thu Mar 19 11:30 EDT cutoff


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _alice() -> dict[str, str]:
    _, user = user_store._users["alice"]
    return _auth(user)


def _named(user_id: str, name: str, practice: str, email: str) -> User:
    return User(user_id=user_id, username=name, practice_id=practice, email=email)


def _roster() -> None:
    jordan = clinician_store.get("practice-a", "clin-a-jordan")
    sam = clinician_store.get("practice-a", "clin-a-sam")
    dana = clinician_store.get("practice-a", "clin-a-dana")
    assert jordan and sam and dana
    jordan.email = "jordan@heart4change.org"
    jordan.display_name = "Jordan Lee"
    sam.email = "sam@heart4change.org"
    sam.display_name = "Sam Rivera"
    dana.email = "evan@heart4change.org"
    dana.display_name = "Evan Admin"
    dana.default_role = "admin"
    from app.access.store import access_store

    access_store.set_assignment("practice-a", "jordan@heart4change.org", "clinician")
    access_store.set_assignment("practice-a", "sam@heart4change.org", "clinician")


def _jordan() -> User:
    return _named("user-jordan", "Jordan Lee", "practice-a", "jordan@heart4change.org")


def _sam() -> User:
    return _named("user-sam", "Sam Rivera", "practice-a", "sam@heart4change.org")


def _evan() -> User:
    return _named("user-evan", "Evan Admin", "practice-a", "evan@heart4change.org")


def _melissa() -> User:
    return _named("user-melissa", "Melissa Smith", "practice-a", MELISSA_EMAIL)


def _settings(**overrides: object) -> dict:
    body: dict = {
        "participant_emails": ["jordan@heart4change.org", "sam@heart4change.org"],
        "reminder_first_weekday": 2,
        "reminder_first_time": "09:00",
        "reminder_due_weekday": 3,
        "reminder_due_time": "08:00",
        "summary_weekday": 3,
        "summary_time": "11:30",
        "summary_recipient": MELISSA_EMAIL,
        "automation_enabled": True,
    }
    body.update(overrides)
    return body


def _save_settings(client: TestClient, **overrides: object) -> dict:
    response = client.put("/hr/time/settings", headers=_alice(), json=_settings(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


def _enter(client: TestClient, user: User, hours: int, minutes: int, *, week: str = WEEK, for_email: str = ""):
    return client.post(
        "/hr/time/entries",
        headers=_auth(user),
        json={"week_ending": week, "hours": hours, "minutes": minutes, "for_email": for_email},
    )


@pytest.mark.parametrize(
    ("raw", "rounded", "decimal"),
    [
        (0, 0, "0.00"),
        (300, 300, "5.00"),  # 5:00
        (301, 315, "5.25"),  # 5:01
        (314, 315, "5.25"),  # 5:14
        (315, 315, "5.25"),  # 5:15 exact quarter
        (316, 330, "5.50"),  # 5:16
        (1, 15, "0.25"),
        (14, 15, "0.25"),
        (16, 30, "0.50"),
    ],
)
def test_round_up_to_next_quarter_hour(raw: int, rounded: int, decimal: str) -> None:
    from app.hr.time_entry import decimal_hours

    assert round_payable_minutes(raw) == rounded
    assert decimal_hours(rounded) == decimal


def test_hours_cap_is_the_minutes_in_a_week_not_eighty() -> None:
    assert combine_hours_minutes(80, 0) == 80 * 60
    assert combine_hours_minutes(80, 1) == 80 * 60 + 1
    assert combine_hours_minutes(168, 0) == MAX_WEEK_MINUTES
    with pytest.raises(ValueError):
        combine_hours_minutes(168, 1)
    with pytest.raises(ValueError):
        normalize_lines([{"category": "supervision", "minutes": 15}])


def test_prior_monday_friday_payroll_thursday_across_dst() -> None:
    from app.hr.time_entry import TimeSettings, coverage_end, payroll_thursday, pay_week_start

    # Reminders on Wed Nov 11 and Thu Nov 12, 2026 collect Mon Nov 2–Fri Nov 6.
    # Sat Nov 7 and Sun Nov 8 are inside that same total. Payroll is Thu Nov 12.
    wednesday = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)  # 09:00 EST
    assert pay_week_ending(wednesday).isoformat() == "2026-11-06"
    assert pay_week_start(pay_week_ending(wednesday)).isoformat() == "2026-11-02"
    assert coverage_end(pay_week_ending(wednesday)).isoformat() == "2026-11-08"
    assert payroll_thursday(pay_week_ending(wednesday)).isoformat() == "2026-11-12"
    # The next workweek is not open on Friday Nov 6; it opens Saturday Nov 7.
    friday_evening = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)
    assert pay_week_ending(friday_evening).isoformat() == "2026-10-30"
    saturday = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    assert pay_week_ending(saturday).isoformat() == "2026-11-06"

    # Spring forward Sunday 2026-03-08. Payroll Thursday Mar 19 is EDT (UTC-4).
    spring_week = pay_week_ending(datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc))
    spring_lock = deadline_instant(
        spring_week,
        TimeSettings(practice_id="p", participant_emails=[], summary_weekday=3, summary_time="11:30"),
    )
    assert spring_week.isoformat() == "2026-03-13"
    assert spring_lock.astimezone(timezone.utc) == datetime(2026, 3, 19, 15, 30, tzinfo=timezone.utc)

    # Fall back Sunday 2026-11-01. Payroll Thursday Nov 12 is EST (UTC-5).
    fall_lock = deadline_instant(
        pay_week_ending(wednesday),
        TimeSettings(practice_id="p", participant_emails=[], summary_weekday=3, summary_time="11:30"),
    )
    assert fall_lock.astimezone(timezone.utc) == datetime(2026, 11, 12, 16, 30, tzinfo=timezone.utc)


def test_old_default_schedule_migrates_and_custom_times_stay(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    stored = {
        "practice_id": "practice-a",
        "participant_emails": ["jordan@heart4change.org"],
        "reminder_first_weekday": 3,
        "reminder_first_time": "09:00",
        "reminder_due_weekday": 4,
        "reminder_due_time": "08:00",
        "summary_weekday": 4,
        "summary_time": "11:30",
        "summary_recipient": MELISSA_EMAIL,
    }
    from app.hr.time_entry import TimeSettings

    migrated = TimeSettings.from_dict(stored)
    assert migrated.schedule_migrated is True
    assert (migrated.reminder_first_weekday, migrated.reminder_due_weekday, migrated.summary_weekday) == (2, 3, 3)
    custom = TimeSettings.from_dict({**stored, "reminder_first_time": "10:15"})
    assert custom.schedule_migrated is False
    assert custom.reminder_first_time == "10:15"
    assert custom.reminder_first_weekday == 3
    assert custom.automation_enabled is False


def test_health_shows_scheduled_mail_only_when_the_checkbox_and_cron_are_on(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    health = client.get("/health").json()
    assert health["scheduled_mail"] == "off"
    assert health["time_mail_mode"] == "off"
    assert health["cron_configured"] is False
    monkeypatch.setenv("CRON_SECRET", "present-not-a-real-secret")
    armed = client.get("/health").json()
    assert armed["scheduled_mail"] == "off"
    assert armed["cron_configured"] is True
    _roster()
    set_clock(WED)
    time_store.save_settings(_alice_user(), _settings(automation_enabled=True), WED)
    health = client.get("/health").json()
    assert health["scheduled_mail"] == "on"
    view = time_store.settings_view(_alice_user(), WED)
    assert view["scheduled_mail"] == "on"
    assert view["automation_enabled"] is True
    assert health["time_mail_mode"] == "off"
    assert "present-not-a-real-secret" not in client.get("/health").text


def test_november_payroll_does_not_backfill_and_thursday_goes_to_the_right_people() -> None:
    """Wed Nov 11 after 9:00 ET creates nothing. Thu Nov 12 8:00 and 11:30 are the first sends."""
    from app.hr.time_entry import preview_new_sends, upcoming_slots

    _roster()
    late_morning = datetime(2026, 11, 11, 15, 30, tzinfo=timezone.utc)  # 10:30 AM EST
    thursday_eight = datetime(2026, 11, 12, 13, 0, tzinfo=timezone.utc)  # 8:00 AM EST
    thursday_cutoff = datetime(2026, 11, 12, 16, 30, tzinfo=timezone.utc)  # 11:30 AM EST
    time_store.save_settings(
        _alice_user(),
        _settings(participant_emails=["jordan@heart4change.org"], automation_enabled=True),
        late_morning,
    )
    settings = time_store._settings["practice-a"]
    assert preview_new_sends(settings, late_morning) == []
    upcoming = upcoming_slots(settings, late_morning)
    assert [row["kind"] for row in upcoming] == ["reminder_due", "summary"]
    assert upcoming[0]["at"].startswith("2026-11-12T08:00:00")
    assert upcoming[1]["at"].startswith("2026-11-12T11:30:00")
    mailer = RecordingMailer()
    assert run_tick(mailer=mailer, now=late_morning)["sent"] == 0
    assert mailer.sent == []

    due = run_tick(mailer=mailer, now=thursday_eight)
    assert due["sent"] == 1
    assert [message.to for message in mailer.sent] == ["jordan@heart4change.org"]
    assert MELISSA_EMAIL not in [message.to for message in mailer.sent]
    again = run_tick(mailer=mailer, now=thursday_eight + timedelta(seconds=30))
    assert again["sent"] == 0
    assert len(mailer.sent) == 1

    mailer.sent.clear()
    summary = run_tick(mailer=mailer, now=thursday_cutoff)
    assert summary["sent"] == 1
    assert [message.to for message in mailer.sent] == [MELISSA_EMAIL]
    repeat = run_tick(mailer=mailer, now=thursday_cutoff + timedelta(seconds=20))
    assert repeat["sent"] == 0
    assert len(mailer.sent) == 1


def test_turning_automation_on_does_not_catch_up_a_passed_reminder() -> None:
    _roster()
    late = datetime(2026, 3, 18, 19, 0, tzinfo=timezone.utc)  # Wed 3:00 PM EDT
    time_store.save_settings(
        _alice_user(),
        _settings(participant_emails=["jordan@heart4change.org"], automation_enabled=True),
        late,
    )
    mailer = RecordingMailer()
    assert run_tick(mailer=mailer, now=late)["sent"] == 0
    assert mailer.sent == []


def test_legacy_week_is_not_relabeled(client: TestClient) -> None:
    from app.hr.time_entry import SCHEDULE_V1, WeekState

    _roster()
    set_clock(WED)
    _save_settings(client)
    time_store._put_week(
        WeekState(
            practice_id="practice-a",
            week_ending=WEEK,
            locked_at="2026-03-13T15:30:00+00:00",
            snapshot=[{"email": "jordan@heart4change.org", "display_name": "Jordan Lee", "clinician_id": ""}],
            summary_recipient=MELISSA_EMAIL,
            lock_deadline="2026-03-13T15:30:00+00:00",
            schedule_version=SCHEDULE_V1,
        )
    )
    legacy = client.get(f"/hr/time/week?week_ending={WEEK}&v=1", headers=_alice()).json()
    assert legacy["schedule_version"] == 1
    assert "Saturday" in legacy["label"]
    current = client.get(f"/hr/time/week?week_ending={WEEK}&v=2", headers=_alice()).json()
    assert current["schedule_version"] == 2
    assert current["label"].startswith("Monday, March 9, 2026")
    assert "Jordan Lee" in legacy["label"] or legacy["rows"][0]["display_name"] == "Jordan Lee"


def test_selected_staff_can_save_zero_and_history_stays_private(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client)
    saved = _enter(client, _jordan(), 0, 0)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["raw_minutes"] == 0
    assert body["rounded_minutes"] == 0
    assert body["decimal_hours"] == "0.00"
    assert body["lines"] == [{"category": "total", "minutes": 0}]

    denied = _enter(client, _evan(), 1, 0)
    assert denied.status_code == 403

    me = client.get("/hr/time/me", headers=_auth(_jordan()))
    assert me.status_code == 200
    assert HOURS_HELPER_TEXT in me.json()["helper_text"]
    assert me.json()["entry"]["raw_minutes"] == 0
    assert all(row["email"] == "jordan@heart4change.org" for row in me.json()["history"])

    sam = client.get("/hr/time/me", headers=_auth(_sam()))
    assert sam.json()["entry"] is None
    assert sam.json()["history"] == []

    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice())
    rows = {row["email"]: row for row in week.json()["rows"]}
    assert rows["jordan@heart4change.org"]["status"] == "submitted"
    assert rows["jordan@heart4change.org"]["raw_minutes"] == 0
    assert rows["sam@heart4change.org"]["status"] == "missing"
    assert rows["sam@heart4change.org"]["raw_minutes"] is None
    assert week.json()["missing"] == ["Sam Rivera"]


def test_pay_report_rounds_examples_and_csv_does_not_treat_missing_as_zero(client: TestClient) -> None:
    _roster()
    clinician_store.get("practice-a", "clin-a-sam").display_name = "=cmd"
    set_clock(WED)
    _save_settings(client)
    assert _enter(client, _jordan(), 5, 1).status_code == 200
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    jordan = next(row for row in week["rows"] if row["email"] == "jordan@heart4change.org")
    assert (jordan["raw_minutes"], jordan["rounded_minutes"], jordan["decimal_hours"]) == (301, 315, "5.25")
    csv_text = client.get(f"/hr/time/week.csv?week_ending={WEEK}", headers=_alice()).text
    assert "Jordan Lee,jordan@heart4change.org,submitted,301,315,5.25" in csv_text
    assert "'=cmd,sam@heart4change.org,missing,,," in csv_text
    assert "missing,0,0,0.00" not in csv_text
    # Sorted by display name: '=cmd before Jordan because apostrophe is not part of sort.
    # Snapshot names are the roster names; '=cmd sorts before Jordan Lee.
    names = [row["display_name"] for row in week["rows"]]
    assert names == ["=cmd", "Jordan Lee"]


def test_server_locks_at_cutoff_without_a_tick_and_only_melissa_can_correct(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client)
    set_clock(FRI_1129)
    saved = _enter(client, _jordan(), 5, 0)
    assert saved.status_code == 200, saved.text
    submission_id = saved.json()["submission_id"]

    set_clock(FRI_1130)
    late_self = _enter(client, _jordan(), 5, 1)
    assert late_self.status_code == 403
    assert "Melissa" in late_self.json()["detail"]

    for headers in (_alice(), _auth(_evan())):
        denied = client.patch(
            f"/hr/time/entries/{submission_id}",
            headers=headers,
            json={"hours": 9, "minutes": 0},
        )
        assert denied.status_code == 403, denied.text

    corrected = client.patch(
        f"/hr/time/entries/{submission_id}",
        headers=_auth(_melissa()),
        json={"hours": 5, "minutes": 16},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["raw_minutes"] == 316
    assert corrected.json()["rounded_display"] == "5:30"
    assert corrected.json()["decimal_hours"] == "5.50"

    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    audit = week["audits"][-1]
    assert audit["late"] is True
    assert audit["actor_email"] == MELISSA_EMAIL
    assert audit["before_minutes"] == 300
    assert audit["after_minutes"] == 316
    assert audit["at"]
    assert audit["before_lines"] == [{"category": "total", "minutes": 300}]
    assert audit["after_lines"] == [{"category": "total", "minutes": 316}]
    # Earlier audit is unchanged.
    assert week["audits"][0]["after_minutes"] == 300

    missing = client.post(
        "/hr/time/entries",
        headers=_auth(_melissa()),
        json={"week_ending": WEEK, "hours": 1, "minutes": 0, "for_email": "sam@heart4change.org"},
    )
    assert missing.status_code == 200, missing.text
    filled = next(row for row in client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()["audits"] if row["employee_email"] == "sam@heart4change.org")
    assert filled["late"] is True
    assert filled["before_minutes"] is None
    assert filled["after_minutes"] == 60


def test_generic_admin_and_lookalike_cannot_edit_after_cutoff(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client)
    set_clock(FRI_1129)
    submission_id = _enter(client, _jordan(), 2, 0).json()["submission_id"]
    set_clock(FRI_1130)
    monkeypatch.setenv("ATTUNE_HR_ADMIN_EMAILS", "msmith@heart4change.com")
    lookalike = _named("user-lookalike", "Not Melissa", "practice-a", "msmith@heart4change.com")
    denied = client.patch(
        f"/hr/time/entries/{submission_id}",
        headers=_auth(lookalike),
        json={"hours": 3, "minutes": 0},
    )
    assert denied.status_code == 403
    untouched = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    jordan = next(row for row in untouched["rows"] if row["email"] == "jordan@heart4change.org")
    assert jordan["raw_minutes"] == 120


def test_settings_do_not_unlock_or_grant_correction_or_rewrite_the_locked_week(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client)
    set_clock(FRI_1130)
    client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice())
    changed = _save_settings(
        client,
        participant_emails=["jordan@heart4change.org", "evan@heart4change.org"],
        summary_time="16:00",
        summary_recipient="evan@heart4change.org",
        reminder_first_time="09:07",
    )
    assert changed["corrector_email"] == MELISSA_EMAIL
    assert "does not change who may edit" in changed["corrector_note"]
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    assert week["locked"] is True
    assert "11:30" in week["deadline_label"]
    emails = [row["email"] for row in week["rows"]]
    assert emails == ["jordan@heart4change.org", "sam@heart4change.org"]
    denied = _enter(client, _evan(), 1, 0, for_email="jordan@heart4change.org")
    assert denied.status_code == 403
    allowed = _enter(client, _melissa(), 4, 0, for_email="jordan@heart4change.org")
    assert allowed.status_code == 200, allowed.text


def test_cross_practice_week_csv_and_entry_are_isolated(client: TestClient) -> None:
    _roster()
    riley = clinician_store.get("practice-b", "clin-b-riley")
    morgan = clinician_store.get("practice-b", "clin-b-morgan")
    assert riley and morgan
    riley.email = "riley@example.com"
    riley.display_name = "Riley Chen"
    morgan.email = "adminb@example.com"
    morgan.default_role = "admin"
    admin_b = _named("user-admin-b", "Admin B", "practice-b", "adminb@example.com")
    from app.access.store import access_store

    access_store.set_assignment("practice-b", "adminb@example.com", "admin")
    set_clock(WED)
    _save_settings(client)
    submission_id = _enter(client, _jordan(), 3, 0).json()["submission_id"]
    client.put(
        "/hr/time/settings",
        headers=_auth(admin_b),
        json=_settings(participant_emails=["riley@example.com"]),
    )
    other = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_auth(admin_b))
    assert other.status_code == 200, other.text
    assert [row["email"] for row in other.json()["rows"]] == ["riley@example.com"]
    csv_text = client.get(f"/hr/time/week.csv?week_ending={WEEK}", headers=_auth(admin_b)).text
    assert "Jordan" not in csv_text
    assert "riley@example.com" in csv_text
    missing = client.patch(
        f"/hr/time/entries/{submission_id}",
        headers=_auth(admin_b),
        json={"hours": 1, "minutes": 0},
    )
    assert missing.status_code == 404
    bob = user_store._users["bob"][1]
    assert client.get("/hr/time/week?week_ending=" + WEEK, headers=_auth(bob)).status_code == 403
    assert client.get("/hr/time/settings", headers=_auth(bob)).status_code == 403
    assert client.get("/hr/roster", headers=_auth(_melissa())).status_code == 403


def test_preview_and_test_reminder_never_send(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client)
    _enter(client, _jordan(), 5, 14)
    mailer = RecordingMailer()
    mailer.send = lambda message: (_ for _ in ()).throw(AssertionError("send"))  # type: ignore[method-assign]
    set_mailer_for_tests(mailer)
    preview = client.post(f"/hr/time/settings/preview?week_ending={WEEK}", headers=_alice())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["sent"] is False
    assert "Submitted: 1. Missing: 1." in body["body"]
    assert f"https://clynotion.com/#time-admin?week={WEEK}&v=2" in body["body"]
    assert body["csv"] == ""
    assert "Monday, March 9, 2026" in body["body"]
    assert "Friday, March 13, 2026" in body["body"]
    reminder = client.post(
        "/hr/time/settings/test-reminder",
        headers=_alice(),
        json={"slot": "due", "week_ending": WEEK},
    )
    assert reminder.status_code == 200, reminder.text
    payload = reminder.json()
    assert payload["sent"] is False
    assert [item["to"] for item in payload["messages"]] == ["sam@heart4change.org"]
    assert payload["messages"][0]["subject"] == REMINDER_DUE_SUBJECT
    assert "already worked" in payload["messages"][0]["body"]
    assert TIME_ENTRY_URL in payload["messages"][0]["body"]
    assert "Monday, March 9, 2026" in payload["messages"][0]["body"]
    assert "Friday, March 13, 2026" in payload["messages"][0]["body"]
    assert "Saturday, March 14, 2026" in payload["messages"][0]["body"]
    assert "Sunday, March 15, 2026" in payload["messages"][0]["body"]
    assert "expect to work Friday" not in payload["messages"][0]["body"]
    first = client.post(
        "/hr/time/settings/test-reminder",
        headers=_alice(),
        json={"slot": "first", "week_ending": WEEK},
    ).json()
    assert {item["to"] for item in first["messages"]} == {
        "jordan@heart4change.org",
        "sam@heart4change.org",
    }
    assert first["messages"][0]["subject"] == REMINDER_FIRST_SUBJECT


def test_reminders_honor_exact_minutes_and_skip_submitted_including_zero(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client, reminder_first_time="09:07")
    _enter(client, _jordan(), 0, 0)
    mailer = RecordingMailer()
    set_mailer_for_tests(mailer)
    set_clock(datetime(2026, 3, 18, 13, 6, tzinfo=timezone.utc))  # Wed 09:06 EDT
    early = run_tick(mailer=mailer, now=datetime(2026, 3, 18, 13, 6, tzinfo=timezone.utc))
    assert early["sent"] == 0
    due = run_tick(mailer=mailer, now=datetime(2026, 3, 18, 13, 7, tzinfo=timezone.utc))
    assert due["sent"] == 2
    assert {message.to for message in mailer.sent} == {
        "jordan@heart4change.org",
        "sam@heart4change.org",
    }
    assert all(message.subject == REMINDER_FIRST_SUBJECT for message in mailer.sent)
    assert all("already worked" in message.text for message in mailer.sent)
    assert all(TIME_ENTRY_URL in message.text for message in mailer.sent)

    mailer.sent.clear()
    friday = run_tick(mailer=mailer, now=FRI_0800)
    assert friday["sent"] == 1
    assert mailer.sent[0].to == "sam@heart4change.org"
    assert mailer.sent[0].subject == REMINDER_DUE_SUBJECT
    again = run_tick(mailer=mailer, now=FRI_0800)
    assert again["sent"] == 0
    assert len(mailer.sent) == 1


def test_summary_is_practice_isolated_and_melissa_edits_do_not_send_mail(client: TestClient) -> None:
    _roster()
    riley = clinician_store.get("practice-b", "clin-b-riley")
    morgan = clinician_store.get("practice-b", "clin-b-morgan")
    assert riley and morgan
    riley.email = "riley@example.com"
    riley.display_name = "Riley Chen"
    morgan.email = "adminb@example.com"
    morgan.default_role = "admin"
    admin_b = _named("user-admin-b", "Admin B", "practice-b", "adminb@example.com")
    from app.access.store import access_store

    access_store.set_assignment("practice-b", "adminb@example.com", "admin")
    set_clock(WED)
    _save_settings(client)
    time_store.save_settings(admin_b, _settings(participant_emails=["riley@example.com"]), WED)
    _enter(client, _jordan(), 5, 1)
    mailer = RecordingMailer()
    set_mailer_for_tests(mailer)
    result = run_tick(mailer=mailer, now=FRI_1130)
    summaries = [message for message in mailer.sent if message.subject.startswith("Payroll hours are ready")]
    assert result["sent"] == 2
    assert len(summaries) == 2
    practice_a = next(message for message in summaries if "Submitted: 1." in message.text)
    practice_b = next(message for message in summaries if "Submitted: 0." in message.text)
    assert "Riley" not in practice_a.text
    assert "Jordan" not in practice_b.text
    assert f"week={WEEK}&v=2" in practice_a.text
    assert practice_a.csv_text == ""
    assert practice_b.csv_text == ""
    before = len(mailer.sent)
    patched = client.patch(
        f"/hr/time/entries/{_submission_id(client)}",
        headers=_auth(_melissa()),
        json={"hours": 5, "minutes": 16},
    )
    assert patched.status_code == 200
    assert len(mailer.sent) == before


def _submission_id(client: TestClient) -> str:
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    return next(row["submission_id"] for row in week["rows"] if row["email"] == "jordan@heart4change.org")


def test_parallel_ticks_claim_once_and_uncertain_is_not_retried() -> None:
    _roster()
    set_clock(THU_0900)
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), THU_0900)
    started = threading.Event()
    release = threading.Event()

    def slow(_message: OutboundMail) -> MailResult:
        started.set()
        assert release.wait(5)
        return MailResult(status="sent", detail="ok", provider_id="once")

    mailer = RecordingMailer(slow)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            run_tick(mailer=mailer, now=THU_0900)
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    first = threading.Thread(target=_run)
    second = threading.Thread(target=_run)
    first.start()
    assert started.wait(5)
    second.start()
    release.set()
    first.join(5)
    second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert errors == []
    assert len(mailer.sent) == 1

    uncertain = RecordingMailer(MailResult(status="uncertain", detail="gmail_uncertain"))
    time_store.reset()
    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), THU_0900)
    assert run_tick(mailer=uncertain, now=THU_0900)["uncertain"] == 1
    assert run_tick(mailer=uncertain, now=THU_0900 + timedelta(hours=2))["sent"] == 0
    assert len(uncertain.sent) == 1


def _alice_user() -> User:
    return user_store._users["alice"][1]


def test_failed_sends_retry_with_backoff_then_stop_and_not_ready_does_not_burn_the_budget() -> None:
    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), WED)
    calls = {"n": 0}

    def fail(_message: OutboundMail) -> MailResult:
        calls["n"] += 1
        return MailResult(status="failed", detail="gmail_http_500")

    mailer = RecordingMailer(fail)
    moment = THU_0900
    assert run_tick(mailer=mailer, now=moment)["failed"] == 1
    assert run_tick(mailer=mailer, now=moment + timedelta(minutes=6))["failed"] == 1
    assert run_tick(mailer=mailer, now=moment + timedelta(hours=1))["failed"] == 1
    stopped = run_tick(mailer=mailer, now=moment + timedelta(hours=3))
    assert stopped["sent"] == 0 and stopped["failed"] == 0
    assert calls["n"] == 3

    time_store.reset()
    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), WED)
    calls["n"] = 0

    def not_ready(message: OutboundMail) -> MailResult:
        calls["n"] += 1
        if calls["n"] < 5:
            return MailResult(status="not_ready", detail="gmail_token_http_401")
        return MailResult(status="sent", detail="ok", provider_id="late")

    retry = RecordingMailer(not_ready)
    now = THU_0900
    for _ in range(4):
        run_tick(mailer=retry, now=now)
        now = now + timedelta(minutes=6)
    done = run_tick(mailer=retry, now=now)
    assert done["sent"] == 1
    assert calls["n"] == 5


def test_expired_claim_can_be_taken_and_a_stale_finish_does_not_overwrite_it() -> None:
    from app.hr.time_entry import _iso

    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), WED)
    first = time_store.claim_due_jobs(
        THU_0900,
        lease=timedelta(seconds=120),
        backoff=(timedelta(minutes=5),),
        max_attempts=3,
    )
    assert len(first.jobs) == 1
    job = first.jobs[0]
    with time_store.exclusive():
        delivery = time_store._deliveries[job.delivery_id]
        delivery.claim_expires_at = _iso(THU_0900 - timedelta(minutes=1))
    second = time_store.claim_due_jobs(
        THU_0900,
        lease=timedelta(seconds=120),
        backoff=(timedelta(minutes=5),),
        max_attempts=3,
    )
    assert second.jobs == []
    assert time_store.finish_delivery(job.delivery_id, job.claim_token, MailResult("sent", "ok"), THU_0900) == "stale"
    delivery = next(iter(time_store._deliveries.values()))
    assert delivery.status == "uncertain"
    assert delivery.last_error == "claim_expired_unconfirmed"
    mailer = RecordingMailer()
    again = run_tick(mailer=mailer, now=THU_0900 + timedelta(hours=1))
    assert again["sent"] == 0
    assert mailer.sent == []
    week = time_store.week_view(_alice_user(), WEEK, THU_0900 + timedelta(hours=1))
    note = next(item for item in week["notifications"] if item["status"] == "uncertain")
    assert note["last_error"] == "claim_expired_unconfirmed"


def test_mailer_exception_is_uncertain_and_unconfigured_mail_does_not_consume_the_week() -> None:
    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), WED)

    def explode(_message: OutboundMail) -> MailResult:
        raise RuntimeError("provider blew up")

    mailer = RecordingMailer(explode)
    assert run_tick(mailer=mailer, now=THU_0900)["uncertain"] == 1
    assert run_tick(mailer=mailer, now=THU_0900 + timedelta(hours=1))["claimed"] == 0

    time_store.reset()
    _roster()
    time_store.save_settings(_alice_user(), _settings(participant_emails=["jordan@heart4change.org"]), WED)
    blocked = RecordingMailer()
    blocked.set_ready("sa_missing")
    assert run_tick(mailer=blocked, now=THU_0900)["reason"] == "sa_missing"
    assert blocked.sent == []
    blocked.set_ready(None)
    assert run_tick(mailer=blocked, now=THU_0900)["sent"] == 1


def test_tick_endpoint_requires_the_secret_and_does_not_echo_it(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "cron-secret-do-not-echo"
    missing = client.post("/internal/time/tick")
    assert missing.status_code == 503
    assert secret not in missing.text
    monkeypatch.setenv("CRON_SECRET", secret)
    wrong = client.post("/internal/time/tick", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert secret not in wrong.text
    ok = client.post("/internal/time/tick", headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["mail"] == "configured"
    assert ok.json()["sent"] == 0
    assert secret not in ok.text


def test_catalog_tile_is_live(client: TestClient) -> None:
    catalog = client.get("/catalog", headers=_alice()).json()
    hr = next(unit for unit in catalog if unit["id"] == "hr")
    tile = next(tool for tool in hr["tools"] if tool["id"] == "time_entry")
    assert tile["status"] == "live"
    assert tile["href"] == "/#time-settings"
    assert tile["open_label"] == "Weekly hours settings"


def test_encrypted_file_is_atomic_and_isolated_across_stores(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _roster()
    path = tmp_path / "hr_time.enc"
    store = TimeStore(mode="file", path=path)
    store.save_settings(_alice_user(), _settings(), WED)
    text = path.read_text(encoding="utf-8")
    assert "jordan@heart4change.org" not in text
    assert "Sam Rivera" not in text
    other = TimeStore(mode="file", path=path)
    loaded = other.settings_view(_alice_user(), WED)
    assert loaded["participant_emails"] == ["jordan@heart4change.org", "sam@heart4change.org"]

    original = path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr("app.hr.time_entry.os.replace", boom)
    with pytest.raises(OSError):
        store.save_settings(_alice_user(), _settings(summary_recipient="ops@heart4change.org"), WED)
    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []

    garbage = tmp_path / "bad.enc"
    garbage.write_text("not-a-secret-store", encoding="utf-8")
    broken = TimeStore(mode="file", path=garbage)
    with pytest.raises(TimeStoreError):
        broken.save_settings(_alice_user(), _settings(), WED)
    assert garbage.read_text(encoding="utf-8") == "not-a-secret-store"


def test_two_file_stores_serialize_concurrent_saves(tmp_path) -> None:
    _roster()
    path = tmp_path / "hours.enc"
    writer = TimeStore(mode="file", path=path)
    writer.save_settings(_alice_user(), _settings(), WED)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def save(hours: int, user: User) -> None:
        try:
            barrier.wait(5)
            store = TimeStore(mode="file", path=path)
            store.save_hours(user, week_ending=WEEK, hours=hours, minutes=0, now=WED)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=save, args=(4, _jordan())),
        threading.Thread(target=save, args=(6, _sam())),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []
    report = TimeStore(mode="file", path=path).week_view(_alice_user(), WEEK, WED)
    totals = {row["email"]: row["raw_minutes"] for row in report["rows"]}
    assert totals == {"jordan@heart4change.org": 240, "sam@heart4change.org": 360}


def test_service_account_errors_hide_the_secret_and_timeouts_are_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(MailConfigError) as exc:
        load_service_account("super-secret-key-material")
    assert exc.value.code == "sa_unreadable"
    assert "super-secret" not in str(exc.value)

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    sa = {"client_email": "hours-bot@example.iam.gserviceaccount.com", "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            assert "PRIVATE" not in request.content.decode()
            return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})
        if "gmail.googleapis.com" in str(request.url):
            assert request.headers["authorization"] == "Bearer ya29.test-token"
            return httpx.Response(200, json={"id": "msg123"})
        return httpx.Response(500)

    monkeypatch.setenv("TIME_MAIL_SENDER", "operations@heart4change.org")
    sender = GmailSender(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        sa_loader=lambda: sa,
    )
    result = sender.send(OutboundMail(to="a@heart4change.org", subject="s", text="body"))
    assert result.status == "sent"
    assert result.provider_id == "msg123"
    assert "ya29" not in result.detail

    def timeout(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})
        raise httpx.ReadTimeout("timed out")

    slow = GmailSender(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(timeout)),
        sa_loader=lambda: sa,
    )
    uncertain = slow.send(OutboundMail(to="a@heart4change.org", subject="s", text="body"))
    assert uncertain.status == "uncertain"
    assert "ya29" not in uncertain.detail

    def refused(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})
        raise httpx.ConnectError("refused")

    down = GmailSender(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(refused)),
        sa_loader=lambda: sa,
    )
    failed = down.send(OutboundMail(to="a@heart4change.org", subject="s", text="body"))
    assert failed.status == "failed"
    assert failed.detail == "gmail_connect"


def test_nav_enables_only_selected_people(client: TestClient) -> None:
    _roster()
    bob = user_store._users["bob"][1]
    hidden = client.get("/catalog", headers=_auth(bob)).json()
    bob_tile = next(tool for unit in hidden if unit["id"] == "hr" for tool in unit["tools"] if tool["id"] == "time_entry")
    assert bob_tile["status"] == "disabled"
    assert bob_tile["href"] == ""
    home = client.get("/home", headers=_auth(bob)).json()
    assert all(tool["id"] != "time_entry" for tool in home["tools"])

    set_clock(WED)
    _save_settings(client, participant_emails=["jordan@heart4change.org"])
    jordan = client.get("/catalog", headers=_auth(_jordan())).json()
    jordan_tile = next(tool for unit in jordan if unit["id"] == "hr" for tool in unit["tools"] if tool["id"] == "time_entry")
    assert jordan_tile["href"] == "/#time"
    assert jordan_tile["status"] == "live"
    sam = client.get("/catalog", headers=_auth(_sam())).json()
    sam_tile = next(tool for unit in sam if unit["id"] == "hr" for tool in unit["tools"] if tool["id"] == "time_entry")
    assert sam_tile["status"] == "disabled"
    denied = _enter(client, _sam(), 1, 0)
    assert denied.status_code == 403
    melissa = client.get("/catalog", headers=_auth(_melissa())).json()
    melissa_tile = next(tool for unit in melissa if unit["id"] == "hr" for tool in unit["tools"] if tool["id"] == "time_entry")
    assert melissa_tile["href"] == "/#time-admin"


def test_directory_refresh_preserves_selection_and_does_not_grant_admin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client, participant_emails=["jordan@heart4change.org"])
    saved = _enter(client, _jordan(), 4, 0)
    assert saved.status_code == 200, saved.text
    monkeypatch.setenv("ATTUNE_WORKSPACE_DIRECTORY", "mock")
    monkeypatch.setenv("GOOGLE_ALLOWED_DOMAINS", "example.com")
    refreshed = client.post("/hr/time/directory/refresh", headers=_alice())
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert body["directory"]["complete"] is True
    assert body["directory"]["status"] == "ok"
    assert "jordan@heart4change.org" in body["participant_emails"]
    selected = {row["email"] for row in body["roster"] if row["selected"]}
    assert selected == {"jordan@heart4change.org"}
    directory_emails = {row["email"] for row in body["roster"] if row["source"] == "directory"}
    assert "jamie.clinician@example.com" in directory_emails
    assert "jamie.clinician@example.com" not in selected
    again = client.post("/hr/time/directory/refresh", headers=_alice()).json()
    assert again["directory"]["listed"] == body["directory"]["listed"]
    assert again["participant_emails"] == body["participant_emails"]
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    jordan = next(row for row in week["rows"] if row["email"] == "jordan@heart4change.org")
    assert jordan["raw_minutes"] == 240
    stranger = _named(
        "user-stranger",
        "Alex Supervisor",
        "practice-a",
        "alex.supervisor@example.com",
    )
    assert client.get("/hr/time/settings", headers=_auth(stranger)).status_code == 403


def test_self_test_is_isolated_from_payroll_and_ignores_cutoff(
    client: TestClient,
) -> None:
    from app.hr.time_mail import RecordingMailer, set_mailer_for_tests

    _roster()
    alice = user_store._users["alice"][1]
    dana = clinician_store.get("practice-a", "clin-a-dana")
    assert dana is not None
    dana.email = "alice@example.com"
    dana.display_name = "Alice Example"
    set_clock(WED)
    _save_settings(client, participant_emails=["alice@example.com", "jordan@heart4change.org"])
    mailer = RecordingMailer(lambda _message: (_ for _ in ()).throw(AssertionError("send")))
    set_mailer_for_tests(mailer)
    set_clock(FRI_1130)
    blocked = _enter(client, alice, 1, 0)
    assert blocked.status_code == 403
    denied = client.get("/hr/time/self-test", headers=_auth(_jordan()))
    assert denied.status_code == 403
    bob = user_store._users["bob"][1]
    assert client.get("/hr/time/self-test", headers=_auth(bob)).status_code == 403
    preview = client.get("/hr/time/self-test", headers=_auth(alice))
    assert preview.status_code == 200, preview.text
    opened = preview.json()
    assert opened["sent"] is False
    assert opened["thursday"]["to"] == "alice@example.com"
    assert opened["thursday"]["subject"].startswith("[TEST] ")
    assert opened["friday"]["subject"].startswith("[TEST] ")
    assert opened["friday"]["to"] == "alice@example.com"
    assert opened["summary"] is None
    assert opened["summary_recipient"] == "msmith@heart4change.org"
    saved = client.put(
        "/hr/time/self-test",
        headers=_auth(alice),
        json={"hours": 5, "minutes": 1},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["sent"] is False
    assert body["entry"]["raw_minutes"] == 301
    assert body["entry"]["decimal_hours"] == "5.25"
    assert body["friday"]["body"]
    assert body["summary"]["to"] == "msmith@heart4change.org"
    assert body["summary"]["sent"] is False
    assert "Alice Example" in body["summary"]["body"]
    assert "Jordan" not in body["summary"]["body"]
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    emails = {row["email"]: row for row in week["rows"]}
    assert emails["alice@example.com"]["status"] == "missing"
    assert emails["alice@example.com"]["raw_minutes"] is None
    assert client.patch(
        "/hr/time/entries/does-not-exist",
        headers=_auth(alice),
        json={"hours": 2, "minutes": 0},
    ).status_code == 404


def test_open_week_uses_live_roster_until_cutoff(client: TestClient) -> None:
    _roster()
    set_clock(WED)
    _save_settings(client, participant_emails=["jordan@heart4change.org"])
    week = client.get(f"/hr/time/week?week_ending={WEEK}", headers=_alice()).json()
    assert [row["email"] for row in week["rows"]] == ["jordan@heart4change.org"]
    assert week["locked"] is False
