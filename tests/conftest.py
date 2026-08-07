"""Force MOCK providers for the test suite even if env.local enables real vendors."""

from __future__ import annotations

import os

import pytest

# Must run before test modules import app.main (which loads env.local).
os.environ["ATTUNE_ASR"] = "mock"
os.environ["ATTUNE_LLM"] = "mock"


@pytest.fixture(autouse=True)
def _reset_clinician_store():
    from app.clinicians import clinician_store

    clinician_store.reset()
    yield
    clinician_store.reset()
