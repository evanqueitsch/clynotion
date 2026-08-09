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

To keep voice enrollments across restarts (encrypted at rest):

```
ATTUNE_CLINICIAN_PERSISTENCE=file
ATTUNE_DATA_ENCRYPTION_KEY=...   # python -c "from app.crypto import generate_key; print(generate_key())"
```

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
| `ATTUNE_CLINICIAN_PERSISTENCE` | `memory` | `memory` \| `file` — encrypted voice enrollments on disk |
| `ATTUNE_CLINICIAN_DATA_PATH` | `.attune_data/clinicians.enc` | Path when clinician persistence is `file` |
| `ATTUNE_JWT_SECRET` | *(ephemeral in mock)* | HS256 secret. **Required in real.** |

Fake users (dev only, `ATTUNE_AUTH=dev`): `alice` / `alice-pass` → `practice-a`; `bob` / `bob-pass` → `practice-b`.

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

Also set Fly secret `ATTUNE_DATA_ENCRYPTION_KEY` (stable Fernet key) so encrypted files remain readable across deploys.

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
