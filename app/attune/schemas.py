"""Attune shell API schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PracticeOut(BaseModel):
    practice_id: str
    display_name: str
    slug: str = ""
    allowed_domain: str = ""
    tools: list[str] = Field(default_factory=lambda: ["clynotion"])


class AttuneHomeOut(BaseModel):
    product: str = "attune"
    user_id: str
    username: str
    email: str = ""
    practice: PracticeOut
    tools: list[dict[str, str]] = Field(default_factory=list)
    note: Optional[str] = None
