"""Typed schemas. Protocol numbers (EMDR, future) are strict ints; supervision is Phase 1."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# --- Phase 1: clinical supervision (Clynotion) ---

ParticipantRole = Literal["supervisor", "supervisee", "other"]
SupervisionFormat = Literal["individual", "triadic", "group"]


class Participant(BaseModel):
    speaker_label: str = Field(min_length=1)
    name: str = ""
    role: ParticipantRole = "other"


class CaseDiscussed(BaseModel):
    label: str = Field(min_length=1)  # de-identified only, e.g. "Client A"
    presenting_focus: str = ""
    supervisee_owner: str = ""


class ActionItem(BaseModel):
    owner_name: str = ""
    item: str = Field(min_length=1)
    due: str = ""


class SupervisionFields(BaseModel):
    """Extracted + validated clinical supervision session fields."""

    session_date: str = ""
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=600)
    supervision_format: SupervisionFormat = "group"
    setting: str = ""
    supervisor: str = ""
    participants: list[Participant] = Field(default_factory=list)
    speaker_map: dict[str, str] = Field(default_factory=dict)
    agenda_items: list[str] = Field(default_factory=list)
    cases_discussed: list[CaseDiscussed] = Field(default_factory=list)
    discussion_themes: list[str] = Field(default_factory=list)
    guidance_given: str = ""
    supervisee_reflections: str = ""
    competency_focus: str = ""
    risk_ethics_flags: str = ""
    gatekeeping_notes: str = ""
    action_items: list[ActionItem] = Field(default_factory=list)
    plan_next: str = ""
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def reject_non_int_duration(cls, v: object) -> object:
        if v is None or v == "":
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be a JSON integer (not a string or other type)")
        return v

    @model_validator(mode="after")
    def sync_speaker_map_from_participants(self) -> SupervisionFields:
        """Fill speaker_map from participant names when map entry missing."""
        sm = dict(self.speaker_map)
        for p in self.participants:
            if p.name and p.speaker_label not in sm:
                sm[p.speaker_label] = p.name
        if sm != self.speaker_map:
            self.speaker_map = sm
        return self

    def unmapped_speaker_labels(self) -> list[str]:
        """Labels that still need a clinician name (multi-participant gate)."""
        if len(self.participants) < 2:
            return []
        missing: list[str] = []
        for p in self.participants:
            mapped = (self.speaker_map.get(p.speaker_label) or p.name or "").strip()
            if not mapped or mapped.lower().startswith("speaker"):
                missing.append(p.speaker_label)
        return missing

    def display_name(self, speaker_label: str) -> str:
        return self.speaker_map.get(speaker_label) or speaker_label


class PresentMember(BaseModel):
    """Who's in the room / on the call for this Clynotion session."""

    clinician_id: str = Field(min_length=1)
    role: ParticipantRole = "supervisee"


class ClinicianOut(BaseModel):
    clinician_id: str
    practice_id: str
    display_name: str
    default_role: ParticipantRole
    email: str = ""
    google_id: str = ""
    source: str = "seed"
    included: bool = True
    voice_status: str
    voice_enrolled_at: Optional[str] = None
    voice_sample_bytes: int = 0
    voice_enrolled: bool = False


class WorkspaceUserOut(BaseModel):
    google_id: str
    email: str
    display_name: str
    suspended: bool = False
    org_unit: str = ""
    already_included: bool = False


class WorkspaceIncludeMember(BaseModel):
    google_id: str = Field(min_length=1)
    email: str = Field(min_length=3)
    display_name: str = ""
    default_role: ParticipantRole = "supervisee"


class WorkspaceIncludeBody(BaseModel):
    members: list[WorkspaceIncludeMember] = Field(min_length=1)
    clear_seed_roster: bool = True


class PresentClinicianOut(BaseModel):
    clinician_id: str
    role: ParticipantRole
    display_name: str
    voice_status: str


class VoiceAssignmentOut(BaseModel):
    speaker_label: str
    clinician_id: str
    display_name: str
    score: float
    source: str = "voice_match"


class VoiceCheckinResponse(BaseModel):
    clinician_id: str
    display_name: str
    claimed_score: float
    threshold: float
    verified: bool
    top_match: Optional[dict[str, Any]] = None


