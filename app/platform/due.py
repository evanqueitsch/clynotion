"""Due engine — platform obligations for Home bands.

Every module writes obligations here (owner + due date required). Titles and
metadata are IDs/actions only — never note text, transcripts, or client identifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import uuid4

from app.platform.due_persist import DuePersistence, build_due_persistence

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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DueEngine:
    """Practice-scoped due register. Optional encrypted file persistence."""

    def __init__(self, persistence: Optional[DuePersistence] = None) -> None:
        self._persistence = persistence or build_due_persistence()
        self._by_id: dict[str, Obligation] = {}
        self._by_source: dict[tuple[str, str, str], str] = {}
        self._load()

    def _load(self) -> None:
        rows = self._persistence.load()
        for item in rows:
            try:
                ob = Obligation(
                    obligation_id=str(item["obligation_id"]),
                    practice_id=str(item["practice_id"]),
                    domain=item.get("domain") or "platform",  # type: ignore[arg-type]
                    source=str(item["source"]),
                    title=str(item["title"]),
                    owner_user_id=str(item["owner_user_id"]),
                    due_at=str(item["due_at"]),
                    status=item.get("status") or "open",  # type: ignore[arg-type]
                    href=str(item.get("href") or "/"),
                    source_ref=str(item.get("source_ref") or ""),
                    created_at=str(item.get("created_at") or _now().isoformat()),
                    updated_at=str(item.get("updated_at") or _now().isoformat()),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._by_id[ob.obligation_id] = ob
            if ob.source_ref:
                self._by_source[(ob.practice_id, ob.source, ob.source_ref)] = ob.obligation_id

    def _flush(self) -> None:
        rows = [ob.to_public_dict() for ob in self._by_id.values()]
        self._persistence.save(rows)

    def reset(self) -> None:
        self._by_id.clear()
        self._by_source.clear()
        self._persistence.clear()

    def get(self, obligation_id: str) -> Optional[Obligation]:
        return self._by_id.get(obligation_id)

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
            # Don't reopen a completed seeded clock unless caller forces open.
            if ob.status == "done" and status == "open" and source == "compliance_registry":
                self._flush()
                return ob
            ob.title = title
            ob.owner_user_id = owner_user_id
            if status == "open" and ob.status == "open":
                # Keep existing due_at for compliance seeds so weekly check is stable.
                if source != "compliance_registry":
                    ob.due_at = due_at
            else:
                ob.due_at = due_at
            ob.href = href
            ob.domain = domain
            if not (
                ob.status == "done"
                and source == "compliance_registry"
                and status == "open"
            ):
                ob.status = status
            ob.updated_at = now
            self._flush()
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
        self._flush()
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
        return self.complete_by_id(oid, practice_id=practice_id)

    def complete_by_id(
        self, obligation_id: str, *, practice_id: str
    ) -> Optional[Obligation]:
        ob = self._by_id.get(obligation_id)
        if ob is None or ob.practice_id != practice_id:
            return None
        ob.status = "done"
        ob.updated_at = _now().isoformat()
        self._flush()
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
