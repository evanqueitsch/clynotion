"""Due engine — platform obligations for Home bands.

Every module writes obligations here (owner + due date required). Titles and
metadata are IDs/actions only — never note text, transcripts, or client names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import uuid4

Domain = Literal["comply", "people", "grow", "books", "platform"]
ObligationStatus = Literal["open", "done", "cancelled"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Obligation:
    obligation_id: str
    practice_id: str
    domain: Domain
    source: str
    title: str
    owner_user_id: str
    due_at: str
    status: ObligationStatus = "open"
    href: str = "/"
    source_ref: str = ""
    created_at: str = field(default_factory=lambda: _now().isoformat())
    updated_at: str = field(default_factory=lambda: _now().isoformat())

    def to_public_dict(self) -> dict:
        return {
            "obligation_id": self.obligation_id,
            "practice_id": self.practice_id,
            "domain": self.domain,
            "source": self.source,
            "title": self.title,
            "owner_user_id": self.owner_user_id,
            "due_at": self.due_at,
            "status": self.status,
            "href": self.href,
            "source_ref": self.source_ref,
        }


class DueEngine:
    """In-memory due register (stub). Postgres/RLS comes with Platform Core persistence."""

    def __init__(self) -> None:
        self._by_id: dict[str, Obligation] = {}
        # (practice_id, source, source_ref) → obligation_id
        self._by_source: dict[tuple[str, str, str], str] = {}

    def reset(self) -> None:
        self._by_id.clear()
        self._by_source.clear()

    def upsert(
        self,
        *,
        practice_id: str,
        domain: Domain,
        source: str,
        title: str,
        owner_user_id: str,
        due_at: str,
        href: str,
        source_ref: str = "",
        status: ObligationStatus = "open",
    ) -> Obligation:
        if not practice_id:
            raise ValueError("practice_id is required")
        if not owner_user_id:
            raise ValueError("owner_user_id is required")
        if not due_at:
            raise ValueError("due_at is required")
        if not title.strip():
            raise ValueError("title is required")

        key = (practice_id, source, source_ref) if source_ref else None
        existing_id = self._by_source.get(key) if key else None
        now = _now().isoformat()
        if existing_id and existing_id in self._by_id:
            ob = self._by_id[existing_id]
            ob.title = title
            ob.owner_user_id = owner_user_id
            ob.due_at = due_at
            ob.href = href
            ob.domain = domain
            ob.status = status
            ob.updated_at = now
            return ob

        oid = str(uuid4())
        ob = Obligation(
            obligation_id=oid,
            practice_id=practice_id,
            domain=domain,
            source=source,
            title=title,
            owner_user_id=owner_user_id,
            due_at=due_at,
            status=status,
            href=href,
            source_ref=source_ref,
            created_at=now,
            updated_at=now,
        )
        self._by_id[oid] = ob
        if key:
            self._by_source[key] = oid
        return ob

    def complete(
        self,
        *,
        practice_id: str,
        source: str,
        source_ref: str,
    ) -> Optional[Obligation]:
        key = (practice_id, source, source_ref)
        oid = self._by_source.get(key)
        if not oid:
            return None
        ob = self._by_id.get(oid)
        if ob is None:
            return None
        ob.status = "done"
        ob.updated_at = _now().isoformat()
        return ob

    def list_open(self, practice_id: str) -> list[Obligation]:
        items = [
            ob
            for ob in self._by_id.values()
            if ob.practice_id == practice_id and ob.status == "open"
        ]
        items.sort(key=lambda o: (_parse_iso(o.due_at), o.obligation_id))
        return items

    def bands(
        self,
        practice_id: str,
        *,
        now: Optional[datetime] = None,
        week_days: int = 7,
    ) -> dict[str, list[Obligation]]:
        """Split open obligations into overdue / this_week (Home)."""
        now = now or _now()
        week_end = now + timedelta(days=week_days)
        overdue: list[Obligation] = []
        this_week: list[Obligation] = []
        for ob in self.list_open(practice_id):
            due = _parse_iso(ob.due_at)
            if due <= now:
                overdue.append(ob)
            elif due <= week_end:
                this_week.append(ob)
        return {"overdue": overdue, "this_week": this_week}


due_engine = DueEngine()

SUPERVISION_SOURCE = "supervision"


def upsert_supervision_draft_obligation(
    *,
    practice_id: str,
    owner_user_id: str,
    session_id: str,
    due_at: Optional[str] = None,
) -> Obligation:
    """Register an open finalize obligation for an unfinalized supervision draft."""
    return due_engine.upsert(
        practice_id=practice_id,
        domain="people",
        source=SUPERVISION_SOURCE,
        title="Finalize supervision draft",
        owner_user_id=owner_user_id,
        due_at=due_at or _now().isoformat(),
        href="/#supervision",
        source_ref=session_id,
        status="open",
    )


def complete_supervision_draft_obligation(
    *,
    practice_id: str,
    session_id: str,
) -> Optional[Obligation]:
    return due_engine.complete(
        practice_id=practice_id,
        source=SUPERVISION_SOURCE,
        source_ref=session_id,
    )


def reconcile_supervision_drafts(
    *,
    practice_id: str,
    owner_user_id: str,
    sessions: list,
) -> None:
    """Ensure unfinalized supervision sessions appear on Home; mark finalized done."""
    for session in sessions:
        if getattr(session, "modality", "supervision") != "supervision":
            continue
        sid = session.session_id
        if session.finalized:
            complete_supervision_draft_obligation(
                practice_id=practice_id, session_id=sid
            )
            continue
        upsert_supervision_draft_obligation(
            practice_id=practice_id,
            owner_user_id=owner_user_id,
            session_id=sid,
            due_at=getattr(session, "updated_at", None) or _now().isoformat(),
        )
