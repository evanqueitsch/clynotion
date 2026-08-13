"""Encrypted clinician voice-profile persistence (survives store reload).

Only ``source=="workspace"`` clinicians are persisted to disk — synthetic/seed
fixtures (tests-only via ``install_test_fixtures()``) never touch disk, and a
legacy on-disk snapshot containing seed-source rows must never be loaded back
into memory (production rosters start empty; see app/clinicians.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.clinicians import ClinicianStore, clinician_persist_path
from app.crypto import (
    ENV_KEY,
    decrypt_utf8,
    encrypt_utf8,
    generate_key,
    reset_ephemeral_key_for_tests,
)


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


def _add_workspace_user(store: ClinicianStore, *, google_id: str, name: str) -> str:
    clin = store.upsert_workspace_user(
        "practice-a",
        google_id=google_id,
        email=f"{google_id}@example.com",
        display_name=name,
        default_role="supervisee",
    )
    return clin.clinician_id


def test_persist_path_none_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTUNE_CLINICIAN_PERSISTENCE", raising=False)
    monkeypatch.delenv("ATTUNE_CLINICIAN_DATA_PATH", raising=False)
    assert clinician_persist_path() is None


def test_voice_enrollment_survives_reload(file_persist: Path) -> None:
    emb = [0.1, 0.2, 0.3] + [0.0] * 61
    store = ClinicianStore(load_disk=False)
    cid = _add_workspace_user(store, google_id="g-dana", name="Dana Okonkwo")
    store.enroll_voice("practice-a", cid, sample_bytes=99, embedding=emb)
    assert file_persist.is_file()

    # Ciphertext must not contain plaintext name or raw embedding digits as JSON field labels alone —
    # stronger check: embedding floats not present as plaintext JSON array.
    raw = file_persist.read_bytes()
    assert b"Dana Okonkwo" not in raw
    assert b"0.1, 0.2, 0.3" not in raw

    reloaded = ClinicianStore(load_disk=True)
    clin = reloaded.get("practice-a", cid)
    assert clin is not None
    assert clin.source == "workspace"
    assert clin.voice.status == "enrolled"
    assert clin.voice.embedding == emb
    assert clin.voice.sample_bytes == 99


def test_clear_enrollment_persists(file_persist: Path) -> None:
    store = ClinicianStore(load_disk=False)
    cid = _add_workspace_user(store, google_id="g-jordan", name="Jordan Lee")
    store.enroll_voice(
        "practice-a",
        cid,
        sample_bytes=10,
        embedding=[1.0] + [0.0] * 63,
    )
    store.clear_enrollment("practice-a", cid)
    reloaded = ClinicianStore(load_disk=True)
    clin = reloaded.get("practice-a", cid)
    assert clin is not None
    assert clin.voice.status == "none"
    assert clin.voice.embedding == []


def test_wrong_key_does_not_crash_startup(
    file_persist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClinicianStore(load_disk=False)
    cid = _add_workspace_user(store, google_id="g-sam", name="Sam Rivera")
    store.enroll_voice(
        "practice-a",
        cid,
        sample_bytes=10,
        embedding=[0.5] + [0.0] * 63,
    )
    # Flip encryption key
    monkeypatch.setenv(ENV_KEY, Fernet.generate_key().decode("ascii"))
    reset_ephemeral_key_for_tests()
    reloaded = ClinicianStore(load_disk=True)
    # Wrong key -> load is skipped entirely (no crash); roster starts empty (no seed).
    assert reloaded.get("practice-a", cid) is None
    assert reloaded.list_all_for_practice("practice-a") == []


def test_ciphertext_roundtrip_contains_embedding_after_decrypt(
    file_persist: Path,
) -> None:
    emb = [0.01, 0.02, 0.03] + [0.0] * 61
    store = ClinicianStore(load_disk=False)
    cid = _add_workspace_user(store, google_id="g-dana2", name="Dana Okonkwo")
    store.enroll_voice("practice-a", cid, sample_bytes=5, embedding=emb)
    plain = json.loads(decrypt_utf8(file_persist.read_bytes()))
    assert plain["version"] == 2
    assert plain["practices"]["practice-a"][cid]["voice"]["embedding"] == emb
    assert plain["practices"]["practice-a"][cid]["source"] == "workspace"


def test_legacy_seed_rows_are_not_loaded(file_persist: Path) -> None:
    """A snapshot written by an older build with source='seed' rows must never load.

    Production rosters start empty; only Workspace-imported clinicians persist.
    """
    snapshot = {
        "version": 2,
        "practices": {
            "practice-a": {
                "clin-a-dana": {
                    "display_name": "Dana Okonkwo",
                    "default_role": "supervisor",
                    "email": "",
                    "google_id": "",
                    "source": "seed",
                    "included": True,
                    "voice": {
                        "status": "enrolled",
                        "enrolled_at": "2024-01-01T00:00:00+00:00",
                        "sample_bytes": 10,
                        "embedding": [1.0] + [0.0] * 63,
                    },
                }
            }
        },
    }
    file_persist.write_bytes(encrypt_utf8(json.dumps(snapshot)))
    reloaded = ClinicianStore(load_disk=True)
    assert reloaded.get("practice-a", "clin-a-dana") is None
    assert reloaded.list_all_for_practice("practice-a") == []
