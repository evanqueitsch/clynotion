"""ASR / LLM provider interfaces. MOCK is default; real vendors are opt-in via env."""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Canned supervision vignette — MOCK default for Phase 1 (Clynotion).
CANNED_SUPERVISION_RAW: dict = {
    "session_date": "",
    "duration_minutes": 60,
    "supervision_format": "group",
    "setting": "telehealth",
    "supervisor": "Dana Okonkwo",
    "participants": [
        {"speaker_label": "Speaker 0", "name": "Dana Okonkwo", "role": "supervisor"},
        {"speaker_label": "Speaker 1", "name": "Jordan Lee", "role": "supervisee"},
        {"speaker_label": "Speaker 2", "name": "Sam Rivera", "role": "supervisee"},
    ],
    "speaker_map": {
        "Speaker 0": "Dana Okonkwo",
        "Speaker 1": "Jordan Lee",
        "Speaker 2": "Sam Rivera",
    },
    "agenda_items": [
        "Jordan on Client A exposure work",
        "Sam on Client B enactment",
        "risk review and action items",
    ],
    "cases_discussed": [
        {
            "label": "Client A",
            "presenting_focus": "teen anxiety; exposure pacing",
            "supervisee_owner": "Jordan Lee",
        },
        {
            "label": "Client B",
            "presenting_focus": "couples conflict; enactment setup",
            "supervisee_owner": "Sam Rivera",
        },
    ],
    "discussion_themes": [
        "graded exposure pacing",
        "affect regulation in enactments",
        "de-identified case labeling",
    ],
    "guidance_given": (
        "Jordan: slow the hierarchy one step, name alliance rupture risk, rehearse "
        "collaborative pacing script. Sam: pause enactment earlier, mirror affect first, "
        "re-invite a smaller slice."
    ),
    "supervisee_reflections": (
        "Jordan felt behind on progress and was pushing; will try the script. "
        "Sam froze on containment when a partner flooded."
    ),
    "competency_focus": (
        "Jordan: graded exposure and collaborative agenda-setting. "
        "Sam: affect regulation and pacing in enactments."
    ),
    "risk_ethics_flags": (
        "No SI/abuse disclosures for Client A; Client B frustrated not unsafe, "
        "no physical conflict. Keep case labels de-identified."
    ),
    "gatekeeping_notes": "",
    "action_items": [
        {
            "owner_name": "Jordan Lee",
            "item": "Draft a paced exposure ladder and bring it next week",
            "due": "next supervision",
        },
        {
            "owner_name": "Sam Rivera",
            "item": "Write a containment script and practice once in role-play",
            "due": "next supervision",
        },
    ],
    "plan_next": "Review both scripts and any stuck points",
    "evidence": {
        "supervisor": "I'm Dana Okonkwo, licensed supervisor",
        "duration_minutes": "Today we have about sixty minutes",
        "setting": "thanks for joining on telehealth",
        "guidance_given": "Guidance: slow the hierarchy one step",
        "competency_focus": "Competency focus for you is graded exposure",
        "risk_ethics_flags": "No risk flags on Client A — no SI",
        "plan_next": "Next supervision we'll review both scripts",
    },
}

# Canned EMDR vignette fields — parked modality; MOCK still returns for EMDR extractors.
CANNED_EMDR_RAW: dict = {
    "target_memory": "accident on Route 30",
    "image": (
        "It's the headlights. The accident on Route 30. "
        "I'm in the car and the lights are coming straight at me."
    ),
    "negative_cognition": "I'm not safe.",
    "positive_cognition": "I survived — I can keep myself safe.",
    "suds_pre": 8,
    "suds_post": 2,
    "voc_pre": 3,
    "voc_post": 6,
    "phase": 4,
    "bls_type": "eye movements",
    "imagery_shift": "The lights are farther away; I can breathe.",
    "closure_method": "calm place",
    "plan_next_target": "Continue desensitization on Route 30 target",
    "emotions_body": "chest tightness easing",
    "evidence": {
        "suds_pre": "Suds is an 8",
        "suds_post": "Suds is now a 2",
        "voc_pre": "Voc is a 3",
        "voc_post": "Voc is a 6",
        "phase": "Phase 4",
        "negative_cognition": "I'm not safe",
        "positive_cognition": "I survived",
    },
}

