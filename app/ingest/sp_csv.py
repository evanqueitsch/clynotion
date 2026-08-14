"""SimplePractice ingest — batched CSV uploads only (no scrape, no SP API).

Persists provenance + normalized non-name columns. Client/patient name columns
are stripped and never stored. Idempotent on content hash per practice+report.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from app.crypto import decrypt_utf8, encrypt_utf8
from app.platform.due import due_engine

ENV_INGEST_PERSISTENCE = "ATTUNE_INGEST_PERSISTENCE"
ENV_INGEST_DATA_PATH = "ATTUNE_INGEST_DATA_PATH"
DEFAULT_INGEST_DATA_PATH = ".attune_data/ingest.enc"

INGEST_SOURCE = "ingest"

# Columns we refuse to persist (likely PHI identifiers). Matching is case-insensitive.
_BLOCKED_COLUMN_RE = re.compile(
    r"(name|client|patient|dob|ssn|email|phone|address|member.?id|subscriber)",
    re.I,
)

# Allowed normalized keys for documentation report rows.
_DOC_ALLOWED = frozenset(
    {
        "appointment_id",
        "clinician_id",
        "documentation_status",
        "signed",
        "age_hours",
        "service_date",
    }
)

REPORT_TYPES = frozenset(
    {
        "documentation",
        "attendance",
        "billing",
        "demographics",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class IngestUpload:
    upload_id: str
    practice_id: str
    report_type: str
    content_hash: str
    uploaded_by: str
    uploaded_at: str
    row_count: int
    status: str = "ok"  # ok | failed | duplicate
    error_code: str = ""
    unsigned_aging_count: int = 0
    column_map: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "practice_id": self.practice_id,
            "report_type": self.report_type,
            "content_hash": self.content_hash,
            "uploaded_by": self.uploaded_by,
            "uploaded_at": self.uploaded_at,
            "row_count": self.row_count,
            "status": self.status,
            "error_code": self.error_code,
            "unsigned_aging_count": self.unsigned_aging_count,
            "metrics": dict(self.metrics),
        }


class IngestStore:
    def __init__(self) -> None:
        self._uploads: dict[str, IngestUpload] = {}
        self._hash_index: dict[tuple[str, str, str], str] = {}
        self._path = Path(
            (os.environ.get(ENV_INGEST_DATA_PATH) or DEFAULT_INGEST_DATA_PATH).strip()
        )
        self._mode = (os.environ.get(ENV_INGEST_PERSISTENCE) or "memory").strip().lower()
        self._memory_blob: Optional[bytes] = None
        self._load()

    def reset(self) -> None:
        self._uploads.clear()
        self._hash_index.clear()
        self._memory_blob = None
        if self._mode == "file" and self._path.is_file():
            self._path.unlink()

    def _load(self) -> None:
        raw: Optional[bytes] = None
        if self._mode == "file" and self._path.is_file():
            try:
                import base64
                import json

                wrapper = json.loads(self._path.read_text(encoding="utf-8"))
                raw = base64.b64decode(str(wrapper.get("blob") or ""))
            except (ValueError, OSError, TypeError):
                return
        elif self._memory_blob is not None:
            raw = self._memory_blob
        if not raw:
            return
        try:
            import json

            rows = json.loads(decrypt_utf8(raw))
        except ValueError:
            return
        if not isinstance(rows, list):
            return
        for item in rows:
            try:
                up = IngestUpload(
                    upload_id=str(item["upload_id"]),
                    practice_id=str(item["practice_id"]),
                    report_type=str(item["report_type"]),
                    content_hash=str(item["content_hash"]),
                    uploaded_by=str(item["uploaded_by"]),
                    uploaded_at=str(item["uploaded_at"]),
                    row_count=int(item.get("row_count") or 0),
                    status=str(item.get("status") or "ok"),
                    error_code=str(item.get("error_code") or ""),
                    unsigned_aging_count=int(item.get("unsigned_aging_count") or 0),
                    column_map=dict(item.get("column_map") or {}),
                    metrics=dict(item.get("metrics") or {}),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._uploads[up.upload_id] = up
            self._hash_index[(up.practice_id, up.report_type, up.content_hash)] = up.upload_id

    def _flush(self) -> None:
        import base64
        import json

        rows = [
            {
                **u.to_public_dict(),
                "column_map": u.column_map,
                "metrics": u.metrics,
            }
            for u in self._uploads.values()
        ]
        blob = encrypt_utf8(json.dumps(rows))
        if self._mode == "file":
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"version": 1, "blob": base64.b64encode(blob).decode("ascii")}),
                encoding="utf-8",
            )
        else:
            self._memory_blob = blob

    def list_for_practice(self, practice_id: str) -> list[IngestUpload]:
        items = [u for u in self._uploads.values() if u.practice_id == practice_id]
        items.sort(key=lambda u: u.uploaded_at, reverse=True)
        return items

    def latest_ok(self, practice_id: str, report_type: str) -> Optional[IngestUpload]:
        for u in self.list_for_practice(practice_id):
            if u.report_type == report_type and u.status in ("ok", "duplicate"):
                return u
        return None

    def ingest_csv(
        self,
        *,
        practice_id: str,
        report_type: str,
        csv_text: str,
        uploaded_by: str,
        column_map: Optional[dict[str, str]] = None,
    ) -> IngestUpload:
        if report_type not in REPORT_TYPES:
            raise ValueError(f"unsupported report_type: {report_type}")
        # Demographics are PHI-heavy — accept upload metadata only in v1 stub.
        if report_type == "demographics":
            raise ValueError(
                "demographics ingest deferred — use case codes in a later OPS-5 pass"
            )

        digest = _content_hash(csv_text)
        existing_id = self._hash_index.get((practice_id, report_type, digest))
        if existing_id and existing_id in self._uploads:
            dup = self._uploads[existing_id]
            # Idempotent: return prior upload marked duplicate for this call's bookkeeping.
            return IngestUpload(
                upload_id=dup.upload_id,
                practice_id=dup.practice_id,
                report_type=dup.report_type,
                content_hash=dup.content_hash,
                uploaded_by=dup.uploaded_by,
                uploaded_at=dup.uploaded_at,
                row_count=dup.row_count,
                status="duplicate",
                error_code="",
                unsigned_aging_count=dup.unsigned_aging_count,
                column_map=dict(dup.column_map),
                metrics=dict(dup.metrics),
            )

        try:
            row_count, unsigned_aging, resolved_map, metrics = _parse_report_csv(
                csv_text,
                report_type=report_type,
                column_map=column_map or {},
            )
        except ValueError as e:
            up = IngestUpload(
                upload_id=str(uuid4()),
                practice_id=practice_id,
                report_type=report_type,
                content_hash=digest,
                uploaded_by=uploaded_by,
                uploaded_at=_now().isoformat(),
                row_count=0,
                status="failed",
                error_code="parse_error",
            )
            self._uploads[up.upload_id] = up
            self._flush()
            _surface_ingest_failure(practice_id, uploaded_by, report_type, up.upload_id)
            raise ValueError(str(e)) from e

        up = IngestUpload(
            upload_id=str(uuid4()),
            practice_id=practice_id,
            report_type=report_type,
            content_hash=digest,
            uploaded_by=uploaded_by,
            uploaded_at=_now().isoformat(),
            row_count=row_count,
            status="ok",
            unsigned_aging_count=unsigned_aging,
            column_map=resolved_map,
            metrics=metrics,
        )
        self._uploads[up.upload_id] = up
        self._hash_index[(practice_id, report_type, digest)] = up.upload_id
        self._flush()
        _clear_ingest_failure(practice_id, report_type)
        if report_type == "documentation" and unsigned_aging > 0:
            due_engine.upsert(
                practice_id=practice_id,
                domain="comply",
                source=INGEST_SOURCE,
                title=f"Unsigned notes aging: {unsigned_aging} rows",
                owner_user_id=uploaded_by,
                due_at=_now().isoformat(),
                href="/#comply",
                source_ref=f"unsigned:{up.upload_id}",
                status="open",
            )
        return up


def _normalize_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")


def _col(row: dict[str, str], headers: dict[str, str], *logical_names: str) -> str:
    """Pick first matching header value (case-normalized). Never used for blocked cols."""
    for name in logical_names:
        key = _normalize_header(name)
        raw_header = headers.get(key)
        if raw_header and raw_header in row:
            return str(row.get(raw_header) or "").strip()
    return ""


def _parse_money(raw: str) -> float:
    cleaned = (raw or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _read_rows(csv_text: str) -> tuple[list[dict[str, str]], dict[str, str], list[str]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    fieldnames = [h for h in reader.fieldnames if h]
    headers = {_normalize_header(h): h for h in fieldnames}
    rows = [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]
    return rows, headers, fieldnames


def _parse_report_csv(
    csv_text: str,
    *,
    report_type: str,
    column_map: dict[str, str],
) -> tuple[int, int, dict[str, str], dict[str, Any]]:
    if report_type == "documentation":
        return _parse_documentation_csv(csv_text, column_map=column_map)
    if report_type == "attendance":
        return _parse_attendance_csv(csv_text)
    if report_type == "billing":
        return _parse_billing_csv(csv_text)
    return _parse_generic_csv(csv_text)


def _parse_generic_csv(
    csv_text: str,
) -> tuple[int, int, dict[str, str], dict[str, Any]]:
    rows, _headers, fieldnames = _read_rows(csv_text)
    for name in fieldnames:
        if _BLOCKED_COLUMN_RE.search(name or ""):
            # Count rows but never retain blocked column values.
            pass
    return len(rows), 0, {}, {"rows_total": len(rows)}


def _parse_attendance_csv(
    csv_text: str,
) -> tuple[int, int, dict[str, str], dict[str, Any]]:
    """Session counts by clinician/status — no client identifiers retained."""
    rows, headers, fieldnames = _read_rows(csv_text)
    for name in fieldnames:
        if _BLOCKED_COLUMN_RE.search(name or "") and not re.search(
            r"clinician|provider|therapist|staff", name or "", re.I
        ):
            # Client/patient name columns ignored entirely.
            continue
    by_clinician: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        clinician = (
            _col(row, headers, "Clinician", "Provider", "Therapist", "Staff")
            or "Unassigned"
        )
        status = (
            _col(row, headers, "Status", "Attendance", "Appt Status", "Appointment Status")
            or "Unknown"
        )
        by_clinician[clinician] = by_clinician.get(clinician, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    metrics: dict[str, Any] = {
        "sessions_total": len(rows),
        "clinician_count": len(by_clinician),
        "by_clinician": dict(sorted(by_clinician.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    return len(rows), 0, {}, metrics


def _parse_billing_csv(
    csv_text: str,
) -> tuple[int, int, dict[str, str], dict[str, Any]]:
    """Claim/payment aggregates — payer labels only, no client identifiers."""
    rows, headers, fieldnames = _read_rows(csv_text)
    for name in fieldnames:
        if _BLOCKED_COLUMN_RE.search(name or ""):
            continue
    by_payer: dict[str, int] = {}
    charged = 0.0
    paid = 0.0
    for row in rows:
        payer = (
            _col(
                row,
                headers,
                "Payer",
                "Insurance",
                "Primary Insurance",
                "Payer Name",
            )
            or "Unknown"
        )
        by_payer[payer] = by_payer.get(payer, 0) + 1
        charged += _parse_money(
            _col(row, headers, "Amount", "Charged", "Fee", "Billed", "Charge")
        )
        paid += _parse_money(
            _col(row, headers, "Paid", "Payment", "Amount Paid", "Received")
        )
    metrics: dict[str, Any] = {
        "claims_total": len(rows),
        "payer_count": len(by_payer),
        "charged_total": round(charged, 2),
        "paid_total": round(paid, 2),
        "balance_total": round(charged - paid, 2),
        "by_payer": dict(sorted(by_payer.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    return len(rows), 0, {}, metrics


def _parse_documentation_csv(
    csv_text: str,
    *,
    column_map: dict[str, str],
) -> tuple[int, int, dict[str, str], dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    headers = {_normalize_header(h): h for h in reader.fieldnames if h}

    # Default map: logical → normalized header
    defaults = {
        "appointment_id": "appointment_id",
        "documentation_status": "documentation_status",
        "signed": "signed",
        "age_hours": "age_hours",
        "clinician_id": "clinician_id",
        "service_date": "service_date",
    }
    resolved: dict[str, str] = {}
    for logical, default_hdr in defaults.items():
        raw = column_map.get(logical) or default_hdr
        key = _normalize_header(raw)
        if key in headers:
            resolved[logical] = headers[key]

    if "documentation_status" not in resolved and "signed" not in resolved:
        raise ValueError(
            "documentation CSV needs documentation_status or signed column "
            "(configure column_map if headers differ)"
        )

    unsigned = 0
    count = 0
    for row in reader:
        count += 1
        status_val = ""
        if "documentation_status" in resolved:
            status_val = str(row.get(resolved["documentation_status"]) or "").strip().lower()
        signed_val = ""
        if "signed" in resolved:
            signed_val = str(row.get(resolved["signed"]) or "").strip().lower()
        age_raw = row.get(resolved["age_hours"]) if "age_hours" in resolved else ""
        try:
            age_hours = float(age_raw) if age_raw not in (None, "") else 0.0
        except ValueError:
            age_hours = 0.0
        is_unsigned = (
            signed_val in ("0", "false", "no", "unsigned", "n")
            or status_val in ("unsigned", "missing", "incomplete", "draft")
        )
        if is_unsigned and age_hours >= 72:
            unsigned += 1
        elif is_unsigned and "age_hours" not in resolved:
            unsigned += 1
    metrics: dict[str, Any] = {
        "rows_total": count,
        "unsigned_aging": unsigned,
    }
    return count, unsigned, resolved, metrics


def _surface_ingest_failure(
    practice_id: str, owner_user_id: str, report_type: str, upload_id: str
) -> None:
    due_engine.upsert(
        practice_id=practice_id,
        domain="platform",
        source=INGEST_SOURCE,
        title=f"Ingest failed: {report_type} report",
        owner_user_id=owner_user_id,
        due_at=_now().isoformat(),
        href="/#ingest",
        source_ref=f"fail:{report_type}",
        status="open",
    )


def _clear_ingest_failure(practice_id: str, report_type: str) -> None:
    due_engine.complete(
        practice_id=practice_id,
        source=INGEST_SOURCE,
        source_ref=f"fail:{report_type}",
    )


def reconcile_stale_ingest(
    *,
    practice_id: str,
    owner_user_id: str,
    max_age_days: int = 10,
) -> None:
    """If weekly documentation feed is stale, surface on Home."""
    latest = ingest_store.latest_ok(practice_id, "documentation")
    now = _now()
    if latest is None:
        due_engine.upsert(
            practice_id=practice_id,
            domain="platform",
            source=INGEST_SOURCE,
            title="Upload weekly documentation report",
            owner_user_id=owner_user_id,
            due_at=now.isoformat(),
            href="/#ingest",
            source_ref="stale:documentation",
            status="open",
        )
        return
    uploaded = datetime.fromisoformat(latest.uploaded_at.replace("Z", "+00:00"))
    if uploaded.tzinfo is None:
        uploaded = uploaded.replace(tzinfo=timezone.utc)
    if now - uploaded > timedelta(days=max_age_days):
        due_engine.upsert(
            practice_id=practice_id,
            domain="platform",
            source=INGEST_SOURCE,
            title="Documentation ingest stale — re-upload SP report",
            owner_user_id=owner_user_id,
            due_at=now.isoformat(),
            href="/#ingest",
            source_ref="stale:documentation",
            status="open",
        )
    else:
        due_engine.complete(
            practice_id=practice_id,
            source=INGEST_SOURCE,
            source_ref="stale:documentation",
        )


ingest_store = IngestStore()
