"""Runtime mode and startup secret gates. Never commit secrets."""

from __future__ import annotations

import os
from pathlib import Path

ENV_MODE = "ATTUNE_MODE"
MODE_MOCK = "mock"
MODE_REAL = "real"

_ROOT = Path(__file__).resolve().parents[1]


def load_env_local(*, override: bool = False) -> Path | None:
    """
    Load key=value pairs from repo-root env.local (gitignored).
    Does not log values. Uses setdefault unless override=True so shell env wins.
    """
    path = _ROOT / "env.local"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            continue
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    return path


def get_mode() -> str:
    return os.environ.get(ENV_MODE, MODE_MOCK).strip().lower() or MODE_MOCK


def is_real_mode() -> bool:
    return get_mode() == MODE_REAL


def is_mock_mode() -> bool:
    return get_mode() == MODE_MOCK


def validate_startup_secrets() -> None:
    """
    REAL mode requires ATTUNE_DATA_ENCRYPTION_KEY and ATTUNE_JWT_SECRET.
    MOCK may use ephemeral secrets. Refuse unknown modes.
    """
    mode = get_mode()
    if mode not in (MODE_MOCK, MODE_REAL):
        raise RuntimeError(f"Unknown ATTUNE_MODE={mode!r}; use 'mock' or 'real'")
    if mode != MODE_REAL:
        return
    missing: list[str] = []
    if not os.environ.get("ATTUNE_DATA_ENCRYPTION_KEY", "").strip():
        missing.append("ATTUNE_DATA_ENCRYPTION_KEY")
    if not os.environ.get("ATTUNE_JWT_SECRET", "").strip():
        missing.append("ATTUNE_JWT_SECRET")
    if missing:
        raise RuntimeError(
            "REAL mode refuses to start without secrets set: " + ", ".join(missing)
        )
