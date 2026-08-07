#!/usr/bin/env python3
"""
Synthetic (non-PHI) real-provider eval: ASR → extract → scorecard.

Env:
  ATTUNE_ASR=deepgram|whisper|mock
  ATTUNE_LLM=anthropic|openai|mock
  DEEPGRAM_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY as needed

Usage:
  python scripts/run_synthetic_eval.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_env = ROOT / "env.local"
if _env.is_file():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

from app.pipeline import extract_and_validate, extract_and_validate_couples  # noqa: E402
from app.providers import get_asr_provider, get_llm_extractor  # noqa: E402

AUDIO_DIR = ROOT / "synthetic_audio"
EMDR_WAV = AUDIO_DIR / "emdr.wav"
COUPLES_WAV = AUDIO_DIR / "couples_distinct.wav"
SUPERVISION_TXT = ROOT / "sample_supervision_transcript.txt"

KEY_EMDR = {
    "suds_pre": 8,
    "suds_post": 2,
    "voc_pre": 3,
    "voc_post": 6,
    "phase": 4,
    "negative_cognition": ["not safe"],
    "positive_cognition": ["survived", "keep myself safe"],
}
KEY_COUPLES = {
    "pursuer": ["sam"],
    "withdrawer": ["dev"],
}


def _score_contains(actual: str, needles: list[str]) -> bool:
    low = (actual or "").lower()
    return any(n.lower() in low for n in needles)


def score_supervision(transcript: str) -> dict:
    fields = extract_and_validate(transcript)
    return {
        "supervisor": fields.supervisor,
        "duration_minutes": fields.duration_minutes,
        "participants": len(fields.participants),
        "ok": bool(fields.supervisor) and (fields.duration_minutes == 60),
    }


def main() -> int:
    print("providers:", {"asr": os.environ.get("ATTUNE_ASR", "mock"), "llm": os.environ.get("ATTUNE_LLM", "mock")})
    _ = get_asr_provider()
    _ = get_llm_extractor()

    if SUPERVISION_TXT.is_file():
        result = score_supervision(SUPERVISION_TXT.read_text(encoding="utf-8"))
        print("supervision:", json.dumps(result, indent=2))
    else:
        print("missing", SUPERVISION_TXT)

    if EMDR_WAV.is_file():
        text = get_asr_provider().transcribe(str(EMDR_WAV))
        print("emdr transcript chars:", len(text))
    else:
        print("skip EMDR audio — place synthetic wav at", EMDR_WAV)

    if COUPLES_WAV.is_file():
        text = get_asr_provider().transcribe(str(COUPLES_WAV))
        fields = extract_and_validate_couples(text)
        print(
            "couples:",
            {
                "pursuer": fields.pursuer,
                "withdrawer": fields.withdrawer,
                "ok": _score_contains(fields.pursuer, KEY_COUPLES["pursuer"])
                and _score_contains(fields.withdrawer, KEY_COUPLES["withdrawer"]),
            },
        )
    else:
        print("skip couples audio — place synthetic wav at", COUPLES_WAV)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
