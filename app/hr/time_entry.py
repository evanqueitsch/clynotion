"""Weekly hours collector (legacy alias OPS-6).

Hourly staff submit one Saturday–Friday total. Payable minutes round up to the
next 15 on read; raw minutes are stored exactly. After cutoff only Melissa
(msmith@heart4change.org) may fill or correct a week. Summary-recipient
changes do not grant that authority.

No client identifiers, no punch clock, no payroll vendor. Design of record:
docs/modules/ops6-weekly-hours.md.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.auth import User, is_hr_admin
from app.clinicians import clinician_store
from app.crypto import decrypt_utf8, encrypt_utf8
from app.hr.time_mail import MailResult, safe_error_code, safe_provider_id

ET = ZoneInfo("America/New_York")

ENV_PERSIST = "ATTUNE_HR_TIME_PERSISTENCE"
ENV_PATH = "ATTUNE_HR_TIME_DATA_PATH"
DEFAULT_PATH = ".attune_data/hr_time.enc"

MELISSA_EMAIL = "msmith@heart4change.org"
TIME_ENTRY_URL = "https://clynotion.com/#time"
TIME_SELF_TEST_URL = "https://clynotion.com/#time-self-test"
SELF_TEST_BANNER = (
    "THIS IS A SELF-TEST for your signed-in account only. "
    "It is not a payroll submission, it does not change who is enabled, "
    "and it does not change the practice cutoff. "
    "Mail goes out only when you press Send, and each Send runs once."
)
CATEGORY_TOTAL = "total"
KNOWN_CATEGORIES: dict[str, str] = {CATEGORY_TOTAL: "Weekly total"}

# Every minute that exists in a Saturday–Friday week. Not an overtime policy.
MAX_WEEK_MINUTES = 7 * 24 * 60

REMINDER_FIRST_SUBJECT = "Weekly hours are due tomorrow"
REMINDER_DUE_SUBJECT = "Enter your weekly hours today!"
EXPECTED_FRIDAY_HOURS_TEXT = (
    "Include the hours you expect to work Friday afternoon. "
    "The cutoff is before the end of Friday, so enter expected Friday afternoon hours before the deadline. "
    "Discrepancies after cutoff go to Melissa for correction."
)
HOURS_CAP_NOTE = (
    "Weekly totals are capped at 168:00 — every minute in the pay week. "
    "That is a physical limit so a typo cannot store an impossible week. "
    "It is not an overtime or payroll-policy cap; totals through 168:00 are accepted, including hours past 80."
)
CORRECTOR_NOTE = (
    "After cutoff, only msmith@heart4change.org can enter missing hours or correct entries. "
    "Changing the summary recipient does not change who may edit."
)
SCHEDULE_NOTE = (
    "Times use America/New_York, including daylight saving, and are honored to the minute. "
    "The scheduler must run every minute. A 15-minute poll would miss other minutes."
)

MAX_DELIVERY_ATTEMPTS = 3
DEFAULT_LEASE = timedelta(seconds=120)
DEFAULT_RETRY_BACKOFF = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=45),
)

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_now_override: Optional[datetime] = None


class TimeAuthzError(PermissionError):
    """Caller is authenticated but may not perform this hours action."""


class NotFoundError(LookupError):
    """Resource is missing or not in this practice. Do not distinguish which."""


class TimeStoreError(RuntimeError):
    """Encrypted store could not be read. The file is left untouched."""

    def __init__(self) -> None:
        super().__init__(
            "Weekly hours store could not be read. It was not overwritten."
        )


def utcnow() -> datetime:
    if _now_override is not None:
        if _now_override.tzinfo is None:
            raise RuntimeError("clock must be timezone-aware")
        return _now_override
    return datetime.now(timezone.utc)


def set_clock(moment: Optional[datetime]) -> None:
    global _now_override
    _now_override = moment


def is_melissa(user: User) -> bool:
    return _email(user.email) == MELISSA_EMAIL


def can_manage_time(user: User) -> bool:
    """HR admins (including Evan) and Melissa. Melissa is not implied by HR admin."""
    return is_hr_admin(user) or is_melissa(user)


def pay_week_ending(moment: datetime) -> date:
    """Friday that closes the Saturday–Friday pay week containing ``moment`` (ET)."""
    local = moment.astimezone(ET)
    weekday = local.weekday()  # Monday=0 … Sunday=6, Friday=4
    if weekday <= 4:
        delta = 4 - weekday
    else:
        delta = 4 - weekday + 7
    return local.date() + timedelta(days=delta)


def pay_week_start(week_ending: date) -> date:
    _require_friday(week_ending)
    return week_ending - timedelta(days=6)


def round_payable_minutes(raw_minutes: int) -> int:
    """Round raw minutes up to the next 15. Exact quarters stay put. 0 stays 0."""
    if raw_minutes < 0:
        raise ValueError("minutes cannot be negative")
    return ((raw_minutes + 14) // 15) * 15


def decimal_hours(rounded_minutes: int) -> str:
    return f"{rounded_minutes / 60:.2f}"


def clock_display(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}:{mins:02d}"


def combine_hours_minutes(hours: int, minutes: int) -> int:
    if isinstance(hours, bool) or not isinstance(hours, int):
        raise ValueError("hours must be a whole number")
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("minutes must be a whole number")
    if hours < 0 or minutes < 0 or minutes > 59:
        raise ValueError("Enter whole hours and minutes from 0 to 59.")
    total = hours * 60 + minutes
    if total > MAX_WEEK_MINUTES:
        raise ValueError(
            "Weekly total cannot exceed 168:00 (every minute in the pay week). "
            "This is a physical cap, not an overtime policy."
        )
    return total


def normalize_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate category lines. v1 accepts only ``total``; the shape is the extension point."""
    if not lines:
        raise ValueError("at least one category line is required")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        category = str(line.get("category") or "")
        if category not in KNOWN_CATEGORIES:
            raise ValueError(f"unknown hours category: {category}")
        if category in seen:
            raise ValueError(f"duplicate category: {category}")
        seen.add(category)
        minutes = line.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
            raise ValueError("category minutes must be a non-negative integer")
        out.append({"category": category, "minutes": minutes})
    if sum(item["minutes"] for item in out) > MAX_WEEK_MINUTES:
        raise ValueError(
            "Weekly total cannot exceed 168:00 (every minute in the pay week)."
        )
    return out


def total_line(raw_minutes: int) -> list[dict[str, Any]]:
    return normalize_lines([{"category": CATEGORY_TOTAL, "minutes": raw_minutes}])


def line_minutes(lines: list[dict[str, Any]]) -> int:
    return sum(int(line["minutes"]) for line in lines)


def admin_url(week_ending: str) -> str:
    return f"https://clynotion.com/#time-admin?week={week_ending}"


def week_range_label(week_ending: date) -> str:
    start = pay_week_start(week_ending)
    return f"{_long_date(start)} – {_long_date(week_ending)}"


def deadline_label(moment: datetime) -> str:
    local = moment.astimezone(ET)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{_long_date(local.date())} at {hour}:{local.minute:02d} {suffix} ET"


def parse_hhmm(value: str) -> tuple[int, int]:
    match = _HHMM.fullmatch((value or "").strip())
    if not match:
        raise ValueError("Time must be HH:MM in 24-hour form (any minute).")
    return int(match.group(1)), int(match.group(2))


def parse_week_ending(raw: str) -> date:
    try:
        day = date.fromisoformat((raw or "")[:10])
    except ValueError as e:
        raise ValueError("week_ending must be a Friday (YYYY-MM-DD).") from e
    _require_friday(day)
    return day


def date_for_weekday(week_ending: date, weekday: int) -> date:
    """Map Python weekday (Monday=0) onto the Saturday–Friday pay week."""
    _require_friday(week_ending)
    if weekday < 0 or weekday > 6:
        raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).")
    saturday = week_ending - timedelta(days=6)
    return saturday + timedelta(days=(weekday - 5) % 7)


def at_local(day: date, hhmm: str) -> datetime:
    """Wall time in America/New_York.

    Spring-forward gaps and fall-back overlaps follow zoneinfo (fold=0, the
    first occurrence). Default reminder and cutoff times are not in those hours.
    """
    hour, minute = parse_hhmm(hhmm)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def deadline_instant(week_ending: date, settings: "TimeSettings") -> datetime:
    day = date_for_weekday(week_ending, settings.summary_weekday)
    return at_local(day, settings.summary_time)


def schedule_warnings(settings: "TimeSettings", week_ending: date) -> list[str]:
    lock_at = deadline_instant(week_ending, settings)
    first_at = at_local(
        date_for_weekday(week_ending, settings.reminder_first_weekday),
        settings.reminder_first_time,
    )
    due_at = at_local(
        date_for_weekday(week_ending, settings.reminder_due_weekday),
        settings.reminder_due_time,
    )
    warnings: list[str] = []
    if first_at >= lock_at:
        warnings.append("The first reminder is at or after cutoff, so it will not send.")
    if due_at >= lock_at:
        warnings.append("The day-of reminder is at or after cutoff, so it will not send.")
    if due_at < first_at:
        warnings.append("The day-of reminder is earlier than the first reminder.")
    return warnings


