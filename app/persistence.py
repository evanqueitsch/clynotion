"""Encrypted-at-rest session persistence.

- EncryptedMemoryPersistence: default for MOCK / local dev (ciphertext in process memory).
- PostgresSessionPersistence: stub only — real DB needs BAA'd hosting.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

from cryptography.fernet import Fernet

from app.crypto import encrypt_utf8, decrypt_utf8


class SessionPersistence(ABC):
    @abstractmethod
    def put(self, session_id: str, payload: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, session_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def raw_ciphertext(self, session_id: str) -> Optional[bytes]: ...


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


def build_persistence() -> SessionPersistence:
    mode = os.environ.get("ATTUNE_PERSISTENCE", "memory").strip().lower()
    if mode in ("", "memory", "encrypted_memory"):
        return EncryptedMemoryPersistence()
    if mode == "postgres":
        return PostgresSessionPersistence()
    raise RuntimeError(f"Unknown ATTUNE_PERSISTENCE={mode!r}; use memory|postgres")
