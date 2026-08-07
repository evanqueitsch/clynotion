"""Invariant tests — MOCK mode, no API keys, no PHI in audit."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.audit import AuditLog, audit_log
from app.pipeline import (
    SpeakerMapIncompleteError,
    draft_from_audio,
    draft_from_transcript,
    finalize_session,
)
from app.providers import CANNED_SUPERVISION_RAW
from app.schemas import EmdrFields, RatingOverrides, SupervisionFields, SupervisionOverrides
from app.store import store


SAMPLE = Path("sample_supervision_transcript.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean():
    audit_log.clear()
    store.clear()
    yield
    audit_log.clear()
    store.clear()


def test_duration_non_int_rejected():
    with pytest.raises(ValidationError):
        SupervisionFields.model_validate({**CANNED_SUPERVISION_RAW, "duration_minutes": "60"})


def test_emdr_protocol_out_of_range_still_rejected():
    """Parked EMDR schema keeps strict integer guards for a future phase."""
    base = dict(
        target_memory="t",
        image="i",
        negative_cognition="n",
        positive_cognition="p",
        suds_pre=8,
        suds_post=2,
        voc_pre=3,
        voc_post=6,
        phase=4,
    )
    with pytest.raises(ValidationError):
        EmdrFields(**{**base, "suds_pre": 11})
    with pytest.raises(ValidationError):
        RatingOverrides(suds_pre=99)


def test_override_precedence_at_finalize():
    session = draft_from_transcript(SAMPLE, practice_id="practice-a")
    assert session.fields.supervisor == CANNED_SUPERVISION_RAW["supervisor"]
    assert len(session.fields.participants) >= 3

    finalized = finalize_session(
        session.session_id,
        SupervisionOverrides(
            guidance_given="Clinician-corrected guidance.",
            plan_next="Focus on containment drills",
        ),
    )
    assert finalized.fields.guidance_given == "Clinician-corrected guidance."
    assert finalized.fields.plan_next == "Focus on containment drills"
    assert "Clinician-corrected guidance." in finalized.note
    assert finalized.fields.supervisor == CANNED_SUPERVISION_RAW["supervisor"]


def test_speaker_map_required_for_multi_participant():
    session = draft_from_transcript(SAMPLE, practice_id="practice-a")
    data = session.fields.model_dump()
    data["speaker_map"] = {}
    data["participants"] = [
        {"speaker_label": "Speaker 0", "name": "", "role": "supervisor"},
        {"speaker_label": "Speaker 1", "name": "", "role": "supervisee"},
    ]
    session.fields = SupervisionFields.model_validate(data)
    store.save(session)
    with pytest.raises(SpeakerMapIncompleteError):
        finalize_session(session.session_id, SupervisionOverrides())

    # Mapping via override unblocks finalize
    finalized = finalize_session(
        session.session_id,
        SupervisionOverrides(
            speaker_map={"Speaker 0": "Dana Okonkwo", "Speaker 1": "Jordan Lee"}
        ),
    )
    assert finalized.finalized
    assert "Dana Okonkwo" in finalized.note


def test_audio_deleted_after_finalize():
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "sess.wav"
        audio.write_bytes(b"fake-wav")
        audio.with_suffix(".txt").write_text(SAMPLE, encoding="utf-8")
        session = draft_from_audio(str(audio), practice_id="practice-a")
        assert audio.exists()
        finalize_session(session.session_id, SupervisionOverrides())
        assert not audio.exists()


def test_audit_audio_deleted_no_phi():
    session = draft_from_transcript(SAMPLE, practice_id="practice-a")
    finalize_session(session.session_id, SupervisionOverrides())

    actions = [e["action"] for e in audit_log.events()]
    assert "audio_deleted" in actions
    assert "session_finalized" in actions

    forbidden_needles = [
        "dana okonkwo",
        "client a",
        "telehealth",
        "exposure",
        SAMPLE[:40].lower(),
    ]
    for event in audit_log.events():
        blob = str(event).lower()
        for needle in forbidden_needles:
            assert needle not in blob, f"PHI-like content in audit: {needle!r} in {event}"
        for key in event:
            assert key in {"ts", "action", "session_id", "reason"}


def test_audit_refuses_phi_keys():
    log = AuditLog()
    with pytest.raises(ValueError):
        log.audit("draft_created", "sid", transcript="secret")
    with pytest.raises(ValueError):
        log.audit("draft_created", "sid", note_text="secret")


def test_audit_raises_on_disallowed_key_and_invalid_reason():
    log = AuditLog()
    with pytest.raises(ValueError, match="Disallowed audit key"):
        log.audit("draft_created", "sid", detail="anything")
    with pytest.raises(ValueError, match="Invalid audit reason"):
        log.audit("audio_deleted", "sid", reason="because the client said so")
    with pytest.raises(ValueError, match="Invalid audit action"):
        log.audit("not_a_real_action", "sid")
