"""Encrypted role and assignment store. One file per deployment, practice-scoped."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.access.permissions import (
    MANAGE_ROLES,
    NEW_BILLING_GRANTS,
    PAYROLL_CORRECT,
    PAYROLL_DASHBOARD,
    PRACTICE_OWNER_PERMISSIONS,
    SENSITIVE_GRANT_IDS,
    TIME_ENTRY,
    admin_permissions,
    billing_role_permissions,
)
from app.crypto import decrypt_utf8, encrypt_utf8

# Version 1 appends Billing grants. Version 2 stamps that file.
# Version 3 adds the Practice Owner role and assigns Melissa's mailbox.
# It does not remove a permission or change any other assignment.
GRANTS_VERSION = 3

ENV_PATH = "ATTUNE_ACCESS_DATA_PATH"
DEFAULT_PATH = ".attune_data/access_roles.enc"

SEEDED_ROLES: dict[str, dict[str, Any]] = {
    "intake": {"name": "Intake", "permissions": [TIME_ENTRY]},
    "billing": {"name": "Billing", "permissions": billing_role_permissions()},
    "clinician": {"name": "Clinician", "permissions": [TIME_ENTRY]},
    "clinical_supervisor": {"name": "Clinical Supervisor", "permissions": [TIME_ENTRY]},
    "practice_owner": {"name": "Practice Owner", "permissions": list(PRACTICE_OWNER_PERMISSIONS)},
    "admin": {"name": "Admin", "permissions": admin_permissions()},
}


def _path() -> Path:
    raw = (os.environ.get(ENV_PATH) or DEFAULT_PATH).strip()
    return Path(raw)


def others_fingerprint(assignments: dict[str, Any], skip_email: str) -> str:
    """Hash of every assignment except one mailbox. The digest has no addresses."""
    skip = (skip_email or "").strip().lower()
    pairs = [
        [str(email).strip().lower(), str(role)]
        for email, role in sorted(assignments.items(), key=lambda item: str(item[0]).lower())
        if str(email).strip().lower() != skip and role
    ]
    payload = json.dumps(pairs, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _practice_owner_practices() -> set[str]:
    from app.google_oauth import default_practice_id

    return {"practice-hfc", default_practice_id()}


def lookup_melissa_email(practice_id: str) -> str:
    """Melissa's heart4change.org mailbox from the roster.

    The payroll mailbox wins when that address is on the roster. Otherwise
    the only roster person whose name starts with Melissa. Practice HFC falls
    back to the payroll mailbox when the roster has no Melissa, because that
    is the account her payroll capabilities are bound to. Other people are
    not hardcoded.
    """
    from app.clinicians import clinician_store
    from app.hr.time_entry import MELISSA_EMAIL

    known = MELISSA_EMAIL.strip().lower()
    try:
        people = clinician_store.list_all_for_practice(practice_id)
    except Exception:
        people = []
    named: list[str] = []
    for person in people:
        email = (person.email or "").strip().lower()
        if not email:
            continue
        if email == known:
            return known
        first = (person.display_name or "").strip().lower().split(" ", 1)[0]
        if first == "melissa" and email.endswith("@heart4change.org"):
            if email not in named:
                named.append(email)
    if len(named) == 1:
        return named[0]
    if len(named) > 1:
        return ""
    if practice_id in _practice_owner_practices():
        return known
    return ""


def _effective_before(row: dict[str, Any], email: str, role_id: str) -> list[str]:
    """Effective permission ids before Practice Owner is applied. Ids only."""
    from app.hr.time_entry import MELISSA_EMAIL

    if role_id == "admin":
        perms = set(admin_permissions())
    elif role_id:
        role = (row.get("roles") or {}).get(role_id) or {}
        perms = {item for item in (role.get("permissions") or []) if isinstance(item, str)}
    else:
        perms = set()
    account = row.get("account_permissions") if isinstance(row.get("account_permissions"), dict) else {}
    for item in account.get(email) or []:
        if isinstance(item, str):
            perms.add(item)
    if email == MELISSA_EMAIL.strip().lower():
        perms.update({TIME_ENTRY, PAYROLL_DASHBOARD, PAYROLL_CORRECT})
    else:
        perms.discard(PAYROLL_CORRECT)
    return sorted(perms)


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


class AccessStore:
    def __init__(self) -> None:
        self._memory: Optional[dict[str, Any]] = None

    def reset(self) -> None:
        self._memory = None
        path = _path()
        if os.environ.get("ATTUNE_ACCESS_PERSISTENCE", "file") == "memory" and path.is_file():
            return
        if os.environ.get("ATTUNE_ACCESS_PERSISTENCE") == "memory":
            self._memory = _empty()

    def _read(self) -> dict[str, Any]:
        if os.environ.get("ATTUNE_ACCESS_PERSISTENCE") == "memory":
            if self._memory is None:
                self._memory = _empty()
            return self._memory
        path = _path()
        if not path.is_file():
            return _empty()
        try:
            payload = json.loads(decrypt_utf8(path.read_bytes()))
        except (ValueError, json.JSONDecodeError, OSError):
            return _empty()
        if not isinstance(payload, dict):
            return _empty()
        payload.setdefault("practices", {})
        return payload

    def _upgrade_grants(self, row: dict[str, Any], practice_id: str) -> bool:
        """Append-only grant migrations.

        Version 1 appends Billing grants. Version 2 only stamps the file.
        Version 3 adds Practice Owner and assigns Melissa. Never deletes a
        permission or changes any other person's assignment. Employee View
        capabilities are not added to Billing here, and Employee View rollout
        is not touched.
        """
        version = int(row.get("grants_version") or 0)
        if version >= GRANTS_VERSION:
            return False
        if version < 1:
            roles = row.setdefault("roles", {})
            billing = roles.get("billing")
            if not isinstance(billing, dict):
                billing = {"name": "Billing", "permissions": [TIME_ENTRY]}
                roles["billing"] = billing
            perms = [item for item in (billing.get("permissions") or []) if isinstance(item, str)]
            for item in NEW_BILLING_GRANTS:
                if item not in perms:
                    perms.append(item)
            billing["permissions"] = perms
            billing.setdefault("name", "Billing")
        if version < 3:
            self._seed_break_glass_admin(row, practice_id)
            self._install_practice_owner(row, practice_id)
        row["grants_version"] = GRANTS_VERSION
        return True

    def _seed_break_glass_admin(self, row: dict[str, Any], practice_id: str) -> None:
        """First creation only. A later clear of this assignment stays cleared."""
        assignments = row.setdefault("assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
            row["assignments"] = assignments
        if practice_id == "practice-hfc" and not assignments:
            assignments["operations@heart4change.org"] = "admin"

    def _install_practice_owner(self, row: dict[str, Any], practice_id: str) -> None:
        """Create Practice Owner. Assign Melissa on the practice that owns the roster.

        The role list is exactly the Practice Owner set. Permissions she already
        had from another role, other than role editing, are copied onto her
        account so the role stays exact and she does not lose them. HR manage
        and HR sensitive are not added here unless that previous role already
        had them. The code-level payroll bindings are not edited.
        """
        from app.hr.time_entry import MELISSA_EMAIL

        roles = row.setdefault("roles", {})
        assignments = row.setdefault("assignments", {})
        if not isinstance(assignments, dict):
            assignments = {}
            row["assignments"] = assignments
        if practice_id not in _practice_owner_practices():
            roles["practice_owner"] = {
                "name": "Practice Owner",
                "permissions": list(PRACTICE_OWNER_PERMISSIONS),
            }
            return
        email = lookup_melissa_email(practice_id)
        current = str(assignments.get(email) or "") if email else ""
        before = _effective_before(row, email, current) if email else []
        others = others_fingerprint(assignments, email)
        admin_count = sum(1 for role in assignments.values() if role == "admin")
        billing_count = sum(1 for role in assignments.values() if role == "billing")
        roles["practice_owner"] = {
            "name": "Practice Owner",
            "permissions": list(PRACTICE_OWNER_PERMISSIONS),
        }
        if not email or current == "admin":
            row["practice_owner_migration"] = {
                "assigned": False,
                "skipped": "admin" if current == "admin" else "no-mailbox",
                "email": email,
                "before_permissions": before,
                "other_assignments_sha256": others,
                "admin_count": admin_count,
                "billing_count": billing_count,
            }
            return
        previous: list[str] = []
        if current and current != "practice_owner":
            role = roles.get(current) if isinstance(roles.get(current), dict) else {}
            previous = [item for item in (role.get("permissions") or []) if isinstance(item, str)]
        covered = set(PRACTICE_OWNER_PERMISSIONS)
        if email == MELISSA_EMAIL.strip().lower():
            covered.update({TIME_ENTRY, PAYROLL_DASHBOARD, PAYROLL_CORRECT})
        extras: list[str] = []
        for item in previous:
            if item in covered or item == MANAGE_ROLES or item in extras:
                continue
            extras.append(item)
        if extras:
            account = row.setdefault("account_permissions", {})
            if not isinstance(account, dict):
                account = {}
                row["account_permissions"] = account
            existing = [item for item in (account.get(email) or []) if isinstance(item, str)]
            for item in extras:
                if item not in existing:
                    existing.append(item)
            account[email] = existing
        assignments[email] = "practice_owner"
        row["practice_owner_migration"] = {
            "assigned": True,
            "email": email,
            "previous_role_id": current,
            "before_permissions": before,
            "other_assignments_sha256": others,
            "admin_count": admin_count,
            "billing_count": billing_count,
        }

    def _backup_before_grants(self, suffix: str) -> None:
        if os.environ.get("ATTUNE_ACCESS_PERSISTENCE") == "memory":
            return
        path = _path()
        if not path.is_file():
            return
        dest = path.with_name(path.name + suffix)
        if dest.exists():
            return
        shutil.copy2(path, dest)

    def _load(self) -> dict[str, Any]:
        payload = self._read()
        changed = False
        needs_v1 = False
        needs_v2 = False
        needs_v3 = False
        for practice_id, row in (payload.get("practices") or {}).items():
            if not isinstance(row, dict):
                continue
            version = int(row.get("grants_version") or 0)
            if self._upgrade_grants(row, str(practice_id)):
                changed = True
                if version < 1:
                    needs_v1 = True
                if version < 2:
                    needs_v2 = True
                if version < 3:
                    needs_v3 = True
        if changed:
            if needs_v1:
                self._backup_before_grants(".bak-grants-v1")
            if needs_v2:
                self._backup_before_grants(".bak-grants-v2")
            if needs_v3:
                self._backup_before_grants(".bak-grants-v3")
            self._save(payload)
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        if os.environ.get("ATTUNE_ACCESS_PERSISTENCE") == "memory":
            self._memory = payload
            return
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = encrypt_utf8(json.dumps(payload))
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def _practice(self, practice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        practices = payload.setdefault("practices", {})
        row = practices.get(practice_id)
        if not isinstance(row, dict):
            row = {"roles": {}, "assignments": {}}
            practices[practice_id] = row
        roles = row.setdefault("roles", {})
        for role_id, seed in SEEDED_ROLES.items():
            current = roles.get(role_id)
            if not isinstance(current, dict):
                roles[role_id] = {
                    "name": seed["name"],
                    "permissions": list(seed["permissions"]),
                }
            elif role_id == "admin":
                current["name"] = "Admin"
                current["permissions"] = list(admin_permissions())
            else:
                current.setdefault("name", seed["name"])
                current.setdefault("permissions", list(seed["permissions"]))
        assignments = row.setdefault("assignments", {})
        # First creation only. Clearing this later stays cleared; break-glass
        # in resolve.is_owner still unlocks the owner mailbox.
        if practice_id == "practice-hfc" and not assignments:
            assignments["operations@heart4change.org"] = "admin"
        if int(row.get("grants_version") or 0) < GRANTS_VERSION:
            self._upgrade_grants(row, practice_id)
        return row

    def ensure(self, practice_id: str) -> dict[str, Any]:
        payload = self._load()
        row = self._practice(practice_id, payload)
        self._save(payload)
        return row

    def roles_public(self, practice_id: str) -> list[dict[str, Any]]:
        row = self.ensure(practice_id)
        out = []
        for role_id, role in row["roles"].items():
            out.append(
                {
                    "id": role_id,
                    "name": role.get("name") or role_id,
                    "permissions": list(role.get("permissions") or []),
                    "locked": role_id == "admin",
                }
            )
        out.sort(key=lambda item: item["name"].casefold())
        return out

    def update_role_permissions(
        self,
        practice_id: str,
        role_id: str,
        permissions: list[str],
        *,
        actor_email: str = "",
    ) -> dict[str, Any]:
        from app.access.permissions import grantable_ids

        if role_id == "admin":
            raise ValueError("The Admin role always has full product access except post-cutoff corrections.")
        if role_id not in SEEDED_ROLES:
            raise ValueError("Unknown role.")
        allowed = set(grantable_ids())
        cleaned = []
        for item in permissions:
            if item in allowed and item not in cleaned and item != "capability:payroll_correct":
                cleaned.append(item)
        payload = self._load()
        row = self._practice(practice_id, payload)
        previous = [item for item in (row["roles"][role_id].get("permissions") or []) if isinstance(item, str)]
        row["roles"][role_id]["permissions"] = cleaned
        self._audit_sensitive_grants(row, role_id, previous, cleaned, actor_email)
        self._save(payload)
        return {"id": role_id, "permissions": cleaned}

    def _audit_sensitive_grants(
        self,
        row: dict[str, Any],
        role_id: str,
        previous: list[str],
        cleaned: list[str],
        actor_email: str,
    ) -> None:
        events = row.setdefault("permission_audit", [])
        if not isinstance(events, list):
            events = []
            row["permission_audit"] = events
        now = datetime.now(timezone.utc).isoformat()
        actor = (actor_email or "").strip().lower()
        before, after = set(previous), set(cleaned)
        for permission in sorted(after - before):
            if permission in SENSITIVE_GRANT_IDS:
                events.append(
                    {
                        "at": now,
                        "actor": actor,
                        "role_id": role_id,
                        "permission": permission,
                        "action": "grant",
                    }
                )
        for permission in sorted(before - after):
            if permission in SENSITIVE_GRANT_IDS:
                events.append(
                    {
                        "at": now,
                        "actor": actor,
                        "role_id": role_id,
                        "permission": permission,
                        "action": "remove",
                    }
                )

    def assignments_public(self, practice_id: str) -> list[dict[str, str]]:
        row = self.ensure(practice_id)
        return [
            {"email": email, "role_id": role_id}
            for email, role_id in sorted((row.get("assignments") or {}).items())
            if role_id
        ]

    def set_assignment(
        self, practice_id: str, email: str, role_id: str, *, actor_email: str = ""
    ) -> None:
        cleaned = (email or "").strip().lower()
        actor = (actor_email or "").strip().lower()
        if "@" not in cleaned:
            raise ValueError("A staff email is required.")
        if role_id and role_id not in SEEDED_ROLES:
            raise ValueError("Unknown role.")
        payload = self._load()
        row = self._practice(practice_id, payload)
        assignments = row["assignments"]
        current = str(assignments.get(cleaned) or "")
        if current == "admin" and role_id != "admin":
            if actor and cleaned == actor:
                raise ValueError("You can't remove your own Admin role.")
            remaining = [item for item, role in assignments.items() if role == "admin" and item != cleaned]
            if not remaining:
                raise ValueError("The practice needs at least one Admin.")
        if role_id:
            assignments[cleaned] = role_id
        else:
            assignments.pop(cleaned, None)
        self._save(payload)

    def account_permissions_for(self, practice_id: str, email: str) -> list[str]:
        """Permissions stored on one mailbox. Empty when the migration added none."""
        row = self._practice(practice_id, self._load())
        account = row.get("account_permissions") if isinstance(row.get("account_permissions"), dict) else {}
        raw = account.get((email or "").strip().lower()) or []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str)]

    def role_id_for(self, practice_id: str, email: str) -> str:
        row = self._practice(practice_id, self._load())
        return str((row.get("assignments") or {}).get((email or "").strip().lower()) or "")

    def permissions_for_role(self, practice_id: str, role_id: str) -> list[str]:
        if not role_id:
            return []
        row = self._practice(practice_id, self._load())
        role = (row.get("roles") or {}).get(role_id) or {}
        if role_id == "admin":
            return list(admin_permissions())
        return list(role.get("permissions") or [])


access_store = AccessStore()
