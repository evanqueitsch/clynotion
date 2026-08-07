"""Audit log: ACTIONS, IDs, timestamps, controlled reason codes ONLY — never PHI."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union


class AuditAction(str, Enum):
    DRAFT_CREATED = "draft_created"
    AUDIO_DELETED = "audio_deleted"
    SESSION_FINALIZED = "session_finalized"
    VOICE_PROFILE_ENROLLED = "voice_profile_enrolled"


class AuditReason(str, Enum):
    """Controlled vocabulary only — no free-text reasons."""

    OVERRIDE_APPLIED = "override_applied"
    USER_DECLINED = "user_declined"
    FINALIZED = "finalized"
    AUDIO_DELETED = "audio_deleted"
    NO_AUDIO = "no_audio"
    ENROLLMENT_STUB = "enrollment_stub"


# Keys allowed on a persisted audit event (besides required action/session_id/ts).
_EVENT_KEYS = frozenset({"ts", "action", "session_id", "reason"})
# Optional kwargs accepted by audit() — must be a subset of _EVENT_KEYS minus required fields.
_ALLOWED_KWARGS = frozenset({"reason"})


def _coerce_action(action: Union[AuditAction, str]) -> AuditAction:
    if isinstance(action, AuditAction):
        return action
    try:
        return AuditAction(action)
    except ValueError as e:
        raise ValueError(f"Invalid audit action: {action!r}") from e


def _coerce_reason(reason: Union[AuditReason, str, None]) -> Optional[AuditReason]:
    if reason is None:
        return None
    if isinstance(reason, AuditReason):
        return reason
    try:
        return AuditReason(reason)
    except ValueError as e:
        raise ValueError(f"Invalid audit reason (not in controlled vocabulary): {reason!r}") from e


class AuditLog:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def audit(
        self,
        action: Union[AuditAction, str],
        session_id: str,
        **fields: Any,
    ) -> None:
        """
        Append an audit event. Raises on any disallowed key or non-enum reason.
        Never accepts free-text payloads.
        """
        disallowed = set(fields) - _ALLOWED_KWARGS
        if disallowed:
            raise ValueError(f"Disallowed audit key(s): {sorted(disallowed)}")

        action_e = _coerce_action(action)
        reason_e = _coerce_reason(fields.get("reason"))

        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")

        event: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action_e.value,
            "session_id": session_id,
        }
        if reason_e is not None:
            event["reason"] = reason_e.value

        # Final guard: no unexpected keys on the record
        extra = set(event) - _EVENT_KEYS
        if extra:
            raise ValueError(f"Audit event contains disallowed key(s): {sorted(extra)}")

        self._events.append(event)

    # Back-compat alias used by older call sites / tests
    def record(self, action: Union[AuditAction, str], session_id: str, **fields: Any) -> None:
        self.audit(action, session_id, **fields)

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


audit_log = AuditLog()
