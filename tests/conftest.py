"""Force MOCK providers + in-memory clinician store for the test suite."""

from __future__ import annotations

import os

import pytest

# Must run before test modules import app.main (which loads env.local).
os.environ["ATTUNE_ASR"] = "mock"
os.environ["ATTUNE_LLM"] = "mock"
os.environ["ATTUNE_CLINICIAN_PERSISTENCE"] = "memory"
os.environ["ATTUNE_AUTH"] = "dev"
# Prevent a developer env.local Google config from flipping tests to SSO mode.
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)


@pytest.fixture(autouse=True)
def _reset_clinician_store():
    from app.clinicians import clinician_store

    clinician_store.reset()
    yield
    clinician_store.reset()
