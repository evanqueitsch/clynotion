"""Encrypted role and assignment store. One file per deployment, practice-scoped."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.access.permissions import (
    NEW_BILLING_GRANTS,
    SENSITIVE_GRANT_IDS,
    TIME_ENTRY,
    admin_permissions,
    billing_role_permissions,
)
from app.crypto import decrypt_utf8, encrypt_utf8

# Bumped once. The migration only appends Billing grants.
GRANTS_VERSION = 2

ENV_PATH = "ATTUNE_ACCESS_DATA_PATH"
DEFAULT_PATH = ".attune_data/access_roles.enc"

SEEDED_ROLES: dict[str, dict[str, Any]] = {
    "intake": {"name": "Intake", "permissions": [TIME_ENTRY]},
    "billing": {"name": "Billing", "permissions": billing_role_permissions()},
    "clinician": {"name": "Clinician", "permissions": [TIME_ENTRY]},
    "clinical_supervisor": {"name": "Clinical Supervisor", "permissions": [TIME_ENTRY]},
    "admin": {"name": "Admin", "permissions": admin_permissions()},
}


def _path() -> Path:
    raw = (os.environ.get(ENV_PATH) or DEFAULT_PATH).strip()
    return Path(raw)


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

    def _upgrade_grants(self, row: dict[str, Any]) -> bool:
        """Version 1 appends Billing grants. Version 2 only stamps the file.

        Never deletes a permission or an assignment. Employee View capabilities
        are not added to Billing here.
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
        row["grants_version"] = GRANTS_VERSION
        return True

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
        for row in (payload.get("practices") or {}).values():
            if not isinstance(row, dict):
                continue
            version = int(row.get("grants_version") or 0)
            if self._upgrade_grants(row):
                changed = True
                if version < 1:
                    needs_v1 = True
                if version < 2:
                    needs_v2 = True
        if changed:
            if needs_v1:
                self._backup_before_grants(".bak-grants-v1")
            if needs_v2:
                self._backup_before_grants(".bak-grants-v2")
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
