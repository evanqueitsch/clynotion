FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN python -m venv .venv
COPY requirements.txt ./
RUN .venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
# Fail-closed real-PHI defaults (v0.7.0): production image refuses MOCK ASR/LLM and
# requires encryption/JWT/vendor secrets at boot (see app/config.py validate_startup_secrets).
# Override ATTUNE_MODE=mock only for a throwaway synthetic-data demo deploy.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    ATTUNE_MODE=real \
    ATTUNE_ASR=deepgram \
    ATTUNE_LLM=anthropic \
    ATTUNE_PERSISTENCE=file \
    ATTUNE_SESSION_DATA_PATH=/data/sessions.enc \
    ATTUNE_CLINICIAN_PERSISTENCE=file \
    ATTUNE_CLINICIAN_DATA_PATH=/data/clinicians.enc \
    ATTUNE_WORKSPACE_TOKEN_PATH=/data/workspace_tokens.enc \
    ATTUNE_DUE_PERSISTENCE=file \
    ATTUNE_DUE_DATA_PATH=/data/due.enc \
    ATTUNE_INGEST_PERSISTENCE=file \
    ATTUNE_INGEST_DATA_PATH=/data/ingest.enc \
    ATTUNE_INTAKE_PERSISTENCE=file \
    ATTUNE_INTAKE_DATA_PATH=/data/intake.enc \
    ATTUNE_ELIGIBILITY_PERSISTENCE=file \
    ATTUNE_ELIGIBILITY_DATA_PATH=/data/eligibility.enc
WORKDIR /app

COPY --from=builder /app/.venv .venv/
COPY app ./app
COPY requirements.txt sample_supervision_transcript.txt ./

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
