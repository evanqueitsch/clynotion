"""WorkflowEngine seam. RulesEngine is the default. JevAdvisor stays off."""

from __future__ import annotations

import os
from typing import Any, Optional, Protocol


class WorkflowEngine(Protocol):
    def on_event(self, event: dict[str, Any]) -> None:
        """Record a local event. Must not send it anywhere."""

    def suggest(self, question: str) -> Optional[dict[str, Any]]:
        """Return a local suggestion, or None."""


class RulesEngine:
    """Deterministic suggestions from the question text. No network."""

    def on_event(self, event: dict[str, Any]) -> None:
        return None

    def suggest(self, question: str) -> Optional[dict[str, Any]]:
        text = (question or "").casefold()
        if "stalled" in text:
            return {"choice": "review_stalled", "source": "rules"}
        if "follow" in text:
            return {"choice": "follow_up", "source": "rules"}
        return None


class JevAdvisor:
    """Placeholder. Off unless ATTUNE_WORKFLOW_ENGINE=jev, and it still makes no network call."""

    def on_event(self, event: dict[str, Any]) -> None:
        return None

    def suggest(self, question: str) -> Optional[dict[str, Any]]:
        if os.environ.get("ATTUNE_WORKFLOW_ENGINE", "").strip().lower() != "jev":
            return None
        return None


def workflow_engine() -> WorkflowEngine:
    if os.environ.get("ATTUNE_WORKFLOW_ENGINE", "").strip().lower() == "jev":
        return JevAdvisor()
    return RulesEngine()
