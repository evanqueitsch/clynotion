"""Print the saved weekly-hours checklist. Does not write or send mail."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path


def _load_app_env() -> None:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"uvicorn" not in command or b"app.main" not in command:
            continue
        for item in (entry / "environ").read_bytes().split(b"\0"):
            if b"=" not in item:
                continue
            key, value = item.split(b"=", 1)
            os.environ[key.decode()] = value.decode()
        return
    raise SystemExit("uvicorn process was not found")


def main() -> None:
    _load_app_env()
    from app.crypto import decrypt_utf8

    path = Path(os.environ.get("ATTUNE_HR_TIME_DATA_PATH") or "/data/hr_time.enc")
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    payload = json.loads(decrypt_utf8(base64.b64decode(wrapper["blob"])))
    names: dict[str, str] = {}
    for snap in payload.get("directory") or []:
        if not isinstance(snap, dict):
            continue
        for person in snap.get("users") or []:
            if not isinstance(person, dict):
                continue
            email = str(person.get("email") or "").strip().lower()
            if email:
                names[email] = str(person.get("display_name") or email)
    print("CHECKLIST")
    for settings in payload.get("settings") or []:
        if not isinstance(settings, dict):
            continue
        print(
            f"PRACTICE {settings.get('practice_id')} "
            f"automation {bool(settings.get('automation_enabled'))}"
        )
        people = []
        for raw in settings.get("participant_emails") or []:
            email = str(raw).strip().lower()
            people.append((names.get(email, email), email))
        for name, email in sorted(people, key=lambda item: item[0].casefold()):
            print(f"ENABLED {name} {email}")


if __name__ == "__main__":
    main()
