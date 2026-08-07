"""Validation repair + real-provider skip-when-no-key tests."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from app.pipeline import (
    ExtractionValidationError,
    extract_and_validate,
    extract_and_validate_emdr,
)
from app.providers import CANNED_SUPERVISION_RAW, LlmExtractor, get_asr_provider, get_llm_extractor
from app.schemas import EmdrFields


class _BadThenGoodSupervision(LlmExtractor):
    def __init__(self) -> None:
        self.calls = 0

    def extract_supervision_raw(self, transcript: str) -> dict:
        self.calls += 1
        raw = dict(CANNED_SUPERVISION_RAW)
        raw["duration_minutes"] = "sixty"  # invalid — must not silently coerce
        return raw

    def extract_emdr_raw(self, transcript: str) -> dict:
        return {}

    def extract_couples_raw(self, transcript: str) -> dict:
        return {}

    def repair_supervision_raw(self, transcript: str, previous: dict, error: str) -> dict:
        self.calls += 1
        fixed = dict(previous)
        fixed["duration_minutes"] = 60
        return fixed


class _AlwaysBadSupervision(LlmExtractor):
    def extract_supervision_raw(self, transcript: str) -> dict:
        raw = dict(CANNED_SUPERVISION_RAW)
        raw["duration_minutes"] = "sixty"
        return raw

    def extract_emdr_raw(self, transcript: str) -> dict:
        return {}

    def extract_couples_raw(self, transcript: str) -> dict:
        return {}

    def repair_supervision_raw(self, transcript: str, previous: dict, error: str) -> dict:
        return dict(previous)


class _BadThenGoodEmdr(LlmExtractor):
    def __init__(self) -> None:
        self.calls = 0

    def extract_supervision_raw(self, transcript: str) -> dict:
        return dict(CANNED_SUPERVISION_RAW)

    def extract_emdr_raw(self, transcript: str) -> dict:
        self.calls += 1
        return {
            "target_memory": "t",
            "image": "i",
            "negative_cognition": "n",
            "positive_cognition": "p",
            "suds_pre": 8,
            "suds_post": 2,
            "voc_pre": 3,
            "voc_post": 6,
            "phase": "Phase 4",
            "evidence": {},
        }

    def extract_couples_raw(self, transcript: str) -> dict:
        return {}

    def repair_emdr_raw(self, transcript: str, previous: dict, error: str) -> dict:
        self.calls += 1
        fixed = dict(previous)
        fixed["phase"] = 4
        return fixed


def test_emdr_rejects_string_phase_without_silent_coerce() -> None:
    with pytest.raises(ValidationError):
        EmdrFields(
            target_memory="t",
            image="i",
            negative_cognition="n",
            positive_cognition="p",
            suds_pre=8,
            suds_post=2,
            voc_pre=3,
            voc_post=6,
            phase="Phase 4",
        )


def test_validation_repair_succeeds_on_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    ext = _BadThenGoodSupervision()
    monkeypatch.setattr("app.pipeline.get_llm_extractor", lambda: ext)
    fields = extract_and_validate("synthetic transcript")
    assert fields.duration_minutes == 60
    assert ext.calls == 2


def test_validation_repair_surfaces_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    ext = _AlwaysBadSupervision()
    monkeypatch.setattr("app.pipeline.get_llm_extractor", lambda: ext)
    with pytest.raises(ExtractionValidationError):
        extract_and_validate("synthetic transcript")


def test_emdr_validation_repair_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    ext = _BadThenGoodEmdr()
    monkeypatch.setattr("app.pipeline.get_llm_extractor", lambda: ext)
    fields = extract_and_validate_emdr("synthetic transcript")
    assert fields.phase == 4
    assert ext.calls == 2


def test_deepgram_provider_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_ASR", "deepgram")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        get_asr_provider().transcribe("synthetic_audio/emdr.wav")


def test_anthropic_provider_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_LLM", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_llm_extractor()


@pytest.mark.skipif(
    not os.environ.get("DEEPGRAM_API_KEY") or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="real provider keys absent — CI stays MOCK-only",
)
def test_real_provider_classes_construct_when_keys_present() -> None:
    os.environ["ATTUNE_ASR"] = "deepgram"
    os.environ["ATTUNE_LLM"] = "anthropic"
    try:
        asr = get_asr_provider()
        llm = get_llm_extractor()
        assert asr.__class__.__name__ == "DeepgramAsrProvider"
        assert llm.__class__.__name__ == "AnthropicLlmExtractor"
    finally:
        os.environ["ATTUNE_ASR"] = "mock"
        os.environ["ATTUNE_LLM"] = "mock"
