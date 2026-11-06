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
    EXPECTED_FRIDAY_HOURS_TEXT,
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

WEEK = "2026-03-13"
WED = datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc)  # Wed 11:00 EDT
THU_0900 = datetime(2026, 3, 12, 13, 0, tzinfo=timezone.utc)  # Thu 09:00 EDT
FRI_0800 = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)  # Fri 08:00 EDT
FRI_1129 = datetime(2026, 3, 13, 15, 29, tzinfo=timezone.utc)
FRI_1130 = datetime(2026, 3, 13, 15, 30, tzinfo=timezone.utc)


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
        "reminder_first_weekday": 3,
        "reminder_first_time": "09:00",
        "reminder_due_weekday": 4,
        "reminder_due_time": "08:00",
        "summary_weekday": 4,
        "summary_time": "11:30",
        "summary_recipient": MELISSA_EMAIL,
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


def test_pay_week_is_saturday_through_friday_including_dst() -> None:
    assert datetime(2026, 1, 9).weekday() == 4
    assert pay_week_ending(datetime(2026, 1, 10, 4, 59, tzinfo=timezone.utc)).isoformat() == "2026-01-09"
    assert pay_week_ending(datetime(2026, 1, 10, 5, 0, tzinfo=timezone.utc)).isoformat() == "2026-01-16"

    # Spring forward Sunday 2026-03-08. Saturday midnight is still EST (UTC-5).
    assert datetime(2026, 3, 8).weekday() == 6
    assert pay_week_ending(datetime(2026, 3, 7, 4, 59, tzinfo=timezone.utc)).isoformat() == "2026-03-06"
    assert pay_week_ending(datetime(2026, 3, 7, 5, 0, tzinfo=timezone.utc)).isoformat() == "2026-03-13"
    from app.hr.time_entry import TimeSettings

    spring_lock = deadline_instant(
        pay_week_ending(datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)),
        TimeSettings(practice_id="p", participant_emails=[], summary_weekday=4, summary_time="11:30"),
    )
    assert spring_lock.astimezone(timezone.utc) == datetime(2026, 3, 13, 15, 30, tzinfo=timezone.utc)
    assert datetime(2026, 3, 13, 15, 29, tzinfo=timezone.utc) < spring_lock

    # Fall back Sunday 2026-11-01. Friday lock that week is EST (UTC-5).
    assert datetime(2026, 11, 1).weekday() == 6
    assert pay_week_ending(datetime(2026, 10, 31, 3, 59, tzinfo=timezone.utc)).isoformat() == "2026-10-30"
    assert pay_week_ending(datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc)).isoformat() == "2026-11-06"
    fall_lock = deadline_instant(
        pay_week_ending(datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)),
        TimeSettings(practice_id="p", participant_emails=[], summary_weekday=4, summary_time="11:30"),
    )
    assert fall_lock.astimezone(timezone.utc) == datetime(2026, 11, 6, 16, 30, tzinfo=timezone.utc)


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
    assert EXPECTED_FRIDAY_HOURS_TEXT in me.json()["helper_text"]
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
    assert "5:14" in body["body"] and "5:15" in body["body"] and "5.25" in body["body"]
    assert "Sam Rivera" in body["body"]
    assert f"https://clynotion.com/#time-admin?week={WEEK}" in body["body"]
    assert "submitted,314,315,5.25" in body["csv"]
    reminder = client.post(
        "/hr/time/settings/test-reminder",
        headers=_auth(_melissa()),
        json={"slot": "due", "week_ending": WEEK},
    )
    assert reminder.status_code == 200, reminder.text
    payload = reminder.json()
    assert payload["sent"] is False
    assert [item["to"] for item in payload["messages"]] == ["sam@heart4change.org"]
    assert payload["messages"][0]["subject"] == REMINDER_DUE_SUBJECT
    assert EXPECTED_FRIDAY_HOURS_TEXT in payload["messages"][0]["body"]
    assert TIME_ENTRY_URL in payload["messages"][0]["body"]
    assert "Saturday, March 7, 2026" in payload["messages"][0]["body"]
    assert "Friday, March 13, 2026" in payload["messages"][0]["body"]
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
    set_clock(datetime(2026, 3, 12, 13, 6, tzinfo=timezone.utc))  # Thu 09:06 EDT
    early = run_tick(mailer=mailer, now=datetime(2026, 3, 12, 13, 6, tzinfo=timezone.utc))
    assert early["sent"] == 0
    due = run_tick(mailer=mailer, now=datetime(2026, 3, 12, 13, 7, tzinfo=timezone.utc))
    assert due["sent"] == 2
    assert {message.to for message in mailer.sent} == {
        "jordan@heart4change.org",
        "sam@heart4change.org",
    }
    assert all(message.subject == REMINDER_FIRST_SUBJECT for message in mailer.sent)
    assert all(EXPECTED_FRIDAY_HOURS_TEXT in message.text for message in mailer.sent)
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
    set_clock(WED)
    _save_settings(client)
    time_store.save_settings(admin_b, _settings(participant_emails=["riley@example.com"]), WED)
    _enter(client, _jordan(), 5, 1)
    mailer = RecordingMailer()
    set_mailer_for_tests(mailer)
    result = run_tick(mailer=mailer, now=FRI_1130)
    summaries = [message for message in mailer.sent if message.subject.startswith("Weekly hours summary")]
    assert result["sent"] == 2
    assert len(summaries) == 2
    practice_a = next(message for message in summaries if "Jordan Lee" in message.text)
    practice_b = next(message for message in summaries if "Riley Chen" in message.text)
    assert "Riley" not in practice_a.text
    assert "Jordan" not in practice_b.text
    assert "5:01" in practice_a.text and "5:15" in practice_a.text and "5.25" in practice_a.text
    assert "Sam Rivera" in practice_a.text
    assert "missing,0" not in practice_a.csv_text
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
    assert ok.json()["mail"] == "not_configured"
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
    assert melissa_tile["href"] == "/#time-settings"


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
