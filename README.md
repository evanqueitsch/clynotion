# Attune — Clynotion (Phase 1)

**Attune** is the practice product. **Clynotion** is the capture + note-taking tool inside it.

**Phase 1–2:** clinical supervision — who’s present → local voice enroll/check-in → session-surface capture (meeting bot stubbed until BAA) → ASR → extract → **local voice match** of diarized speakers to enrolled profiles → roster reconcile → review → finalize (audio deleted). Voice matching is **offline** (no third-party biometrics SaaS).

The practice roster starts **empty** — there are no seed/demo clinicians in a real deployment. Add people via **Google Workspace import** (see below); synthetic fixtures (Dana/Jordan/etc.) exist only in tests, installed with `install_test_fixtures()`.

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

To keep voice enrollments + roster across restarts (encrypted at rest):

```
ATTUNE_CLINICIAN_PERSISTENCE=file
ATTUNE_DATA_ENCRYPTION_KEY=...   # python -c "from app.crypto import generate_key; print(generate_key())"
```

To keep sessions/notes across restarts too (encrypted at rest, one file):

```
ATTUNE_PERSISTENCE=file
ATTUNE_SESSION_DATA_PATH=.attune_data/sessions.enc
```

Raw transcripts are cleared from a session as soon as it is finalized — only the validated
fields + rendered note remain (see `app/pipeline.py:finalize_session`).

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
| `ATTUNE_MODE` | `mock` | `mock` or `real` — see [Real mode / PHI path](#real-mode--phi-path-v070) below |
| `ATTUNE_DATA_ENCRYPTION_KEY` | *(ephemeral in mock)* | Fernet key. **Required in real. Never commit.** |
| `ATTUNE_PERSISTENCE` | `memory` | `memory` \| `file` (encrypted session file, survives restart) \| `postgres` (stub — needs BAA) |
| `ATTUNE_SESSION_DATA_PATH` | `.attune_data/sessions.enc` | Path when session persistence is `file` |
| `ATTUNE_CLINICIAN_PERSISTENCE` | `memory` | `memory` \| `file` — encrypted voice enrollments + Workspace roster on disk |
| `ATTUNE_CLINICIAN_DATA_PATH` | `.attune_data/clinicians.enc` | Path when clinician persistence is `file` |
| `ATTUNE_JWT_SECRET` | *(ephemeral in mock)* | HS256 secret. **Required in real.** |

Fake users (dev only, `ATTUNE_AUTH=dev`): `alice` / `alice-pass` → `practice-a`; `bob` / `bob-pass` → `practice-b`.

## Real mode / PHI path (v0.7.0)

`ATTUNE_MODE=real` marks a deployment as touching real PHI and **fails closed** at startup
(`app/config.py:validate_startup_secrets`):

- Refuses to boot if `ATTUNE_ASR=mock` or `ATTUNE_LLM=mock` is set explicitly — real mode
  never silently runs MOCK against real client audio/transcripts.
- When ASR/LLM are left unset, real mode **defaults to Deepgram + Anthropic** (not mock).
- Requires `ATTUNE_DATA_ENCRYPTION_KEY` and `ATTUNE_JWT_SECRET`.
- Requires the vendor API key for whichever provider is resolved (`DEEPGRAM_API_KEY` for
  `deepgram`, `ANTHROPIC_API_KEY` for `anthropic`, `OPENAI_API_KEY` for `openai`).
- `MockAsrProvider` / `MockLlmExtractor` additionally refuse to construct at all under
  `ATTUNE_MODE=real` — belt-and-suspenders in case a code path ever tried to build one.
- `GET /health` reports `mode` and `phi_path` (`true` when `ATTUNE_MODE=real`); the UI shows
  a persistent banner warning when the PHI path is live (real vendors handle audio/transcripts —
  do not go live until BAAs are signed with those vendors and encryption/JWT secrets are set).

The shipped `Dockerfile` defaults to `ATTUNE_MODE=real`, `ATTUNE_ASR=deepgram`,
`ATTUNE_LLM=anthropic` — a production image will refuse to start until the secrets above are
provided (e.g. Fly secrets). Override `ATTUNE_MODE=mock` only for a throwaway synthetic-data
demo deploy; never point real client audio at a MOCK deployment.

**MOCK stays MOCK for tests/smoke.** `tests/conftest.py` forces `ATTUNE_MODE=mock` and
`ATTUNE_ASR=ATTUNE_LLM=mock` so the whole suite (and `run_smoke_test.py`) runs fully offline,
regardless of what's in a developer's `env.local`. See `tests/test_real_mode_gates.py`.

## Google Workspace SSO (Fly / production)

Production gates the app behind Google OAuth. Password login is disabled when `ATTUNE_AUTH=google`.

Google sees **identity only** (email/profile) on the login path — not session audio, transcripts, or notes. Still use your org’s Workspace policies; do not put real PHI through Attune until vendor BAAs are in place.

### 1) Google Cloud OAuth client

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → Create **OAuth client ID** (Web application).
2. Authorized redirect URI: `https://clynotion.fly.dev/auth/google/callback`
3. Copy Client ID + Client Secret.
4. (Preferred) In Google Admin, restrict the OAuth app to your Workspace org.

### 2) Fly secrets

```bash
fly secrets set -a clynotion \
  ATTUNE_AUTH=google \
  ATTUNE_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  ATTUNE_PUBLIC_BASE_URL=https://clynotion.fly.dev \
  GOOGLE_CLIENT_ID=... \
  GOOGLE_CLIENT_SECRET=... \
  GOOGLE_ALLOWED_DOMAINS=your-workspace-domain.com \
  ATTUNE_DEFAULT_PRACTICE_ID=practice-hfc
```

| Var | Required | Purpose |
|---|---|---|
| `GOOGLE_CLIENT_ID` | yes (google mode) | OAuth client ID from Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | yes (google mode) | OAuth client secret |
| `GOOGLE_ALLOWED_DOMAINS` | yes (google mode) | Comma-separated Workspace domains allowed to sign in |
| `ATTUNE_PUBLIC_BASE_URL` | yes (google mode) | Public origin used to build the redirect URI |
| `ATTUNE_JWT_SECRET` | yes (google mode) | Signs session cookies / JWTs |
| `ATTUNE_DEFAULT_PRACTICE_ID` | optional | Defaults to `practice-hfc` |
| `ATTUNE_AUTH` | optional | `google` or `dev` (default: google if client ID set) |

Sign-in URL: `https://clynotion.fly.dev/` → **Sign in with Google**.

## Google Workspace directory → practice roster

Clynotion can list users from your Workspace domain and let you **choose who to include** on the practice clinician roster (identity only — not session PHI).

### Google Cloud setup (one-time)
1. Enable **Admin SDK API** on the `attune-clynotion` project.
2. OAuth consent → add scope  
   `https://www.googleapis.com/auth/admin.directory.user.readonly`
3. OAuth client → Authorized redirect URIs, add:  
   `https://clynotion.fly.dev/auth/google/directory/callback`  
   (keep the existing login callback too)
4. Connect must be done by a **Workspace admin** (or delegated admin with User Read privilege).

### In the app
1. Sign in with Google.
2. Open **Practice roster · Google Workspace**.
3. **Import from Google Workspace** (first time may ask a Workspace admin to approve Directory access).
4. Check who to include → set role (Admin / Supervisor / Supervisee / Other) → **Add selected to roster**.

### Env
| Var | Default | Meaning |
|---|---|---|
| `ATTUNE_WORKSPACE_DIRECTORY` | `live` when `ATTUNE_AUTH=google`, else `mock` | `live` \| `mock` \| `off` |
| `ATTUNE_CLINICIAN_PERSISTENCE` | `memory` | Use `file` on Fly so roster survives restarts |
| `ATTUNE_DATA_ENCRYPTION_KEY` | ephemeral in mock | Required for durable encrypted roster + directory tokens |
| `ATTUNE_WORKSPACE_TOKEN_PATH` | `.attune_data/workspace_tokens.enc` | Encrypted Workspace refresh tokens |

### Fly volume (`/data`)
Deploy mounts Fly volume `attune_data` at `/data`. The GitHub Action creates it in `iad` if missing and scales to **1 machine** (one volume writer).

Paths on Fly:
- `/data/clinicians.enc` — roster + voice embeddings
- `/data/workspace_tokens.enc` — Workspace directory refresh token
- `/data/sessions.enc` — sessions/notes (set `ATTUNE_PERSISTENCE=file`; raw transcript is
  cleared at finalize, only validated fields + rendered note remain)

Also set Fly secret `ATTUNE_DATA_ENCRYPTION_KEY` (stable Fernet key) so encrypted files remain readable across deploys.

## Tests

```powershell
pytest -q
python run_smoke_test.py
```

## Compliance notes

- Audit log: actions, session IDs, timestamps only — never transcript/note/audio content.
- Synthetic vignette/fixture data only — the practice roster starts empty, and seed
  clinicians are installed only in tests via `install_test_fixtures()`.
- `ATTUNE_MODE=real` fails closed at startup (see [Real mode / PHI path](#real-mode--phi-path-v070)):
  it refuses MOCK ASR/LLM and requires encryption/JWT/vendor secrets before it will boot.
- Do not point real supervision audio at this stack until entity + BAAs + encryption are in place.
- Deepgram/LLM real providers are env-flagged for synthetic eval only until BAAs are signed.
- Raw transcript is deleted from a session as soon as it's finalized; audio is deleted on
  finalize too (`DELETE_AUDIO_ON_FINALIZE`).