CANNED_COUPLES_RAW: dict = {
    "pursuer": "Sam",
    "withdrawer": "Dev",
    "presenting_issue": "conflict cycle around closeness",
    "cycle_named": "pursue–withdraw",
    "intervention": "EFT enactment",
    "partner_shifts": "brief soft start from Sam; Dev stayed engaged one turn longer",
    "risk_screen": "no IPV reported",
    "speakers": [
        {"label": "Speaker 0", "inferred_name": "Therapist"},
        {"label": "Speaker 1", "inferred_name": "Sam"},
        {"label": "Speaker 2", "inferred_name": "Dev"},
    ],
    "attributions": [],
    "evidence": {
        "pursuer": "Sam, when you chase",
        "withdrawer": "Dev tends to pull back",
    },
}

SUPERVISION_SYSTEM = """You are a clinical documentation extractor for clinical supervision sessions.
Speakers may be labeled Speaker 0, Speaker 1, etc. Map each speaker to a clinician name when
stated. Extract ONLY what is explicitly stated. Return strict JSON.
Case labels must stay de-identified (e.g. Client A) — never invent real client names.
duration_minutes MUST be a JSON integer or null (never a string).
supervision_format must be one of: individual, triadic, group.
participant role must be one of: admin, supervisor, supervisee, other.
Schema keys: session_date, duration_minutes, supervision_format, setting, supervisor,
participants (list of {speaker_label, name, role}), speaker_map (object label->name),
agenda_items (string list), cases_discussed (list of {label, presenting_focus, supervisee_owner}),
discussion_themes (string list), guidance_given, supervisee_reflections, competency_focus,
risk_ethics_flags, gatekeeping_notes, action_items (list of {owner_name, item, due}),
plan_next, evidence (object mapping field name -> verbatim quote)."""

EMDR_SYSTEM = """You are a clinical documentation extractor for EMDR therapy sessions.
From the transcript, extract ONLY what is explicitly stated. Return strict JSON matching the
schema. Do NOT infer or invent numbers — if a SUDS or VOC value is not spoken, return null.
phase MUST be a JSON integer 1–8 (never a string like "Phase 4").
Schema keys: target_memory, image, negative_cognition, positive_cognition, suds_pre, suds_post,
voc_pre, voc_post, phase, bls_type, imagery_shift, closure_method, plan_next_target,
emotions_body, evidence."""

COUPLES_SYSTEM = """You are a clinical documentation extractor for couples therapy.
Speakers may be labeled generically (Speaker 0…). Attribute pursuer/withdrawer from explicit
therapist naming or clear role language. Extract ONLY what is stated. Return strict JSON.
Schema keys: pursuer, withdrawer, presenting_issue, cycle_named, intervention, partner_shifts,
risk_screen, speakers, attributions, evidence."""


def _asr_mode() -> str:
    explicit = (
        os.environ.get("ATTUNE_ASR") or os.environ.get("ATTUNE_ASR_PROVIDER") or ""
    ).strip().lower()
    if explicit:
        return explicit
    # Keys in env.local alone should enable Deepgram for the API (tests set ATTUNE_ASR=mock).
    if os.environ.get("DEEPGRAM_API_KEY", "").strip():
        return "deepgram"
    return "mock"


def _llm_mode() -> str:
    explicit = (
        os.environ.get("ATTUNE_LLM") or os.environ.get("ATTUNE_LLM_PROVIDER") or ""
    ).strip().lower()
    if explicit:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return "mock"


