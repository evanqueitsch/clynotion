"""Match diarized speakers / check-in clips to enrolled clinician embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.clinicians import PresentClinician, clinician_store
from app.schemas import Participant, SupervisionFields
from app.voice_id import (
    VoiceMatch,
    best_matches,
    cosine_similarity,
    get_voice_id_provider,
    load_mono_pcm,
    match_threshold,
)


@dataclass(frozen=True)
class SpeakerAssignment:
    speaker_label: str
    clinician_id: str
    display_name: str
    score: float
    source: str  # voice_match | roster_reconcile


@dataclass(frozen=True)
class TimedWord:
    word: str
    speaker: int
    start: float
    end: float


def pcm_for_speaker(
    samples: list[float],
    sample_rate: int,
    words: list[TimedWord],
    speaker: int,
    *,
    pad_sec: float = 0.05,
) -> list[float]:
    """Concatenate PCM for one diarized speaker from timed words."""
    spans = [(w.start, w.end) for w in words if w.speaker == speaker]
    if not spans:
        return []
    chunks: list[float] = []
    for start, end in spans:
        a = max(0, int((start - pad_sec) * sample_rate))
        b = min(len(samples), int((end + pad_sec) * sample_rate))
        if b > a:
            chunks.extend(samples[a:b])
    return chunks


def assign_speakers_from_audio(
    *,
    practice_id: str,
    audio_path: str,
    words: list[TimedWord],
    present: list[PresentClinician],
) -> list[SpeakerAssignment]:
    """
    Embed each diarized speaker's audio and greedily assign to enrolled present clinicians.
    Speakers without a confident match are omitted (roster reconcile can fill).
    """
    if not words or not present:
        return []
    gallery = clinician_store.gallery_embeddings(
        practice_id, [p.clinician_id for p in present]
    )
    if not gallery:
        return []

    try:
        samples, rate = load_mono_pcm(audio_path)
    except Exception:
        return []

    provider = get_voice_id_provider()
    speakers = sorted({w.speaker for w in words})
    # Score matrix: list of (speaker, match)
    candidates: list[tuple[int, VoiceMatch]] = []
    for spk in speakers:
        pcm = pcm_for_speaker(samples, rate, words, spk)
        if len(pcm) < rate // 5:  # < 0.2s
            continue
        try:
            emb = provider.embed_pcm(pcm, rate)
        except Exception:
            continue
        matches = best_matches(emb, gallery)
        if matches:
            candidates.append((spk, matches[0]))

    # Greedy one-to-one by score
    candidates.sort(key=lambda t: t[1].score, reverse=True)
    used_clin: set[str] = set()
    used_spk: set[int] = set()
    out: list[SpeakerAssignment] = []
    for spk, match in candidates:
        if spk in used_spk or match.clinician_id in used_clin:
            continue
        used_spk.add(spk)
        used_clin.add(match.clinician_id)
        out.append(
            SpeakerAssignment(
                speaker_label=f"Speaker {spk}",
                clinician_id=match.clinician_id,
                display_name=match.display_name,
                score=match.score,
                source="voice_match",
            )
        )
    return out


def apply_voice_assignments(
    fields: SupervisionFields,
    assignments: list[SpeakerAssignment],
) -> SupervisionFields:
    if not assignments:
        return fields
    data = fields.model_dump()
    speaker_map = dict(data.get("speaker_map") or {})
    participants = list(data.get("participants") or [])
    evidence = dict(data.get("evidence") or {})

    for a in assignments:
        speaker_map[a.speaker_label] = a.display_name
        row = next(
            (p for p in participants if p.get("speaker_label") == a.speaker_label),
            None,
        )
        if row is None:
            participants.append(
                {
                    "speaker_label": a.speaker_label,
                    "name": a.display_name,
                    "role": "other",
                }
            )
        else:
            row["name"] = a.display_name
        evidence[f"voice_match.{a.speaker_label}"] = (
            f"local voice match score={a.score:.3f} → {a.display_name}"
        )

    data["speaker_map"] = speaker_map
    data["participants"] = [Participant.model_validate(p).model_dump() for p in participants]
    data["evidence"] = evidence
    return SupervisionFields.model_validate(data)


def verify_checkin(
    *,
    practice_id: str,
    clinician_id: str,
    audio_path: str,
    present_ids: Optional[list[str]] = None,
) -> dict:
    """
    Match a check-in clip against the claimed clinician (and optional present gallery).
    Returns scores only — no audio retained by caller after this returns.
    """
    clin = clinician_store.get(practice_id, clinician_id)
    if clin is None:
        raise KeyError(clinician_id)
    if not clin.voice.has_embedding():
        raise ValueError("clinician has no enrolled voice profile")

    provider = get_voice_id_provider()
    probe = provider.embed_file(audio_path)
    claimed_score = cosine_similarity(probe, clin.voice.embedding)

    gallery_ids = present_ids or [clinician_id]
    gallery = clinician_store.gallery_embeddings(practice_id, gallery_ids)
    matches = best_matches(probe, gallery, threshold=0.0)
    top = matches[0] if matches else None
    thr = match_threshold()
    verified = claimed_score >= thr and (
        top is None or top.clinician_id == clinician_id or claimed_score >= (top.score - 0.02)
    )
    return {
        "clinician_id": clinician_id,
        "display_name": clin.display_name,
        "claimed_score": round(claimed_score, 4),
        "threshold": thr,
        "verified": verified,
        "top_match": (
            {
                "clinician_id": top.clinician_id,
                "display_name": top.display_name,
                "score": round(top.score, 4),
            }
            if top
            else None
        ),
    }
