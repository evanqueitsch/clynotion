"""Weekly Billing digest. Off unless an Admin turns it on. This module never sends mail."""

from __future__ import annotations

from app.credentialing.store import credentialing_store
from app.platform.due import due_engine


def digest_enabled(practice_id: str) -> bool:
    return credentialing_store.digest_enabled(practice_id)


def digest_preview(practice_id: str) -> str:
    """Credentialing due titles only.

    Never include Employee View fields: address, personal phone, personal
    email, emergency contact, or other HR record values.
    """
    lines = ["Credentialing items this week:"]
    bands = due_engine.bands(practice_id)
    items = [
        ob
        for ob in bands["overdue"] + bands["this_week"]
        if ob.domain == "credentialing"
    ]
    if not items:
        lines.append("None.")
    for ob in items:
        lines.append(ob.title)
    return "\n".join(lines)


def send_weekly_digest(practice_id: str) -> dict[str, str | bool]:
    """Does not call Gmail. A later phase can send only after the Admin toggle is on."""
    if not digest_enabled(practice_id):
        return {"sent": False, "reason": "off"}
    _ = digest_preview(practice_id)
    return {"sent": False, "reason": "not_sent"}
