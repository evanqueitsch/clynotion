"""Read-only counts of encrypted production stores. Prints no tokens or note text."""

from __future__ import annotations

import base64
import json
import os
from collections import Counter
from pathlib import Path


def adopt_uvicorn_env() -> None:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        if b"uvicorn" not in cmd:
            continue
        for item in raw.split(b"\0"):
            if b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
        return
    raise SystemExit("uvicorn process not found")


def domain(email: object) -> str:
    text = str(email or "").strip().lower()
    if "@" not in text:
        return "none"
    return text.split("@", 1)[1]


def domains(emails: list) -> dict[str, int]:
    return dict(Counter(domain(item) for item in emails))


def load_json(path: str):
    file = Path(path)
    if not file.is_file():
        return None
    data = file.read_bytes()
    try:
        wrapper = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        wrapper = None
    if isinstance(wrapper, dict) and isinstance(wrapper.get("sessions"), dict):
        return {"session_count": len(wrapper["sessions"])}
    blob = data
    if isinstance(wrapper, dict) and "blob" in wrapper:
        blob = base64.b64decode(str(wrapper.get("blob") or ""))
    from app.crypto import decrypt_utf8

    return json.loads(decrypt_utf8(blob))


def emit(label: str, payload: object) -> None:
    print(f"INVENTORY {label} {json.dumps(payload, sort_keys=True)}")


