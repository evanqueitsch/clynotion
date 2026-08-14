"""Compliance registry seeds — recurring obligations → due engine.

Titles come from the master catalog (plain names). Legacy o1…o12 codes remain
as source_ref for idempotent upserts only — not user-facing labels.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.platform.catalog import COMPLIANCE_CLOCKS
from app.platform.due import due_engine

COMPLY_SOURCE = "compliance_registry"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_seeded_clocks(
    *,
    practice_id: str,
    owner_user_id: str,
    now: Optional[datetime] = None,
) -> list[str]:
    """Upsert open compliance clocks for a practice. Returns clock codes touched."""
    now = now or _now()
    touched: list[str] = []
    for clock in COMPLIANCE_CLOCKS:
        cadence = int(clock.get("cadence_days") or 0)
        if cadence <= 0:
            continue  # o13/o14 are per-episode — not auto-seeded
        if cadence <= 7:
            due = now + timedelta(days=2)
        elif cadence <= 30:
            due = now + timedelta(days=5)
        else:
            due = now + timedelta(days=12)
        code = str(clock["code"])
        # Retire legacy ops2-sourced rows (same clock code) so Home isn't duplicated.
        due_engine.complete(
            practice_id=practice_id, source="ops2", source_ref=code
        )
        due_engine.upsert(
            practice_id=practice_id,
            domain="comply",
            source=COMPLY_SOURCE,
            title=str(clock["name"]),
            owner_user_id=owner_user_id,
            due_at=due.isoformat(),
            href="/#comply",
            source_ref=code,
            status="open",
        )
        touched.append(code)
    return touched


def list_seed_catalog() -> list[dict[str, str | int | list[str]]]:
    return [
        {
            "id": c["id"],
            "code": c["code"],
            "title": c["name"],
            "cadence_days": c["cadence_days"],
            "owner_role": c["owner_role"],
            "authority": "; ".join(c["authorities"]),
            "authorities": list(c["authorities"]),
        }
        for c in COMPLIANCE_CLOCKS
        if int(c.get("cadence_days") or 0) > 0
    ]