def _parse_json_object(raw: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError("model did not return a JSON object")
    return json.loads(m.group(0))


@dataclass
class TimedAsrWord:
    word: str
    speaker: int
    start: float
    end: float


@dataclass
class TranscriptResult:
    text: str
    words: list[TimedAsrWord] = field(default_factory=list)


class AsrProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> str:
        ...

    def transcribe_detailed(self, audio_path: str) -> TranscriptResult:
        """Default: text only (no timing) — voice match on session audio is skipped."""
        return TranscriptResult(text=self.transcribe(audio_path), words=[])


class LlmExtractor(ABC):
    @abstractmethod
    def extract_supervision_raw(self, transcript: str) -> dict:
        ...

    @abstractmethod
    def extract_emdr_raw(self, transcript: str) -> dict:
        ...

    @abstractmethod
    def extract_couples_raw(self, transcript: str) -> dict:
        ...

    def repair_supervision_raw(self, transcript: str, previous: dict, error: str) -> dict:
        return self.extract_supervision_raw(
            transcript
            + "\n\nVALIDATION_ERROR_ON_PREVIOUS_JSON:\n"
            + error
            + "\nPREVIOUS_JSON:\n"
            + json.dumps(previous)
            + "\nReturn corrected JSON only. Integers must be JSON numbers."
        )

    def repair_emdr_raw(self, transcript: str, previous: dict, error: str) -> dict:
        """Default: one-shot re-extract with validation error context."""
        return self.extract_emdr_raw(
            transcript
            + "\n\nVALIDATION_ERROR_ON_PREVIOUS_JSON:\n"
            + error
            + "\nPREVIOUS_JSON:\n"
            + json.dumps(previous)
            + "\nReturn corrected JSON only. Integers must be JSON numbers."
        )

    def repair_couples_raw(self, transcript: str, previous: dict, error: str) -> dict:
        return self.extract_couples_raw(
            transcript
            + "\n\nVALIDATION_ERROR_ON_PREVIOUS_JSON:\n"
            + error
            + "\nPREVIOUS_JSON:\n"
            + json.dumps(previous)
            + "\nReturn corrected JSON only."
        )


class MockAsrProvider(AsrProvider):
    def __init__(self, fallback_transcript_path: str | None = None) -> None:
        self.fallback_transcript_path = fallback_transcript_path

    def transcribe(self, audio_path: str) -> str:
        path = Path(audio_path)
        candidates = [
            path.with_suffix(".txt"),
            path.with_suffix(".asr.txt"),
            Path(str(path) + ".txt"),
            Path(self.fallback_transcript_path) if self.fallback_transcript_path else None,
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"MOCK ASR has no transcript for {audio_path}. "
            "Provide a sibling .txt or sample transcript."
        )


class MockLlmExtractor(LlmExtractor):
    def extract_supervision_raw(self, transcript: str) -> dict:
        _ = transcript
        return dict(CANNED_SUPERVISION_RAW)

    def extract_emdr_raw(self, transcript: str) -> dict:
        _ = transcript
        return dict(CANNED_EMDR_RAW)

    def extract_couples_raw(self, transcript: str) -> dict:
        _ = transcript
        return dict(CANNED_COUPLES_RAW)

    def repair_supervision_raw(self, transcript: str, previous: dict, error: str) -> dict:
        _ = (transcript, previous, error)
        return dict(CANNED_SUPERVISION_RAW)

    def repair_emdr_raw(self, transcript: str, previous: dict, error: str) -> dict:
        _ = (transcript, previous, error)
        return dict(CANNED_EMDR_RAW)

    def repair_couples_raw(self, transcript: str, previous: dict, error: str) -> dict:
        _ = (transcript, previous, error)
        return dict(CANNED_COUPLES_RAW)


class DeepgramAsrProvider(AsrProvider):
    """Deepgram nova-3 with diarize=true. Key: DEEPGRAM_API_KEY (never commit)."""

    def _response_data(self, audio_path: str) -> dict:
        api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is required when ATTUNE_ASR=deepgram")
        from deepgram import DeepgramClient

        with open(audio_path, "rb") as f:
            buffer = f.read()
        dg = DeepgramClient(api_key=api_key)
        resp = dg.listen.v1.media.transcribe_file(
            request=buffer,
            model="nova-3",
            diarize=True,
            punctuate=True,
            smart_format=True,
        )
        if hasattr(resp, "model_dump"):
            return resp.model_dump()
        if isinstance(resp, dict):
            return resp
        return json.loads(resp.json()) if hasattr(resp, "json") else dict(resp)

    @staticmethod
    def _words_and_text(data: dict) -> tuple[list[TimedAsrWord], str]:
        results = data.get("results") or {}
        channels = results.get("channels") or []
        if not channels:
            return [], ""
        alt = (channels[0].get("alternatives") or [{}])[0]
        raw_words = alt.get("words") or []
        if not raw_words:
            return [], str(alt.get("transcript", "")).strip()
        timed: list[TimedAsrWord] = []
        lines: list[str] = []
        cur: Optional[int] = None
        buf: list[str] = []
        for w in raw_words:
            spk = int(w.get("speaker", 0) or 0)
            token = w.get("punctuated_word") or w.get("word") or ""
            start = float(w.get("start") or 0.0)
            end = float(w.get("end") or start)
            timed.append(TimedAsrWord(word=token, speaker=spk, start=start, end=end))
            if spk != cur and buf:
                lines.append(f"Speaker {cur}: {' '.join(buf)}")
                buf = []
            cur = spk
            buf.append(token)
        if buf:
            lines.append(f"Speaker {cur}: {' '.join(buf)}")
        return timed, "\n".join(lines)

    def transcribe(self, audio_path: str) -> str:
        _, text = self._words_and_text(self._response_data(audio_path))
        return text

    def transcribe_detailed(self, audio_path: str) -> TranscriptResult:
        timed, text = self._words_and_text(self._response_data(audio_path))
        return TranscriptResult(text=text, words=timed)


class WhisperLocalAsrProvider(AsrProvider):
    """Local faster-whisper — optional offline ASR for synthetic eval without Deepgram."""

    def transcribe(self, audio_path: str) -> str:
        from faster_whisper import WhisperModel

        model_size = os.environ.get("ATTUNE_WHISPER_MODEL", "base")
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, beam_size=5, vad_filter=True)
        return "\n".join(s.text.strip() for s in segments if s.text.strip())


