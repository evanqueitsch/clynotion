"""Pipeline: audio/transcript → extract → validate (+ one repair) → deterministic note → finalize."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, TypeVar

from pydantic import BaseModel, ValidationError

from app.audit import AuditAction, AuditReason, audit_log
from app.clinicians import PresentClinician
from app.notes import render_supervision_note
from app.providers import get_asr_provider, get_llm_extractor
from app.roster import reconcile_with_roster
from app.schemas import (
    CouplesFields,
    EmdrFields,
    PresentClinicianOut,
    RatingOverrides,
    SupervisionFields,
    SupervisionOverrides,
    VoiceAssignmentOut,
)
from app.store import Session, store
from app.voice_match import (
    TimedWord,
    apply_voice_assignments,
    assign_speakers_from_audio,
)

DELETE_AUDIO_ON_FINALIZE = os.environ.get("DELETE_AUDIO_ON_FINALIZE", "true").lower() != "false"

T = TypeVar("T", bound=BaseModel)


class ExtractionValidationError(ValueError):
    """Raised when extraction fails Pydantic validation even after one repair attempt."""

    def __init__(self, message: str, *, raw: dict, repair_raw: Optional[dict] = None) -> None:
        super().__init__(message)
        self.raw = raw
        self.repair_raw = repair_raw


class SpeakerMapIncompleteError(ValueError):
    """Raised when ≥2 participants and one or more speakers lack a clinician name."""

    def __init__(self, unmapped: list[str]) -> None:
        self.unmapped = unmapped
        super().__init__(
            "speaker_map incomplete; map these labels to clinician names before finalize: "
            + ", ".join(unmapped)
        )


def _validate_with_one_repair(
    *,
    transcript: str,
    raw: dict,
    model: type[T],
    repair_fn,
) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as first_err:
        repaired = repair_fn(transcript, raw, str(first_err))
        try:
            return model.model_validate(repaired)
        except ValidationError as second_err:
            raise ExtractionValidationError(
                f"extraction failed validation after one repair: {second_err}",
                raw=raw,
                repair_raw=repaired,
            ) from second_err


def extract_and_validate(transcript: str) -> SupervisionFields:
    """Extract supervision fields; on validation failure re-prompt once — never silently coerce."""
    extractor = get_llm_extractor()
    raw = extractor.extract_supervision_raw(transcript)
    return _validate_with_one_repair(
        transcript=transcript,
        raw=raw,
        model=SupervisionFields,
        repair_fn=extractor.repair_supervision_raw,
    )


def extract_and_validate_emdr(transcript: str) -> EmdrFields:
    """Parked EMDR path — not Phase 1 default."""
    extractor = get_llm_extractor()
    raw = extractor.extract_emdr_raw(transcript)
    return _validate_with_one_repair(
        transcript=transcript,
        raw=raw,
        model=EmdrFields,
        repair_fn=extractor.repair_emdr_raw,
    )


def extract_and_validate_couples(transcript: str) -> CouplesFields:
    extractor = get_llm_extractor()
    raw = extractor.extract_couples_raw(transcript)
    return _validate_with_one_repair(
        transcript=transcript,
        raw=raw,
        model=CouplesFields,
        repair_fn=extractor.repair_couples_raw,
    )


def _present_out(present: list[PresentClinician]) -> list[PresentClinicianOut]:
    return [
        PresentClinicianOut(
            clinician_id=p.clinician_id,
            role=p.role,
            display_name=p.display_name,
            voice_status=p.voice_status,
        )
        for p in present
    ]


def draft_from_transcript(
    transcript: str,
    *,
    practice_id: str,
    audio_path: Optional[str] = None,
    present: Optional[list[PresentClinician]] = None,
    capture_mode: str = "session_surface",
    voice_assignments: Optional[list[VoiceAssignmentOut]] = None,
) -> Session:
    fields = extract_and_validate(transcript)
    present = list(present or [])
    if present:
        fields = reconcile_with_roster(fields, present)
    note = render_supervision_note(fields)
    session = store.create(
        practice_id=practice_id,
        fields=fields,
        note=note,
        transcript=transcript,
        audio_path=audio_path,
        modality="supervision",
        capture_mode=capture_mode,
        present=_present_out(present),
    )
    session.voice_assignments = list(voice_assignments or [])  # type: ignore[attr-defined]
    audit_log.audit(AuditAction.DRAFT_CREATED, session.session_id)
    return session


def draft_from_audio(
    audio_path: str,
    *,
    practice_id: str,
    present: Optional[list[PresentClinician]] = None,
    capture_mode: str = "session_surface",
) -> Session:
    present = list(present or [])
    detailed = get_asr_provider().transcribe_detailed(audio_path)
    fields = extract_and_validate(detailed.text)

    voice_out: list[VoiceAssignmentOut] = []
    if present and detailed.words:
        timed = [
            TimedWord(word=w.word, speaker=w.speaker, start=w.start, end=w.end)
            for w in detailed.words
        ]
        assignments = assign_speakers_from_audio(
            practice_id=practice_id,
            audio_path=audio_path,
            words=timed,
            present=present,
        )
        fields = apply_voice_assignments(fields, assignments)
        voice_out = [
            VoiceAssignmentOut(
                speaker_label=a.speaker_label,
                clinician_id=a.clinician_id,
                display_name=a.display_name,
                score=round(a.score, 4),
                source=a.source,
            )
            for a in assignments
        ]

    if present:
        fields = reconcile_with_roster(fields, present)
    note = render_supervision_note(fields)
    session = store.create(
        practice_id=practice_id,
        fields=fields,
        note=note,
        transcript=detailed.text,
        audio_path=audio_path,
        modality="supervision",
        capture_mode=capture_mode,
        present=_present_out(present),
    )
    session.voice_assignments = voice_out  # type: ignore[attr-defined]
    audit_log.audit(AuditAction.DRAFT_CREATED, session.session_id)
    return session


def apply_overrides(
    fields: SupervisionFields, overrides: SupervisionOverrides
) -> SupervisionFields:
    data = fields.model_dump()
    patch = overrides.model_dump(exclude_none=True)
    for key, value in patch.items():
        data[key] = value
        if key == "speaker_map" and isinstance(value, dict):
            data.setdefault("evidence", {})["speaker_map"] = "clinician override: speaker_map"
            # Keep participant names in sync with map
            participants = list(data.get("participants") or [])
            for p in participants:
                label = p.get("speaker_label")
                if label in value and value[label]:
                    p["name"] = value[label]
            data["participants"] = participants
        else:
            data.setdefault("evidence", {})[key] = f"clinician override: {value}"
    return SupervisionFields.model_validate(data)


def apply_emdr_overrides(fields: EmdrFields, overrides: RatingOverrides) -> EmdrFields:
    data = fields.model_dump()
    for key, value in overrides.model_dump(exclude_none=True).items():
        data[key] = value
        data.setdefault("evidence", {})[key] = f"clinician override: {value}"
    return EmdrFields.model_validate(data)


def finalize_session(
    session_id: str, overrides: SupervisionOverrides | None = None
) -> Session:
    session = store.get(session_id)
    if session is None:
        raise KeyError(f"unknown session_id: {session_id}")
    if session.finalized:
        raise RuntimeError("session already finalized")

    overrides = overrides or SupervisionOverrides()
    applied = overrides.model_dump(exclude_none=True)
    fields = apply_overrides(session.fields, overrides)

    unmapped = fields.unmapped_speaker_labels()
    if unmapped:
        raise SpeakerMapIncompleteError(unmapped)

    note = render_supervision_note(fields)

    audio_deleted = False
    if DELETE_AUDIO_ON_FINALIZE and session.audio_path:
        path = Path(session.audio_path)
        if path.is_file():
            path.unlink()
            audio_deleted = True
        session.audio_path = None
        audit_log.audit(
            AuditAction.AUDIO_DELETED,
            session_id,
            reason=AuditReason.AUDIO_DELETED,
        )
    elif session.audio_path is None:
        audio_deleted = True
        audit_log.audit(
            AuditAction.AUDIO_DELETED,
            session_id,
            reason=AuditReason.NO_AUDIO,
        )

    session.fields = fields
    session.note = note
    session.finalized = True
    store.save(session)
    finalize_reason = (
        AuditReason.OVERRIDE_APPLIED if applied else AuditReason.FINALIZED
    )
    audit_log.audit(
        AuditAction.SESSION_FINALIZED,
        session_id,
        reason=finalize_reason,
    )
    session._audio_deleted = audio_deleted  # type: ignore[attr-defined]
    return session

