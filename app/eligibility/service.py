"""OPS-5 eligibility verification — one interface, pluggable backends.

Checks are append-only. Case codes only (no client names). Mock backend for
offline tests; live Availity/EVS adapters require BAAs + human decisions B-21–23.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from app.crypto import decrypt_utf8, encrypt_utf8

ENV_ELIG_PERSISTENCE = "ATTUNE_ELIGIBILITY_PERSISTENCE"
ENV_ELIG_DATA_PATH = "ATTUNE_ELIGIBILITY_DATA_PATH"
DEFAULT_ELIG_DATA_PATH = ".attune_data/eligibility.enc"

Method = Literal["manual", "mock", "sp", "availity", "aggregator", "evs"]
Outcome = Literal["eligible", "ineligible", "error"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EligibilityCheck:
    check_id: str
    practice_id: str
    case_code: str
    payer: str
    plan: str
    service_date: str
    checked_at: str
    checked_by: str
    method: Method
    outcome: Outcome
    coverage_detail: str = ""
    evidence_ref: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "practice_id": self.practice_id,
            "case_code": self.case_code,
            "payer": self.payer,
            "plan": self.plan,
            "service_date": self.service_date,
            "checked_at": self.checked_at,
            "checked_by": self.checked_by,
            "method": self.method,
            "outcome": self.outcome,
            "coverage_detail": self.coverage_detail,
            "evidence_ref": self.evidence_ref,
        }


class EligibilityBackend(ABC):
    @abstractmethod
    def verify(
        self,
        *,
        case_code: str,
        payer: str,
        plan: str,
        service_date: str,
    ) -> tuple[Outcome, str]:
        """Return (outcome, coverage_detail). Never accepts client names."""


class MockEligibilityBackend(EligibilityBackend):
    """Deterministic offline backend — eligible unless case_code ends with X."""

    def verify(
        self,
        *,
        case_code: str,
        payer: str,
        plan: str,
        service_date: str,
    ) -> tuple[Outcome, str]:
        _ = (payer, plan, service_date)
        if case_code.upper().endswith("X"):
            return "ineligible", "mock: case_code suffix X → ineligible"
        return "eligible", "mock: eligible for service_date"


class ManualEligibilityBackend(EligibilityBackend):
    """Records a human-asserted outcome (caller supplies outcome via store)."""

    def verify(
        self,
        *,
        case_code: str,
        payer: str,
        plan: str,
        service_date: str,
    ) -> tuple[Outcome, str]:
        raise RuntimeError("manual backend requires explicit outcome")


def get_backend(method: Method) -> EligibilityBackend:
    if method == "mock":
        return MockEligibilityBackend()
    if method == "manual":
        return ManualEligibilityBackend()
    raise ValueError(
        f"eligibility method {method!r} not configured — needs BAA/adapter (B-21–23)"
    )


class EligibilityStore:
    def __init__(self) -> None:
        self._rows: list[EligibilityCheck] = []
        self._path = Path(
            (os.environ.get(ENV_ELIG_DATA_PATH) or DEFAULT_ELIG_DATA_PATH).strip()
        )
        self._mode = (
            os.environ.get(ENV_ELIG_PERSISTENCE) or "memory"
        ).strip().lower()
        self._memory_blob: Optional[bytes] = None
        self._load()

    def reset(self) -> None:
        self._rows.clear()
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
            items = json.loads(decrypt_utf8(raw))
        except ValueError:
            return
        for item in items or []:
            try:
                self._rows.append(
                    EligibilityCheck(
                        check_id=str(item["check_id"]),
                        practice_id=str(item["practice_id"]),
                        case_code=str(item["case_code"]),
                        payer=str(item["payer"]),
                        plan=str(item.get("plan") or ""),
                        service_date=str(item["service_date"]),
                        checked_at=str(item["checked_at"]),
                        checked_by=str(item["checked_by"]),
                        method=item["method"],
                        outcome=item["outcome"],
                        coverage_detail=str(item.get("coverage_detail") or ""),
                        evidence_ref=str(item.get("evidence_ref") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _flush(self) -> None:
        import base64

        blob = encrypt_utf8(json.dumps([r.to_public_dict() for r in self._rows]))
        if self._mode == "file":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "blob": base64.b64encode(blob).decode("ascii")}),
                encoding="utf-8",
            )
        else:
            self._memory_blob = blob

    def list_for_practice(self, practice_id: str) -> list[EligibilityCheck]:
        rows = [r for r in self._rows if r.practice_id == practice_id]
        rows.sort(key=lambda r: r.checked_at, reverse=True)
        return rows

    def record_check(
        self,
        *,
        practice_id: str,
        case_code: str,
        payer: str,
        plan: str,
        service_date: str,
        checked_by: str,
        method: Method = "mock",
        outcome: Optional[Outcome] = None,
        coverage_detail: str = "",
        evidence_ref: str = "",
    ) -> EligibilityCheck:
        code = case_code.strip().upper()
        if not code or not all(c.isalnum() or c in "-_" for c in code):
            raise ValueError("case_code required — no client names")
        if method == "manual":
            if outcome is None:
                raise ValueError("manual method requires outcome")
            detail = coverage_detail or "manual assertion"
        else:
            backend = get_backend(method)
            outcome, detail = backend.verify(
                case_code=code,
                payer=payer,
                plan=plan,
                service_date=service_date,
            )
            if coverage_detail:
                detail = coverage_detail
        row = EligibilityCheck(
            check_id=str(uuid4()),
            practice_id=practice_id,
            case_code=code,
            payer=payer.strip() or "unknown",
            plan=plan.strip(),
            service_date=service_date,
            checked_at=_now().isoformat(),
            checked_by=checked_by,
            method=method,
            outcome=outcome,
            coverage_detail=detail,
            evidence_ref=evidence_ref,
        )
        self._rows.append(row)
        self._flush()
        return row


eligibility_store = EligibilityStore()
