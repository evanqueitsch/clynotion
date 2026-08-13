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
    REAL mode is the PHI path and fails closed:
      - refuses ATTUNE_ASR=mock / ATTUNE_LLM=mock outright (never a silent MOCK fallback)
      - requires ATTUNE_DATA_ENCRYPTION_KEY and ATTUNE_JWT_SECRET
      - requires the vendor API key for whichever real ASR/LLM provider is selected
        (deepgram/anthropic/openai — defaults to deepgram+anthropic when unset)
    Google auth requires OAuth client secrets, allowed domains, public URL, JWT secret.
    MOCK + ATTUNE_AUTH=dev may use ephemeral secrets.
    """
    mode = get_mode()
    if mode not in (MODE_MOCK, MODE_REAL):
        raise RuntimeError(f"Unknown ATTUNE_MODE={mode!r}; use 'mock' or 'real'")

    from app.auth import auth_mode
    from app.google_oauth import (
        ENV_ALLOWED_DOMAINS,
        ENV_CLIENT_ID,
        ENV_CLIENT_SECRET,
        ENV_PUBLIC_BASE_URL,
        allowed_domains,
        google_configured,
    )

    missing: list[str] = []
    refused: list[str] = []

    if mode == MODE_REAL:
        asr_explicit = (
            os.environ.get("ATTUNE_ASR") or os.environ.get("ATTUNE_ASR_PROVIDER") or ""
        ).strip().lower()
        llm_explicit = (
            os.environ.get("ATTUNE_LLM") or os.environ.get("ATTUNE_LLM_PROVIDER") or ""
        ).strip().lower()
        if asr_explicit == "mock":
            refused.append("ATTUNE_ASR=mock")
        if llm_explicit == "mock":
            refused.append("ATTUNE_LLM=mock")

        if not os.environ.get("ATTUNE_DATA_ENCRYPTION_KEY", "").strip():
            missing.append("ATTUNE_DATA_ENCRYPTION_KEY")
        if not os.environ.get("ATTUNE_JWT_SECRET", "").strip():
            missing.append("ATTUNE_JWT_SECRET")

        if not refused:
            # Only resolve concrete vendor providers once we know real mode isn't
            # already refusing to boot on an explicit mock request above.
            from app.providers import resolve_asr_mode, resolve_llm_mode

            if resolve_asr_mode() == "deepgram" and not os.environ.get(
                "DEEPGRAM_API_KEY", ""
            ).strip():
                missing.append("DEEPGRAM_API_KEY")
            if resolve_llm_mode() == "anthropic" and not os.environ.get(
                "ANTHROPIC_API_KEY", ""
            ).strip():
                missing.append("ANTHROPIC_API_KEY")
            if resolve_llm_mode() == "openai" and not os.environ.get(
                "OPENAI_API_KEY", ""
            ).strip():
                missing.append("OPENAI_API_KEY")

    if auth_mode() == "google":
        if not google_configured():
            missing.extend([ENV_CLIENT_ID, ENV_CLIENT_SECRET])
        if not allowed_domains():
            missing.append(ENV_ALLOWED_DOMAINS)
        if not os.environ.get(ENV_PUBLIC_BASE_URL, "").strip():
            missing.append(ENV_PUBLIC_BASE_URL)
        if not os.environ.get("ATTUNE_JWT_SECRET", "").strip():
            missing.append("ATTUNE_JWT_SECRET")

    if refused:
        raise RuntimeError(
            "Refusing to start: ATTUNE_MODE=real is a PHI path and must never use "
            "MOCK providers — " + ", ".join(sorted(set(refused)))
        )

    if missing:
        # de-dupe while preserving order
        seen: set[str] = set()
        ordered = []
        for item in missing:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        raise RuntimeError(
            "Refusing to start without required secrets/config: " + ", ".join(ordered)
        )
