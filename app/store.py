"""Session store: in-process Session objects + encrypted-at-rest persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from app.persistence import EncryptedMemoryPersistence, SessionPersistence, build_persistence
from app.schemas import PresentClinicianOut, SupervisionFields

# PHI that must only exist inside the encrypted persistence blob — never plaintext at rest.
PHI_PERSISTED_FIELD_KEYS = (
    "guidance_given",
    "supervisee_reflections",
    "competency_focus",
    "risk_ethics_flags",
    "plan_next",
)


@dataclass
class Session:
    session_id: str
    practice_id: str
    fields: SupervisionFields
    note: str
    transcript: str = ""
    audio_path: Optional[str] = None
    finalized: bool = False
    modality: str = "supervision"
    capture_mode: str = "session_surface"
    present: list[PresentClinicianOut] = field(default_factory=list)


def _session_payload(session: Session) -> dict[str, Any]:
    """Full session document; encrypted wholesale by SessionPersistence."""
    fields = session.fields.model_dump()
    for key in PHI_PERSISTED_FIELD_KEYS:
        if key not in fields:
            raise ValueError(f"missing required PHI field before persist: {key}")
    return {
        "practice_id": session.practice_id,
        "modality": session.modality,
        "capture_mode": session.capture_mode,
        "present": [p.model_dump() for p in session.present],
        "transcript": session.transcript,
        "note": session.note,
        "fields": fields,
        "audio_path": session.audio_path,
        "finalized": session.finalized,
    }


def _session_from_payload(session_id: str, payload: dict[str, Any]) -> Session:
    present_raw = payload.get("present") or []
    return Session(
        session_id=session_id,
        practice_id=str(payload["practice_id"]),
        fields=SupervisionFields.model_validate(payload["fields"]),
        note=payload["note"],
        transcript=str(payload.get("transcript", "")),
        audio_path=payload.get("audio_path"),
        finalized=bool(payload.get("finalized", False)),
        modality=str(payload.get("modality", "supervision")),
        capture_mode=str(payload.get("capture_mode", "session_surface")),
        present=[PresentClinicianOut.model_validate(p) for p in present_raw],
    )


class SessionStore:
    def __init__(self, persistence: Optional[SessionPersistence] = None) -> None:
        self.persistence: SessionPersistence = persistence or build_persistence()
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        *,
        practice_id: str,
        fields: SupervisionFields,
        note: str,
        transcript: str = "",
        audio_path: Optional[str] = None,
        modality: str = "supervision",
        capture_mode: str = "session_surface",
        present: Optional[list[PresentClinicianOut]] = None,
    ) -> Session:
        if not practice_id:
            raise ValueError("practice_id is required")
        sid = str(uuid4())
        session = Session(
            session_id=sid,
            practice_id=practice_id,
            fields=fields,
            note=note,
            transcript=transcript,
            audio_path=audio_path,
            modality=modality,
            capture_mode=capture_mode,
            present=list(present or []),
        )
        self._sessions[sid] = session
        self.persistence.put(sid, _session_payload(session))
        return session

    def get(self, session_id: str) -> Optional[Session]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        payload = self.persistence.get(session_id)
        if payload is None:
            return None
        session = _session_from_payload(session_id, payload)
        self._sessions[session_id] = session
        return session

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self.persistence.put(session.session_id, _session_payload(session))

    def clear(self) -> None:
        self._sessions.clear()
        self.persistence.clear()

    def raw_ciphertext(self, session_id: str) -> Optional[bytes]:
        """At-rest blob for encryption acceptance tests."""
        return self.persistence.raw_ciphertext(session_id)


store = SessionStore(persistence=EncryptedMemoryPersistence())
