"""Mask sensitive workforce values before they are stored on the audit chain."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"ssn|dob|date_of_birth|birth|bank|routing|account|license|npi|tin|caqh|fein|password|secret",
    re.IGNORECASE,
)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_LONG_DIGITS = re.compile(r"\b\d{9,}\b")
_PAY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?")


def mask_text(value: Any) -> str:
    raw = "" if value is None else str(value).strip()
    if not raw:
        return ""
    tail = raw[-4:] if len(raw) > 4 else raw
    return "••••" + tail


def scrub_text(value: Any, *, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    text = _PAY.sub("[pay]", text)
    text = _SSN.sub("[ssn]", text)
    text = _LONG_DIGITS.sub("[id]", text)
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit]
    return text


def _present(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return scrub_text(str(value), limit=80)
    if _SSN.search(str(value or "")) or _LONG_DIGITS.search(str(value or "")):
        return mask_text(value)
    return scrub_text(value)


def mask_change(field: str, before: Any, after: Any) -> dict[str, dict[str, str]]:
    if _SENSITIVE_KEY.search(field or ""):
        return {field: {"before": mask_text(before), "after": mask_text(after)}}
    return {field: {"before": _present(before), "after": _present(after)}}


def mask_diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    old = before or {}
    new = after or {}
    out: dict[str, dict[str, str]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) == new.get(key):
            continue
        out.update(mask_change(str(key), old.get(key), new.get(key)))
    return out