class SupervisionOverrides(BaseModel):
    """Clinician overrides at finalize. speaker_map and set fields always win."""

    speaker_map: Optional[dict[str, str]] = None
    supervisor: Optional[str] = None
    supervision_format: Optional[SupervisionFormat] = None
    setting: Optional[str] = None
    guidance_given: Optional[str] = None
    supervisee_reflections: Optional[str] = None
    competency_focus: Optional[str] = None
    risk_ethics_flags: Optional[str] = None
    gatekeeping_notes: Optional[str] = None
    plan_next: Optional[str] = None
    agenda_items: Optional[list[str]] = None
    discussion_themes: Optional[list[str]] = None
    participants: Optional[list[Participant]] = None
    cases_discussed: Optional[list[CaseDiscussed]] = None
    action_items: Optional[list[ActionItem]] = None
    session_date: Optional[str] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=600)

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def reject_non_int_override(cls, v: object) -> object:
        if v is None or v == "":
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be a JSON integer (not a string or other type)")
        return v


class DraftResponse(BaseModel):
    session_id: str
    modality: str = "supervision"
    capture_mode: str = "session_surface"  # session_surface | meeting_bot
    fields: SupervisionFields
    note: str
    audio_path: Optional[str] = None
    unmapped_speakers: list[str] = Field(default_factory=list)
    asr_provider: str = "mock"
    llm_provider: str = "mock"
    used_vignette: bool = False  # True when MOCK ASR/LLM ignored real audio/words
    present: list[PresentClinicianOut] = Field(default_factory=list)
    roster_names: list[str] = Field(default_factory=list)
    voice_assignments: list[VoiceAssignmentOut] = Field(default_factory=list)


class FinalizeRequest(BaseModel):
    session_id: str
    overrides: SupervisionOverrides = Field(default_factory=SupervisionOverrides)


class FinalizeResponse(BaseModel):
    session_id: str
    modality: str = "supervision"
    fields: SupervisionFields
    note: str
    audio_deleted: bool


# --- Parked: EMDR (future phase) ---

class EmdrFields(BaseModel):
    """Extracted + validated EMDR session fields (parked — not Phase 1 default)."""

    target_memory: str
    image: str
    negative_cognition: str
    positive_cognition: str
    suds_pre: int = Field(ge=0, le=10)
    suds_post: int = Field(ge=0, le=10)
    voc_pre: int = Field(ge=1, le=7)
    voc_post: int = Field(ge=1, le=7)
    phase: int = Field(ge=1, le=8)
    bls_type: str = ""
    imagery_shift: str = ""
    closure_method: str = ""
    plan_next_target: str = ""
    emotions_body: str = ""
    evidence: dict[str, str] = Field(default_factory=dict)

    @field_validator("phase", "suds_pre", "suds_post", "voc_pre", "voc_post", mode="before")
    @classmethod
    def reject_non_int(cls, v: object) -> object:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be a JSON integer (not a string or other type)")
        return v


class CouplesFields(BaseModel):
    """Extracted + validated couples session fields (parked)."""

    pursuer: str
    withdrawer: str
    presenting_issue: str = ""
    cycle_named: str = ""
    intervention: str = ""
    partner_shifts: str = ""
    risk_screen: str = ""
    speakers: list[dict[str, Any]] = Field(default_factory=list)
    attributions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)


class RatingOverrides(BaseModel):
    """EMDR one-tap overrides (parked)."""

    suds_pre: Optional[int] = Field(default=None, ge=0, le=10)
    suds_post: Optional[int] = Field(default=None, ge=0, le=10)
    voc_pre: Optional[int] = Field(default=None, ge=1, le=7)
    voc_post: Optional[int] = Field(default=None, ge=1, le=7)
    phase: Optional[int] = Field(default=None, ge=1, le=8)

    @field_validator("phase", "suds_pre", "suds_post", "voc_pre", "voc_post", mode="before")
    @classmethod
    def reject_non_int_override(cls, v: object) -> object:
        if v is None:
            return v
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("must be a JSON integer (not a string or other type)")
        return v
