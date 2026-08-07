# Attune — kickoff prompts (paste into chat; not auto-injected)

Persistent rules live in `.cursor/rules/attune.mdc` (`alwaysApply: true`).
Use the prompts below when starting work.

## Kickoff prompt (after the M1 skeleton is in the repo)

> I've added an M1 skeleton to this repo (a FastAPI service under `app/`, plus `run_smoke_test.py`,
> `sample_emdr_transcript.txt`, `requirements.txt`, and `README.md`). Follow `.cursor/rules/attune.mdc`.
>
> Do this, in order, and stop after each step to show me the result:
> 1. Read `README.md`, `app/schemas.py`, `app/pipeline.py`, and `run_smoke_test.py` so you understand
>    the architecture: audio → typed extraction → deterministic note → review → finalize (audio deleted).
> 2. Create a virtualenv, install `requirements.txt`, and run `python run_smoke_test.py`. Confirm it
>    passes (typed-schema guard, one-tap override re-render, audio-delete-on-finalize).
> 3. Start the API (`uvicorn app.main:app`) and run the curl flow from the README against it. Show the
>    JSON from `/draft` and `/finalize`, and confirm `/finalize` returns `audio_deleted: true`.
> 4. Add a `tests/` folder with pytest coverage for: (a) SUDS/VOC/phase out-of-range values are
>    rejected, (b) override precedence at finalize, (c) audio file is gone after finalize, (d) the
>    audit log contains an `audio_deleted` event and NO transcript/note text. Keep MOCK mode; no keys.
>
> Constraints: do not add any external network service; do not persist audio past finalize; do not log
> PHI. Report the diff and the test output after each step.

## Next-increment template

> Task: \<one specific increment\>.
>
> Follow `.cursor/rules/attune.mdc`. Requirements:
> - Keep MOCK mode working with no keys; the new path is opt-in via env.
> - `run_smoke_test.py` and all pytest tests must still pass; add tests for the new behavior.
> - No PHI in logs; audio stays transient; no new PHI-path vendor without me explicitly approving a BAA.
> Show me a short plan first, then the diff, then the test run.

## Suggested order of increments
1. Tests + CI — lock invariants before adding surface area.
2. Encrypted persistence interface (in-memory now, Postgres later).
3. Auth (JWT + user/practice model).
4. Real provider pass (Deepgram + BAA'd LLM behind env flag; synthetic audio only).
5. SimplePractice write-back Chrome extension (separate project).

None of this touches a real Heart for Change session until HIPAA foundation + BAAs are in place.
