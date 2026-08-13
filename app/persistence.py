"""Encrypted-at-rest session persistence.

- EncryptedMemoryPersistence: default for MOCK / local dev (ciphertext in process memory).
- EncryptedFileSessionPersistence: single encrypted-at-rest file on disk (Fernet per row);
  survives restarts (e.g. Fly volume). Opt in with ATTUNE_PERSISTENCE=file.
- PostgresSessionPersistence: stub only — real DB needs BAA'd hosting.
"""

from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet

from app.crypto import encrypt_utf8, decrypt_utf8

ENV_PERSISTENCE = "ATTUNE_PERSISTENCE"
ENV_SESSION_DATA_PATH = "ATTUNE_SESSION_DATA_PATH"
DEFAULT_SESSION_DATA_PATH = ".attune_data/sessions.enc"


class SessionPersistence(ABC):
    @abstractmethod
    def put(self, session_id: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, session_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def raw_ciphertext(self, session_id: str) -> Optional[bytes]: ...

    @abstractmethod
    def list_ids(self) -> list[str]: ...


class EncryptedMemoryPersistence(SessionPersistence):
    """In-memory store of Fernet ciphertext blobs (never plaintext rows)."""

    def __init__(self, fernet: Optional[Fernet] = None) -> None:
        self._fernet = fernet
        self._rows: dict[str, bytes] = {}

    def put(self, session_id: str, payload: dict[str, Any]) -> None:
        blob = encrypt_utf8(json.dumps(payload), fernet=self._fernet)
        self._rows[session_id] = blob

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        raw = self._rows.get(session_id)
        if raw is None:
            return None
        try:
            return json.loads(decrypt_utf8(raw, fernet=self._fernet))
        except ValueError as e:
            raise ValueError(f"failed to decrypt session {session_id}") from e

    def clear(self) -> None:
        self._rows.clear()

    def raw_ciphertext(self, session_id: str) -> Optional[bytes]:
        return self._rows.get(session_id)

    def list_ids(self) -> list[str]:
        return list(self._rows.keys())


def session_persist_path() -> Path:
    """Path for the encrypted session file when ATTUNE_PERSISTENCE=file."""
    raw = (os.environ.get(ENV_SESSION_DATA_PATH) or DEFAULT_SESSION_DATA_PATH).strip()
    return Path(raw)


class EncryptedFileSessionPersistence(SessionPersistence):
    """
    Single encrypted-at-rest file holding one Fernet-encrypted row per session.

    Each session payload is individually encrypted (same as EncryptedMemoryPersistence);
    the file only ever contains ciphertext bytes (base64-wrapped), never plaintext PHI.
    Whole file is rewritten on each ``put``/``clear`` — fine at Phase-1 scale (single
    practice volume); swap for Postgres before real multi-tenant load.
    """

    def __init__(self, path: Optional[Path] = None, *, fernet: Optional[Fernet] = None) -> None:
        self._path = path or session_persist_path()
        self._fernet = fernet
        self._rows: dict[str, bytes] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return
        if not isinstance(raw, dict):
            return
        rows = raw.get("sessions")
        if not isinstance(rows, dict):
            return
        for sid, b64 in rows.items():
            try:
                self._rows[str(sid)] = base64.b64decode(str(b64))
            except (ValueError, TypeError):
                continue

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "sessions": {
                sid: base64.b64encode(blob).decode("ascii")
                for sid, blob in self._rows.items()
            },
        }
        self._path.write_text(json.dumps(payload), encoding="utf-8")

    def put(self, session_id: str, payload: dict[str, Any]) -> None:
        blob = encrypt_utf8(json.dumps(payload), fernet=self._fernet)
        self._rows[session_id] = blob
        self._flush()

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        raw = self._rows.get(session_id)
        if raw is None:
            return None
        try:
            return json.loads(decrypt_utf8(raw, fernet=self._fernet))
        except ValueError as e:
            raise ValueError(f"failed to decrypt session {session_id}") from e

    def clear(self) -> None:
        self._rows.clear()
        if self._path.is_file():
            self._path.unlink()

    def raw_ciphertext(self, session_id: str) -> Optional[bytes]:
        return self._rows.get(session_id)

    def list_ids(self) -> list[str]:
        return list(self._rows.keys())


class PostgresSessionPersistence(SessionPersistence):
    """
    Stub only. Does not open network connections — wiring a real DSN requires BAA'd infra.
    """

    def __init__(self) -> None:
        raise RuntimeError(
            "PostgresSessionPersistence is a stub. "
            "Provision BAA'd Postgres before enabling ATTUNE_PERSISTENCE=postgres."
        )

    def put(self, session_id: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def get(self, session_id: str) -> Optional[dict[str, Any]]:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def raw_ciphertext(self, session_id: str) -> Optional[bytes]:
        raise NotImplementedError

    def list_ids(self) -> list[str]:
        raise NotImplementedError


def build_persistence() -> SessionPersistence:
    mode = os.environ.get(ENV_PERSISTENCE, "memory").strip().lower()
    if mode in ("", "memory", "encrypted_memory"):
        return EncryptedMemoryPersistence()
    if mode == "file":
        return EncryptedFileSessionPersistence()
    if mode == "postgres":
        return PostgresSessionPersistence()
    raise RuntimeError(f"Unknown {ENV_PERSISTENCE}={mode!r}; use memory|file|postgres")
