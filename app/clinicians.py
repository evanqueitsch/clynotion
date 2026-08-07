"""Practice clinician roster + local voice-profile enrollment (Phase 2).

Enrollment embeds a sample locally, stores the embedding in-process, deletes the sample.
No third-party voice-biometrics network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from app.schemas import ParticipantRole

VoiceEnrollmentStatus = Literal["none", "enrolled"]


@dataclass
class ClinicianVoiceProfile:
    """Voice signature — embedding only; sample bytes are never retained."""

    status: VoiceEnrollmentStatus = "none"
    enrolled_at: Optional[str] = None
    sample_bytes: int = 0
    embedding: list[float] = field(default_factory=list)

    def has_embedding(self) -> bool:
        return self.status == "enrolled" and bool(self.embedding)


@dataclass
class Clinician:
    clinician_id: str
    practice_id: str
    display_name: str
    default_role: ParticipantRole = "supervisee"
    voice: ClinicianVoiceProfile = field(default_factory=ClinicianVoiceProfile)

    def to_public_dict(self) -> dict:
        return {
            "clinician_id": self.clinician_id,
            "practice_id": self.practice_id,
            "display_name": self.display_name,
            "default_role": self.default_role,
            "voice_status": self.voice.status,
            "voice_enrolled_at": self.voice.enrolled_at,
            "voice_sample_bytes": self.voice.sample_bytes,
            "voice_enrolled": self.voice.has_embedding(),
        }


@dataclass(frozen=True)
class PresentClinician:
    clinician_id: str
    role: ParticipantRole
    display_name: str
    voice_status: VoiceEnrollmentStatus


class ClinicianStore:
    def __init__(self) -> None:
        self._by_practice: dict[str, dict[str, Clinician]] = {}
        self._seed()

    def _seed(self) -> None:
        """Synthetic practice rosters for MOCK/dev — not real clinicians."""
        self._by_practice = {
            "practice-a": {
                "clin-a-dana": Clinician(
                    clinician_id="clin-a-dana",
                    practice_id="practice-a",
                    display_name="Dana Okonkwo",
                    default_role="supervisor",
                ),
                "clin-a-jordan": Clinician(
                    clinician_id="clin-a-jordan",
                    practice_id="practice-a",
                    display_name="Jordan Lee",
                    default_role="supervisee",
                ),
                "clin-a-sam": Clinician(
                    clinician_id="clin-a-sam",
                    practice_id="practice-a",
                    display_name="Sam Rivera",
                    default_role="supervisee",
                ),
            },
            "practice-b": {
                "clin-b-morgan": Clinician(
                    clinician_id="clin-b-morgan",
                    practice_id="practice-b",
                    display_name="Morgan Blake",
                    default_role="supervisor",
                ),
                "clin-b-riley": Clinician(
                    clinician_id="clin-b-riley",
                    practice_id="practice-b",
                    display_name="Riley Chen",
                    default_role="supervisee",
                ),
            },
        }

    def list_for_practice(self, practice_id: str) -> list[Clinician]:
        rows = self._by_practice.get(practice_id, {})
        return sorted(rows.values(), key=lambda c: c.display_name.lower())

    def get(self, practice_id: str, clinician_id: str) -> Optional[Clinician]:
        return self._by_practice.get(practice_id, {}).get(clinician_id)

    def resolve_present(
        self,
        practice_id: str,
        present: list[tuple[str, ParticipantRole]],
    ) -> list[PresentClinician]:
        out: list[PresentClinician] = []
        seen: set[str] = set()
        for clinician_id, role in present:
            if clinician_id in seen:
                continue
            clin = self.get(practice_id, clinician_id)
            if clin is None:
                raise KeyError(clinician_id)
            seen.add(clinician_id)
            out.append(
                PresentClinician(
                    clinician_id=clin.clinician_id,
                    role=role,
                    display_name=clin.display_name,
                    voice_status=clin.voice.status,
                )
            )
        return out

    def gallery_embeddings(
        self, practice_id: str, clinician_ids: list[str]
    ) -> list[tuple[str, str, list[float]]]:
        """(clinician_id, display_name, embedding) for enrolled present members."""
        out: list[tuple[str, str, list[float]]] = []
        for cid in clinician_ids:
            clin = self.get(practice_id, cid)
            if clin is None or not clin.voice.has_embedding():
                continue
            out.append((clin.clinician_id, clin.display_name, list(clin.voice.embedding)))
        return out

    def enroll_voice(
        self,
        practice_id: str,
        clinician_id: str,
        *,
        sample_bytes: int,
        embedding: list[float],
    ) -> Clinician:
        clin = self.get(practice_id, clinician_id)
        if clin is None:
            raise KeyError(clinician_id)
        if sample_bytes <= 0:
            raise ValueError("enrollment sample empty")
        if not embedding:
            raise ValueError("embedding empty")
        clin.voice = ClinicianVoiceProfile(
            status="enrolled",
            enrolled_at=datetime.now(timezone.utc).isoformat(),
            sample_bytes=sample_bytes,
            embedding=list(embedding),
        )
        return clin

    # Back-compat alias used by older call sites
    def enroll_voice_stub(
        self,
        practice_id: str,
        clinician_id: str,
        *,
        sample_bytes: int,
        embedding: Optional[list[float]] = None,
    ) -> Clinician:
        emb = embedding if embedding is not None else []
        if not emb:
            # Deterministic tiny embedding from size so stub path still "enrolled"
            emb = [float((sample_bytes % 97) + 1)] + [0.0] * 63
            norm = sum(v * v for v in emb) ** 0.5 or 1.0
            emb = [v / norm for v in emb]
        return self.enroll_voice(
            practice_id, clinician_id, sample_bytes=sample_bytes, embedding=emb
        )

    def clear_enrollment(self, practice_id: str, clinician_id: str) -> Clinician:
        clin = self.get(practice_id, clinician_id)
        if clin is None:
            raise KeyError(clinician_id)
        clin.voice = ClinicianVoiceProfile()
        return clin

    def reset(self) -> None:
        self._seed()


clinician_store = ClinicianStore()
