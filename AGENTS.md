# Attune / Clynotion — agent notes

**Attune** = practice product (multi-tenant shell). **Clynotion** = capture + clinical supervision notes tool inside Attune.

## Layout

- `app/attune/` — practice registry + shell APIs (`/attune/home`, `/attune/practice`)
- `app/` — Clynotion capture pipeline, roster, providers (moving into `app/clynotion/` later)
- UI: Attune home → Open Clynotion (`/#clynotion`)

## Stack

- Python 3.11+ / FastAPI / Pydantic v2
- Production Fly image: `ATTUNE_MODE=real` + Deepgram/Anthropic (fail-closed; no vignette)
- Tests/smoke: `ATTUNE_MODE=mock`
- Local voice enroll/match (no third-party biometrics network)
- Meeting bot: stubbed / deferred

## Commands

```bash
python3 -m pip install -r requirements.txt
python3 run_smoke_test.py
python3 -m pytest -q
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Dev login: `alice` / `alice-pass` (practice-a).

## Do not

- Scaffold a Vite/React notes demo — this repo is already a FastAPI app
- Commit secrets (`env.local`, API keys)
- Put real client/supervisee PHI in fixtures
- Add PHI-path network vendors without a signed BAA (see `.cursor/rules/attune.mdc`)

## Cloud setup

`.cursor/environment.json` installs Python deps and starts uvicorn on port 8000.
Optional gitignored `env.local` for `DEEPGRAM_API_KEY` / `ANTHROPIC_API_KEY`.
Set `ATTUNE_CLINICIAN_PERSISTENCE=file` (+ `ATTUNE_DATA_ENCRYPTION_KEY`) so voice enrollments survive restarts.
