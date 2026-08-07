# Attune — Clynotion (Phase 1)

**Attune** is the practice product. **Clynotion** is the capture + note-taking tool inside it.

**Phase 1–2:** clinical supervision — who’s present → local voice enroll/check-in → session-surface capture (meeting bot stubbed until BAA) → ASR → extract → **local voice match** of diarized speakers to enrolled profiles → roster reconcile → review → finalize (audio deleted). Voice matching is **offline** (no third-party biometrics SaaS).

EMDR / couples modalities are parked for a later phase.

This repo is throwaway-friendly scaffolding under HIPAA-minded invariants (see `.cursor/rules/attune.mdc`).

## Architecture

```
audio/transcript → [MOCK|Deepgram ASR, diarized] → speaker-labeled transcript
                 → [MOCK|LLM extract] → SupervisionFields (Pydantic)
                 → [deterministic render] → supervision note
                 → clinician speaker_map + field overrides at /finalize
                 → delete audio + audit (actions/IDs only)
```

## Setup (MOCK — no API keys)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_smoke_test.py
```

## Run API + minimal UI

```powershell
uvicorn app.main:app --reload --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) — sign in (`alice` / `alice-pass`), then live capture, upload, or paste transcript.

**Providers:** On API startup the app loads gitignored `env.local`. If `DEEPGRAM_API_KEY` / `ANTHROPIC_API_KEY` are set, ASR/LLM auto-select Deepgram + Anthropic (override with `ATTUNE_ASR` / `ATTUNE_LLM`). Smoke/pytest force MOCK.

```
DEEPGRAM_API_KEY=...          # https://console.deepgram.com/
ANTHROPIC_API_KEY=...         # https://console.anthropic.com/
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Hard-refresh the UI after restart. If draft fails, the red status now shows the vendor status/body (not just `ApiError`). Synthetic audio only until BAAs.

### Curl flow (transcript)

```powershell
python -c "from pathlib import Path; import httpx, json; t=Path('sample_supervision_transcript.txt').read_text(encoding='utf-8'); tok=httpx.post('http://127.0.0.1:8000/auth/token', json={'username':'alice','password':'alice-pass'}).json()['access_token']; h={'Authorization': f'Bearer {tok}'}; r=httpx.post('http://127.0.0.1:8000/sessions/draft/json', headers=h, json={'transcript': t}); print(r.json()['note']); sid=r.json()['session_id']; f=httpx.post(f'http://127.0.0.1:8000/sessions/{sid}/finalize', headers=h, json={}); print(f.json()['audio_deleted'])"
```

## Env flags

| Var | Default | Meaning |
|---|---|---|
| `ATTUNE_ASR` | `mock` | `mock` \| `deepgram` (nova-3, diarize) \| `whisper` (local). Key: `DEEPGRAM_API_KEY` |
| `ATTUNE_LLM` | `mock` | `mock` \| `anthropic` \| `openai` |
| `ATTUNE_VOICE_ID` | `local` | Offline voice embed: `local` (spectral) \| `hash` \| `resemblyzer` (optional local model) |
| `ATTUNE_VOICE_MATCH_THRESHOLD` | `0.72` | Cosine similarity cutoff for voice match / check-in |
| `ATTUNE_SAMPLE_TRANSCRIPT` | `sample_supervision_transcript.txt` | MOCK ASR fallback |
| `DELETE_AUDIO_ON_FINALIZE` | `true` | Unlink audio on finalize |
| `ATTUNE_MODE` | `mock` | `mock` or `real` (requires encryption + JWT secrets) |
| `ATTUNE_DATA_ENCRYPTION_KEY` | *(ephemeral in mock)* | Fernet key. **Required in real. Never commit.** |
| `ATTUNE_PERSISTENCE` | `memory` | `memory` or `postgres` (stub — needs BAA) |
| `ATTUNE_JWT_SECRET` | *(ephemeral in mock)* | HS256 secret. **Required in real.** |

Fake users (dev): `alice` / `alice-pass` → `practice-a`; `bob` / `bob-pass` → `practice-b`.

## Tests

```powershell
pytest -q
python run_smoke_test.py
```

## Compliance notes

- Audit log: actions, session IDs, timestamps only — never transcript/note/audio content.
- Synthetic vignette data only in this skeleton.
- Do not point real supervision audio at this stack until entity + BAAs + encryption are in place.
- Deepgram/LLM real providers are env-flagged for synthetic eval only until BAAs are signed.