def main() -> None:
    adopt_uvicorn_env()
    files = []
    for path in sorted(Path("/data").glob("*")):
        if path.is_file():
            files.append({"name": path.name, "bytes": path.stat().st_size})
    emit("files", files)

    secretish = ("KEY", "SECRET", "TOKEN", "PASSWORD", "JSON")
    flags = {}
    for key, value in sorted(os.environ.items()):
        if not (key.startswith("ATTUNE_") or key.startswith("TIME_") or key.startswith("CRON")):
            continue
        if any(part in key for part in secretish):
            flags[key] = "set" if value.strip() else "empty"
        elif key.endswith(("PATH", "MODE", "PERSISTENCE", "AUTH")) or key in {"TIME_MAIL_MODE"}:
            flags[key] = value
    emit("flags", flags)
    for name in ("ATTUNE_HR_ADMIN_EMAILS", "ATTUNE_OWNER_EMAILS"):
        parts = [item.strip() for item in (os.environ.get(name) or "").split(",") if item.strip()]
        emit(name, {"count": len(parts), "domains": domains(parts)})

    hours = load_json("/data/hr_time.enc")
    if not isinstance(hours, dict):
        emit("hours", "missing")
    else:
        settings = hours.get("settings") or []
        submissions = hours.get("submissions") or []
        emit(
            "hours",
            {
                "settings": [
                    {
                        "practice_id": row.get("practice_id"),
                        "participants": len(row.get("participant_emails") or []),
                        "participant_domains": domains(row.get("participant_emails") or []),
                        "automation_enabled": row.get("automation_enabled"),
                        "schedule_version": row.get("schedule_version"),
                        "schedule_migrated": row.get("schedule_migrated"),
                    }
                    for row in settings
                    if isinstance(row, dict)
                ],
                "weeks": [
                    {
                        "week_ending": row.get("week_ending"),
                        "schedule_version": row.get("schedule_version"),
                        "locked": bool(row.get("locked_at")),
                    }
                    for row in hours.get("weeks") or []
                    if isinstance(row, dict)
                ],
                "submissions": len(submissions),
                "submission_domains": domains(
                    [row.get("email") for row in submissions if isinstance(row, dict)]
                ),
                "submission_weeks": sorted(
                    {row.get("week_ending") for row in submissions if isinstance(row, dict)}
                ),
                "audits": len(hours.get("audits") or []),
                "deliveries": [
                    {
                        "kind": row.get("kind"),
                        "status": row.get("status"),
                        "recipient_domain": domain(row.get("recipient")),
                    }
                    for row in hours.get("deliveries") or []
                    if isinstance(row, dict)
                ],
                "self_tests": [
                    {
                        "email_domain": domain(row.get("email")),
                        "sends": {
                            kind: (item.get("status") if isinstance(item, dict) else "")
                            for kind, item in (row.get("sends") or {}).items()
                        },
                    }
                    for row in hours.get("self_tests") or []
                    if isinstance(row, dict)
                ],
                "directory_snapshots": len(hours.get("directory") or []),
            },
        )

    tokens = load_json("/data/workspace_tokens.enc")
    if not isinstance(tokens, dict):
        emit("workspace_tokens", "missing")
    else:
        practices = []
        for practice_id, row in (tokens.get("practices") or {}).items():
            if not isinstance(row, dict):
                continue
            gmail = row.get("gmail_send") if isinstance(row.get("gmail_send"), dict) else {}
            practices.append(
                {
                    "practice_id": practice_id,
                    "directory_refresh": bool(row.get("refresh_token")),
                    "directory_email_domain": domain(row.get("email")),
                    "gmail_refresh": bool(gmail.get("refresh_token")),
                    "gmail_sender_domain": domain(gmail.get("email")),
                    "gmail_sender_is_operations": (gmail.get("email") or "").lower()
                    == "operations@heart4change.org",
                }
            )
        emit("workspace_tokens", practices)

    roster = load_json("/data/clinicians.enc")
    if not isinstance(roster, dict):
        emit("roster", "missing")
    else:
        rows = []
        practices = roster.get("practices") if isinstance(roster.get("practices"), dict) else roster
        for practice_id, people in practices.items():
            if not isinstance(people, dict):
                continue
            roles = Counter()
            enrolled = 0
            included = 0
            emails = []
            admins = 0
            for person in people.values():
                if not isinstance(person, dict):
                    continue
                roles[str(person.get("default_role") or "")] += 1
                emails.append(person.get("email"))
                if person.get("included"):
                    included += 1
                voice = person.get("voice") if isinstance(person.get("voice"), dict) else {}
                if voice.get("status") == "enrolled":
                    enrolled += 1
                if person.get("default_role") == "admin":
                    admins += 1
            rows.append(
                {
                    "practice_id": practice_id,
                    "people": len(people),
                    "included": included,
                    "voice_enrolled": enrolled,
                    "roles": dict(roles),
                    "admin_count": admins,
                    "email_domains": domains(emails),
                }
            )
        emit("roster", rows)

    due = load_json("/data/due.enc")
    if isinstance(due, list):
        emit(
            "due",
            {
                "count": len(due),
                "by_status": dict(Counter(str(row.get("status")) for row in due if isinstance(row, dict))),
                "by_source": dict(Counter(str(row.get("source")) for row in due if isinstance(row, dict))),
                "by_domain": dict(Counter(str(row.get("domain")) for row in due if isinstance(row, dict))),
                "practices": sorted({str(row.get("practice_id")) for row in due if isinstance(row, dict)}),
            },
        )
    else:
        emit("due", "missing")

    for label, path, key in (
        ("ingest", "/data/ingest.enc", None),
        ("intake", "/data/intake.enc", None),
        ("eligibility", "/data/eligibility.enc", None),
        ("credentials", "/data/credentials.enc", None),
    ):
        payload = load_json(path)
        if payload is None:
            emit(label, "missing")
        elif isinstance(payload, list):
            emit(
                label,
                {
                    "count": len(payload),
                    "practices": sorted(
                        {str(row.get("practice_id")) for row in payload if isinstance(row, dict)}
                    ),
                },
            )
        elif isinstance(payload, dict):
            emit(label, {"keys": sorted(payload.keys()), "type": "object"})
        else:
            emit(label, {"type": type(payload).__name__})

    sessions = load_json("/data/sessions.enc")
    emit("sessions", sessions if isinstance(sessions, dict) else "missing")

    onboarding_path = os.environ.get("ATTUNE_HR_DATA_PATH") or ""
    if onboarding_path:
        payload = load_json(onboarding_path)
        if isinstance(payload, dict):
            emit(
                "onboarding",
                {
                    "path": onboarding_path,
                    "employees": len(payload.get("employees") or payload.get("onboarding") or []),
                    "keys": sorted(payload.keys()),
                },
            )
        else:
            emit("onboarding", {"path": onboarding_path, "present": payload is not None})
    else:
        emit("onboarding", {"path": "", "note": "ATTUNE_HR_DATA_PATH unset; file persistence not configured"})

    emit("access_roles", "present" if Path("/data/access_roles.enc").is_file() else "absent")
    emit("done", "read-only")


if __name__ == "__main__":
    main()
