"""Credentialing reminders. Reads the store and writes due-engine rows. Does not rewrite enrollments."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
from typing import Any, Optional

from app.credentialing.backup import backup_once
from app.credentialing.store import credentialing_store, expiration_day
from app.dates import calendar_date
from app.platform.catalog import CREDENTIAL_CLOCKS
from app.platform.due import due_engine
from app.platform.due_persist import due_persist_mode, due_persist_path

SOURCE = "credentialing"
SUBMITTED = {"pending", "submitted"}
FOLLOW_UP_DAYS = 14
STALL_DAYS = 90
CAQH_DAYS = 120
CAQH_LEAD_DAYS = 14

CLOCK_CADENCE = {str(row["id"]): int(row["cadence_days"]) for row in CREDENTIAL_CLOCKS}
CLOCK_NAMES = {str(row["id"]): str(row["name"]) for row in CREDENTIAL_CLOCKS}
CADENCE_REMINDERS = (
    "malpractice_rider",
    "ma_revalidation",
    "clinician_exclusion_screening",
)


def _today(now: Optional[datetime]) -> date:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date()


def _parse_day(value: str) -> Optional[date]:
    return calendar_date(value)


def _due(day: date) -> str:
    """Noon Eastern so the calendar date does not shift when shown in New York."""
    local = datetime(day.year, day.month, day.day, 12, 0, tzinfo=EASTERN)
    return local.astimezone(timezone.utc).isoformat()


def _days(payer: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(payer.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def cell_href(payer_id: str, subject_id: str) -> str:
    return f"/#credentialing?payer={payer_id}&subject={subject_id}"


def profile_href(subject_id: str) -> str:
    return f"/#credentialing?profile={subject_id}"


def _clock_date(subject: dict[str, Any], clock_id: str) -> str:
    clocks = subject.get("clocks") if isinstance(subject.get("clocks"), dict) else {}
    return str(clocks.get(clock_id) or "")


def sync_reminders(
    practice_id: str, owner_user_id: str, *, now: Optional[datetime] = None
) -> dict[str, int]:
    """Upsert open reminders and complete ones whose trigger is gone. Returns counts by type."""
    if due_persist_mode() == "file":
        backup_once(due_persist_path())
    credentialing_store.migrate_clock_fields(practice_id)
    view = credentialing_store.reminder_view(practice_id)
    today = _today(now)
    desired: dict[str, dict[str, str]] = {}
    payers = {row["id"]: row for row in view["payers"]}
    subjects = {row["id"]: row for row in view["subjects"]}

    for enrollment in view["enrollments"]:
        subject = subjects.get(enrollment["subject_id"])
        payer = payers.get(enrollment["payer_id"])
        if subject is None or payer is None:
            continue
        _enrollment_reminders(desired, subject, payer, enrollment, today)

    for subject in view["subjects"]:
        if subject.get("kind") != "clinician" or subject.get("archived"):
            continue
        _profile_reminders(desired, subject, today)

    for ref, item in desired.items():
        due_engine.upsert(
            practice_id=practice_id,
            domain="credentialing",
            source=SOURCE,
            title=item["title"],
            owner_user_id=owner_user_id,
            due_at=item["due_at"],
            href=item["href"],
            source_ref=ref,
            status="open",
        )
    open_refs = {
        ob.source_ref
        for ob in due_engine.list_open(practice_id)
        if ob.source == SOURCE and ob.source_ref
    }
    for ref in open_refs - set(desired):
        due_engine.complete(practice_id=practice_id, source=SOURCE, source_ref=ref)
    counts: dict[str, int] = {}
    for ref in desired:
        kind = ref.split("|", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _enrollment_reminders(
    desired: dict[str, dict[str, str]],
    subject: dict[str, Any],
    payer: dict[str, Any],
    enrollment: dict[str, Any],
    today: date,
) -> None:
    status = str(enrollment.get("status") or "")
    subject_id = str(subject["id"])
    payer_id = str(payer["id"])
    href = cell_href(payer_id, subject_id)
    label = f"{payer.get('name') or 'Payer'} / {subject.get('name') or 'Person'}"
    submitted = _parse_day(str(enrollment.get("submitted_on") or ""))
    if status in SUBMITTED and submitted is not None:
        interval = _days(payer, "follow_up_days", FOLLOW_UP_DAYS)
        follow = submitted + timedelta(days=interval)
        desired[f"follow_up|{subject_id}|{payer_id}"] = {
            "title": f"Follow up {label}",
            "due_at": _due(follow),
            "href": href,
        }
        if (today - submitted).days >= STALL_DAYS:
            desired[f"stalled|{subject_id}|{payer_id}"] = {
                "title": f"Stalled {label}",
                "due_at": _due(submitted + timedelta(days=STALL_DAYS)),
                "href": href,
            }
    expires = expiration_day(enrollment) if status == "in_network" else None
    if expires is not None:
        stamp = expires.isoformat()
        desired[f"recredential_90|{subject_id}|{payer_id}|{stamp}"] = {
            "title": f"Recredential in 90 days {label}",
            "due_at": _due(expires - timedelta(days=90)),
            "href": href,
        }
        desired[f"recredential_30|{subject_id}|{payer_id}|{stamp}"] = {
            "title": f"Recredential in 30 days {label}",
            "due_at": _due(expires - timedelta(days=30)),
            "href": href,
        }
        follow = _parse_day(str(enrollment.get("next_follow_up") or ""))
        if follow is not None:
            desired[f"expiration_follow_up|{subject_id}|{payer_id}|{follow.isoformat()}"] = {
                "title": f"Expiration follow-up {label}",
                "due_at": _due(follow),
                "href": href,
            }


def _profile_reminders(
    desired: dict[str, dict[str, str]], subject: dict[str, Any], today: date
) -> None:
    subject_id = str(subject["id"])
    href = profile_href(subject_id)
    name = subject.get("name") or "Clinician"
    expires = _parse_day(str(subject.get("license_expires_on") or ""))
    if expires is not None:
        stamp = expires.isoformat()
        desired[f"license_60|{subject_id}|{stamp}"] = {
            "title": f"License expires in 60 days — {name}",
            "due_at": _due(expires - timedelta(days=60)),
            "href": href,
        }
        desired[f"license_30|{subject_id}|{stamp}"] = {
            "title": f"License expires in 30 days — {name}",
            "due_at": _due(expires - timedelta(days=30)),
            "href": href,
        }
    attested = _parse_day(str(subject.get("caqh_attested_on") or "")) or _parse_day(
        _clock_date(subject, "caqh_reattestation")
    )
    if attested is not None:
        stamp = attested.isoformat()
        due = attested + timedelta(days=CAQH_DAYS - CAQH_LEAD_DAYS)
        desired[f"caqh|{subject_id}|{stamp}"] = {
            "title": f"CAQH attestation — {name}",
            "due_at": _due(due),
            "href": href,
        }
    for clock_id in CADENCE_REMINDERS:
        last = _parse_day(_clock_date(subject, clock_id))
        if last is None:
            continue
        stamp = last.isoformat()
        kind = {
            "malpractice_rider": "malpractice",
            "ma_revalidation": "ma_revalidation",
            "clinician_exclusion_screening": "exclusion",
        }[clock_id]
        due = last + timedelta(days=CLOCK_CADENCE[clock_id])
        desired[f"{kind}|{subject_id}|{stamp}"] = {
            "title": f"{CLOCK_NAMES[clock_id]} — {name}",
            "due_at": _due(due),
            "href": href,
        }
    _ = today


def profile_clocks(subject: dict[str, Any]) -> list[dict[str, Any]]:
    """Six clocks for the profile. Dates already stored on the subject, plus the computed due date."""
    clocks = subject.get("clocks") if isinstance(subject.get("clocks"), dict) else {}
    rows = []
    for clock_id, name in CLOCK_NAMES.items():
        last = str(clocks.get(clock_id) or "")
        if clock_id == "caqh_reattestation" and not last:
            last = str(subject.get("caqh_attested_on") or "")
        due = ""
        parsed = _parse_day(last)
        if parsed is not None:
            due = (parsed + timedelta(days=CLOCK_CADENCE[clock_id])).isoformat()
        rows.append(
            {
                "id": clock_id,
                "name": name,
                "cadence_days": CLOCK_CADENCE[clock_id],
                "last_completed": last,
                "due_on": due,
            }
        )
    return rows


def sync_stored_practices() -> None:
    """Startup sync for file-backed stores. Prints counts only."""
    import json

    from app.credentialing.store import _file_mode

    if not _file_mode():
        return
    try:
        payload = credentialing_store._read()  # noqa: SLF001 — practices keys only
    except Exception:
        print("credentialing_reminder_sync_failed")
        return
    practices = payload.get("practices") if isinstance(payload, dict) else None
    if not isinstance(practices, dict):
        return
    for practice_id in practices:
        try:
            counts = sync_reminders(str(practice_id), "billing")
        except Exception:
            print("credentialing_reminder_sync_failed")
            continue
        print("credentialing_reminder_counts " + json.dumps(counts, sort_keys=True))
    from app.credentialing.store import clocks_migrated_total

    print("credentialing_clocks_migrated " + str(clocks_migrated_total()))
