"""Practice (tenant) registry for Clynotion.

Every clinician, session, and Workspace token is scoped by practice_id.
This store is the platform source of truth for practice metadata; capture
routes keep using practice_id from the auth User.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from app.google_oauth import default_practice_id


@dataclass(frozen=True)
class Practice:
    practice_id: str
    display_name: str
    slug: str = ""
    allowed_domain: str = ""
    tools: tuple[str, ...] = ("supervision", "comply", "ingest")

    def to_public_dict(self) -> dict:
        return {
            "practice_id": self.practice_id,
            "display_name": self.display_name,
            "slug": self.slug or self.practice_id,
            "allowed_domain": self.allowed_domain,
            "tools": list(self.tools),
        }


def _env_domains() -> list[str]:
    raw = (os.environ.get("GOOGLE_ALLOWED_DOMAINS") or "").strip()
    if not raw:
        return []
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


class PracticeStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Practice] = {}
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        """Built-in practices for boot. HFC is a normal tenant row, not a fork."""
        domains = _env_domains()
        hfc_domain = domains[0] if domains else "heart4change.org"
        defaults = [
            Practice(
                practice_id="practice-a",
                display_name="Practice A (dev)",
                slug="practice-a",
                allowed_domain="example.com",
            ),
            Practice(
                practice_id="practice-b",
                display_name="Practice B (dev)",
                slug="practice-b",
                allowed_domain="example.com",
            ),
            Practice(
                practice_id=default_practice_id(),
                display_name="Heart for Change",
                slug="hfc",
                allowed_domain=hfc_domain,
            ),
        ]
        for p in defaults:
            self._by_id[p.practice_id] = p

    def get(self, practice_id: str) -> Optional[Practice]:
        return self._by_id.get(practice_id)

    def ensure(
        self,
        practice_id: str,
        *,
        display_name: str = "",
        allowed_domain: str = "",
    ) -> Practice:
        existing = self._by_id.get(practice_id)
        if existing is not None:
            return existing
        practice = Practice(
            practice_id=practice_id,
            display_name=display_name or practice_id,
            slug=practice_id,
            allowed_domain=allowed_domain,
        )
        self._by_id[practice_id] = practice
        return practice

    def list_all(self) -> list[Practice]:
        return sorted(self._by_id.values(), key=lambda p: p.display_name.lower())

    def reset(self) -> None:
        self._by_id.clear()
        self._seed_defaults()


practice_store = PracticeStore()
