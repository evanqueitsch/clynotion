"""OPS-2 compliance calendar seeds — recurring obligations → due engine.

Titles and authority citations only. No client names, chart text, or PHI.
Attune/Clynotion records state; people file/act.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.platform.due import due_engine

COMPLY_SOURCE = "ops2"


@dataclass(frozen=True)
class SeededClock:
    code: str
    title: str
    cadence_days: int
    owner_role: str
    authority: str


# o1–o12 from master scope §6.4 (cadence clocks). o13/o14 are per-episode — not seeded.
SEEDED_CLOCKS: tuple[SeededClock, ...] = (
    SeededClock("o1", "Exclusion screening — all staff/contractors/vendors", 30, "admin", "42 CFR 455.436"),
    SeededClock("o2", "Chart audit sample — 5 charts per clinician", 90, "coo", "Compliance program element 6"),
    SeededClock("o3", "Compliance committee meeting + minutes", 90, "coo", "Compliance program element 2"),
    SeededClock("o4", "Encounter form reconciliation against notes", 30, "billing", "MA Bulletin 99-89-05"),
    SeededClock("o5", "Unsigned note sweep — aging over 72 hours", 7, "coo", "55 Pa. Code 1101.51"),
    SeededClock("o6", "Program Exception Attestation to Provider Relations", 365, "coo", "PerformCare Ch. VI"),
    SeededClock("o7", "Annual compliance + HIPAA training, all staff", 365, "coo", "Compliance program element 3"),
    SeededClock("o8", "HIPAA Security Risk Analysis review", 365, "coo", "45 CFR 164.308"),
    SeededClock("o9", "BAA registry review — all PHI vendors", 365, "coo", "45 CFR 164.502(e)"),
    SeededClock("o10", "Ownership & control disclosure refresh", 365, "ceo", "42 CFR 455.104"),
    SeededClock("o11", "Policy register review — versions and owners", 365, "coo", "Compliance program element 1"),
    SeededClock("o12", "Claims aging review — 365-day filing bar", 30, "billing", "PerformCare Ch. VI"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_seeded_clocks(
    *,
    practice_id: str,
    owner_user_id: str,
    now: Optional[datetime] = None,
) -> list[str]:
    """Upsert open OPS-2 clocks for a practice. Returns source_ref codes touched."""
    now = now or _now()
    touched: list[str] = []
    for clock in SEEDED_CLOCKS:
        # Stagger first due so Home isn't a wall of identical timestamps.
        due = now + timedelta(days=min(clock.cadence_days, 14) if clock.cadence_days > 7 else clock.cadence_days)
        # Weekly clocks (o5) due soon; monthly ones land in this-week or just beyond.
        if clock.cadence_days <= 7:
            due = now + timedelta(days=2)
        elif clock.cadence_days <= 30:
            due = now + timedelta(days=5)
        else:
            due = now + timedelta(days=12)
        due_engine.upsert(
            practice_id=practice_id,
            domain="comply",
            source=COMPLY_SOURCE,
            title=f"{clock.code}: {clock.title}",
            owner_user_id=owner_user_id,
            due_at=due.isoformat(),
            href="/#comply",
            source_ref=clock.code,
            status="open",
        )
        touched.append(clock.code)
    return touched


def list_seed_catalog() -> list[dict[str, str | int]]:
    return [
        {
            "code": c.code,
            "title": c.title,
            "cadence_days": c.cadence_days,
            "owner_role": c.owner_role,
            "authority": c.authority,
        }
        for c in SEEDED_CLOCKS
    ]
