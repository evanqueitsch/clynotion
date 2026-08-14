"""Clynotion platform shell API schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PracticeOut(BaseModel):
    practice_id: str
    display_name: str
    slug: str = ""
    allowed_domain: str = ""
    tools: list[str] = Field(default_factory=lambda: ["supervision"])


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


class HomeOut(BaseModel):
    product: str = "clynotion"
    user_id: str
    username: str
    email: str = ""
    practice: PracticeOut
    tools: list[dict[str, str]] = Field(default_factory=list)
    bands: HomeBandsOut = Field(default_factory=HomeBandsOut)
    note: Optional[str] = None


# Back-compat alias for older imports/tests during rename.
AttuneHomeOut = HomeOut
