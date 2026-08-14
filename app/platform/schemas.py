"""Clynotion platform shell API schemas."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


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


class HomeOut(BaseModel):
    product: str = "clynotion"
    user_id: str
    username: str
    email: str = ""
    practice: PracticeOut
    tools: list[dict[str, str]] = Field(default_factory=list)
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
    code: str
    title: str
    cadence_days: int
    owner_role: str
    authority: str


# Back-compat alias
AttuneHomeOut = HomeOut
