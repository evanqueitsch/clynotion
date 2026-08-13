"""Real-PHI fail-closed gates (v0.7.0) — ATTUNE_MODE=real must never fall back to MOCK.

conftest.py forces ATTUNE_MODE=mock + ATTUNE_ASR/LLM=mock for the whole suite; each test here
explicitly monkeypatches the env it needs and never talks to a real vendor.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import validate_startup_secrets
from app.main import app
from app.providers import (
    MockAsrProvider,
    MockLlmExtractor,
    provider_modes,
    resolve_asr_mode,
    resolve_llm_mode,
)


def _real_mode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "real")
    monkeypatch.setenv("ATTUNE_AUTH", "dev")
    monkeypatch.delenv("ATTUNE_ASR", raising=False)
    monkeypatch.delenv("ATTUNE_LLM", raising=False)
    monkeypatch.delenv("ATTUNE_ASR_PROVIDER", raising=False)
    monkeypatch.delenv("ATTUNE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ATTUNE_DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ATTUNE_JWT_SECRET", raising=False)


def test_real_mode_defaults_to_deepgram_and_anthropic_not_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_mode_env(monkeypatch)
    assert resolve_asr_mode() == "deepgram"
    assert resolve_llm_mode() == "anthropic"


def test_mock_mode_still_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "mock")
    monkeypatch.delenv("ATTUNE_ASR", raising=False)
    monkeypatch.delenv("ATTUNE_LLM", raising=False)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_asr_mode() == "mock"
    assert resolve_llm_mode() == "mock"


def test_mock_asr_provider_raises_under_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "real")
    with pytest.raises(RuntimeError, match="real"):
        MockAsrProvider()


def test_mock_llm_extractor_raises_under_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "real")
    with pytest.raises(RuntimeError, match="real"):
        MockLlmExtractor()


def test_mock_providers_construct_fine_under_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "mock")
    MockAsrProvider()
    MockLlmExtractor()


def test_provider_modes_reports_mode_and_phi_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "mock")
    monkeypatch.setenv("ATTUNE_ASR", "mock")
    monkeypatch.setenv("ATTUNE_LLM", "mock")
    modes = provider_modes()
    assert modes == {"mode": "mock", "asr": "mock", "llm": "mock", "phi_path": False}

    monkeypatch.setenv("ATTUNE_MODE", "real")
    monkeypatch.setenv("ATTUNE_ASR", "deepgram")
    monkeypatch.setenv("ATTUNE_LLM", "anthropic")
    modes = provider_modes()
    assert modes == {"mode": "real", "asr": "deepgram", "llm": "anthropic", "phi_path": True}


def test_real_mode_refuses_explicit_mock_asr(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mode_env(monkeypatch)
    monkeypatch.setenv("ATTUNE_ASR", "mock")
    monkeypatch.setenv("ATTUNE_LLM", "anthropic")
    with pytest.raises(RuntimeError, match="ATTUNE_ASR=mock"):
        validate_startup_secrets()


def test_real_mode_refuses_explicit_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mode_env(monkeypatch)
    monkeypatch.setenv("ATTUNE_ASR", "deepgram")
    monkeypatch.setenv("ATTUNE_LLM", "mock")
    with pytest.raises(RuntimeError, match="ATTUNE_LLM=mock"):
        validate_startup_secrets()


def test_real_mode_requires_encryption_jwt_and_vendor_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_mode_env(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        validate_startup_secrets()
    msg = str(exc.value)
    assert "ATTUNE_DATA_ENCRYPTION_KEY" in msg
    assert "ATTUNE_JWT_SECRET" in msg
    assert "DEEPGRAM_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" in msg


def test_real_mode_requires_vendor_key_matching_resolved_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_mode_env(monkeypatch)
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", "k" * 32)
    monkeypatch.setenv("ATTUNE_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("ATTUNE_LLM", "openai")
    with pytest.raises(RuntimeError) as exc:
        validate_startup_secrets()
    msg = str(exc.value)
    assert "DEEPGRAM_API_KEY" in msg
    assert "OPENAI_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" not in msg


def test_real_mode_passes_when_fully_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _real_mode_env(monkeypatch)
    monkeypatch.setenv("ATTUNE_DATA_ENCRYPTION_KEY", "k" * 32)
    monkeypatch.setenv("ATTUNE_JWT_SECRET", "s" * 32)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "fake-deepgram-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    validate_startup_secrets()  # must not raise


def test_mock_mode_needs_no_vendor_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTUNE_MODE", "mock")
    monkeypatch.setenv("ATTUNE_AUTH", "dev")
    monkeypatch.delenv("ATTUNE_DATA_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ATTUNE_JWT_SECRET", raising=False)
    validate_startup_secrets()  # must not raise


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_mode_and_phi_path_mock(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["phi_path"] is False
    assert body["asr"] == "mock"
    assert body["llm"] == "mock"
