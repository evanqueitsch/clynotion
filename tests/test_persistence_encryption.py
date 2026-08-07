"""Encrypted-at-rest persistence — prove encryption, not encoding theater."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.crypto import (
    ENV_KEY,
    decrypt_utf8,
    generate_key,
    reset_ephemeral_key_for_tests,
)
from app.notes import render_supervision_note
from app.persistence import EncryptedMemoryPersistence
from app.providers import CANNED_SUPERVISION_RAW
from app.schemas import SupervisionFields
from app.store import PHI_PERSISTED_FIELD_KEYS, Session, SessionStore, _session_payload


SAMPLE_TRANSCRIPT = (
    "Speaker 0: I'm Dana Okonkwo. Speaker 1: Client A exposure pacing needs work."
)


@pytest.fixture
def key_a(monkeypatch: pytest.MonkeyPatch) -> str:
    reset_ephemeral_key_for_tests()
    key = generate_key()
    monkeypatch.setenv(ENV_KEY, key)
    reset_ephemeral_key_for_tests()
    return key


def _sample_session() -> Session:
    fields = SupervisionFields.model_validate(dict(CANNED_SUPERVISION_RAW))
    return Session(
        session_id="test-sess",
        practice_id="practice-test",
        fields=fields,
        note=render_supervision_note(fields),
        transcript=SAMPLE_TRANSCRIPT,
        audio_path=None,
        finalized=False,
        modality="supervision",
    )


def test_raw_column_contains_no_plaintext(key_a: str) -> None:
    fernet = Fernet(key_a.encode("ascii"))
    store = SessionStore(persistence=EncryptedMemoryPersistence(fernet=fernet))
    sample = _sample_session()
    session = store.create(
        practice_id="practice-test",
        fields=sample.fields,
        note=sample.note,
        transcript=sample.transcript,
    )
    raw = store.raw_ciphertext(session.session_id)
    assert raw is not None
    textish = raw.decode("latin-1", errors="ignore")
    for needle in ("Dana Okonkwo", "Client A", "exposure pacing", "telehealth"):
        assert needle.encode("utf-8") not in raw
        assert needle not in textish


def test_raw_row_has_no_phi_plaintext(key_a: str) -> None:
    fernet = Fernet(key_a.encode("ascii"))
    store = SessionStore(persistence=EncryptedMemoryPersistence(fernet=fernet))
    sample = _sample_session()
    session = store.create(
        practice_id="practice-test",
        fields=sample.fields,
        note=sample.note,
        transcript=sample.transcript,
    )
    raw = store.raw_ciphertext(session.session_id)
    assert raw is not None
    textish = raw.decode("latin-1", errors="ignore")
    for key in PHI_PERSISTED_FIELD_KEYS:
        value = getattr(sample.fields, key)
        if not value:
            continue
        assert value.encode("utf-8") not in raw
        assert value not in textish
    assert b"Dana Okonkwo" not in raw
    assert SAMPLE_TRANSCRIPT not in textish
    plain = json.loads(decrypt_utf8(raw, fernet=fernet))
    assert plain["fields"]["guidance_given"] == sample.fields.guidance_given
    assert plain["transcript"] == SAMPLE_TRANSCRIPT
    assert plain["note"] == sample.note


def test_key_flip_decryption_fails(key_a: str) -> None:
    fernet_a = Fernet(key_a.encode("ascii"))
    key_b = generate_key()
    assert key_b != key_a
    fernet_b = Fernet(key_b.encode("ascii"))

    persistence = EncryptedMemoryPersistence(fernet=fernet_a)
    persistence.put("s1", _session_payload(_sample_session()))
    raw = persistence.raw_ciphertext("s1")
    assert raw is not None

    roundtrip = json.loads(decrypt_utf8(raw, fernet=fernet_a))
    assert "Dana Okonkwo" in roundtrip["fields"]["supervisor"]
    assert "Dana Okonkwo" in roundtrip["transcript"]

    with pytest.raises((ValueError, InvalidToken)):
        decrypt_utf8(raw, fernet=fernet_b)

    persistence_b = EncryptedMemoryPersistence(fernet=fernet_b)
    persistence_b._rows["s1"] = raw
    with pytest.raises(ValueError):
        persistence_b.get("s1")


def test_encoding_theater_still_reversible_without_key() -> None:
    plain = json.dumps({"guidance_given": "slow the hierarchy one step"})
    theater = base64.b64encode(plain.encode("utf-8"))
    recovered = base64.b64decode(theater).decode("utf-8")
    assert "slow the hierarchy" in recovered
