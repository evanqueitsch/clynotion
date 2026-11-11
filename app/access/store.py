"""Encrypted role and assignment store. One file per deployment, practice-scoped."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from app.access.permissions import TIME_ENTRY, admin_permissions
from app.crypto import decrypt_utf8, encrypt_utf8

ENV_PATH = "ATTUNE_ACCESS_DATA_PATH"
DEFAULT_PATH = ".attune_data/access_roles.enc"

SEEDED_ROLES: dict[str, dict[str, Any]] = {
    "intake": {"name": "Intake", "permissions": [TIME_ENTRY]},
    "billing": {"name": "Billing", "permissions": [TIME_ENTRY]},
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

    def _load(self) -> dict[str, Any]:
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

    def update_role_permissions(self, practice_id: str, role_id: str, permissions: list[str]) -> dict[str, Any]:
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
        row["roles"][role_id]["permissions"] = cleaned
        self._save(payload)
        return {"id": role_id, "permissions": cleaned}

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
