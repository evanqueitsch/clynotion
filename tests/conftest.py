"""Force MOCK providers + in-memory clinician store for the test suite."""

from __future__ import annotations

import os
import tempfile

import pytest

# Must run before test modules import app.main (which loads env.local).
os.environ["ATTUNE_MODE"] = "mock"
os.environ["ATTUNE_ASR"] = "mock"
os.environ["ATTUNE_LLM"] = "mock"
os.environ["ATTUNE_CLINICIAN_PERSISTENCE"] = "memory"
os.environ["ATTUNE_PERSISTENCE"] = "memory"
os.environ["ATTUNE_DUE_PERSISTENCE"] = "memory"
os.environ["ATTUNE_INGEST_PERSISTENCE"] = "memory"
os.environ["ATTUNE_INTAKE_PERSISTENCE"] = "memory"
os.environ["ATTUNE_ELIGIBILITY_PERSISTENCE"] = "memory"
os.environ["ATTUNE_CREDENTIAL_PERSISTENCE"] = "memory"
os.environ["ATTUNE_AUTH"] = "dev"
os.environ["ATTUNE_HR_TIME_PERSISTENCE"] = "memory"
os.environ["ATTUNE_ACCESS_PERSISTENCE"] = "memory"
os.environ["ATTUNE_CREDENTIALING_PERSISTENCE"] = "memory"
os.environ["ATTUNE_EMPLOYEES_PERSISTENCE"] = "memory"
os.environ.pop("ATTUNE_EMPLOYEES_ENCRYPTION_KEY", None)
os.environ["ATTUNE_DENIALS_PERSISTENCE"] = "memory"
os.environ.pop("DENIALS_FERNET_KEY", None)
os.environ["ATTUNE_ONBOARDING_PERSISTENCE"] = "memory"
os.environ["ATTUNE_COMPLIANCE_PERSISTENCE"] = "memory"
os.environ.pop("SAM_API_KEY", None)
os.environ.pop("ATTUNE_COMPLIANCE_DATA_PATH", None)
os.environ["ATTUNE_ONBOARDING_FILES_PATH"] = tempfile.mkdtemp(prefix="clynotion-hiring-")
os.environ["ATTUNE_OFFER_PDF"] = "stub"
os.environ["ATTUNE_HIRING_SCHEDULER"] = "off"
os.environ["FILE_STORE_BACKEND"] = "local"
os.environ.pop("GOOGLE_DRIVE_SA_JSON", None)
os.environ["TIME_MAIL_MODE"] = "off"
os.environ.pop("CRON_SECRET", None)
os.environ.pop("GOOGLE_SA_JSON", None)
os.environ.pop("TIME_MAIL_SENDER", None)
# Prevent a developer env.local Google config from flipping tests to SSO mode.
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)


@pytest.fixture(autouse=True)
def _reset_clinician_store():
    from app.clinicians import clinician_store, install_test_fixtures
    from app.hr.time_entry import set_clock, time_store
    from app.hr.time_mail import clear_mailer_for_tests

    clinician_store.reset()
    install_test_fixtures()
    time_store.reset()
    from app.access.store import access_store
    from app.credentialing.store import credentialing_store
    from app.employees.store import employees_store

    access_store.reset()
    credentialing_store.reset()
    employees_store.reset()
    from app.denials.store import denials_store

    denials_store.reset()
    from app.hiring.engine import hiring
    from app.hiring.mailer import clear_capture_for_tests

    hiring.reset()
    clear_capture_for_tests()
    from app.hiring.personnel_drive import clear_personnel_drive_for_tests

    clear_personnel_drive_for_tests()
    access_store.set_assignment("practice-b", "bob@example.com", "clinician")
    access_store.update_role_permissions(
        "practice-b",
        "clinician",
        [
            "tool:supervision_notes",
            "tool:compliance_registry",
            "tool:sp_ingest",
            "tool:intake_log",
            "tool:eligibility",
            "tool:time_entry",
        ],
    )
    from app.compliance.store import compliance_store

    compliance_store.reset()
    set_clock(None)
    clear_mailer_for_tests()
    yield
    time_store.reset()
    access_store.reset()
    credentialing_store.reset()
    employees_store.reset()
    denials_store.reset()
    from app.hiring.engine import hiring
    from app.hiring.mailer import clear_capture_for_tests

    hiring.reset()
    clear_capture_for_tests()
    from app.hiring.personnel_drive import clear_personnel_drive_for_tests

    clear_personnel_drive_for_tests()
    from app.compliance.store import compliance_store

    compliance_store.reset()
    set_clock(None)
    clear_mailer_for_tests()
    clinician_store.reset()
