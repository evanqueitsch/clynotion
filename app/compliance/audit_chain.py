"""Tamper-evident audit chain.

Each entry stores the SHA-256 of the previous entry and of its own canonical
content. The chain starts with a genesis entry that counts and hashes the
existing raw audit and history rows. Those rows are not rewritten.

Nobody, including Admin, gets a function that edits or deletes an entry.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.compliance.masking import mask_diff, scrub_text
from app.compliance.store import compliance_store

EASTERN = ZoneInfo("America/New_York")
GENESIS_PREV = "0" * 64

_CHAIN_FIELDS = (
    "seq",
    "ts",
    "actor",
    "actor_role",
    "action",
    "object_type",
    "object_id",
    "ip",
    "user_agent",
    "diff",
    "prev_hash",
)


@dataclass
class AuditContext:
    actor: str = ""
    role: str = ""
    practice_id: str = ""
    ip: str = ""
    user_agent: str = ""


_context: ContextVar[AuditContext] = ContextVar("clynotion_audit_context", default=AuditContext())


def set_audit_context(ctx: AuditContext):
    return _context.set(ctx)


def reset_audit_context(token) -> None:
    _context.reset(token)


def current_context() -> AuditContext:
    return _context.get()


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def sha256_text(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _chain_body(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry.get(key) for key in _CHAIN_FIELDS}


def entry_hash(entry: dict[str, Any]) -> str:
    return sha256_text(canonical(_chain_body(entry)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def show_et(ts: str) -> str:
    raw = (ts or "").strip()
    if not raw:
        return ""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return ts
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")


def _material(practice_id: str) -> dict[str, Any]:
    """Read existing audit and history rows. Does not write those stores."""
    counts: dict[str, int] = {}
    pieces: list[Any] = []

    def take(name: str, rows: list[Any]) -> None:
        counts[name] = len(rows)
        pieces.append({name: rows})

    try:
        from app.employees.store import employees_store

        _, row = employees_store._load(practice_id)
        employee_rows: list[Any] = [item for item in (row.get("audit") or []) if isinstance(item, dict)]
        for record in (row.get("records") or {}).values():
            if isinstance(record, dict):
                employee_rows.extend(item for item in (record.get("history") or []) if isinstance(item, dict))
        take("employees", employee_rows)
    except Exception:
        counts["employees"] = -1
        pieces.append({"employees": "unreadable"})

    try:
        from app.credentialing.store import credentialing_store

        _, row = credentialing_store._load_practice(practice_id)
        cred_rows: list[Any] = [item for item in (row.get("reveal_audit") or []) if isinstance(item, dict)]
        for subject in (row.get("subjects") or {}).values():
            if isinstance(subject, dict):
                cred_rows.extend(item for item in (subject.get("history") or []) if isinstance(item, dict))
        take("credentialing", cred_rows)
    except Exception:
        counts["credentialing"] = -1
        pieces.append({"credentialing": "unreadable"})

    try:
        from app.hiring.engine import hiring

        payload = hiring._read()
        row = hiring._practice(payload, practice_id)
        take("hiring", [item for item in (row.get("history") or []) if isinstance(item, dict)])
    except Exception:
        counts["hiring"] = -1
        pieces.append({"hiring": "unreadable"})

    try:
        from app.audit import audit_log

        take("supervision", list(audit_log.events()))
    except Exception:
        counts["supervision"] = -1
        pieces.append({"supervision": "unreadable"})

    digest = sha256_text(canonical({"pieces": pieces}))
    return {"counts": counts, "legacy_hash": digest, "total": sum(value for value in counts.values() if value > 0)}


def _genesis(practice_id: str) -> dict[str, Any]:
    material = _material(practice_id)
    body = {
        "seq": 1,
        "ts": _now(),
        "actor": "clynotion",
        "actor_role": "system",
        "action": "genesis",
        "object_type": "legacy_audit",
        "object_id": practice_id,
        "ip": "",
        "user_agent": "",
        "diff": {
            "counts": material["counts"],
            "legacy_hash": material["legacy_hash"],
            "total": material["total"],
        },
        "prev_hash": GENESIS_PREV,
    }
    body["hash"] = entry_hash(body)
    return body


def note_from_request(practice_id: str = "", **kwargs: Any) -> None:
    """Append when a practice is known. Background calls with no practice are skipped."""
    practice = practice_id or current_context().practice_id
    if not practice:
        return
    note(practice, **kwargs)


def note(
    practice_id: str,
    *,
    action: str,
    object_type: str,
    object_id: str,
    actor: str = "",
    actor_role: str = "",
    diff: Optional[dict[str, Any]] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """Append one chain entry. Creates genesis first when the chain is empty."""
    if not practice_id:
        raise ValueError("practice_id is required")
    ctx = current_context()
    masked = diff if diff is not None else mask_diff(before, after)
    if isinstance(masked, dict):
        safe_diff: dict[str, Any] = {}
        for key, value in masked.items():
            if isinstance(value, str):
                safe_diff[str(key)] = scrub_text(value)
            else:
                safe_diff[str(key)] = value
    else:
        safe_diff = {"note": scrub_text(masked)}
    content = {
        "actor": (actor or ctx.actor or "unknown")[:180],
        "actor_role": (actor_role or ctx.role or "")[:80],
        "action": scrub_text(action, limit=80),
        "object_type": scrub_text(object_type, limit=80),
        "object_id": scrub_text(object_id, limit=120),
        "ip": (ip or ctx.ip or "")[:80],
        "user_agent": scrub_text(user_agent or ctx.user_agent, limit=180),
        "diff": safe_diff,
    }
    from app.compliance.store import _lock

    with _lock:
        entries = compliance_store.audit_entries(practice_id)
        if not entries:
            compliance_store.append_audit(practice_id, _genesis(practice_id))
            entries = compliance_store.audit_entries(practice_id)
        previous = entries[-1]
        body = {
            "seq": int(previous.get("seq") or len(entries)) + 1,
            "ts": _now(),
            "prev_hash": str(previous.get("hash") or ""),
            **content,
        }
        body["hash"] = entry_hash(body)
        return compliance_store.append_audit(practice_id, body)


def verify_chain(
    practice_id: str,
    *,
    actor: str = "clynotion",
    actor_role: str = "system",
) -> dict[str, Any]:
    """Walk the chain. A break opens a high-priority Admin home item."""
    entries = compliance_store.audit_entries(practice_id)
    problems: list[str] = []
    if not entries:
        problems.append("empty")
    previous_hash = ""
    for index, entry in enumerate(entries):
        if index == 0:
            if entry.get("prev_hash") != GENESIS_PREV or entry.get("action") != "genesis":
                problems.append(f"seq {entry.get('seq')} is not genesis")
        elif entry.get("prev_hash") != previous_hash:
            problems.append(f"seq {entry.get('seq')} previous hash does not match")
        if entry_hash(entry) != entry.get("hash"):
            problems.append(f"seq {entry.get('seq')} content hash does not match")
        previous_hash = str(entry.get("hash") or "")
    ok = not problems
    head = previous_hash
    record = compliance_store.append_integrity(
        practice_id,
        {
            "at": _now(),
            "ok": ok,
            "checked": len(entries),
            "head_hash": head,
            "problem_count": len(problems),
        },
    )
    _home_for_break(practice_id, ok)
    try:
        note(
            practice_id,
            action="verify_log",
            object_type="audit_chain",
            object_id=str(record.get("id") or ""),
            actor=actor or "clynotion",
            actor_role=actor_role or "system",
            diff={"ok": ok, "checked": len(entries), "problem_count": len(problems)},
        )
    except Exception:
        pass
    return {
        "ok": ok,
        "checked": len(entries),
        "head_hash": head,
        "problem_count": len(problems),
        "problems": problems[:10],
        "integrity_id": record.get("id") or "",
    }


def _home_for_break(practice_id: str, ok: bool) -> None:
    from app.platform.due import due_engine

    if ok:
        due_engine.complete(practice_id=practice_id, source="audit-integrity", source_ref="chain")
        return
    due_engine.upsert(
        practice_id=practice_id,
        domain="platform",
        source="audit-integrity",
        title="High priority: audit log integrity check failed",
        owner_user_id="admin",
        due_at=datetime.now(timezone.utc).isoformat(),
        href="/#audit-log",
        source_ref="chain",
        status="open",
    )


def public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": entry.get("seq"),
        "ts": entry.get("ts") or "",
        "ts_et": show_et(str(entry.get("ts") or "")),
        "actor": entry.get("actor") or "",
        "actor_role": entry.get("actor_role") or "",
        "action": entry.get("action") or "",
        "object_type": entry.get("object_type") or "",
        "object_id": entry.get("object_id") or "",
        "ip": entry.get("ip") or "",
        "user_agent": entry.get("user_agent") or "",
        "diff": entry.get("diff") or {},
        "prev_hash": entry.get("prev_hash") or "",
        "hash": entry.get("hash") or "",
    }
