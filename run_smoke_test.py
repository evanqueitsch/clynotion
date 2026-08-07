#!/usr/bin/env python3
"""
Attune / Clynotion Phase 1 smoke test — MOCK mode, no API keys.

Checks:
  1) Typed-schema guard (bad duration type rejected)
  2) Happy-path extract → validate → deterministic supervision note
  3) Speaker map + guidance overrides at finalize
  4) Unmapped speakers block finalize when ≥2 participants
  5) Audio deleted on finalize
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.audit import audit_log
from app.pipeline import SpeakerMapIncompleteError, draft_from_audio, finalize_session
from app.providers import CANNED_SUPERVISION_RAW
from app.schemas import SupervisionFields, SupervisionOverrides
from app.store import store


def _ok(label: str) -> None:
    print(f"  PASS  {label}")


def _fail(label: str, detail: str) -> None:
    print(f"  FAIL  {label}: {detail}")
    sys.exit(1)


def main() -> None:
    print("Attune / Clynotion Phase 1 smoke test (MOCK)\n")
    audit_log.clear()
    store.clear()

    # --- 1) typed-schema guard ---
    try:
        SupervisionFields.model_validate(
            {
                **CANNED_SUPERVISION_RAW,
                "duration_minutes": "sixty",  # must be int or null
            }
        )
        _fail("typed-schema guard", "duration_minutes='sixty' should have been rejected")
    except ValidationError:
        _ok("typed-schema guard rejects non-int duration_minutes")

    # --- 2) happy path from sample transcript via mock audio ---
    sample = Path("sample_supervision_transcript.txt")
    if not sample.is_file():
        _fail("sample transcript", "sample_supervision_transcript.txt missing")

    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "session.wav"
        audio.write_bytes(b"RIFF....WAVEfmt ")
        sibling = audio.with_suffix(".txt")
        sibling.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")

        from app.clinicians import clinician_store

        present = clinician_store.resolve_present(
            "practice-a",
            [
                ("clin-a-dana", "supervisor"),
                ("clin-a-jordan", "supervisee"),
                ("clin-a-sam", "supervisee"),
            ],
        )
        session = draft_from_audio(
            str(audio),
            practice_id="practice-a",
            present=present,
        )
        f = session.fields
        if f.supervisor != "Dana Okonkwo":
            _fail("extract supervisor", f"got {f.supervisor!r}")
        if len(f.participants) < 3:
            _fail("participants", f"expected ≥3, got {len(f.participants)}")
        if len(session.present) != 3:
            _fail("present roster", f"expected 3, got {len(session.present)}")
        _ok("extract supervisor + multi-participant map + present roster")

        if "CLINICAL SUPERVISION NOTE" not in session.note:
            _fail("deterministic note", session.note[:80])
        if "Jordan Lee" not in session.note or "Sam Rivera" not in session.note:
            _fail("named clinicians in note", session.note)
        _ok("deterministic note uses mapped clinician names")

        if not f.evidence.get("supervisor"):
            _fail("evidence", "missing supervisor evidence quote")
        _ok("evidence quotes present")

        # --- 3) overrides win ---
        finalized = finalize_session(
            session.session_id,
            SupervisionOverrides(
                guidance_given="Revised guidance from clinician review.",
                speaker_map={
                    "Speaker 0": "Dana Okonkwo",
                    "Speaker 1": "Jordan Lee",
                    "Speaker 2": "Sam Rivera",
                },
            ),
        )
        if finalized.fields.guidance_given != "Revised guidance from clinician review.":
            _fail("overrides", finalized.fields.guidance_given)
        if "Revised guidance from clinician review." not in finalized.note:
            _fail("override re-render", finalized.note)
        _ok("clinician overrides win and note re-renders")

        # --- 4) audio deleted ---
        if audio.exists():
            _fail("audio delete", f"audio still at {audio}")
        _ok("audio deleted on finalize")

        actions = [e["action"] for e in audit_log.events()]
        if "audio_deleted" not in actions:
            _fail("audit", f"events={actions}")
        _ok("audit log has audio_deleted")

        for e in audit_log.events():
            blob = str(e).lower()
            for needle in ("dana okonkwo", "client a", "exposure", "transcript", "telehealth"):
                if needle in blob:
                    _fail("audit PHI leak", f"found {needle!r} in {e}")
        _ok("audit log contains no transcript/note text")

    # --- 5) speaker map gate ---
    store.clear()
    audit_log.clear()
    from app.pipeline import draft_from_transcript

    session_b = draft_from_transcript(
        sample.read_text(encoding="utf-8"),
        practice_id="practice-smoke",
    )
    # Clear names to simulate unmapped diarization
    bad = session_b.fields.model_dump()
    bad["speaker_map"] = {}
    bad["participants"] = [
        {"speaker_label": "Speaker 0", "name": "", "role": "supervisor"},
        {"speaker_label": "Speaker 1", "name": "", "role": "supervisee"},
        {"speaker_label": "Speaker 2", "name": "", "role": "supervisee"},
    ]
    session_b.fields = SupervisionFields.model_validate(bad)
    store.save(session_b)
    try:
        finalize_session(session_b.session_id, SupervisionOverrides())
        _fail("speaker map gate", "finalize should have been blocked")
    except SpeakerMapIncompleteError as err:
        if set(err.unmapped) != {"Speaker 0", "Speaker 1", "Speaker 2"}:
            _fail("speaker map gate", f"unmapped={err.unmapped}")
        _ok("finalize blocked when speakers unmapped")

    print("\n*** ALL SMOKE CHECKS PASSED ***\n")


if __name__ == "__main__":
    main()
