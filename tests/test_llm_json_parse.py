"""Unit tests for LLM JSON extraction hardening (no network, no PHI)."""

from __future__ import annotations

import json

import pytest

from app.providers import _parse_json_object


def test_parse_plain_object() -> None:
    assert _parse_json_object('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_markdown_fenced() -> None:
    raw = """Here you go:
```json
{"supervisor": "Dana", "duration_minutes": 60}
```
"""
    assert _parse_json_object(raw)["supervisor"] == "Dana"


def test_parse_trailing_comma() -> None:
    raw = '{"a": 1, "nested": {"b": 2,},}'
    assert _parse_json_object(raw) == {"a": 1, "nested": {"b": 2}}


def test_parse_ignores_braces_inside_strings() -> None:
    raw = '{"evidence": {"guidance_given": "said {pause} then continued"}, "x": 1}'
    out = _parse_json_object(raw)
    assert "pause" in out["evidence"]["guidance_given"]
    assert out["x"] == 1


def test_parse_rejects_truncated() -> None:
    with pytest.raises(ValueError, match="truncated"):
        _parse_json_object('{"a": 1, "b": ')


def test_parse_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="object"):
        _parse_json_object("[1, 2, 3]")


def test_parse_prose_before_object() -> None:
    raw = 'Sure — extracting now.\n{"ok": true}\nThanks!'
    assert _parse_json_object(raw) == {"ok": True}


def test_unescaped_quote_still_fails_cleanly() -> None:
    # Model slip we cannot auto-fix — must raise, not hang.
    with pytest.raises(json.JSONDecodeError):
        _parse_json_object('{"quote": "he said "hello" then left"}')
