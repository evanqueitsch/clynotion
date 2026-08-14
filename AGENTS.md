# Clynotion — agent notes

**Clynotion** is the practice product (multi-tenant shell + ops). Domain: clynotion.com.

Organize work by **business unit** (Clinical Tools, HR, Marketing, Finance, Technology) —
not by OPS-/CLY- prefixes. Those codes are legacy aliases only; see
`docs/item-authority-crosswalk.md` and `app/platform/catalog.py`.

Historical name “Attune” referred to the same product before the domain decision; prefer Clynotion everywhere user-facing. Env vars still use the `ATTUNE_*` prefix for deploy compatibility.

## Layout

- `app/platform/` — practice registry, due engine, **catalog / crosswalk**, shell APIs
- `app/comply/` — compliance registry clocks → due engine
- `app/ingest/` — SimplePractice CSV ingest (documentation reports; demographics deferred)
- `app/grow/` — intake log (case codes only) → due engine access clocks
- `app/eligibility/` — eligibility checks (mock/manual; live adapters deferred)
- `app/` — supervision capture pipeline, roster, providers
- UI: Home by business unit + Supervision, Compliance, Ingest, Intake, Eligibility

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
- Add PHI-path network vendors without a signed BAA (see `.cursor/rules/clynotion.mdc`)

## Cloud setup

`.cursor/environment.json` installs Python deps and starts uvicorn on port 8000.
Optional gitignored `env.local` for `DEEPGRAM_API_KEY` / `ANTHROPIC_API_KEY`.
Set `ATTUNE_CLINICIAN_PERSISTENCE=file` (+ `ATTUNE_DATA_ENCRYPTION_KEY`) so voice enrollments survive restarts.
