"""Phase 2: local voice enrollment + check-in matching (offline, no vendor)."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.audit import audit_log
from app.auth import issue_token, user_store
from app.clinicians import clinician_store, install_test_fixtures
from app.main import app
from app.store import store
from app.voice_id import cosine_similarity, get_voice_id_provider
from app.voice_match import TimedWord, assign_speakers_from_audio, verify_checkin


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ATTUNE_VOICE_ID", "local")
    monkeypatch.setenv("ATTUNE_VOICE_MATCH_THRESHOLD", "0.55")
    audit_log.clear()
    store.clear()
    clinician_store.reset()
    install_test_fixtures()
    yield
    audit_log.clear()
    store.clear()
    clinician_store.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _auth(username: str) -> dict[str, str]:
    _, user = user_store._users[username]
    return {"Authorization": f"Bearer {issue_token(user)}"}


def _write_tone_wav(path: Path, *, freq: float, seconds: float = 1.2, rate: int = 16000) -> None:
    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        # Mix a second harmonic so spectral fingerprints differ more clearly.
        val = 0.55 * math.sin(2 * math.pi * freq * i / rate)
        val += 0.25 * math.sin(2 * math.pi * (freq * 2) * i / rate)
        frames += struct.pack("<h", int(max(-1.0, min(1.0, val)) * 30000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)


def test_same_tone_embeds_more_similar_than_different(tmp_path: Path) -> None:
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    c = tmp_path / "a2.wav"
    _write_tone_wav(a, freq=220.0)
    _write_tone_wav(b, freq=880.0)
    _write_tone_wav(c, freq=220.0)
    provider = get_voice_id_provider()
    ea, eb, ec = provider.embed_file(str(a)), provider.embed_file(str(b)), provider.embed_file(str(c))
    assert cosine_similarity(ea, ec) > cosine_similarity(ea, eb)


def test_enroll_and_checkin_roundtrip(client: TestClient, tmp_path: Path) -> None:
    sample = tmp_path / "dana.wav"
    _write_tone_wav(sample, freq=196.0)
    r = client.post(
        "/clinicians/clin-a-dana/voice-enroll",
        headers=_auth("alice"),
        files={"audio": ("dana.wav", sample.read_bytes(), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["voice_status"] == "enrolled"

    check = client.post(
        "/clinicians/clin-a-dana/voice-checkin",
        headers=_auth("alice"),
        files={"audio": ("dana2.wav", sample.read_bytes(), "audio/wav")},
        data={"present_json": '[{"clinician_id":"clin-a-dana","role":"supervisor"}]'},
    )
    assert check.status_code == 200, check.text
    body = check.json()
    assert body["verified"] is True
    assert body["claimed_score"] >= body["threshold"]


def test_checkin_rejects_other_voice(client: TestClient, tmp_path: Path) -> None:
    dana = tmp_path / "dana.wav"
    jordan = tmp_path / "jordan.wav"
    _write_tone_wav(dana, freq=196.0)
    _write_tone_wav(jordan, freq=740.0)
    assert (
        client.post(
            "/clinicians/clin-a-dana/voice-enroll",
            headers=_auth("alice"),
            files={"audio": ("dana.wav", dana.read_bytes(), "audio/wav")},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/clinicians/clin-a-jordan/voice-enroll",
            headers=_auth("alice"),
            files={"audio": ("jordan.wav", jordan.read_bytes(), "audio/wav")},
        ).status_code
        == 200
    )
    # Check in as Dana using Jordan's tone — should not verify
    bad = client.post(
        "/clinicians/clin-a-dana/voice-checkin",
        headers=_auth("alice"),
        files={"audio": ("wrong.wav", jordan.read_bytes(), "audio/wav")},
        data={
            "present_json": (
                '[{"clinician_id":"clin-a-dana","role":"supervisor"},'
                '{"clinician_id":"clin-a-jordan","role":"supervisee"}]'
            )
        },
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["verified"] is False


def test_assign_speakers_from_audio_greedy(tmp_path: Path) -> None:
    dana = tmp_path / "dana.wav"
    jordan = tmp_path / "jordan.wav"
    session = tmp_path / "session.wav"
    _write_tone_wav(dana, freq=196.0, seconds=1.0)
    _write_tone_wav(jordan, freq=740.0, seconds=1.0)
    # Build a 2s session: dana then jordan
    _write_concat_tones(session, [(196.0, 1.0), (740.0, 1.0)])

    provider = get_voice_id_provider()
    clinician_store.enroll_voice(
        "practice-a",
        "clin-a-dana",
        sample_bytes=dana.stat().st_size,
        embedding=provider.embed_file(str(dana)),
    )
    clinician_store.enroll_voice(
        "practice-a",
        "clin-a-jordan",
        sample_bytes=jordan.stat().st_size,
        embedding=provider.embed_file(str(jordan)),
    )
    present = clinician_store.resolve_present(
        "practice-a",
        [("clin-a-dana", "supervisor"), ("clin-a-jordan", "supervisee")],
    )
    words = [
        TimedWord(word="hello", speaker=0, start=0.1, end=0.9),
        TimedWord(word="there", speaker=1, start=1.1, end=1.9),
    ]
    assignments = assign_speakers_from_audio(
        practice_id="practice-a",
        audio_path=str(session),
        words=words,
        present=present,
    )
    by_label = {a.speaker_label: a.display_name for a in assignments}
    assert by_label.get("Speaker 0") == "Dana Okonkwo"
    assert by_label.get("Speaker 1") == "Jordan Lee"


def _write_concat_tones(path: Path, parts: list[tuple[float, float]], rate: int = 16000) -> None:
    frames = bytearray()
    for freq, seconds in parts:
        n = int(rate * seconds)
        for i in range(n):
            val = 0.55 * math.sin(2 * math.pi * freq * i / rate)
            val += 0.25 * math.sin(2 * math.pi * (freq * 2) * i / rate)
            frames += struct.pack("<h", int(max(-1.0, min(1.0, val)) * 30000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)


def test_verify_checkin_helper(tmp_path: Path) -> None:
    sample = tmp_path / "s.wav"
    _write_tone_wav(sample, freq=330.0)
    emb = get_voice_id_provider().embed_file(str(sample))
    clinician_store.enroll_voice(
        "practice-a", "clin-a-sam", sample_bytes=10, embedding=emb
    )
    result = verify_checkin(
        practice_id="practice-a",
        clinician_id="clin-a-sam",
        audio_path=str(sample),
    )
    assert result["verified"] is True