@dataclass
class TimeSettings:
    practice_id: str
    participant_emails: list[str]
    reminder_first_weekday: int = 3  # Thursday
    reminder_first_time: str = "09:00"
    reminder_due_weekday: int = 4  # Friday
    reminder_due_time: str = "08:00"
    summary_weekday: int = 4
    summary_time: str = "11:30"
    summary_recipient: str = MELISSA_EMAIL
    active_from_week: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "practice_id": self.practice_id,
            "participant_emails": list(self.participant_emails),
            "reminder_first_weekday": self.reminder_first_weekday,
            "reminder_first_time": self.reminder_first_time,
            "reminder_due_weekday": self.reminder_due_weekday,
            "reminder_due_time": self.reminder_due_time,
            "summary_weekday": self.summary_weekday,
            "summary_time": self.summary_time,
            "summary_recipient": self.summary_recipient,
            "active_from_week": self.active_from_week,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TimeSettings":
        return cls(
            practice_id=str(data["practice_id"]),
            participant_emails=[str(e).strip().lower() for e in data.get("participant_emails") or []],
            reminder_first_weekday=int(data.get("reminder_first_weekday", 3)),
            reminder_first_time=str(data.get("reminder_first_time") or "09:00"),
            reminder_due_weekday=int(data.get("reminder_due_weekday", 4)),
            reminder_due_time=str(data.get("reminder_due_time") or "08:00"),
            summary_weekday=int(data.get("summary_weekday", 4)),
            summary_time=str(data.get("summary_time") or "11:30"),
            summary_recipient=str(data.get("summary_recipient") or MELISSA_EMAIL).strip().lower(),
            active_from_week=str(data.get("active_from_week") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


@dataclass
class WeekState:
    practice_id: str
    week_ending: str
    locked_at: str = ""
    snapshot: list[dict[str, str]] = field(default_factory=list)
    summary_recipient: str = ""
    lock_deadline: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "practice_id": self.practice_id,
            "week_ending": self.week_ending,
            "locked_at": self.locked_at,
            "snapshot": [dict(row) for row in self.snapshot],
            "summary_recipient": self.summary_recipient,
            "lock_deadline": self.lock_deadline,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WeekState":
        snapshot = []
        for row in data.get("snapshot") or []:
            if not isinstance(row, dict):
                continue
            snapshot.append(
                {
                    "email": str(row.get("email") or "").strip().lower(),
                    "display_name": str(row.get("display_name") or ""),
                    "clinician_id": str(row.get("clinician_id") or ""),
                }
            )
        return cls(
            practice_id=str(data["practice_id"]),
            week_ending=str(data["week_ending"]),
            locked_at=str(data.get("locked_at") or ""),
            snapshot=snapshot,
            summary_recipient=str(data.get("summary_recipient") or "").strip().lower(),
            lock_deadline=str(data.get("lock_deadline") or ""),
        )


@dataclass
class Submission:
    submission_id: str
    practice_id: str
    week_ending: str
    email: str
    display_name: str
    clinician_id: str
    lines: list[dict[str, Any]]
    created_at: str
    updated_at: str
    updated_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "practice_id": self.practice_id,
            "week_ending": self.week_ending,
            "email": self.email,
            "display_name": self.display_name,
            "clinician_id": self.clinician_id,
            "lines": [dict(line) for line in self.lines],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Submission":
        return cls(
            submission_id=str(data["submission_id"]),
            practice_id=str(data["practice_id"]),
            week_ending=str(data["week_ending"]),
            email=str(data.get("email") or "").strip().lower(),
            display_name=str(data.get("display_name") or ""),
            clinician_id=str(data.get("clinician_id") or ""),
            lines=normalize_lines(list(data.get("lines") or [])),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            updated_by=str(data.get("updated_by") or ""),
        )


@dataclass
class Delivery:
    delivery_id: str
    practice_id: str
    week_ending: str
    kind: str
    recipient: str
    status: str
    claim_token: str = ""
    claimed_at: str = ""
    claim_expires_at: str = ""
    attempts: int = 0
    unlimited_retry: bool = False
    last_error: str = ""
    sent_at: str = ""
    updated_at: str = ""
    provider_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "practice_id": self.practice_id,
            "week_ending": self.week_ending,
            "kind": self.kind,
            "recipient": self.recipient,
            "status": self.status,
            "claim_token": self.claim_token,
            "claimed_at": self.claimed_at,
            "claim_expires_at": self.claim_expires_at,
            "attempts": self.attempts,
            "unlimited_retry": self.unlimited_retry,
            "last_error": self.last_error,
            "sent_at": self.sent_at,
            "updated_at": self.updated_at,
            "provider_id": self.provider_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Delivery":
        return cls(
            delivery_id=str(data["delivery_id"]),
            practice_id=str(data["practice_id"]),
            week_ending=str(data["week_ending"]),
            kind=str(data.get("kind") or ""),
            recipient=str(data.get("recipient") or "").strip().lower(),
            status=str(data.get("status") or "failed"),
            claim_token=str(data.get("claim_token") or ""),
            claimed_at=str(data.get("claimed_at") or ""),
            claim_expires_at=str(data.get("claim_expires_at") or ""),
            attempts=int(data.get("attempts") or 0),
            unlimited_retry=bool(data.get("unlimited_retry", False)),
            last_error=safe_error_code(str(data.get("last_error") or ""), fallback=""),
            sent_at=str(data.get("sent_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            provider_id=safe_provider_id(str(data.get("provider_id") or "")),
        )


@dataclass(frozen=True)
class ClaimedJob:
    delivery_id: str
    claim_token: str
    to: str
    subject: str
    body: str
    csv_filename: str = ""
    csv_text: str = ""


@dataclass
class ClaimBatch:
    jobs: list[ClaimedJob]
    locked_weeks: int


def default_settings(practice_id: str, now: datetime) -> TimeSettings:
    moment = _iso(now)
    return TimeSettings(
        practice_id=practice_id,
        participant_emails=[],
        active_from_week=pay_week_ending(now).isoformat(),
        created_at=moment,
        updated_at=moment,
    )


class TimeStore:
    """Encrypted hours store.

    File mode reloads under an exclusive flock and writes via temp file +
    fsync + os.replace. That serializes processes on one machine (the Fly
    volume is a single writer). It is not a multi-host consensus lock.
    Memory mode uses a thread lock and is the test/dev default.
    """

    def __init__(self, *, mode: Optional[str] = None, path: Optional[os.PathLike[str] | str] = None) -> None:
        chosen = (mode if mode is not None else (os.environ.get(ENV_PERSIST) or "memory")).strip().lower()
        if chosen not in ("memory", "file"):
            raise RuntimeError(f"Unknown {ENV_PERSIST}={chosen!r}; use memory|file")
        self._mode = chosen
        raw_path = path if path is not None else (os.environ.get(ENV_PATH) or DEFAULT_PATH)
        self._path = Path(raw_path)
        self._thread_lock = threading.Lock()
        self._clear()

    def reset(self) -> None:
        with self.exclusive():
            self._clear()
            self._dirty = True
            self._flush_unlocked()

    def probe_readable(self) -> None:
        """Load the file if it exists. Do not create or replace it."""
        if self._mode != "file":
            return
        if not self._path.is_file():
            return
        with self.exclusive():
            return

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Serialize writers and flush dirty state even when the body raises.

        File mode reloads from disk after the lock is held so a peer process's
        commit is visible. A failed decrypt raises before any flush.
        """
        if self._mode != "file":
            with self._thread_lock:
                try:
                    yield
                finally:
                    self._flush_if_dirty()
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        handle = open(lock_path, "a+")
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            self._load_from_disk()
            try:
                yield
            finally:
                self._flush_if_dirty()
        finally:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def settings_view(self, user: User, now: Optional[datetime] = None) -> dict[str, Any]:
        self._require_manager(user)
        moment = now or utcnow()
        with self.exclusive():
            saved = self._settings.get(user.practice_id)
            settings = saved or default_settings(user.practice_id, moment)
            if saved is not None:
                self._apply_scope_locks(user.practice_id, saved, moment)
                self._flush_if_dirty()
            payload = self._settings_public(settings, moment, saved=saved is not None)
            payload["can_self_test"] = self._is_self_tester(user)
            return payload

    def save_settings(self, user: User, patch: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
        self._require_manager(user)
        moment = now or utcnow()
        with self.exclusive():
            existing = self._settings.get(user.practice_id)
            # Lock under the previous schedule before applying edits, so a later
            # cutoff cannot reopen a week whose deadline already passed.
            if existing is not None:
                self._apply_scope_locks(user.practice_id, existing, moment)
            updated = self._merge_settings(user.practice_id, existing, patch, moment)
            self._settings[user.practice_id] = updated
            self._dirty = True
            self._apply_scope_locks(user.practice_id, updated, moment)
            self._flush_if_dirty()
            payload = self._settings_public(updated, moment, saved=True)
            payload["can_self_test"] = self._is_self_tester(user)
            return payload

    def me_view(self, user: User, now: Optional[datetime] = None) -> dict[str, Any]:
        moment = now or utcnow()
        week = pay_week_ending(moment)
        email = _email(user.email)
        with self.exclusive():
            settings = self._settings.get(user.practice_id)
            if settings is not None:
                self._apply_scope_locks(user.practice_id, settings, moment)
                self._flush_if_dirty()
            effective = settings or default_settings(user.practice_id, moment)
            state = self._weeks.get((user.practice_id, week.isoformat()))
            locked = bool(state and state.locked_at)
            entry = self._own_entry(user.practice_id, week.isoformat(), email)
            history = self._history(user.practice_id, email)
            participant = bool(
                settings and any(row["email"] == email for row in self._active_participants(user.practice_id, settings))
            )
            return {
                "practice_id": user.practice_id,
                "email": email,
                "is_participant": participant,
                "can_manage_settings": can_manage_time(user),
                "can_correct_locked": is_melissa(user),
                "can_self_test": self._is_self_tester(user),
                "can_edit": self._can_write(user, email, week, moment, settings, state),
                "helper_text": EXPECTED_FRIDAY_HOURS_TEXT,
                "pay_week_note": "Pay week is Saturday through Friday and is paid that Friday. Times are America/New_York.",
                "hours_cap_note": HOURS_CAP_NOTE,
                "categories": [
                    {"id": key, "label": label} for key, label in KNOWN_CATEGORIES.items()
                ],
                "week": self._week_header(effective, week, state),
                "entry": entry,
                "history": history,
                "locked": locked,
            }

    def save_hours(
        self,
        user: User,
        *,
        week_ending: str,
        hours: int,
        minutes: int,
        for_email: str = "",
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        moment = now or utcnow()
        week = parse_week_ending(week_ending)
        raw = combine_hours_minutes(hours, minutes)
        lines = total_line(raw)
        target = _email(for_email) or _email(user.email)
        if not target:
            raise ValueError("An email is required to store hours.")
        with self.exclusive():
            settings = self._settings.get(user.practice_id)
            if settings is not None:
                self._apply_scope_locks(user.practice_id, settings, moment)
            state = self._weeks.get((user.practice_id, week.isoformat()))
            self._authorize_write(user, target, week, moment, settings, state)
            display_name, clinician_id = self._identity(user, target, settings, state)
            key = (user.practice_id, week.isoformat(), target)
            existing_id = self._submission_index.get(key)
            late = bool(state and state.locked_at)
            if existing_id:
                sub = self._submissions[existing_id]
                before_lines = [dict(line) for line in sub.lines]
                before_minutes = line_minutes(before_lines)
                if before_minutes == raw:
                    self._flush_if_dirty()
                    return self._entry_public(sub)
                sub.lines = lines
                sub.display_name = display_name or sub.display_name
                sub.updated_at = _iso(moment)
                sub.updated_by = user.user_id
                self._append_audit(
                    sub,
                    user=user,
                    now=moment,
                    late=late,
                    before_lines=before_lines,
                    before_minutes=before_minutes,
                )
            else:
                sub = Submission(
                    submission_id=str(uuid4()),
                    practice_id=user.practice_id,
                    week_ending=week.isoformat(),
                    email=target,
                    display_name=display_name or target,
                    clinician_id=clinician_id,
                    lines=lines,
                    created_at=_iso(moment),
                    updated_at=_iso(moment),
                    updated_by=user.user_id,
                )
                self._submissions[sub.submission_id] = sub
                self._submission_index[key] = sub.submission_id
                self._append_audit(
                    sub,
                    user=user,
                    now=moment,
                    late=late,
                    before_lines=None,
                    before_minutes=None,
                )
            self._dirty = True
            self._flush_if_dirty()
            return self._entry_public(sub)

    def patch_hours(
        self,
        user: User,
        submission_id: str,
        *,
        hours: int,
        minutes: int,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        moment = now or utcnow()
        with self.exclusive():
            sub = self._submissions.get(submission_id)
            if sub is None or sub.practice_id != user.practice_id:
                raise NotFoundError()
            return self._save_existing(user, sub, hours, minutes, moment)

    def week_view(self, user: User, week_ending: str, now: Optional[datetime] = None) -> dict[str, Any]:
        self._require_manager(user)
        moment = now or utcnow()
        week = parse_week_ending(week_ending)
        with self.exclusive():
            self._quarantine_expired_claims(moment)
            settings = self._settings.get(user.practice_id)
            if settings is not None:
                self._apply_scope_locks(user.practice_id, settings, moment)
            report = self._report_unlocked(user.practice_id, week, moment, settings)
            report["can_correct"] = is_melissa(user)
            self._flush_if_dirty()
            return report

    def week_csv(self, user: User, week_ending: str, now: Optional[datetime] = None) -> str:
        report = self.week_view(user, week_ending, now)
        return rows_to_csv(report["rows"])

    def preview_summary(self, user: User, week_ending: str, now: Optional[datetime] = None) -> dict[str, Any]:
        report = self.week_view(user, week_ending, now)
        subject, body, csv_text, filename = render_summary(report)
        return {
            "sent": False,
            "subject": subject,
            "body": body,
            "csv": csv_text,
            "csv_filename": filename,
            "recipient": report["summary_recipient"],
            "rows": report["rows"],
            "missing": report["missing"],
        }

    def preview_reminder(
        self,
        user: User,
        *,
        slot: str,
        week_ending: str,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        self._require_manager(user)
        if slot not in ("first", "due"):
            raise ValueError("slot must be first or due.")
        moment = now or utcnow()
        week = parse_week_ending(week_ending) if week_ending else pay_week_ending(moment)
        with self.exclusive():
            settings = self._settings.get(user.practice_id) or default_settings(user.practice_id, moment)
            if user.practice_id in self._settings:
                self._apply_scope_locks(user.practice_id, settings, moment)
                self._flush_if_dirty()
            report = self._report_unlocked(user.practice_id, week, moment, self._settings.get(user.practice_id))
        messages = []
        for row in report["rows"]:
            if slot == "due" and row["status"] != "missing":
                continue
            subject, body = render_reminder(
                slot=slot,
                week_ending=week,
                deadline=_parse_iso(report["deadline"]),
            )
            messages.append({"to": row["email"], "subject": subject, "body": body})
        return {"sent": False, "slot": slot, "messages": messages}

    def _is_self_tester(self, user: User) -> bool:
        """Enabled HR admin, own account only. Directory membership is not an admin grant."""
        if not can_manage_time(user):
            return False
        email = _email(user.email)
        settings = self._settings.get(user.practice_id)
        if not email or settings is None:
            return False
        return any(row["email"] == email for row in self._active_participants(user.practice_id, settings))

    def self_test_view(self, user: User, now: Optional[datetime] = None) -> dict[str, Any]:
        moment = now or utcnow()
        with self.exclusive():
            self._require_self_tester(user)
            return self._self_test_public(user, moment)

    def save_self_test_hours(
        self,
        user: User,
        *,
        hours: int,
        minutes: int,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Store hours on the self-test only. Ignores the payroll-week cutoff."""
        moment = now or utcnow()
        raw = combine_hours_minutes(hours, minutes)
        with self.exclusive():
            self._require_self_tester(user)
            email = _email(user.email)
            display_name, _clinician_id = self._name_for_email(user.practice_id, email)
            key = (user.practice_id, user.user_id)
            prior = self._self_tests.get(key) or {}
            self._self_tests[key] = {
                "practice_id": user.practice_id,
                "user_id": user.user_id,
                "email": email,
                "display_name": display_name or user.username or email,
                "lines": total_line(raw),
                "updated_at": _iso(moment),
                "label": "self_test",
                "sends": dict(prior.get("sends") or {}),
            }
            self._dirty = True
            payload = self._self_test_public(user, moment)
            self._flush_if_dirty()
            return payload

    def send_self_test(
        self,
        user: User,
        kind: str,
        transport: Any,
        now: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """Send one labelled self-test message. A sent or uncertain result is not repeated."""
        if kind not in ("thursday", "friday", "summary"):
            raise ValueError("kind must be thursday, friday, or summary.")
        moment = now or utcnow()
        with self.exclusive():
            self._require_self_tester(user)
            message, blocked = self._prepare_self_test_send(user, kind, moment)
            if blocked is not None:
                return blocked
            assert message is not None
            token = str(uuid4())
            key = (user.practice_id, user.user_id)
            record = dict(self._self_tests.get(key) or {})
            sends = dict(record.get("sends") or {})
            sends[kind] = {
                "status": "sending",
                "claim_token": token,
                "claim_expires_at": _iso(moment + timedelta(seconds=120)),
                "to": message.to,
                "at": _iso(moment),
            }
            record["sends"] = sends
            record.setdefault("practice_id", user.practice_id)
            record.setdefault("user_id", user.user_id)
            record.setdefault("email", _email(user.email))
            record.setdefault("label", "self_test")
            self._self_tests[key] = record
            self._dirty = True
            self._flush_if_dirty()
        try:
            result = transport(message)
        except Exception as exc:
            # The sender reports an ambiguous network result itself. An
            # exception here means Gmail was not called.
            print("self_test_send_error", {"phase": "before_response", "type": type(exc).__name__}, flush=True)
            from app.hr.time_mail import MailResult

            result = MailResult(status="failed", detail="send_not_attempted")
        with self.exclusive():
            key = (user.practice_id, user.user_id)
            record = dict(self._self_tests.get(key) or {})
            sends = dict(record.get("sends") or {})
            slot = dict(sends.get(kind) or {})
            if slot.get("claim_token") != token or slot.get("status") != "sending":
                payload = self._self_test_public(user, moment)
                payload["send_result"] = {"kind": kind, "status": "stale", "duplicate": True}
                return payload
            status = getattr(result, "status", "uncertain")
            if status == "sent":
                slot["status"] = "sent"
                slot["sent_at"] = _iso(moment)
                slot["last_error"] = ""
                slot["gmail_message_id"] = safe_provider_id(str(getattr(result, "provider_id", "") or ""))
            elif status == "uncertain":
                slot["status"] = "uncertain"
                slot["last_error"] = safe_error_code(
                    str(getattr(result, "detail", "") or ""),
                    fallback="gmail_uncertain",
                )
            else:
                slot["status"] = "failed"
                slot["last_error"] = safe_error_code(str(getattr(result, "detail", "") or ""), fallback="mail_failed")
            slot["claim_token"] = ""
            sends[kind] = slot
            record["sends"] = sends
            self._self_tests[key] = record
            self._dirty = True
            payload = self._self_test_public(user, moment)
            payload["send_result"] = {
                "kind": kind,
                "status": slot["status"],
                "to": slot.get("to") or "",
                "duplicate": False,
                "last_error": slot.get("last_error") or "",
                "gmail_message_id": slot.get("gmail_message_id") or "",
            }
            self._flush_if_dirty()
            return payload

    def _prepare_self_test_send(
        self, user: User, kind: str, moment: datetime
    ) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
        """Caller holds the lock. Returns a message, or a finished payload when not sending."""
        from app.hr.time_mail import OutboundMail

        key = (user.practice_id, user.user_id)
        record = dict(self._self_tests.get(key) or {})
        if _email(str(record.get("email") or user.email)) != _email(user.email) and record.get("email"):
            raise TimeAuthzError("Test weekly hours can only be sent for your own account.")
        sends = dict(record.get("sends") or {})
        prior = dict(sends.get(kind) or {})
        status = str(prior.get("status") or "")
        if status in ("sent", "uncertain"):
            payload = self._self_test_public(user, moment)
            payload["send_result"] = {
                "kind": kind,
                "status": status,
                "to": prior.get("to") or "",
                "duplicate": True,
                "last_error": prior.get("last_error") or "",
                "gmail_message_id": prior.get("gmail_message_id") or "",
            }
            return None, payload
        if status == "sending":
            expiry = str(prior.get("claim_expires_at") or "")
            if expiry and _parse_iso(expiry) > moment:
                raise TimeAuthzError("That test email is already being sent.")
            prior["status"] = "uncertain"
            prior["last_error"] = "claim_expired_unconfirmed"
            prior["claim_token"] = ""
            sends[kind] = prior
            record["sends"] = sends
            self._self_tests[key] = record
            self._dirty = True
            self._flush_if_dirty()
            payload = self._self_test_public(user, moment)
            payload["send_result"] = {"kind": kind, "status": "uncertain", "duplicate": True}
            return None, payload
        public = self._self_test_public(user, moment)
        if kind == "summary":
            summary = public.get("summary")
            if not summary:
                raise ValueError("Enter your test hours before sending the summary.")
            message = OutboundMail(
                to=str(summary["to"]),
                subject=str(summary["subject"]),
                text=str(summary["body"]),
                csv_filename=str(summary.get("csv_filename") or ""),
                csv_text=str(summary.get("csv") or ""),
            )
            if message.to != MELISSA_EMAIL:
                raise TimeAuthzError("The test summary can only be addressed to Melissa.")
        else:
            slot = public["thursday" if kind == "thursday" else "friday"]
            if slot["to"] != _email(user.email):
                raise TimeAuthzError("Thursday and Friday test emails go only to your own account.")
            message = OutboundMail(to=slot["to"], subject=slot["subject"], text=slot["body"])
        if not record.get("user_id"):
            record.update(
                {
                    "practice_id": user.practice_id,
                    "user_id": user.user_id,
                    "email": _email(user.email),
                    "display_name": public.get("display_name") or _email(user.email),
                    "label": "self_test",
                    "lines": list((public.get("entry") or {}).get("lines") or []),
                }
            )
            self._self_tests[key] = record
        return message, None

    def _require_self_tester(self, user: User) -> None:
        if not self._is_self_tester(user):
            raise TimeAuthzError(
                "Test weekly hours is only for an HR admin who is enabled for time entry, and only for their own account."
            )

    def _self_test_public(self, user: User, now: datetime) -> dict[str, Any]:
        email = _email(user.email)
        settings = self._settings.get(user.practice_id) or default_settings(user.practice_id, now)
        week = pay_week_ending(now)
        state = self._weeks.get((user.practice_id, week.isoformat()))
        header = self._week_header(settings, week, state)
        deadline = _parse_iso(header["deadline"])
        record = self._self_tests.get((user.practice_id, user.user_id))
        display_name = email
        lines: list[dict[str, Any]] = []
        if record and _email(str(record.get("email") or "")) == email:
            display_name = str(record.get("display_name") or email)
            lines = list(record.get("lines") or [])
        else:
            record = None
        thursday = _labelled_reminder("first", week, deadline)
        friday = _labelled_reminder("due", week, deadline)
        entry = None
        summary = None
        summary_note = "Enter your own hours in this test before the summary preview. Nothing is filled in for you."
        if lines:
            entry = self._entry_from_lines(
                practice_id=user.practice_id,
                week_ending=week.isoformat(),
                email=email,
                display_name=display_name,
                lines=lines,
                updated_at=str((record or {}).get("updated_at") or ""),
            )
            summary = _self_test_summary(entry, header)
            summary["delivery"] = _send_state((record or {}).get("sends", {}).get("summary") if record else None)
            summary_note = "Summary is addressed only to Melissa. Press Send to deliver this test once."
        from app.hr.gmail_oauth import connect_status

        mailbox = connect_status(user.practice_id)
        sender = str(mailbox.get("sender_email") or "")
        sends = dict((record or {}).get("sends") or {})
        thursday_state = _send_state(sends.get("thursday"))
        friday_state = _send_state(sends.get("friday"))
        summary_state = _send_state(sends.get("summary"))
        return {
            "label": "self_test",
            "sent": False,
            "banner": SELF_TEST_BANNER,
            "email": email,
            "display_name": display_name,
            "scheduled_mail": "off",
            "mailbox": mailbox,
            "sender": sender,
            "sender_note": str(mailbox.get("status_detail") or "")
            or (
                f"Verified sending mailbox: {sender}. Scheduled payroll mail stays off."
                if sender
                else "No sending mailbox is connected. Scheduled payroll mail stays off. Connect a heart4change.org mailbox, then press Send."
            ),
            "thursday": {
                "to": email,
                "subject": thursday[0],
                "body": thursday[1],
                "sent": thursday_state["status"] == "sent",
                "delivery": thursday_state,
            },
            "friday": {
                "to": email,
                "subject": friday[0],
                "body": friday[1],
                "sent": friday_state["status"] == "sent",
                "delivery": friday_state,
                "note": "Shown even if you already entered test hours. The real Friday reminder still goes only to people who have not submitted.",
            },
            "entry": entry,
            "summary": summary,
            "summary_note": summary_note,
            "summary_recipient": MELISSA_EMAIL,
            "week": header,
        }

    def _entry_from_lines(
        self,
        *,
        practice_id: str,
        week_ending: str,
        email: str,
        display_name: str,
        lines: list[dict[str, Any]],
        updated_at: str,
    ) -> dict[str, Any]:
        normalized = normalize_lines(lines)
        raw = line_minutes(normalized)
        rounded = round_payable_minutes(raw)
        hours, minutes = divmod(raw, 60)
        return {
            "submission_id": "",
            "practice_id": practice_id,
            "week_ending": week_ending,
            "email": email,
            "display_name": display_name,
            "hours": hours,
            "minutes": minutes,
            "raw_minutes": raw,
            "raw_display": clock_display(raw),
            "rounded_minutes": rounded,
            "rounded_display": clock_display(rounded),
            "decimal_hours": decimal_hours(rounded),
            "lines": normalized,
            "updated_at": updated_at,
            "label": "self_test",
        }

    def apply_due_locks(self, now: Optional[datetime] = None) -> int:
        moment = now or utcnow()
        with self.exclusive():
            count = 0
            for practice_id, settings in list(self._settings.items()):
                count += self._apply_scope_locks(practice_id, settings, moment)
            self._flush_if_dirty()
            return count

    def claim_due_jobs(
        self,
        now: datetime,
        *,
        lease: timedelta,
        backoff: tuple[timedelta, ...],
        max_attempts: int,
    ) -> ClaimBatch:
        with self.exclusive():
            self._quarantine_expired_claims(now)
            locked = 0
            jobs: list[ClaimedJob] = []
            for practice_id, settings in list(self._settings.items()):
                locked += self._apply_scope_locks(practice_id, settings, now)
                for week in _scope_weeks(settings, now):
                    jobs.extend(
                        self._claim_week(
                            practice_id,
                            settings,
                            week,
                            now,
                            lease=lease,
                            backoff=backoff,
                            max_attempts=max_attempts,
                        )
                    )
            self._flush_if_dirty()
            return ClaimBatch(jobs=jobs, locked_weeks=locked)

    def finish_delivery(
        self,
        delivery_id: str,
        claim_token: str,
        result: MailResult,
        now: datetime,
    ) -> str:
        with self.exclusive():
            outcome = self._finish_unlocked(delivery_id, claim_token, result, now)
            self._flush_if_dirty()
            return outcome

    # --- internals -----------------------------------------------------

    def _clear(self) -> None:
        self._settings: dict[str, TimeSettings] = {}
        self._weeks: dict[tuple[str, str], WeekState] = {}
        self._submissions: dict[str, Submission] = {}
        self._submission_index: dict[tuple[str, str, str], str] = {}
        self._audits: list[dict[str, Any]] = []
        self._deliveries: dict[str, Delivery] = {}
        self._delivery_index: dict[tuple[str, str, str, str], str] = {}
        self._directory: dict[str, dict[str, Any]] = {}
        self._self_tests: dict[tuple[str, str], dict[str, Any]] = {}
        self._dirty = False
        self._memory_blob: Optional[bytes] = None

    def _require_manager(self, user: User) -> None:
        if not can_manage_time(user):
            raise TimeAuthzError(
                "Weekly hours settings are limited to HR admins and Melissa."
            )

    def _merge_settings(
        self,
        practice_id: str,
        existing: Optional[TimeSettings],
        patch: dict[str, Any],
        now: datetime,
    ) -> TimeSettings:
        base = existing or default_settings(practice_id, now)
        recipient = _require_email(str(patch.get("summary_recipient") or base.summary_recipient), field="summary recipient")
        emails = self._validate_participants(practice_id, list(patch.get("participant_emails") or []))
        first_weekday = _weekday(patch.get("reminder_first_weekday", base.reminder_first_weekday))
        due_weekday = _weekday(patch.get("reminder_due_weekday", base.reminder_due_weekday))
        summary_weekday = _weekday(patch.get("summary_weekday", base.summary_weekday))
        first_time = str(patch.get("reminder_first_time") or base.reminder_first_time)
        due_time = str(patch.get("reminder_due_time") or base.reminder_due_time)
        summary_time = str(patch.get("summary_time") or base.summary_time)
        parse_hhmm(first_time)
        parse_hhmm(due_time)
        parse_hhmm(summary_time)
        return TimeSettings(
            practice_id=practice_id,
            participant_emails=emails,
            reminder_first_weekday=first_weekday,
            reminder_first_time=first_time,
            reminder_due_weekday=due_weekday,
            reminder_due_time=due_time,
            summary_weekday=summary_weekday,
            summary_time=summary_time,
            summary_recipient=recipient,
            active_from_week=base.active_from_week or pay_week_ending(now).isoformat(),
            created_at=base.created_at or _iso(now),
            updated_at=_iso(now),
        )

    def _validate_participants(self, practice_id: str, emails: list[str]) -> list[str]:
        existing = set()
        current = self._settings.get(practice_id)
        if current is not None:
            existing = set(current.participant_emails)
        directory = self._directory_emails(practice_id)
        roster: dict[str, int] = {}
        if directory is None:
            for clin in clinician_store.list_for_practice(practice_id):
                email = _email(clin.email)
                if email:
                    roster[email] = roster.get(email, 0) + 1
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in emails:
            email = _email(str(raw))
            if not email or email in seen:
                continue
            if email in existing:
                seen.add(email)
                cleaned.append(email)
                continue
            if directory is not None:
                if email not in directory:
                    raise ValueError(
                        f"{email} is not in the latest complete Google Workspace directory list."
                    )
            else:
                if email not in roster:
                    raise ValueError(
                        "Refresh Google Workspace before enabling someone who is not already saved. "
                        f"{email} is not an existing selection."
                    )
                if roster[email] > 1:
                    raise ValueError(f"The roster has more than one person with {email}.")
            seen.add(email)
            cleaned.append(email)
        return cleaned

    def _directory_emails(self, practice_id: str) -> Optional[set[str]]:
        snap = self._directory.get(practice_id) or {}
        if not snap.get("last_complete_at"):
            return None
        return {_email(str(row.get("email") or "")) for row in snap.get("users") or [] if _email(str(row.get("email") or ""))}

    def replace_directory_snapshot(
        self,
        practice_id: str,
        users: list[dict[str, str]],
        *,
        complete: bool = True,
    ) -> None:
        """Test and refresh helper. Does not change who is enabled."""
        deduped: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in users:
            email = _email(str(row.get("email") or ""))
            if not email or email in seen:
                continue
            seen.add(email)
            deduped.append(
                {
                    "email": email,
                    "google_id": str(row.get("google_id") or ""),
                    "display_name": str(row.get("display_name") or email),
                }
            )
        deduped.sort(key=lambda item: (item["display_name"].casefold(), item["email"]))
        self._directory[practice_id] = {
            "practice_id": practice_id,
            "status": "ok" if complete else "error",
            "complete": complete,
            "last_complete_at": _iso(utcnow()) if complete else "",
            "last_attempt_at": _iso(utcnow()),
            "error_code": "" if complete else "directory_incomplete",
            "pages": 1,
            "listed": len(deduped),
            "skipped": {},
            "users": deduped,
        }
        self._dirty = True

    def _settings_public(self, settings: TimeSettings, now: datetime, *, saved: bool) -> dict[str, Any]:
        from app.hr.time_mail import delivery_flags

        week = pay_week_ending(now)
        state = self._weeks.get((settings.practice_id, week.isoformat()))
        return {
            "saved": saved,
            "practice_id": settings.practice_id,
            "participant_emails": list(settings.participant_emails),
            "reminder_first_weekday": settings.reminder_first_weekday,
            "reminder_first_time": settings.reminder_first_time,
            "reminder_first_subject": REMINDER_FIRST_SUBJECT,
            "reminder_due_weekday": settings.reminder_due_weekday,
            "reminder_due_time": settings.reminder_due_time,
            "reminder_due_subject": REMINDER_DUE_SUBJECT,
            "summary_weekday": settings.summary_weekday,
            "summary_time": settings.summary_time,
            "summary_recipient": settings.summary_recipient,
            "corrector_email": MELISSA_EMAIL,
            "corrector_note": CORRECTOR_NOTE,
            "active_from_week": settings.active_from_week,
            "timezone": "America/New_York",
            "schedule_note": SCHEDULE_NOTE,
            "schedule_warnings": schedule_warnings(settings, week),
            "hours_cap_note": HOURS_CAP_NOTE,
            "helper_text": EXPECTED_FRIDAY_HOURS_TEXT,
            "current_week": self._week_header(settings, week, state),
            "roster": self._staff_choices(settings.practice_id, set(settings.participant_emails)),
            "directory": self._directory_public(settings.practice_id),
            "delivery": delivery_flags(),
            "categories": [
                {"id": key, "label": label} for key, label in KNOWN_CATEGORIES.items()
            ],
        }

    def _apply_scope_locks(self, practice_id: str, settings: TimeSettings, now: datetime) -> int:
        count = 0
        for week in _scope_weeks(settings, now):
            if self._lock_if_due(practice_id, settings, week, now):
                count += 1
        return count

    def _lock_if_due(self, practice_id: str, settings: TimeSettings, week: date, now: datetime) -> bool:
        key = (practice_id, week.isoformat())
        existing = self._weeks.get(key)
        if existing and existing.locked_at:
            return False
        deadline = deadline_instant(week, settings)
        if now < deadline:
            return False
        self._weeks[key] = WeekState(
            practice_id=practice_id,
            week_ending=week.isoformat(),
            locked_at=_iso(now),
            snapshot=self._active_participants(practice_id, settings),
            summary_recipient=settings.summary_recipient,
            lock_deadline=deadline.isoformat(),
        )
        self._dirty = True
        return True

    def _active_participants(self, practice_id: str, settings: TimeSettings) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for email in settings.participant_emails:
            display_name, clinician_id = self._name_for_email(practice_id, email)
            rows.append(
                {
                    "email": email,
                    "display_name": display_name,
                    "clinician_id": clinician_id,
                }
            )
        rows.sort(key=lambda row: (row["display_name"].casefold(), row["email"]))
        return rows

    def _name_for_email(self, practice_id: str, email: str) -> tuple[str, str]:
        for row in (self._directory.get(practice_id) or {}).get("users") or []:
            if _email(str(row.get("email") or "")) == email:
                return str(row.get("display_name") or email), str(row.get("google_id") or "")
        for clin in clinician_store.list_for_practice(practice_id):
            if _email(clin.email) == email:
                return clin.display_name, clin.clinician_id
        return email, ""

    def _staff_choices(self, practice_id: str, selected: set[str]) -> list[dict[str, Any]]:
        """Directory people, plus already-enabled emails the last complete list no longer has."""
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        snap = self._directory.get(practice_id) or {}
        if snap.get("last_complete_at"):
            for person in snap.get("users") or []:
                email = _email(str(person.get("email") or ""))
                if not email or email in seen:
                    continue
                seen.add(email)
                rows.append(
                    {
                        "email": email,
                        "display_name": str(person.get("display_name") or email),
                        "google_id": str(person.get("google_id") or ""),
                        "clinician_id": "",
                        "source": "directory",
                        "selectable": True,
                        "selected": email in selected,
                    }
                )
        else:
            for clin in clinician_store.list_for_practice(practice_id):
                email = _email(clin.email)
                if not email or email in seen:
                    continue
                seen.add(email)
                rows.append(
                    {
                        "email": email,
                        "display_name": clin.display_name,
                        "google_id": clin.google_id,
                        "clinician_id": clin.clinician_id,
                        "source": "roster_until_directory_refresh",
                        "selectable": True,
                        "selected": email in selected,
                    }
                )
        for email in sorted(selected):
            if email in seen:
                continue
            display_name, clinician_id = self._name_for_email(practice_id, email)
            rows.append(
                {
                    "email": email,
                    "display_name": display_name,
                    "google_id": "",
                    "clinician_id": clinician_id,
                    "source": "kept_selection",
                    "selectable": True,
                    "selected": True,
                }
            )
        rows.sort(key=lambda row: (row["display_name"].casefold(), row["email"]))
        return rows

    def _directory_public(self, practice_id: str) -> dict[str, Any]:
        from app.workspace_directory import DIRECTORY_SCOPE, directory_connection_status

        snap = self._directory.get(practice_id) or {}
        status = directory_connection_status(practice_id)
        return {
            "mode": status.get("mode") or "",
            "connected": bool(status.get("connected")),
            "domain": status.get("domain") or "",
            "scope": DIRECTORY_SCOPE,
            "connect_url": status.get("connect_url") or "",
            "status": str(snap.get("status") or "never"),
            "complete": bool(snap.get("complete")),
            "last_complete_at": str(snap.get("last_complete_at") or ""),
            "last_attempt_at": str(snap.get("last_attempt_at") or ""),
            "error_code": str(snap.get("error_code") or ""),
            "pages": int(snap.get("pages") or 0),
            "listed": int(snap.get("listed") or 0),
            "skipped": dict(snap.get("skipped") or {}),
            "note": (
                "The staff list is the last complete Google Workspace directory read for this practice domain. "
                "A failed refresh keeps that list and is marked incomplete. "
                "Being in the directory does not make someone an HR admin or enable time entry."
            ),
        }

    def refresh_directory(self, user: User, now: Optional[datetime] = None) -> dict[str, Any]:
        """Reload the selectable staff list. Does not enable anyone or change roles."""
        self._require_manager(user)
        moment = now or utcnow()
        from app.workspace_directory import WorkspaceDirectoryError, list_directory_fetch

        error_code = ""
        fetched = None
        try:
            fetched = list_directory_fetch(user.practice_id)
        except WorkspaceDirectoryError as exc:
            error_code = exc.code
        with self.exclusive():
            snap = dict(self._directory.get(user.practice_id) or {})
            snap.setdefault("users", [])
            snap["practice_id"] = user.practice_id
            snap["last_attempt_at"] = _iso(moment)
            if fetched is not None and fetched.complete:
                snap["users"] = [
                    {
                        "email": person.email,
                        "google_id": person.google_id,
                        "display_name": person.display_name,
                    }
                    for person in fetched.users
                ]
                snap["status"] = "ok"
                snap["complete"] = True
                snap["last_complete_at"] = _iso(moment)
                snap["error_code"] = ""
                snap["pages"] = fetched.pages
                snap["listed"] = len(fetched.users)
                snap["skipped"] = dict(fetched.skipped)
            else:
                snap["status"] = "error"
                snap["complete"] = False
                snap["error_code"] = error_code or (fetched.error_code if fetched else "directory_list_failed")
                if fetched is not None:
                    snap["pages"] = fetched.pages
                    snap["skipped"] = dict(fetched.skipped)
            self._directory[user.practice_id] = snap
            self._dirty = True
            settings = self._settings.get(user.practice_id) or default_settings(user.practice_id, moment)
            payload = self._settings_public(settings, moment, saved=user.practice_id in self._settings)
            payload["can_self_test"] = self._is_self_tester(user)
            self._flush_if_dirty()
            return payload

    def _people(self, practice_id: str, settings: Optional[TimeSettings], state: Optional[WeekState]) -> list[dict[str, str]]:
        if state and state.locked_at:
            return [dict(row) for row in state.snapshot]
        if settings is None:
            return []
        return self._active_participants(practice_id, settings)

    def _authorize_write(
        self,
        user: User,
        target: str,
        week: date,
        now: datetime,
        settings: Optional[TimeSettings],
        state: Optional[WeekState],
    ) -> None:
        if not self._can_write(user, target, week, now, settings, state):
            own = target == _email(user.email)
            locked = bool(state and state.locked_at)
            if locked and is_melissa(user):
                raise TimeAuthzError("That person is not on this locked week's roster.")
            if not own and not is_melissa(user):
                if can_manage_time(user):
                    raise TimeAuthzError(
                        "Only the employee can edit before cutoff, and only Melissa can edit after cutoff."
                    )
                raise NotFoundError()
            if locked:
                raise TimeAuthzError(
                    "This week is locked. Only Melissa can enter missing hours or corrections."
                )
            if not own:
                raise TimeAuthzError("Before cutoff, each person enters only their own hours.")
            if week != pay_week_ending(now):
                raise ValueError("Hours can only be entered for the current pay week.")
            raise TimeAuthzError("Only selected hourly staff can enter hours.")

    def _can_write(
        self,
        user: User,
        target: str,
        week: date,
        now: datetime,
        settings: Optional[TimeSettings],
        state: Optional[WeekState],
    ) -> bool:
        if not target or user.practice_id is None:
            return False
        own = target == _email(user.email)
        locked = bool(state and state.locked_at)
        if not locked:
            if not own or week != pay_week_ending(now) or settings is None:
                return False
            return any(row["email"] == target for row in self._active_participants(user.practice_id, settings))
        if not is_melissa(user) or state is None:
            return False
        return any(row["email"] == target for row in state.snapshot)

    def _identity(
        self,
        user: User,
        target: str,
        settings: Optional[TimeSettings],
        state: Optional[WeekState],
    ) -> tuple[str, str]:
        for row in self._people(user.practice_id, settings, state):
            if row["email"] == target:
                return row["display_name"], row["clinician_id"]
        if target == _email(user.email):
            return user.username or target, ""
        return target, ""

    def _save_existing(
        self,
        user: User,
        sub: Submission,
        hours: int,
        minutes: int,
        moment: datetime,
    ) -> dict[str, Any]:
        """Caller holds the lock. ``sub`` is already practice-checked."""
        settings = self._settings.get(user.practice_id)
        if settings is not None:
            self._apply_scope_locks(user.practice_id, settings, moment)
        week = parse_week_ending(sub.week_ending)
        state = self._weeks.get((user.practice_id, sub.week_ending))
        self._authorize_write(user, sub.email, week, moment, settings, state)
        raw = combine_hours_minutes(hours, minutes)
        lines = total_line(raw)
        before_lines = [dict(line) for line in sub.lines]
        before_minutes = line_minutes(before_lines)
        if before_minutes != raw:
            display_name, clinician_id = self._identity(user, sub.email, settings, state)
            sub.lines = lines
            if display_name:
                sub.display_name = display_name
            if clinician_id:
                sub.clinician_id = clinician_id
            sub.updated_at = _iso(moment)
            sub.updated_by = user.user_id
            self._append_audit(
                sub,
                user=user,
                now=moment,
                late=bool(state and state.locked_at),
                before_lines=before_lines,
                before_minutes=before_minutes,
            )
            self._dirty = True
        self._flush_if_dirty()
        return self._entry_public(sub)

    def _append_audit(
        self,
        sub: Submission,
        *,
        user: User,
        now: datetime,
        late: bool,
        before_lines: Optional[list[dict[str, Any]]],
        before_minutes: Optional[int],
    ) -> None:
        self._audits.append(
            {
                "audit_id": str(uuid4()),
                "practice_id": sub.practice_id,
                "week_ending": sub.week_ending,
                "submission_id": sub.submission_id,
                "employee_email": sub.email,
                "employee_name": sub.display_name,
                "actor_user_id": user.user_id,
                "actor_email": _email(user.email),
                "at": _iso(now),
                "late": late,
                "before_minutes": before_minutes,
                "after_minutes": line_minutes(sub.lines),
                "before_lines": before_lines,
                "after_lines": [dict(line) for line in sub.lines],
            }
        )
        self._dirty = True

    def _own_entry(self, practice_id: str, week_ending: str, email: str) -> Optional[dict[str, Any]]:
        submission_id = self._submission_index.get((practice_id, week_ending, email))
        if not submission_id:
            return None
        return self._entry_public(self._submissions[submission_id])

    def _history(self, practice_id: str, email: str) -> list[dict[str, Any]]:
        rows = [
            self._entry_public(sub)
            for sub in self._submissions.values()
            if sub.practice_id == practice_id and sub.email == email
        ]
        rows.sort(key=lambda row: row["week_ending"], reverse=True)
        return rows

    def _entry_public(self, sub: Submission) -> dict[str, Any]:
        raw = line_minutes(sub.lines)
        rounded = round_payable_minutes(raw)
        hours, minutes = divmod(raw, 60)
        return {
            "submission_id": sub.submission_id,
            "practice_id": sub.practice_id,
            "week_ending": sub.week_ending,
            "email": sub.email,
            "display_name": sub.display_name,
            "hours": hours,
            "minutes": minutes,
            "raw_minutes": raw,
            "raw_display": clock_display(raw),
            "rounded_minutes": rounded,
            "rounded_display": clock_display(rounded),
            "decimal_hours": decimal_hours(rounded),
            "lines": [dict(line) for line in sub.lines],
            "updated_at": sub.updated_at,
        }

    def _week_header(self, settings: TimeSettings, week: date, state: Optional[WeekState]) -> dict[str, Any]:
        if state and state.lock_deadline:
            deadline = _parse_iso(state.lock_deadline)
        else:
            deadline = deadline_instant(week, settings)
        return {
            "week_ending": week.isoformat(),
            "week_start": pay_week_start(week).isoformat(),
            "label": week_range_label(week),
            "deadline": deadline.isoformat(),
            "deadline_label": deadline_label(deadline),
            "locked": bool(state and state.locked_at),
            "locked_at": state.locked_at if state else "",
        }

    def _report_unlocked(
        self,
        practice_id: str,
        week: date,
        now: datetime,
        settings: Optional[TimeSettings],
    ) -> dict[str, Any]:
        state = self._weeks.get((practice_id, week.isoformat()))
        effective = settings or default_settings(practice_id, now)
        people = self._people(practice_id, settings, state)
        rows: list[dict[str, Any]] = []
        for person in people:
            submission_id = self._submission_index.get((practice_id, week.isoformat(), person["email"]))
            sub = self._submissions.get(submission_id) if submission_id else None
            if sub is None:
                rows.append(
                    {
                        "email": person["email"],
                        "display_name": person["display_name"],
                        "clinician_id": person.get("clinician_id") or "",
                        "status": "missing",
                        "submission_id": "",
                        "raw_minutes": None,
                        "raw_display": "",
                        "rounded_minutes": None,
                        "rounded_display": "",
                        "decimal_hours": "",
                        "lines": [],
                    }
                )
                continue
            public = self._entry_public(sub)
            rows.append(
                {
                    "email": person["email"],
                    "display_name": person["display_name"] or public["display_name"],
                    "clinician_id": person.get("clinician_id") or sub.clinician_id,
                    "status": "submitted",
                    "submission_id": sub.submission_id,
                    "raw_minutes": public["raw_minutes"],
                    "raw_display": public["raw_display"],
                    "rounded_minutes": public["rounded_minutes"],
                    "rounded_display": public["rounded_display"],
                    "decimal_hours": public["decimal_hours"],
                    "lines": public["lines"],
                }
            )
        rows.sort(key=lambda row: (row["display_name"].casefold(), row["email"]))
        header = self._week_header(effective, week, state)
        recipient = state.summary_recipient if state and state.locked_at else effective.summary_recipient
        audits = [
            dict(row)
            for row in self._audits
            if row["practice_id"] == practice_id and row["week_ending"] == week.isoformat()
        ]
        audits.sort(key=lambda row: row["at"])
        notifications = [
            _delivery_public(item)
            for item in self._deliveries.values()
            if item.practice_id == practice_id and item.week_ending == week.isoformat()
        ]
        notifications.sort(key=lambda row: (row["kind"], row["recipient"]))
        return {
            **header,
            "practice_id": practice_id,
            "summary_recipient": recipient,
            "rows": rows,
            "missing": [row["display_name"] for row in rows if row["status"] == "missing"],
            "audits": audits,
            "notifications": notifications,
        }

    def _claim_week(
        self,
        practice_id: str,
        settings: TimeSettings,
        week: date,
        now: datetime,
        *,
        lease: timedelta,
        backoff: tuple[timedelta, ...],
        max_attempts: int,
    ) -> list[ClaimedJob]:
        state = self._weeks.get((practice_id, week.isoformat()))
        locked = bool(state and state.locked_at)
        reminders = _reminder_kinds(now, settings, week, locked)
        summary_is_due = bool(locked) or now >= deadline_instant(week, settings)
        keep = set(reminders)
        if summary_is_due:
            keep.add("summary")
        self._cancel_obsolete(practice_id, week.isoformat(), keep, now)
        jobs: list[ClaimedJob] = []
        report = self._report_unlocked(practice_id, week, now, settings)
        deadline = _parse_iso(report["deadline"])
        if "reminder_first" in reminders:
            for row in report["rows"]:
                job = self._claim_mail(
                    practice_id,
                    week.isoformat(),
                    "reminder_first",
                    row["email"],
                    now,
                    lease=lease,
                    backoff=backoff,
                    max_attempts=max_attempts,
                    subject_body=lambda: render_reminder(slot="first", week_ending=week, deadline=deadline),
                )
                if job:
                    jobs.append(job)
        if "reminder_due" in reminders:
            for row in report["rows"]:
                if row["status"] != "missing":
                    continue
                job = self._claim_mail(
                    practice_id,
                    week.isoformat(),
                    "reminder_due",
                    row["email"],
                    now,
                    lease=lease,
                    backoff=backoff,
                    max_attempts=max_attempts,
                    subject_body=lambda: render_reminder(slot="due", week_ending=week, deadline=deadline),
                )
                if job:
                    jobs.append(job)
        if summary_is_due and report["rows"]:
            subject, body, csv_text, filename = render_summary(report)
            job = self._claim_mail(
                practice_id,
                week.isoformat(),
                "summary",
                report["summary_recipient"],
                now,
                lease=lease,
                backoff=backoff,
                max_attempts=max_attempts,
                subject_body=lambda: (subject, body),
                csv_filename=filename,
                csv_text=csv_text,
            )
            if job:
                jobs.append(job)
        elif summary_is_due:
            self._cancel_kind(practice_id, week.isoformat(), "summary", now)
        return jobs

    def _quarantine_expired_claims(self, now: datetime) -> None:
        """A claim whose lease expired never got a confirmed result.

        Resending would duplicate mail if Gmail had already accepted it, so
        the row becomes uncertain and stays visible for an operator.
        """
        for delivery in self._deliveries.values():
            if delivery.status != "claimed" or not delivery.claim_expires_at:
                continue
            if _parse_iso(delivery.claim_expires_at) > now:
                continue
            delivery.status = "uncertain"
            delivery.last_error = "claim_expired_unconfirmed"
            delivery.claim_token = ""
            delivery.unlimited_retry = False
            delivery.updated_at = _iso(now)
            self._dirty = True

    def access_summary(self, user: User) -> dict[str, Any]:
        """Whether this user may enter hours. Does not enable anyone."""
        email = _email(user.email)
        with self.exclusive():
            self._quarantine_expired_claims(utcnow())
            self._flush_if_dirty()
            settings = self._settings.get(user.practice_id)
            selected = bool(
                settings
                and email
                and any(
                    row["email"] == email
                    for row in self._active_participants(user.practice_id, settings)
                )
            )
        return {
            "can_manage": can_manage_time(user),
            "can_enter": selected,
            "can_correct_locked": is_melissa(user),
        }

    def _cancel_obsolete(self, practice_id: str, week_ending: str, keep: set[str], now: datetime) -> None:
        for delivery in list(self._deliveries.values()):
            if delivery.practice_id != practice_id or delivery.week_ending != week_ending:
                continue
            if delivery.kind in keep or delivery.status != "failed":
                continue
            delivery.status = "cancelled"
            delivery.claim_token = ""
            delivery.updated_at = _iso(now)
            self._dirty = True

    def _cancel_kind(self, practice_id: str, week_ending: str, kind: str, now: datetime) -> None:
        for delivery in list(self._deliveries.values()):
            if (
                delivery.practice_id == practice_id
                and delivery.week_ending == week_ending
                and delivery.kind == kind
                and delivery.status == "failed"
            ):
                delivery.status = "cancelled"
                delivery.claim_token = ""
                delivery.updated_at = _iso(now)
                self._dirty = True

    def _claim_mail(
        self,
        practice_id: str,
        week_ending: str,
        kind: str,
        recipient: str,
        now: datetime,
        *,
        lease: timedelta,
        backoff: tuple[timedelta, ...],
        max_attempts: int,
        subject_body: Any,
        csv_filename: str = "",
        csv_text: str = "",
    ) -> Optional[ClaimedJob]:
        recipient = _email(recipient)
        if not recipient:
            return None
        key = (practice_id, week_ending, kind, recipient)
        existing = self._deliveries.get(self._delivery_index.get(key, ""))
        if existing and existing.status in ("sent", "uncertain", "cancelled"):
            return None
        if existing and not _can_reclaim(existing, now, backoff, max_attempts):
            return None
        if existing is None:
            existing = Delivery(
                delivery_id=str(uuid4()),
                practice_id=practice_id,
                week_ending=week_ending,
                kind=kind,
                recipient=recipient,
                status="claimed",
            )
            self._deliveries[existing.delivery_id] = existing
            self._delivery_index[key] = existing.delivery_id
        existing.status = "claimed"
        existing.claim_token = str(uuid4())
        existing.claimed_at = _iso(now)
        existing.claim_expires_at = _iso(now + lease)
        existing.updated_at = _iso(now)
        self._dirty = True
        subject, body = subject_body()
        return ClaimedJob(
            delivery_id=existing.delivery_id,
            claim_token=existing.claim_token,
            to=recipient,
            subject=subject,
            body=body,
            csv_filename=csv_filename,
            csv_text=csv_text,
        )

    def _finish_unlocked(
        self,
        delivery_id: str,
        claim_token: str,
        result: MailResult,
        now: datetime,
    ) -> str:
        delivery = self._deliveries.get(delivery_id)
        if delivery is None or delivery.claim_token != claim_token or delivery.status != "claimed":
            return "stale"
        status = result.status
        if status == "sent":
            delivery.status = "sent"
            delivery.sent_at = _iso(now)
            delivery.last_error = ""
            delivery.provider_id = safe_provider_id(result.provider_id)
            delivery.unlimited_retry = False
        elif status == "uncertain":
            delivery.status = "uncertain"
            delivery.attempts += 1
            delivery.last_error = safe_error_code(result.detail, fallback="uncertain")
            delivery.unlimited_retry = False
        elif status == "not_ready":
            delivery.status = "failed"
            delivery.last_error = safe_error_code(result.detail, fallback="not_ready")
            delivery.unlimited_retry = True
        else:
            delivery.status = "failed"
            delivery.attempts += 1
            delivery.last_error = safe_error_code(result.detail, fallback="mail_failed")
            delivery.unlimited_retry = False
        delivery.claim_token = ""
        delivery.updated_at = _iso(now)
        self._dirty = True
        return status if status in ("sent", "uncertain", "not_ready") else "failed"

    def refresh_job(self, delivery_id: str, claim_token: str, now: datetime) -> Optional[ClaimedJob]:
        """Rebuild a claimed message under the lock immediately before send.

        A Friday reminder whose person submitted after the claim is cancelled
        instead of sent. A summary picks up hours saved after the claim.
        """
        with self.exclusive():
            delivery = self._deliveries.get(delivery_id)
            if delivery is None or delivery.claim_token != claim_token or delivery.status != "claimed":
                return None
            settings = self._settings.get(delivery.practice_id)
            if settings is None:
                return None
            week = parse_week_ending(delivery.week_ending)
            report = self._report_unlocked(delivery.practice_id, week, now, settings)
            if delivery.kind == "reminder_due":
                row = next((item for item in report["rows"] if item["email"] == delivery.recipient), None)
                if row is None or row["status"] != "missing":
                    self._release_claim(delivery, now, status="cancelled")
                    return None
            if delivery.kind == "summary" and not report["rows"]:
                self._release_claim(delivery, now, status="cancelled")
                return None
            if delivery.kind == "summary":
                subject, body, csv_text, filename = render_summary(report)
                return ClaimedJob(
                    delivery_id=delivery.delivery_id,
                    claim_token=delivery.claim_token,
                    to=delivery.recipient,
                    subject=subject,
                    body=body,
                    csv_filename=filename,
                    csv_text=csv_text,
                )
            slot = "first" if delivery.kind == "reminder_first" else "due"
            subject, body = render_reminder(
                slot=slot,
                week_ending=week,
                deadline=_parse_iso(report["deadline"]),
            )
            return ClaimedJob(
                delivery_id=delivery.delivery_id,
                claim_token=delivery.claim_token,
                to=delivery.recipient,
                subject=subject,
                body=body,
            )

    def _release_claim(self, delivery: Delivery, now: datetime, *, status: str) -> None:
        delivery.status = status
        delivery.claim_token = ""
        delivery.updated_at = _iso(now)
        self._dirty = True

    def _load_from_disk(self) -> None:
        if not self._path.is_file():
            self._clear()
            return
        try:
            wrapper = json.loads(self._path.read_text(encoding="utf-8"))
            blob = base64.b64decode(str(wrapper.get("blob") or ""))
            payload = json.loads(decrypt_utf8(blob))
            if not isinstance(payload, dict):
                raise TimeStoreError()
        except TimeStoreError:
            raise
        except Exception as e:
            raise TimeStoreError() from e
        self._replace_payload(payload)

    def _replace_payload(self, payload: dict[str, Any]) -> None:
        self._clear()
        for item in payload.get("settings") or []:
            try:
                settings = TimeSettings.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            self._settings[settings.practice_id] = settings
        for item in payload.get("weeks") or []:
            try:
                week = WeekState.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            self._weeks[(week.practice_id, week.week_ending)] = week
        for item in payload.get("submissions") or []:
            try:
                sub = Submission.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            self._submissions[sub.submission_id] = sub
            self._submission_index[(sub.practice_id, sub.week_ending, sub.email)] = sub.submission_id
        for item in payload.get("audits") or []:
            if isinstance(item, dict) and item.get("audit_id") and item.get("practice_id"):
                self._audits.append(dict(item))
        for item in payload.get("deliveries") or []:
            try:
                delivery = Delivery.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            self._deliveries[delivery.delivery_id] = delivery
            self._delivery_index[
                (delivery.practice_id, delivery.week_ending, delivery.kind, delivery.recipient)
            ] = delivery.delivery_id
        for item in payload.get("directory") or []:
            if isinstance(item, dict) and item.get("practice_id"):
                self._directory[str(item["practice_id"])] = dict(item)
        for item in payload.get("self_tests") or []:
            if not isinstance(item, dict):
                continue
            practice_id = str(item.get("practice_id") or "")
            user_id = str(item.get("user_id") or "")
            if practice_id and user_id:
                self._self_tests[(practice_id, user_id)] = dict(item)
        self._dirty = False

    def _dump(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "settings": [item.to_dict() for item in self._settings.values()],
            "weeks": [item.to_dict() for item in self._weeks.values()],
            "submissions": [item.to_dict() for item in self._submissions.values()],
            "audits": [dict(item) for item in self._audits],
            "deliveries": [item.to_dict() for item in self._deliveries.values()],
            "directory": [dict(item) for item in self._directory.values()],
            "self_tests": [dict(item) for item in self._self_tests.values()],
        }

    def _flush_unlocked(self) -> None:
        blob = encrypt_utf8(json.dumps(self._dump(), separators=(",", ":")))
        if self._mode == "file":
            wrapper = json.dumps(
                {"version": 1, "blob": base64.b64encode(blob).decode("ascii")},
                separators=(",", ":"),
            )
            _atomic_write(self._path, wrapper)
        else:
            self._memory_blob = blob
        self._dirty = False

    def _flush_if_dirty(self) -> None:
        if self._dirty:
            self._flush_unlocked()


def _delivery_public(delivery: Delivery) -> dict[str, Any]:
    exhausted = (
        delivery.status == "failed"
        and not delivery.unlimited_retry
        and delivery.attempts >= MAX_DELIVERY_ATTEMPTS
    )
    return {
        "delivery_id": delivery.delivery_id,
        "kind": delivery.kind,
        "recipient": delivery.recipient,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "last_error": delivery.last_error,
        "sent_at": delivery.sent_at,
        "attempt_budget_exhausted": exhausted,
    }


def _reminder_kinds(now: datetime, settings: TimeSettings, week: date, locked: bool) -> set[str]:
    if locked or now >= deadline_instant(week, settings):
        return set()
    lock_at = deadline_instant(week, settings)
    first_at = at_local(
        date_for_weekday(week, settings.reminder_first_weekday),
        settings.reminder_first_time,
    )
    due_at = at_local(
        date_for_weekday(week, settings.reminder_due_weekday),
        settings.reminder_due_time,
    )
    kinds: set[str] = set()
    if first_at <= now < min(due_at, lock_at):
        kinds.add("reminder_first")
    if due_at < lock_at and due_at <= now < lock_at:
        kinds.add("reminder_due")
    return kinds


def _scope_weeks(settings: TimeSettings, now: datetime) -> list[date]:
    current = pay_week_ending(now)
    previous = current - timedelta(days=7)
    try:
        start = date.fromisoformat(settings.active_from_week)
    except ValueError:
        start = current
    return [week for week in (previous, current) if week >= start]


def _can_reclaim(
    delivery: Delivery,
    now: datetime,
    backoff: tuple[timedelta, ...],
    max_attempts: int,
) -> bool:
    if delivery.status == "claimed":
        # Expired claims are quarantined instead of resent. The provider may
        # already have accepted the message.
        return False
    if delivery.status != "failed":
        return False
    last = _parse_iso(delivery.updated_at or delivery.claimed_at or _iso(now))
    if delivery.unlimited_retry:
        return now >= last + backoff[0]
    if delivery.attempts >= max_attempts:
        return False
    index = min(max(delivery.attempts - 1, 0), len(backoff) - 1)
    return now >= last + backoff[index]


def _send_state(slot: Optional[dict[str, Any]]) -> dict[str, Any]:
    status = str((slot or {}).get("status") or "")
    return {
        "status": status,
        "to": str((slot or {}).get("to") or ""),
        "at": str((slot or {}).get("sent_at") or (slot or {}).get("at") or ""),
        "last_error": str((slot or {}).get("last_error") or ""),
        "gmail_message_id": str((slot or {}).get("gmail_message_id") or ""),
        "can_send": status not in ("sent", "uncertain", "sending"),
    }


def _labelled_reminder(slot: str, week_ending: date, deadline: datetime) -> tuple[str, str]:
    subject, body = render_reminder(slot=slot, week_ending=week_ending, deadline=deadline)
    body = body.replace(TIME_ENTRY_URL, TIME_SELF_TEST_URL)
    return f"[TEST] {subject}", f"{SELF_TEST_BANNER}\n\n{body}"


def _self_test_summary(entry: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:
    subject = "[TEST] Weekly hours summary — self-test only"
    body = "\n".join(
        [
            SELF_TEST_BANNER,
            "",
            "Intended recipient when a send is approved later: " + MELISSA_EMAIL,
            "This is one person. It is not the practice payroll summary and it does not include anyone else.",
            "",
            f"Pay week shown for context: {header.get('label')}",
            f"Practice deadline shown for context: {header.get('deadline_label')}",
            "",
            f"- {entry['display_name']}: raw {entry['raw_display']}, payable {entry['rounded_display']} ({entry['decimal_hours']})",
            "",
            "Not sent.",
        ]
    )
    row = {
        "display_name": entry["display_name"],
        "email": entry["email"],
        "status": "submitted",
        "raw_minutes": entry["raw_minutes"],
        "rounded_minutes": entry["rounded_minutes"],
        "decimal_hours": entry["decimal_hours"],
    }
    return {
        "to": MELISSA_EMAIL,
        "subject": subject,
        "body": body,
        "csv": rows_to_csv([row]),
        "csv_filename": "weekly-hours-self-test.csv",
        "sent": False,
    }


def render_reminder(*, slot: str, week_ending: date, deadline: datetime) -> tuple[str, str]:
    subject = REMINDER_FIRST_SUBJECT if slot == "first" else REMINDER_DUE_SUBJECT
    body = "\n".join(
        [
            subject,
            "",
            f"Pay week: {week_range_label(week_ending)}",
            f"Deadline: {deadline_label(deadline)}",
            "",
            EXPECTED_FRIDAY_HOURS_TEXT,
            "",
            f"Enter hours: {TIME_ENTRY_URL}",
        ]
    )
    return subject, body


def render_summary(report: dict[str, Any]) -> tuple[str, str, str, str]:
    subject = f"Weekly hours summary — week ending {report['week_ending']}"
    lines = [
        f"Pay week: {report['label']}",
        f"Deadline: {report['deadline_label']}",
        "",
        "Totals include the expected Friday afternoon hours staff were asked to enter before cutoff.",
        "Missing is not zero. An explicit 0:00 counts as submitted.",
        "",
        "Submitted (sorted by name):",
    ]
    submitted = [row for row in report["rows"] if row["status"] == "submitted"]
    if not submitted:
        lines.append("- (none)")
    for row in submitted:
        lines.append(
            f"- {row['display_name']}: raw {row['raw_display']}, payable {row['rounded_display']} ({row['decimal_hours']})"
        )
    lines.extend(["", "Missing:"])
    if not report["missing"]:
        lines.append("- (none)")
    for name in report["missing"]:
        lines.append(f"- {name}")
    lines.extend(
        [
            "",
            f"Admin view: {admin_url(report['week_ending'])}",
            "CSV is attached. Melissa's later corrections are logged and are not emailed again.",
        ]
    )
    filename = f"weekly-hours-{report['week_ending']}.csv"
    return subject, "\n".join(lines), rows_to_csv(report["rows"]), filename


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["employee", "email", "status", "raw_minutes", "rounded_minutes", "decimal_hours"]
    )
    for row in rows:
        if row["status"] == "missing":
            writer.writerow([_csv_cell(row["display_name"]), _csv_cell(row["email"]), "missing", "", "", ""])
        else:
            writer.writerow(
                [
                    _csv_cell(row["display_name"]),
                    _csv_cell(row["email"]),
                    "submitted",
                    row["raw_minutes"],
                    row["rounded_minutes"],
                    row["decimal_hours"],
                ]
            )
    return buffer.getvalue()


def _csv_cell(value: str) -> str:
    text = str(value or "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _roster_options(practice_id: str, selected: set[str]) -> list[dict[str, Any]]:
    rows = []
    for clin in clinician_store.list_for_practice(practice_id):
        email = _email(clin.email)
        rows.append(
            {
                "clinician_id": clin.clinician_id,
                "display_name": clin.display_name,
                "email": email,
                "default_role": clin.default_role,
                "selectable": bool(email),
                "selected": bool(email) and email in selected,
            }
        )
    return rows


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _email(value: str) -> str:
    return (value or "").strip().lower()


def _require_email(value: str, *, field: str) -> str:
    email = _email(value)
    if not _EMAIL.fullmatch(email):
        raise ValueError(f"{field} must be one email address.")
    return email


def _weekday(value: Any) -> int:
    try:
        weekday = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).") from e
    if weekday < 0 or weekday > 6:
        raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).")
    return weekday


def _require_friday(day: date) -> None:
    if day.weekday() != 4:
        raise ValueError("week_ending must be the Friday of a Saturday–Friday pay week.")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_iso(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _long_date(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def nav_for_user(user: User) -> dict[str, Any]:
    """Home-tile fields. Nobody is enabled until an admin selects them."""
    access = time_store.access_summary(user)
    if access["can_manage"]:
        return {
            "href": "/#time-settings",
            "status": "live",
            "description": (
                "Choose which roster staff may enter a Saturday–Friday total. "
                "Nobody is on by default, including you, until you select them. "
                "Mail is off; nothing is emailed."
            ),
            "open_label": "Weekly hours settings",
        }
    if access["can_enter"]:
        return {
            "href": "/#time",
            "status": "live",
            "description": (
                "Enter your Saturday–Friday hours, including the Friday afternoon you expect to work."
            ),
            "open_label": "Enter weekly hours",
        }
    return {
        "href": "",
        "status": "disabled",
        "description": "Not enabled for your account. An admin selects who may enter weekly hours.",
        "open_label": "",
    }


time_store = TimeStore()
