"""Practice clinician roster + local voice-profile enrollment (Phase 2).

Enrollment embeds a sample locally, stores the embedding, deletes the sample.
Optional encrypted file persistence so enrollments survive restarts.
No third-party voice-biometrics network calls.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from app.crypto import decrypt_utf8, encrypt_utf8
from app.schemas import ParticipantRole

VoiceEnrollmentStatus = Literal["none", "enrolled"]

ENV_CLINICIAN_PERSISTENCE = "ATTUNE_CLINICIAN_PERSISTENCE"
ENV_CLINICIAN_DATA_PATH = "ATTUNE_CLINICIAN_DATA_PATH"
DEFAULT_DATA_PATH = ".attune_data/clinicians.enc"


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


def clinician_persist_path() -> Optional[Path]:
    """Return encrypted store path when ATTUNE_CLINICIAN_PERSISTENCE=file; else None."""
    mode = (os.environ.get(ENV_CLINICIAN_PERSISTENCE) or "memory").strip().lower()
    if mode in ("", "memory", "none", "off"):
        return None
    if mode != "file":
        raise RuntimeError(
            f"Unknown {ENV_CLINICIAN_PERSISTENCE}={mode!r}; use memory|file"
        )
    raw = (os.environ.get(ENV_CLINICIAN_DATA_PATH) or DEFAULT_DATA_PATH).strip()
    return Path(raw)


class ClinicianStore:
    def __init__(self, *, load_disk: bool = True) -> None:
        self._by_practice: dict[str, dict[str, Clinician]] = {}
        self._seed()
        if load_disk:
            self._load_from_disk()

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
        self._save_to_disk()
        return clin

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
        self._save_to_disk()
        return clin

    def reset(self) -> None:
        """Re-seed in-memory roster (tests). Does not delete the on-disk file."""
        self._seed()

    def reset_and_clear_disk(self) -> None:
        """Tests: re-seed and remove encrypted clinician file if present."""
        self._seed()
        path = clinician_persist_path()
        if path is not None and path.is_file():
            path.unlink()

    def _voice_snapshot(self) -> dict[str, Any]:
        """Serialize voice profiles only (never log this payload)."""
        practices: dict[str, Any] = {}
        for practice_id, rows in self._by_practice.items():
            bucket: dict[str, Any] = {}
            for cid, clin in rows.items():
                if not clin.voice.has_embedding() and clin.voice.status == "none":
                    continue
                bucket[cid] = {
                    "status": clin.voice.status,
                    "enrolled_at": clin.voice.enrolled_at,
                    "sample_bytes": clin.voice.sample_bytes,
                    "embedding": list(clin.voice.embedding),
                }
            if bucket:
                practices[practice_id] = bucket
        return {"version": 1, "practices": practices}

    def _apply_voice_snapshot(self, payload: dict[str, Any]) -> None:
        practices = payload.get("practices") or {}
        if not isinstance(practices, dict):
            return
        for practice_id, bucket in practices.items():
            if not isinstance(bucket, dict):
                continue
            rows = self._by_practice.get(str(practice_id))
            if rows is None:
                continue
            for cid, voice_raw in bucket.items():
                clin = rows.get(str(cid))
                if clin is None or not isinstance(voice_raw, dict):
                    continue
                status = voice_raw.get("status") or "none"
                emb = voice_raw.get("embedding") or []
                if status == "enrolled" and isinstance(emb, list) and emb:
                    clin.voice = ClinicianVoiceProfile(
                        status="enrolled",
                        enrolled_at=voice_raw.get("enrolled_at"),
                        sample_bytes=int(voice_raw.get("sample_bytes") or 0),
                        embedding=[float(x) for x in emb],
                    )
                else:
                    clin.voice = ClinicianVoiceProfile()

    def _save_to_disk(self) -> None:
        path = clinician_persist_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = encrypt_utf8(json.dumps(self._voice_snapshot()))
        path.write_bytes(blob)

    def _load_from_disk(self) -> None:
        path = clinician_persist_path()
        if path is None or not path.is_file():
            return
        try:
            plain = decrypt_utf8(path.read_bytes())
            payload = json.loads(plain)
        except (ValueError, json.JSONDecodeError, OSError):
            # Wrong key / corrupt file — keep seed roster; do not crash API startup.
            return
        if isinstance(payload, dict):
            self._apply_voice_snapshot(payload)


clinician_store = ClinicianStore()
