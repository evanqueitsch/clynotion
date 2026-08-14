"""Encrypted persistence for the due-engine obligation register."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

from app.crypto import decrypt_utf8, encrypt_utf8

ENV_DUE_PERSISTENCE = "ATTUNE_DUE_PERSISTENCE"
ENV_DUE_DATA_PATH = "ATTUNE_DUE_DATA_PATH"
DEFAULT_DUE_DATA_PATH = ".attune_data/due.enc"


def due_persist_mode() -> str:
    return (os.environ.get(ENV_DUE_PERSISTENCE) or "memory").strip().lower()


def due_persist_path() -> Path:
    raw = (os.environ.get(ENV_DUE_DATA_PATH) or DEFAULT_DUE_DATA_PATH).strip()
    return Path(raw)


class DuePersistence:
    """Load/save the full obligation map as one encrypted blob (IDs/actions only)."""

    def load(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def save(self, rows: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class MemoryDuePersistence(DuePersistence):
    def __init__(self) -> None:
        self._blob: Optional[bytes] = None

    def load(self) -> list[dict[str, Any]]:
        if self._blob is None:
            return []
        return json.loads(decrypt_utf8(self._blob))

    def save(self, rows: list[dict[str, Any]]) -> None:
        self._blob = encrypt_utf8(json.dumps(rows))

    def clear(self) -> None:
        self._blob = None


class FileDuePersistence(DuePersistence):
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or due_persist_path()

    def load(self) -> list[dict[str, Any]]:
        if not self._path.is_file():
            return []
        try:
            wrapper = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []
        b64 = wrapper.get("blob") if isinstance(wrapper, dict) else None
        if not b64:
            return []
        try:
            raw = base64.b64decode(str(b64))
            return json.loads(decrypt_utf8(raw))
        except (ValueError, TypeError, OSError):
            return []

    def save(self, rows: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        blob = encrypt_utf8(json.dumps(rows))
        payload = {
            "version": 1,
            "blob": base64.b64encode(blob).decode("ascii"),
        }
        self._path.write_text(json.dumps(payload), encoding="utf-8")

    def clear(self) -> None:
        if self._path.is_file():
            self._path.unlink()


def build_due_persistence() -> DuePersistence:
    if due_persist_mode() == "file":
        return FileDuePersistence()
    return MemoryDuePersistence()
