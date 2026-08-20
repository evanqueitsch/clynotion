"""Clynotion platform shell API schemas."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class PracticeOut(BaseModel):
    practice_id: str
    display_name: str
    slug: str = ""
    allowed_domain: str = ""
    tools: list[str] = Field(
        default_factory=lambda: ["supervision", "comply", "ingest"]
    )


class ObligationOut(BaseModel):
    """Home due-engine row — actions/IDs only, never note or transcript text."""

    obligation_id: str
    practice_id: str
    domain: str
    source: str
    title: str
    owner_user_id: str
    due_at: str
    status: str = "open"
    href: str = "/"
    source_ref: str = ""


class HomeBandsOut(BaseModel):
    overdue: list[ObligationOut] = Field(default_factory=list)
    this_week: list[ObligationOut] = Field(default_factory=list)


class AttentionItemOut(BaseModel):
    code: str
    title: str
    domain: str
    href: str = "/"


class PulseOut(BaseModel):
    open_obligations: int = 0
    overdue_count: int = 0
    unsigned_aging_rows: int = 0
    open_headcount: int = 0
    documentation_ingest_age_days: Optional[int] = None
    access_pct_met: Optional[float] = None
    eligibility_checks_30d: int = 0


class HomeOut(BaseModel):
    product: str = "clynotion"
    user_id: str
    username: str
    email: str = ""
    practice: PracticeOut
    tools: list[dict[str, str]] = Field(default_factory=list)
    units: list[dict[str, Any]] = Field(default_factory=list)
    bands: HomeBandsOut = Field(default_factory=HomeBandsOut)
    attention: list[AttentionItemOut] = Field(default_factory=list)
    pulse: PulseOut = Field(default_factory=PulseOut)
    note: Optional[str] = None


class IngestUploadOut(BaseModel):
    upload_id: str
    practice_id: str
    report_type: str
    content_hash: str
    uploaded_by: str
    uploaded_at: str
    row_count: int
    status: str
    error_code: str = ""
    unsigned_aging_count: int = 0


class IngestUploadBody(BaseModel):
    report_type: str = "documentation"
    csv_text: str
    column_map: dict[str, str] = Field(default_factory=dict)


class ComplyCatalogItemOut(BaseModel):
    id: str = ""
    code: str = ""
    title: str
    cadence_days: int
    owner_role: str
    authority: str = ""
    authorities: list[str] = Field(default_factory=list)


class IntakeCreateBody(BaseModel):
    case_code: str
    channel: str = "phone"
    triage: str = "routine"
    request_at: Optional[str] = None


class IntakePatchBody(BaseModel):
    date_offered: Optional[str] = None
    date_scheduled: Optional[str] = None
    outcome: Optional[str] = None


class IntakeEventOut(BaseModel):
    intake_id: str
    practice_id: str
    case_code: str
    channel: str
    triage: str
    request_at: str
    date_offered: str = ""
    date_scheduled: str = ""
    outcome: str
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""
    standard_days: int = 7
    within_standard: Optional[bool] = None


class EligibilityCheckBody(BaseModel):
    case_code: str
    payer: str = "PerformCare"
    plan: str = ""
    service_date: str
    method: str = "mock"
    outcome: Optional[str] = None
    coverage_detail: str = ""
    evidence_ref: str = ""


class EligibilityCheckOut(BaseModel):
    check_id: str
    practice_id: str
    case_code: str
    payer: str
    plan: str
    service_date: str
    checked_at: str
    checked_by: str
    method: str
    outcome: str
    coverage_detail: str = ""
    evidence_ref: str = ""


class EmployeeCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    title: str = ""
    license_type: str = "other"
    license_number: str = ""
    license_expiry: str = ""
    supervisor_id: str = ""
    start_date: str = ""
    drive_folder_url: str = ""


class EmployeePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Optional[str] = None
    title: Optional[str] = None
    license_type: Optional[str] = None
    license_number: Optional[str] = None
    license_expiry: Optional[str] = None
    supervisor_id: Optional[str] = None
    start_date: Optional[str] = None
    drive_folder_url: Optional[str] = None
    active: Optional[bool] = None


class OnboardingPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: dict[str, str] = Field(default_factory=dict)
    notes: Optional[str] = None


class ComplianceItemCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    issue_date: str = ""
    expiry_date: str = ""
    renewal_interval_months: int = 60
    document_drive_url: str = ""


class SupervisionEntryCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supervisor_id: str = ""
    date: str = ""
    duration_minutes: int
    format: str = "Telehealth"
    notes: str = ""
    signed_by_both: bool = False


# Back-compat alias
AttuneHomeOut = HomeOut