class AnthropicLlmExtractor(LlmExtractor):
    def __init__(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            raise RuntimeError("ANTHROPIC_API_KEY is required when ATTUNE_LLM=anthropic")
        import anthropic

        self._client = anthropic.Anthropic()
        self._model = os.environ.get("ATTUNE_LLM_MODEL", "claude-sonnet-4-5")

    def _complete(self, system: str, user: str) -> dict:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user + "\n\nReturn ONLY valid JSON."}],
        )
        text = msg.content[0].text
        return _parse_json_object(text)

    def extract_supervision_raw(self, transcript: str) -> dict:
        return self._complete(SUPERVISION_SYSTEM, f"TRANSCRIPT:\n{transcript}")

    def extract_emdr_raw(self, transcript: str) -> dict:
        return self._complete(EMDR_SYSTEM, f"TRANSCRIPT:\n{transcript}")

    def extract_couples_raw(self, transcript: str) -> dict:
        return self._complete(COUPLES_SYSTEM, f"TRANSCRIPT:\n{transcript}")


class OpenAILlmExtractor(LlmExtractor):
    def __init__(self) -> None:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise RuntimeError("OPENAI_API_KEY is required when ATTUNE_LLM=openai")
        from openai import OpenAI

        self._client = OpenAI()
        self._model = os.environ.get("ATTUNE_LLM_MODEL", "gpt-4o-mini")

    def _complete(self, system: str, user: str) -> dict:
        r = self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user + "\n\nReturn ONLY valid JSON."},
            ],
        )
        return _parse_json_object(r.choices[0].message.content or "")

    def extract_supervision_raw(self, transcript: str) -> dict:
        return self._complete(SUPERVISION_SYSTEM, f"TRANSCRIPT:\n{transcript}")

    def extract_emdr_raw(self, transcript: str) -> dict:
        return self._complete(EMDR_SYSTEM, f"TRANSCRIPT:\n{transcript}")

    def extract_couples_raw(self, transcript: str) -> dict:
        return self._complete(COUPLES_SYSTEM, f"TRANSCRIPT:\n{transcript}")


def provider_modes() -> dict[str, str]:
    """Current ASR/LLM modes (for health + UI). Never includes secrets."""
    return {"asr": _asr_mode(), "llm": _llm_mode()}


def get_asr_provider() -> AsrProvider:
    mode = _asr_mode()
    if mode == "mock":
        sample = os.environ.get(
            "ATTUNE_SAMPLE_TRANSCRIPT", "sample_supervision_transcript.txt"
        )
        return MockAsrProvider(fallback_transcript_path=sample)
    if mode == "deepgram":
        return DeepgramAsrProvider()
    if mode == "whisper":
        return WhisperLocalAsrProvider()
    raise RuntimeError(f"Unknown ATTUNE_ASR={mode!r}. Use mock|deepgram|whisper.")


def get_llm_extractor() -> LlmExtractor:
    mode = _llm_mode()
    if mode == "mock":
        return MockLlmExtractor()
    if mode == "anthropic":
        return AnthropicLlmExtractor()
    if mode == "openai":
        return OpenAILlmExtractor()
    raise RuntimeError(f"Unknown ATTUNE_LLM={mode!r}. Use mock|anthropic|openai.")
