"""Six credential clocks per clinician → due engine.

Staff roster clocks (not client PHI). Titles use clinician display names for
operators; due-engine / audit still key by clinician_id + clock id.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.clinicians import clinician_store
from app.crypto import decrypt_utf8, encrypt_utf8
from app.platform.catalog import CREDENTIAL_CLOCKS
from app.platform.due import due_engine

ENV_CRED_PERSISTENCE = "ATTUNE_CREDENTIAL_PERSISTENCE"
ENV_CRED_DATA_PATH = "ATTUNE_CREDENTIAL_DATA_PATH"
DEFAULT_CRED_DATA_PATH = ".attune_data/credentials.enc"

CREDENTIAL_SOURCE = "credential_clocks"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime:
    raw = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class ClockDef:
    clock_id: str
    name: str
    cadence_days: int
    authorities: tuple[str, ...]


def clock_defs() -> list[ClockDef]:
    return [
        ClockDef(
            clock_id=str(c["id"]),
            name=str(c["name"]),
            cadence_days=int(c["cadence_days"]),
            authorities=tuple(c.get("authorities") or ()),
        )
        for c in CREDENTIAL_CLOCKS
    ]


class CredentialStore:
    """last_completed ISO per practice → clinician → clock_id."""

    def __init__(self) -> None:
        # practice_id -> clinician_id -> clock_id -> last_completed ISO
        self._data: dict[str, dict[str, dict[str, str]]] = {}
        self._path = Path(
            (os.environ.get(ENV_CRED_DATA_PATH) or DEFAULT_CRED_DATA_PATH).strip()
        )
        self._mode = (
            os.environ.get(ENV_CRED_PERSISTENCE) or "memory"
        ).strip().lower()
        self._memory_blob: Optional[bytes] = None
        self._load()

    def reset(self) -> None:
        self._data.clear()
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
        practices = payload.get("practices") if isinstance(payload, dict) else None
        if not isinstance(practices, dict):
            return
        out: dict[str, dict[str, dict[str, str]]] = {}
        for pid, bucket in practices.items():
            if not isinstance(bucket, dict):
                continue
            out[str(pid)] = {}
            for cid, clocks in bucket.items():
                if not isinstance(clocks, dict):
                    continue
                out[str(pid)][str(cid)] = {
                    str(k): str(v) for k, v in clocks.items() if v
                }
        self._data = out

    def _flush(self) -> None:
        import base64

        blob = encrypt_utf8(json.dumps({"version": 1, "practices": self._data}))
        if self._mode == "file":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "blob": base64.b64encode(blob).decode("ascii")}),
                encoding="utf-8",
            )
        else:
            self._memory_blob = blob

    def last_completed(
        self, practice_id: str, clinician_id: str, clock_id: str
    ) -> Optional[str]:
        return (
            self._data.get(practice_id, {})
            .get(clinician_id, {})
            .get(clock_id)
        )

    def set_completed(
        self,
        *,
        practice_id: str,
        clinician_id: str,
        clock_id: str,
        completed_at: Optional[str] = None,
        owner_user_id: str,
    ) -> dict[str, Any]:
        if clock_id not in {c.clock_id for c in clock_defs()}:
            raise ValueError(f"unknown clock_id: {clock_id}")
        clin = clinician_store.get(practice_id, clinician_id)
        if clin is None or not clin.included:
            raise KeyError(clinician_id)
        when = completed_at or _now().isoformat()
        _parse(when)  # validate
        self._data.setdefault(practice_id, {}).setdefault(clinician_id, {})[
            clock_id
        ] = when
        self._flush()
        return self._sync_one(
            practice_id=practice_id,
            clinician_id=clinician_id,
            display_name=clin.display_name,
            clock_id=clock_id,
            owner_user_id=owner_user_id,
        )

    def matrix(self, practice_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for clin in clinician_store.list_for_practice(practice_id):
            if not clin.included:
                continue
            clocks = []
            for clock in clock_defs():
                last = self.last_completed(
                    practice_id, clin.clinician_id, clock.clock_id
                )
                due = self._due_at(last, clock.cadence_days)
                clocks.append(
                    {
                        "clock_id": clock.clock_id,
                        "name": clock.name,
                        "cadence_days": clock.cadence_days,
                        "authorities": list(clock.authorities),
                        "last_completed": last or "",
                        "due_at": due.isoformat(),
                        "overdue": due <= _now(),
                    }
                )
            rows.append(
                {
                    "clinician_id": clin.clinician_id,
                    "display_name": clin.display_name,
                    "default_role": clin.default_role,
                    "clocks": clocks,
                }
            )
        rows.sort(key=lambda r: r["display_name"].lower())
        return rows

    def reconcile(self, *, practice_id: str, owner_user_id: str) -> int:
        """Upsert due-engine rows for every included clinician × clock."""
        n = 0
        for clin in clinician_store.list_for_practice(practice_id):
            if not clin.included:
                # Clear open clocks if someone was excluded
                for clock in clock_defs():
                    due_engine.complete(
                        practice_id=practice_id,
                        source=CREDENTIAL_SOURCE,
                        source_ref=f"{clin.clinician_id}:{clock.clock_id}",
                    )
                continue
            for clock in clock_defs():
                self._sync_one(
                    practice_id=practice_id,
                    clinician_id=clin.clinician_id,
                    display_name=clin.display_name,
                    clock_id=clock.clock_id,
                    owner_user_id=owner_user_id,
                )
                n += 1
        return n

    def _due_at(self, last_completed: Optional[str], cadence_days: int) -> datetime:
        if not last_completed:
            # Unknown baseline — surface as overdue so someone sets a date.
            return _now() - timedelta(seconds=1)
        return _parse(last_completed) + timedelta(days=cadence_days)

    def _sync_one(
        self,
        *,
        practice_id: str,
        clinician_id: str,
        display_name: str,
        clock_id: str,
        owner_user_id: str,
    ) -> dict[str, Any]:
        clock = next(c for c in clock_defs() if c.clock_id == clock_id)
        last = self.last_completed(practice_id, clinician_id, clock_id)
        due = self._due_at(last, clock.cadence_days)
        ref = f"{clinician_id}:{clock_id}"
        title = f"{clock.name} — {display_name}"
        ob = due_engine.upsert(
            practice_id=practice_id,
            domain="comply",
            source=CREDENTIAL_SOURCE,
            title=title,
            owner_user_id=owner_user_id,
            due_at=due.isoformat(),
            href="/#comply",
            source_ref=ref,
            status="open",
        )
        return {
            "obligation_id": ob.obligation_id,
            "clinician_id": clinician_id,
            "clock_id": clock_id,
            "title": title,
            "due_at": due.isoformat(),
            "last_completed": last or "",
        }


credential_store = CredentialStore()
