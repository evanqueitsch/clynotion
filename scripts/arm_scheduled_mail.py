"""Read or enable practice-hfc scheduled mail. Does not send."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PRACTICE = "practice-hfc"
HOURS = Path("/data/hr_time.enc")
DATA = Path("/data")


def emit(payload: dict) -> None:
    print("ARM_MAIL " + json.dumps(payload, sort_keys=True), flush=True)


def adopt_uvicorn_env() -> list[int]:
    pids: list[int] = []
    adopted = False
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        if b"uvicorn" not in cmd or b"app.main:app" not in cmd:
            continue
        pids.append(int(pid))
        if adopted:
            continue
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
        adopted = True
    if not pids:
        raise SystemExit("uvicorn process not found")
    return pids


def freeze(pids: list[int]) -> None:
    for pid in pids:
        os.kill(pid, signal.SIGSTOP)


def resume(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
        except OSError:
            pass


def kill(pids: list[int]) -> None:
    for pid in pids:
        os.kill(pid, signal.SIGKILL)


def load_hours() -> dict:
    from app.crypto import decrypt_utf8

    wrapper = json.loads(HOURS.read_text(encoding="utf-8"))
    payload = json.loads(decrypt_utf8(base64.b64decode(str(wrapper["blob"]))))
    if not isinstance(payload, dict):
        raise SystemExit("hours store is not an object")
    return payload


def write_hours(payload: dict) -> None:
    from app.crypto import encrypt_utf8

    blob = encrypt_utf8(json.dumps(payload, separators=(",", ":")))
    wrapper = json.dumps(
        {"version": 1, "blob": base64.b64encode(blob).decode("ascii")},
        separators=(",", ":"),
    )
    tmp = HOURS.with_name("hr_time.enc.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, wrapper.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, HOURS)
    dir_fd = os.open(DATA, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def domains(emails: list) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for email in emails:
        text = str(email or "").strip().lower()
        counts[text.split("@", 1)[1] if "@" in text else "none"] += 1
    return dict(counts)


def report(payload: dict) -> dict:
    from app.hr.time_entry import (
        MELISSA_EMAIL,
        SCHEDULE_V2,
        TimeSettings,
        pay_week_ending,
        preview_new_sends,
        upcoming_slots,
        utcnow,
    )

    settings_rows = [row for row in payload.get("settings") or [] if isinstance(row, dict)]
    row = next((item for item in settings_rows if item.get("practice_id") == PRACTICE), None)
    if row is None:
        raise SystemExit("practice-hfc settings missing")
    settings = TimeSettings.from_dict(row)
    projected = TimeSettings.from_dict({**row, "automation_enabled": True})
    now = utcnow()
    week = pay_week_ending(now).isoformat()
    emails = list(settings.participant_emails)
    submitted = {
        str(item.get("email") or "").lower()
        for item in payload.get("submissions") or []
        if isinstance(item, dict)
        and item.get("practice_id") == PRACTICE
        and item.get("week_ending") == week
        and int(item.get("schedule_version") or 1) == SCHEDULE_V2
    }
    missing = [email for email in emails if email.lower() not in submitted]
    v2_locked = any(
        isinstance(item, dict)
        and item.get("practice_id") == PRACTICE
        and item.get("week_ending") == week
        and int(item.get("schedule_version") or 1) == SCHEDULE_V2
        and item.get("locked_at")
        for item in payload.get("weeks") or []
    )
    deliveries = [item for item in payload.get("deliveries") or [] if isinstance(item, dict)]
    return {
        "automation_enabled": settings.automation_enabled,
        "enabled_count": len(emails),
        "enabled_domains": domains(emails),
        "week_ending": week,
        "missing_count": len(missing),
        "submitted_count": len(submitted),
        "summary_is_melissa": settings.summary_recipient == MELISSA_EMAIL,
        "summary_domain": domains([settings.summary_recipient]),
        "reminder_first": f"{settings.reminder_first_weekday} {settings.reminder_first_time}",
        "reminder_due": f"{settings.reminder_due_weekday} {settings.reminder_due_time}",
        "summary_at": f"{settings.summary_weekday} {settings.summary_time}",
        "schedule_version": settings.schedule_version,
        "v2_week_locked": v2_locked,
        "delivery_statuses": dict(Counter(str(item.get("status") or "") for item in deliveries)),
        "would_send_now": preview_new_sends(projected, now, locked=v2_locked),
        "upcoming": upcoming_slots(projected, now),
        "now": now.isoformat(),
    }


def hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DATA.iterdir())
        if path.is_file()
    }


def dry_run() -> dict:
    info = report(load_hours())
    info["action"] = "dry-run"
    return info


def enable() -> dict:
    before = hashes()
    payload = load_hours()
    info = report(payload)
    if info["would_send_now"]:
        raise SystemExit("refusing to enable: a send window is open")
    if info["delivery_statuses"]:
        raise SystemExit("refusing to enable: deliveries already exist")
    rows = payload.get("settings") or []
    from app.hr.time_entry import MELISSA_EMAIL, TimeSettings

    updated = False
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("practice_id") != PRACTICE:
            continue
        settings = TimeSettings.from_dict(row)
        settings.automation_enabled = True
        if settings.summary_recipient != MELISSA_EMAIL:
            settings.summary_recipient = MELISSA_EMAIL
            info["summary_recipient_corrected"] = True
        rows[index] = settings.to_dict()
        updated = True
        break
    if not updated:
        raise SystemExit("practice-hfc settings missing")
    payload["settings"] = rows
    write_hours(payload)
    confirmed = report(load_hours())
    if not confirmed["automation_enabled"] or not confirmed["summary_is_melissa"]:
        raise SystemExit("settings did not read back as enabled")
    if confirmed["would_send_now"]:
        raise SystemExit("enabled state would send immediately")
    after = hashes()
    changed = sorted(
        name
        for name in set(before) | set(after)
        if before.get(name) != after.get(name) and name != HOURS.name
    )
    if changed:
        raise SystemExit("unexpected file changes: " + ",".join(changed))
    confirmed["action"] = "enabled"
    confirmed["other_files_unchanged"] = True
    confirmed["summary_recipient_corrected"] = bool(info.get("summary_recipient_corrected"))
    return confirmed


def main() -> None:
    pids = adopt_uvicorn_env()
    mode = (os.environ.get("ARM_MODE") or "dry-run").strip().lower()
    freeze(pids)
    try:
        if mode == "dry-run":
            emit(dry_run())
            resume(pids)
            return
        if mode == "enable":
            emit(enable())
        else:
            raise SystemExit("unknown mode")
    except BaseException:
        resume(pids)
        raise
    kill(pids)


if __name__ == "__main__":
    main()
