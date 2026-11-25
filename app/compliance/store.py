"""Encrypted compliance file. Append-only screening results and audit entries.

This file is separate from credentialing.enc and employees.enc. Reads of those
files stay read-only. Nothing here deletes or rewrites an existing result.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

from app.crypto import decrypt_utf8, encrypt_utf8

ENV_PATH = "ATTUNE_COMPLIANCE_DATA_PATH"
ENV_MODE = "ATTUNE_COMPLIANCE_PERSISTENCE"
ENV_FILES = "ATTUNE_COMPLIANCE_FILES_PATH"
DEFAULT_PATH = ".attune_data/compliance.enc"
DEFAULT_FILES = ".attune_data/compliance_files"

LISTS = ("leie", "sam", "medicheck")
RESULTS = ("clear", "possible_match", "not_run")

_lock = threading.RLock()


def _path() -> Path:
    return Path((os.environ.get(ENV_PATH) or DEFAULT_PATH).strip())


def _files_path() -> Path:
    return Path((os.environ.get(ENV_FILES) or DEFAULT_FILES).strip())


def _file_mode() -> bool:
    return os.environ.get(ENV_MODE) == "file"


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def default_settings() -> dict[str, Any]:
    return {
        "monthly_screening_enabled": False,
        "integrity_check_enabled": False,
        "drive_folder_id": "",
        "last_monthly_key": "",
        "last_integrity_day": "",
        "last_drive_day": "",
    }


def _practice_blob() -> dict[str, Any]:
    return {
        "settings": default_settings(),
        "screenings": [],
        "resolutions": [],
        "manual_checks": [],
        "audit": [],
        "integrity": [],
        "drive_copies": [],
        "outside_supervisors": [],
        "genesis": None,
    }


class ComplianceStore:
    def __init__(self) -> None:
        self._memory: Optional[dict[str, Any]] = None

    def reset(self) -> None:
        with _lock:
            self._memory = None
            if not _file_mode():
                self._memory = _empty()

    def _read(self) -> dict[str, Any]:
        if not _file_mode():
            if self._memory is None:
                self._memory = _empty()
            return self._memory
        path = _path()
        if not path.is_file():
            return _empty()
        payload = json.loads(decrypt_utf8(path.read_bytes()))
        if not isinstance(payload, dict):
            return _empty()
        payload.setdefault("practices", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        if not _file_mode():
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
            row = _practice_blob()
            practices[practice_id] = row
        row.setdefault("settings", default_settings())
        for key, value in default_settings().items():
            row["settings"].setdefault(key, value)
        for key in ("screenings", "resolutions", "manual_checks", "audit", "integrity", "drive_copies", "outside_supervisors"):
            if not isinstance(row.get(key), list):
                row[key] = []
        return row

    def settings(self, practice_id: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return default_settings()
            found = dict(default_settings())
            raw = row.get("settings") if isinstance(row.get("settings"), dict) else {}
            found.update({key: raw.get(key, value) for key, value in found.items()})
            found["monthly_screening_enabled"] = bool(found["monthly_screening_enabled"])
            found["integrity_check_enabled"] = bool(found["integrity_check_enabled"])
            found["drive_folder_id"] = str(found.get("drive_folder_id") or "")
            return found

    def any_enabled(self) -> dict[str, bool]:
        """Public health flags. No staff data."""
        with _lock:
            payload = self._read()
            monthly = False
            drive = False
            for row in (payload.get("practices") or {}).values():
                if not isinstance(row, dict):
                    continue
                settings = row.get("settings") if isinstance(row.get("settings"), dict) else {}
                monthly = monthly or bool(settings.get("monthly_screening_enabled"))
                drive = drive or bool(str(settings.get("drive_folder_id") or "").strip())
            return {"monthly": monthly, "drive": drive}

    def save_settings(self, practice_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(practice_id, payload)
            settings = row["settings"]
            if "monthly_screening_enabled" in updates:
                settings["monthly_screening_enabled"] = bool(updates["monthly_screening_enabled"])
            if "integrity_check_enabled" in updates:
                settings["integrity_check_enabled"] = bool(updates["integrity_check_enabled"])
            if "drive_folder_id" in updates:
                settings["drive_folder_id"] = str(updates.get("drive_folder_id") or "").strip()
            for key in ("last_monthly_key", "last_integrity_day", "last_drive_day"):
                if key in updates:
                    settings[key] = str(updates.get(key) or "")
            self._save(payload)
            return self.settings(practice_id)

    def _append(self, practice_id: str, bucket: str, item: dict[str, Any]) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(practice_id, payload)
            stored = dict(item)
            row[bucket].append(stored)
            self._save(payload)
            return stored

    def append_screening(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        stored = dict(item)
        stored["id"] = _new_id("scr-")
        return self._append(practice_id, "screenings", stored)

    def set_list_records(
        self,
        practice_id: str,
        result_id: str,
        records: list[dict[str, str]],
        *,
        replace: bool = False,
    ) -> None:
        """Add list-entry detail onto one result. The result status is left as stored."""
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return
            for item in row.get("screenings") or []:
                if not isinstance(item, dict) or item.get("id") != result_id:
                    continue
                if "list_records" in item and item.get("list_records") and not replace:
                    return
                status = item.get("result")
                item["list_records"] = [dict(record) for record in records]
                item["result"] = status
                self._save(payload)
                return

    def screenings(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("screenings") or [] if isinstance(item, dict)]

    def append_resolution(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        stored = dict(item)
        stored["id"] = _new_id("res-")
        return self._append(practice_id, "resolutions", stored)

    def resolutions(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("resolutions") or [] if isinstance(item, dict)]

    def append_manual(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        stored = dict(item)
        stored["id"] = _new_id("man-")
        return self._append(practice_id, "manual_checks", stored)

    def manual_checks(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("manual_checks") or [] if isinstance(item, dict)]

    def write_manual_file(self, practice_id: str, manual_id: str, data: bytes) -> None:
        """Encrypted attachment. The id is new; this does not replace another file."""
        import base64

        root = _files_path() / practice_id
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{manual_id}.enc"
        if target.exists():
            raise FileExistsError(manual_id)
        blob = encrypt_utf8(base64.b64encode(data).decode("ascii"))
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, blob)
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_manual_file(self, practice_id: str, manual_id: str) -> bytes:
        import base64

        target = _files_path() / practice_id / f"{manual_id}.enc"
        if not target.is_file():
            raise FileNotFoundError(manual_id)
        text = decrypt_utf8(target.read_bytes())
        return base64.b64decode(text)

    def audit_entries(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("audit") or [] if isinstance(item, dict)]

    def append_audit(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        return self._append(practice_id, "audit", dict(item))

    def append_integrity(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        stored = dict(item)
        stored["id"] = _new_id("int-")
        return self._append(practice_id, "integrity", stored)

    def integrity(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("integrity") or [] if isinstance(item, dict)]

    def append_drive_copy(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        stored = dict(item)
        stored["id"] = _new_id("drv-")
        return self._append(practice_id, "drive_copies", stored)

    def outside_supervisors(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = payload.get("practices", {}).get(practice_id)
            if not isinstance(row, dict):
                return []
            return [dict(item) for item in row.get("outside_supervisors") or [] if isinstance(item, dict)]

    def save_outside_supervisor(self, practice_id: str, item: dict[str, Any]) -> dict[str, Any]:
        """Add a paid outside supervisor, or mark one inactive. Rows are not removed."""
        with _lock:
            payload = self._read()
            row = self._practice(practice_id, payload)
            supervisor_id = str(item.get("id") or "").strip()
            if supervisor_id:
                for current in row["outside_supervisors"]:
                    if current.get("id") == supervisor_id:
                        if "active" in item:
                            current["active"] = bool(item["active"])
                        self._save(payload)
                        return dict(current)
                raise KeyError(supervisor_id)
            stored = {
                "id": _new_id("sup-"),
                "name": str(item.get("name") or "").strip(),
                "npi_masked": str(item.get("npi_masked") or ""),
                "paid": bool(item.get("paid", True)),
                "active": True,
                "kind": "outside_supervisor",
            }
            if not stored["name"]:
                raise ValueError("Name is required.")
            row["outside_supervisors"].append(stored)
            self._save(payload)
            return dict(stored)

    def practices(self) -> list[str]:
        with _lock:
            payload = self._read()
            return [str(key) for key in (payload.get("practices") or {})]


compliance_store = ComplianceStore()
