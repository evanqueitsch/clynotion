# Attune / Clynotion — agent notes

**Attune** = practice product. **Clynotion** = capture + clinical supervision notes tool inside Attune.

## Stack

- Python 3.11+ / FastAPI / Pydantic v2
- MOCK ASR + LLM by default (no API keys required)
- Opt-in: Deepgram ASR, Anthropic/OpenAI LLM via `env.local`
- Local voice enroll/match (no third-party biometrics network)
- UI: `app/static/index.html` served at `/`

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
