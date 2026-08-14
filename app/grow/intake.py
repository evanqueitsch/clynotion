"""OPS-3 intake log — access-standard timestamps without free-text client names.

Attune/Clynotion generates the intake number first (IN-0001). Case codes only —
no client names until B-10 resolves. Missed access standards write to the due engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from app.crypto import decrypt_utf8, encrypt_utf8
from app.platform.due import due_engine

ENV_INTAKE_PERSISTENCE = "ATTUNE_INTAKE_PERSISTENCE"
ENV_INTAKE_DATA_PATH = "ATTUNE_INTAKE_DATA_PATH"
DEFAULT_INTAKE_DATA_PATH = ".attune_data/intake.enc"

INTAKE_SOURCE = "ops3"

Channel = Literal[
    "phone",
    "performcare",
    "care_manager",
    "pcp",
    "website",
    "walk_in",
]
Triage = Literal["routine", "urgent", "emergent"]
Outcome = Literal[
    "open",
    "scheduled",
    "declined",
    "referred_out",
    "no_response",
    "waitlisted",
]

# Urgent/emergent windows are placeholders per master scope A-25.
TRIAGE_STANDARD_DAYS: dict[str, int] = {
    "routine": 7,
    "urgent": 1,
    "emergent": 0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime:
    raw = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class IntakeEvent:
    intake_id: str
    practice_id: str
    case_code: str
    channel: Channel
    triage: Triage
    request_at: str
    date_offered: str = ""
    date_scheduled: str = ""
    outcome: Outcome = "open"
    created_by: str = ""
    created_at: str = field(default_factory=lambda: _now().isoformat())
    updated_at: str = field(default_factory=lambda: _now().isoformat())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "intake_id": self.intake_id,
            "practice_id": self.practice_id,
            "case_code": self.case_code,
            "channel": self.channel,
            "triage": self.triage,
            "request_at": self.request_at,
            "date_offered": self.date_offered,
            "date_scheduled": self.date_scheduled,
            "outcome": self.outcome,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "standard_days": TRIAGE_STANDARD_DAYS[self.triage],
            "within_standard": self.within_standard(),
        }

    def within_standard(self) -> Optional[bool]:
        if not self.date_offered and not self.date_scheduled:
            # Still open — overdue if past standard from request.
            deadline = _parse(self.request_at) + timedelta(
                days=TRIAGE_STANDARD_DAYS[self.triage]
            )
            if self.outcome == "open" and _now() > deadline:
                return False
            return None
        offered = _parse(self.date_offered or self.date_scheduled)
        deadline = _parse(self.request_at) + timedelta(
            days=TRIAGE_STANDARD_DAYS[self.triage]
        )
        return offered <= deadline


class IntakeStore:
    def __init__(self) -> None:
        self._by_id: dict[str, IntakeEvent] = {}
        self._seq: dict[str, int] = {}
        self._path = Path(
            (os.environ.get(ENV_INTAKE_DATA_PATH) or DEFAULT_INTAKE_DATA_PATH).strip()
        )
        self._mode = (os.environ.get(ENV_INTAKE_PERSISTENCE) or "memory").strip().lower()
        self._memory_blob: Optional[bytes] = None
        self._load()

    def reset(self) -> None:
        self._by_id.clear()
        self._seq.clear()
        self._memory_blob = None
        if self._mode == "file" and self._path.is_file():
            self._path.unlink()

    def _load(self) -> None:
        raw: Optional[bytes] = None
        if self._mode == "file" and self._path.is_file():
            try:
                import base64

                wrapper = json.loads(self._path.read_text(encoding="utf-8"))
                raw = base64.b64decode(str(wrapper.get("blob") or ""))
            except (ValueError, OSError, TypeError):
                return
        elif self._memory_blob is not None:
            raw = self._memory_blob
        if not raw:
            return
        try:
            payload = json.loads(decrypt_utf8(raw))
        except ValueError:
            return
        self._seq = {str(k): int(v) for k, v in (payload.get("seq") or {}).items()}
        for item in payload.get("events") or []:
            try:
                ev = IntakeEvent(
                    intake_id=str(item["intake_id"]),
                    practice_id=str(item["practice_id"]),
                    case_code=str(item["case_code"]),
                    channel=item["channel"],
                    triage=item["triage"],
                    request_at=str(item["request_at"]),
                    date_offered=str(item.get("date_offered") or ""),
                    date_scheduled=str(item.get("date_scheduled") or ""),
                    outcome=item.get("outcome") or "open",
                    created_by=str(item.get("created_by") or ""),
                    created_at=str(item.get("created_at") or _now().isoformat()),
                    updated_at=str(item.get("updated_at") or _now().isoformat()),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._by_id[ev.intake_id] = ev

    def _flush(self) -> None:
        import base64

        payload = {
            "seq": self._seq,
            "events": [e.to_public_dict() for e in self._by_id.values()],
        }
        # Strip computed fields from persist
        for row in payload["events"]:
            row.pop("standard_days", None)
            row.pop("within_standard", None)
        blob = encrypt_utf8(json.dumps(payload))
        if self._mode == "file":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "blob": base64.b64encode(blob).decode("ascii")}),
                encoding="utf-8",
            )
        else:
            self._memory_blob = blob

    def _next_id(self, practice_id: str) -> str:
        n = self._seq.get(practice_id, 0) + 1
        self._seq[practice_id] = n
        return f"IN-{n:04d}"

    def create(
        self,
        *,
        practice_id: str,
        case_code: str,
        channel: Channel,
        triage: Triage,
        created_by: str,
        request_at: Optional[str] = None,
    ) -> IntakeEvent:
        code = case_code.strip().upper()
        if not code or " " in code or len(code) > 32:
            raise ValueError("case_code required (no spaces, max 32) — no client names")
        if any(ch.isalpha() is False and ch not in "-_" for ch in code):
            # allow alnum + dash/underscore only
            if not all(c.isalnum() or c in "-_" for c in code):
                raise ValueError("case_code must be alphanumeric")
        ev = IntakeEvent(
            intake_id=self._next_id(practice_id),
            practice_id=practice_id,
            case_code=code,
            channel=channel,
            triage=triage,
            request_at=request_at or _now().isoformat(),
            created_by=created_by,
        )
        self._by_id[ev.intake_id] = ev
        self._flush()
        self._sync_due(ev, owner_user_id=created_by)
        return ev

    def update(
        self,
        *,
        practice_id: str,
        intake_id: str,
        date_offered: Optional[str] = None,
        date_scheduled: Optional[str] = None,
        outcome: Optional[Outcome] = None,
        owner_user_id: str,
    ) -> IntakeEvent:
        ev = self._by_id.get(intake_id)
        if ev is None or ev.practice_id != practice_id:
            raise KeyError(intake_id)
        if date_offered is not None:
            ev.date_offered = date_offered
        if date_scheduled is not None:
            ev.date_scheduled = date_scheduled
        if outcome is not None:
            ev.outcome = outcome
        ev.updated_at = _now().isoformat()
        self._flush()
        self._sync_due(ev, owner_user_id=owner_user_id)
        return ev

    def list_for_practice(self, practice_id: str) -> list[IntakeEvent]:
        items = [e for e in self._by_id.values() if e.practice_id == practice_id]
        items.sort(key=lambda e: e.request_at, reverse=True)
        return items

    def access_performance(self, practice_id: str, *, days: int = 30) -> dict[str, Any]:
        cutoff = _now() - timedelta(days=days)
        rows = [
            e
            for e in self.list_for_practice(practice_id)
            if _parse(e.request_at) >= cutoff
        ]
        scored = [e for e in rows if e.within_standard() is not None]
        met = sum(1 for e in scored if e.within_standard() is True)
        return {
            "window_days": days,
            "requests": len(rows),
            "scored": len(scored),
            "met": met,
            "pct_met": round(100.0 * met / len(scored), 1) if scored else None,
        }

    def _sync_due(self, ev: IntakeEvent, *, owner_user_id: str) -> None:
        deadline = _parse(ev.request_at) + timedelta(
            days=TRIAGE_STANDARD_DAYS[ev.triage]
        )
        ref = f"access:{ev.intake_id}"
        if ev.outcome != "open":
            due_engine.complete(
                practice_id=ev.practice_id, source=INTAKE_SOURCE, source_ref=ref
            )
            return
        if ev.within_standard() is False or (
            ev.outcome == "open" and _now() > deadline
        ):
            due_engine.upsert(
                practice_id=ev.practice_id,
                domain="grow",
                source=INTAKE_SOURCE,
                title=f"Access standard missed: {ev.intake_id}",
                owner_user_id=owner_user_id,
                due_at=_now().isoformat(),
                href="/#intake",
                source_ref=ref,
                status="open",
            )
        else:
            # Upcoming clock — due at deadline
            due_engine.upsert(
                practice_id=ev.practice_id,
                domain="grow",
                source=INTAKE_SOURCE,
                title=f"Offer appointment: {ev.intake_id} ({ev.triage})",
                owner_user_id=owner_user_id,
                due_at=deadline.isoformat(),
                href="/#intake",
                source_ref=ref,
                status="open",
            )


intake_store = IntakeStore()
