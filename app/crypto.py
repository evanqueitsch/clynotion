"""Fernet encryption helpers for at-rest session documents. Never log key material."""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

ENV_KEY = "ATTUNE_DATA_ENCRYPTION_KEY"
_ephemeral_key: Optional[bytes] = None


def generate_key() -> str:
    """Return a new Fernet key as an ascii string (url-safe base64)."""
    return Fernet.generate_key().decode("ascii")


def reset_ephemeral_key_for_tests() -> None:
    global _ephemeral_key
    _ephemeral_key = None


def _fernet(fernet: Optional[Fernet] = None) -> Fernet:
    if fernet is not None:
        return fernet
    env = os.environ.get(ENV_KEY, "").strip()
    if env:
        return Fernet(env.encode("ascii"))
    global _ephemeral_key
    if _ephemeral_key is None:
        # MOCK only: process-local; never written to disk.
        _ephemeral_key = Fernet.generate_key()
    return Fernet(_ephemeral_key)


def encrypt_utf8(plaintext: str, *, fernet: Optional[Fernet] = None) -> bytes:
    return _fernet(fernet).encrypt(plaintext.encode("utf-8"))


def decrypt_utf8(blob: bytes, *, fernet: Optional[Fernet] = None) -> str:
    try:
        return _fernet(fernet).decrypt(blob).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("decryption failed") from e
