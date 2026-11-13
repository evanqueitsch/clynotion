"""Read-only production check for Practice Owner. Prints permission ids only.

Does not write the roles file, credentialing, employees, or tokens, and does
not send mail.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

YES = [
    "tool:employee_view",
    "capability:hr_records_view",
    "tool:credentialing",
    "capability:payroll_dashboard",
    "tool:denials_summary",
]
DENY = [
    "capability:hr_records_manage",
    "capability:hr_sensitive",
    "capability:credentialing_manage",
    "capability:credentialing_payers",
    "capability:credentialing_sensitive",
    "tool:billing",
    "tool:hiring_onboarding",
    "capability:payroll_quickbooks_push",
    "capability:quickbooks_money",
    "tool:denials",
    "capability:phi_claims",
    "capability:manage_roles",
]


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


def _stat(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    info = path.stat()
    return (info.st_size, info.st_mtime_ns)


def _health_version() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as response:
            body = json.loads(response.read().decode())
    except Exception:
        return ""
    return str(body.get("version") or "")


def _rollout(practice_id: str) -> bool | None:
    from app.employees.store import employees_store

    return bool(employees_store.rollout_enabled(practice_id))


def main() -> None:
    adopt_uvicorn_env()
    access_path = Path((os.environ.get("ATTUNE_ACCESS_DATA_PATH") or "/data/access_roles.enc").strip())
    cred_path = Path((os.environ.get("ATTUNE_CREDENTIALING_DATA_PATH") or "/data/credentialing.enc").strip())
    employees_path = Path((os.environ.get("ATTUNE_EMPLOYEES_DATA_PATH") or "/data/employees.enc").strip())
    before = {str(path): _stat(path) for path in (access_path, cred_path, employees_path)}
    from app.crypto import decrypt_utf8

    payload = json.loads(decrypt_utf8(access_path.read_bytes()))
    practice_id = (os.environ.get("ATTUNE_DEFAULT_PRACTICE_ID") or "practice-hfc").strip()
    practice = (payload.get("practices") or {}).get(practice_id) or {}
    if int(practice.get("grants_version") or 0) < 3:
        print("VERIFY_NOT_READY")
        raise SystemExit(1)
    record = practice.get("practice_owner_migration") if isinstance(practice.get("practice_owner_migration"), dict) else {}
    email = str(record.get("email") or "").strip().lower()
    role = (practice.get("roles") or {}).get("practice_owner") or {}
    role_permissions = [item for item in (role.get("permissions") or []) if isinstance(item, str)]
    assignments = practice.get("assignments") if isinstance(practice.get("assignments"), dict) else {}
    from app.access.resolve import permissions_for
    from app.access.store import others_fingerprint
    from app.auth import User
    from app.hr.time_entry import MELISSA_EMAIL

    subject = User("access-check", "", practice_id, email)
    after = sorted(permissions_for(subject)) if email else []
    before_permissions = [item for item in (record.get("before_permissions") or []) if isinstance(item, str)]
    lost = sorted(set(before_permissions) - set(after))
    gained_forbidden = sorted(item for item in DENY if item in after and item not in before_permissions)
    admin_count = sum(1 for item in assignments.values() if item == "admin")
    billing_count = sum(1 for item in assignments.values() if item == "billing")
    previous = str(record.get("previous_role_id") or "")
    expected_billing = int(record.get("billing_count") or 0)
    if previous == "billing":
        expected_billing -= 1
    rollout = None
    try:
        rollout = _rollout(practice_id)
    except Exception:
        rollout = None
    report = {
        "version": _health_version(),
        "role_name": role.get("name") or "",
        "checked_permissions": role_permissions,
        "assigned": bool(record.get("assigned")),
        "assigned_known_mailbox": email == MELISSA_EMAIL.strip().lower(),
        "previous_role_id": previous,
        "before_permissions": before_permissions,
        "after_permissions": after,
        "lost": lost,
        "gained_forbidden": gained_forbidden,
        "payroll_correct_in_role": "capability:payroll_correct" in role_permissions,
        "payroll_dashboard_in_role": "capability:payroll_dashboard" in role_permissions,
        "other_assignments_match": record.get("other_assignments_sha256") == others_fingerprint(assignments, email),
        "operations_is_admin": assignments.get("operations@heart4change.org") == "admin",
        "admin_count": admin_count,
        "admin_count_unchanged": admin_count == int(record.get("admin_count") or 0),
        "billing_count": billing_count,
        "billing_count_expected": billing_count == expected_billing,
        "rollout_enabled": rollout,
        "credentialing_present": cred_path.is_file(),
    }
    after_stats = {str(path): _stat(path) for path in (access_path, cred_path, employees_path)}
    report["files_unchanged"] = before == after_stats
    print("VERIFY " + json.dumps(report, sort_keys=True))
    ok = (
        report["version"] == "0.19.10"
        and report["role_name"] == "Practice Owner"
        and role_permissions == YES
        and report["assigned"] is True
        and report["assigned_known_mailbox"] is True
        and not lost
        and not gained_forbidden
        and report["payroll_correct_in_role"] is False
        and report["payroll_dashboard_in_role"] is True
        and "capability:payroll_correct" in after
        and "capability:payroll_dashboard" in after
        and report["other_assignments_match"] is True
        and report["operations_is_admin"] is True
        and report["admin_count_unchanged"] is True
        and report["billing_count_expected"] is True
        and report["files_unchanged"] is True
        and report["credentialing_present"] is True
    )
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
