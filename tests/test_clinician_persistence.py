"""Encrypted clinician voice-profile persistence (survives store reload)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.clinicians import ClinicianStore, clinician_persist_path
from app.crypto import ENV_KEY, decrypt_utf8, generate_key, reset_ephemeral_key_for_tests


@pytest.fixture
def file_persist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    reset_ephemeral_key_for_tests()
    key = generate_key()
    monkeypatch.setenv(ENV_KEY, key)
    reset_ephemeral_key_for_tests()
    path = tmp_path / "clinicians.enc"
    monkeypatch.setenv("ATTUNE_CLINICIAN_PERSISTENCE", "file")
    monkeypatch.setenv("ATTUNE_CLINICIAN_DATA_PATH", str(path))
    return path


def test_persist_path_none_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTUNE_CLINICIAN_PERSISTENCE", raising=False)
    monkeypatch.delenv("ATTUNE_CLINICIAN_DATA_PATH", raising=False)
    assert clinician_persist_path() is None


def test_voice_enrollment_survives_reload(file_persist: Path) -> None:
    emb = [0.1, 0.2, 0.3] + [0.0] * 61
    store = ClinicianStore(load_disk=False)
    store.enroll_voice(
        "practice-a",
        "clin-a-dana",
        sample_bytes=99,
        embedding=emb,
    )
    assert file_persist.is_file()

    # Ciphertext must not contain plaintext name or raw embedding digits as JSON field labels alone —
    # stronger check: embedding floats not present as plaintext JSON array.
    raw = file_persist.read_bytes()
    assert b"Dana Okonkwo" not in raw
    assert b"0.1, 0.2, 0.3" not in raw

    reloaded = ClinicianStore(load_disk=True)
    clin = reloaded.get("practice-a", "clin-a-dana")
    assert clin is not None
    assert clin.voice.status == "enrolled"
    assert clin.voice.embedding == emb
    assert clin.voice.sample_bytes == 99


def test_clear_enrollment_persists(file_persist: Path) -> None:
    store = ClinicianStore(load_disk=False)
    store.enroll_voice(
        "practice-a",
        "clin-a-jordan",
        sample_bytes=10,
        embedding=[1.0] + [0.0] * 63,
    )
    store.clear_enrollment("practice-a", "clin-a-jordan")
    reloaded = ClinicianStore(load_disk=True)
    clin = reloaded.get("practice-a", "clin-a-jordan")
    assert clin is not None
    assert clin.voice.status == "none"
    assert clin.voice.embedding == []


def test_wrong_key_does_not_crash_startup(
    file_persist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClinicianStore(load_disk=False)
    store.enroll_voice(
        "practice-a",
        "clin-a-sam",
        sample_bytes=10,
        embedding=[0.5] + [0.0] * 63,
    )
    # Flip encryption key
    monkeypatch.setenv(ENV_KEY, Fernet.generate_key().decode("ascii"))
    reset_ephemeral_key_for_tests()
    reloaded = ClinicianStore(load_disk=True)
    clin = reloaded.get("practice-a", "clin-a-sam")
    assert clin is not None
    # Seed roster still present; enrollment not applied under wrong key
    assert clin.voice.status == "none"


def test_ciphertext_roundtrip_contains_embedding_after_decrypt(
    file_persist: Path,
) -> None:
    emb = [0.01, 0.02, 0.03] + [0.0] * 61
    store = ClinicianStore(load_disk=False)
    store.enroll_voice(
        "practice-a", "clin-a-dana", sample_bytes=5, embedding=emb
    )
    plain = json.loads(decrypt_utf8(file_persist.read_bytes()))
    assert plain["practices"]["practice-a"]["clin-a-dana"]["embedding"] == emb
