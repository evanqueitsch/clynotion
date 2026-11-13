"""Encrypted denial store. Uses DENIALS_FERNET_KEY only.

This file is separate from credentialing and from ATTUNE_DATA_ENCRYPTION_KEY.
A missing key leaves the module unconfigured and writes nothing.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken

from app.denials.aggregate import summarize
from app.denials.deidentify import DeidentifyError, check_table, forbidden_value
from app.denials.mapping import FIELDS, FILE_TYPES, apply_mapping, clean_mapping, suggest
from app.denials.tabular import read_table
from app.denials.triage import COMPLIANCE_NOTE, action_label, decide

ENV_KEY = "DENIALS_FERNET_KEY"
ENV_PATH = "ATTUNE_DENIALS_DATA_PATH"
ENV_MODE = "ATTUNE_DENIALS_PERSISTENCE"
DEFAULT_PATH = ".attune_data/denials.enc"
_EASTERN = ZoneInfo("America/New_York")

UPLOAD_RETENTION_DAYS = 90
ROW_RETENTION_MONTHS = 24
AUDIT_RETENTION_YEARS = 6
UPLOAD_FLOOR_DAYS = 30
ROW_FLOOR_MONTHS = 6
CONFIRMATION = "PURGE DENIALS"
WORK_STATUSES = ("open", "in_progress", "waiting", "done")
_DENIED = {"denied", "rejected"}

PHI_CHECKLIST = (
    {"id": "baa", "label": "A signed BAA covers every system that would store claim PHI."},
    {"id": "minimum", "label": "The minimum-necessary access list for claim PHI is approved."},
    {"id": "procedure", "label": "The practice has accepted storing claim-level PHI in Clynotion."},
    {"id": "owner", "label": "An owner has approved turning PHI mode on."},
)


class DenialsConfigError(Exception):
    """The denial store is missing its key or the file will not decrypt."""


def _now() -> datetime:
    return datetime.now(_EASTERN)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse(value: str) -> datetime:
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_EASTERN)
    return moment.astimezone(_EASTERN)


def _subtract_months(moment: datetime, months: int) -> datetime:
    year = moment.year
    month = moment.month - months
    while month <= 0:
        month += 12
        year -= 1
    return moment.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _box() -> Optional[Fernet]:
    raw = os.environ.get(ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError):
        return None


def _path():
    from pathlib import Path

    return Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


def _file_mode() -> bool:
    return os.environ.get(ENV_MODE) == "file"


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


def _blank_practice() -> dict[str, Any]:
    return {
        "phi_mode": False,
        "upload_retention_days": UPLOAD_RETENTION_DAYS,
        "row_retention_months": ROW_RETENTION_MONTHS,
        "last_purge_on": "",
        "mappings": {},
        "uploads": [],
        "rows": [],
        "audit": [],
    }


def _annotate(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("claim_status") or "")
    if status not in _DENIED:
        category, action = "", ""
    else:
        category, action = decide(status, str(row.get("carc") or ""))
    copied = dict(row)
    copied["category"] = category
    copied["action"] = action
    return copied


class DenialsStore:
    def __init__(self) -> None:
        self._memory: Optional[dict[str, Any]] = None

    def configured(self) -> bool:
        return _box() is not None

    def reset(self) -> None:
        self._memory = None

    def _read(self) -> dict[str, Any]:
        if not _file_mode():
            if self._memory is None:
                self._memory = _empty()
            return self._memory
        box = _box()
        if box is None:
            raise DenialsConfigError("not configured")
        path = _path()
        if not path.is_file():
            return _empty()
        try:
            payload = json.loads(box.decrypt(path.read_bytes()).decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, OSError, ValueError) as exc:
            raise DenialsConfigError("unreadable") from exc
        if not isinstance(payload, dict):
            raise DenialsConfigError("unreadable")
        payload.setdefault("practices", {})
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        if not _file_mode():
            self._memory = payload
            return
        box = _box()
        if box is None:
            raise DenialsConfigError("not configured")
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = box.encrypt(json.dumps(payload).encode("utf-8"))
        temporary = path.with_name(path.name + ".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, blob)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)

    def _practice(self, payload: dict[str, Any], practice_id: str) -> dict[str, Any]:
        practices = payload.setdefault("practices", {})
        row = practices.get(practice_id)
        if not isinstance(row, dict):
            row = _blank_practice()
            practices[practice_id] = row
        row["phi_mode"] = False
        row.setdefault("upload_retention_days", UPLOAD_RETENTION_DAYS)
        row.setdefault("row_retention_months", ROW_RETENTION_MONTHS)
        row.setdefault("last_purge_on", "")
        row.setdefault("mappings", {})
        row.setdefault("uploads", [])
        row.setdefault("rows", [])
        row.setdefault("audit", [])
        return row

    def _audit(
        self,
        row: dict[str, Any],
        actor: str,
        action: str,
        what: str,
        counts: dict[str, int],
    ) -> None:
        events = row.setdefault("audit", [])
        events.append(
            {
                "at": _iso(_now()),
                "actor": (actor or "").strip().lower()[:120],
                "action": action,
                "what": what[:80],
                "counts": {str(key)[:40]: int(value) for key, value in counts.items()},
            }
        )

    def _require(self) -> None:
        if not self.configured():
            raise DenialsConfigError("Denial analysis is not configured.")

    def denied_count(self, practice_id: str) -> Optional[int]:
        if not self.configured():
            return None
        try:
            payload = self._read()
        except DenialsConfigError:
            return None
        row = (payload.get("practices") or {}).get(practice_id)
        if not isinstance(row, dict):
            return 0
        return sum(1 for item in row.get("rows") or [] if item.get("claim_status") in _DENIED)

    def inspect(self, file_type: str, filename: str, data: bytes, practice_id: str) -> dict[str, Any]:
        self._require()
        if file_type not in FILE_TYPES:
            raise ValueError("Choose a filed-claims file or a payment-report file.")
        headers, body = read_table(filename, data)
        check_table(headers, body)
        payload = self._read()
        saved = dict((self._practice(payload, practice_id).get("mappings") or {}).get(file_type) or {})
        return {
            "file_type": file_type,
            "headers": headers,
            "suggested": suggest(headers),
            "saved_mapping": saved,
            "row_count": len(body),
            "fields": [dict(item) for item in FIELDS],
        }

    def upload(
        self,
        *,
        practice_id: str,
        actor: str,
        file_type: str,
        filename: str,
        data: bytes,
        mapping: dict[str, str],
    ) -> dict[str, Any]:
        self._require()
        if file_type not in FILE_TYPES:
            raise ValueError("Choose a filed-claims file or a payment-report file.")
        headers, body = read_table(filename, data)
        check_table(headers, body)
        cleaned = clean_mapping(mapping, headers)
        parsed = apply_mapping(headers, body, cleaned)
        payload = self._read()
        row = self._practice(payload, practice_id)
        known = {str(item.get("dedupe_key") or "") for item in row["rows"]}
        added = 0
        skipped = 0
        upload_id = _new_id("upl")
        imported_at = _iso(_now())
        for item in parsed:
            stamp = item.get("status_date") or item.get("service_month") or ""
            dedupe = "|".join([item["claim_key"], item["claim_status"], stamp])
            if dedupe in known:
                skipped += 1
                continue
            known.add(dedupe)
            annotated = _annotate(item)
            row["rows"].append(
                {
                    "id": _new_id("row"),
                    "upload_id": upload_id,
                    "imported_at": imported_at,
                    "dedupe_key": dedupe,
                    "claim_key": annotated["claim_key"],
                    "payer": annotated["payer"],
                    "cpt": annotated["cpt"],
                    "modifiers": annotated["modifiers"],
                    "pos": annotated["pos"],
                    "diagnosis": annotated["diagnosis"],
                    "service_month": annotated["service_month"],
                    "billed": annotated["billed"] or "0.00",
                    "allowed": annotated["allowed"] or "0.00",
                    "paid": annotated["paid"] or "0.00",
                    "claim_status": annotated["claim_status"],
                    "carc": annotated["carc"],
                    "rarc": annotated["rarc"],
                    "remark": annotated["remark"],
                    "clinician_code": annotated["clinician_code"],
                    "status_date": annotated["status_date"],
                    "category": annotated["category"],
                    "action": annotated["action"],
                    "owner": "",
                    "work_status": "open",
                }
            )
            added += 1
        previous = dict(row["mappings"].get(file_type) or {})
        row["mappings"][file_type] = cleaned
        row["uploads"].append(
            {
                "id": upload_id,
                "file_type": file_type,
                "uploaded_at": imported_at,
                "uploaded_by": (actor or "").strip().lower()[:120],
                "row_count": len(body),
                "added": added,
                "skipped": skipped,
                "blob_b64": base64.b64encode(data).decode("ascii"),
            }
        )
        self._audit(row, actor, "upload", file_type, {"added": added, "skipped": skipped, "rows": len(body)})
        if previous != cleaned:
            self._audit(row, actor, "mapping", file_type, {"fields": len(cleaned)})
        self._write(payload)
        return {
            "upload_id": upload_id,
            "added": added,
            "skipped_duplicates": skipped,
            "row_count": len(body),
            "configured": True,
        }

    def _summary_body(self, row: dict[str, Any]) -> dict[str, Any]:
        annotated = [_annotate(item) for item in row.get("rows") or []]
        body = summarize(annotated)
        body["configured"] = True
        body["phi_mode"] = False
        body["compliance_note"] = COMPLIANCE_NOTE
        return body

    def dashboard(self, practice_id: str, actor: str) -> dict[str, Any]:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        body = self._summary_body(row)
        self._audit(row, actor, "view", "dashboard", {"claims": int(body["totals"]["claims"])})
        self._write(payload)
        return body

    def worklist(self, practice_id: str, actor: str) -> dict[str, Any]:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        items = []
        for item in row.get("rows") or []:
            if item.get("claim_status") not in _DENIED:
                continue
            annotated = _annotate(item)
            items.append(
                {
                    "id": annotated["id"],
                    "claim_key": annotated["claim_key"],
                    "payer": annotated["payer"],
                    "cpt": annotated["cpt"],
                    "modifiers": annotated["modifiers"],
                    "pos": annotated["pos"],
                    "diagnosis": annotated["diagnosis"],
                    "service_month": annotated["service_month"],
                    "billed": annotated["billed"],
                    "allowed": annotated["allowed"],
                    "paid": annotated["paid"],
                    "claim_status": annotated["claim_status"],
                    "carc": annotated["carc"],
                    "rarc": annotated["rarc"],
                    "remark": annotated["remark"],
                    "clinician_code": annotated["clinician_code"],
                    "category": annotated["category"],
                    "action": annotated["action"],
                    "action_label": action_label(annotated["action"]),
                    "owner": annotated.get("owner") or "",
                    "work_status": annotated.get("work_status") or "open",
                }
            )
        items.sort(key=lambda item: (item["service_month"], item["claim_key"], item["id"]))
        self._audit(row, actor, "view", "worklist", {"rows": len(items)})
        self._write(payload)
        return {"configured": True, "phi_mode": False, "compliance_note": COMPLIANCE_NOTE, "items": items}

    def update_work(
        self,
        practice_id: str,
        actor: str,
        row_id: str,
        *,
        owner: str,
        work_status: str,
    ) -> dict[str, str]:
        self._require()
        status = (work_status or "").strip()
        if status not in WORK_STATUSES:
            raise ValueError("Choose a work status.")
        label = (owner or "").strip()
        if len(label) > 80:
            raise ValueError("That owner label is too long.")
        if forbidden_value(label, "code"):
            raise ValueError("Owner is a short staff label, not contact information.")
        payload = self._read()
        row = self._practice(payload, practice_id)
        match = next((item for item in row["rows"] if item.get("id") == row_id), None)
        if match is None or match.get("claim_status") not in _DENIED:
            raise KeyError(row_id)
        match["owner"] = label
        match["work_status"] = status
        self._audit(row, actor, "edit", "work", {"rows": 1})
        self._write(payload)
        return {"id": row_id, "work_status": status, "owner": label}

    def export_csv(self, practice_id: str, actor: str) -> str:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        items = self._work_rows(row)
        self._audit(row, actor, "export", "worklist", {"rows": len(items)})
        self._write(payload)
        buffer = io.StringIO()
        fields = [
            "claim_key",
            "payer",
            "cpt",
            "modifiers",
            "pos",
            "diagnosis",
            "service_month",
            "billed",
            "allowed",
            "paid",
            "claim_status",
            "carc",
            "rarc",
            "remark",
            "clinician_code",
            "category",
            "action",
            "owner",
            "work_status",
        ]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for item in items:
            writer.writerow(item)
        return buffer.getvalue()

    def _work_rows(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for item in row.get("rows") or []:
            if item.get("claim_status") not in _DENIED:
                continue
            annotated = _annotate(item)
            annotated["action_label"] = action_label(annotated["action"])
            items.append(annotated)
        return items

    def list_uploads(self, practice_id: str) -> list[dict[str, Any]]:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        listed = []
        for item in row.get("uploads") or []:
            listed.append(
                {
                    "id": item.get("id") or "",
                    "file_type": item.get("file_type") or "",
                    "uploaded_at": item.get("uploaded_at") or "",
                    "added": int(item.get("added") or 0),
                    "skipped": int(item.get("skipped") or 0),
                    "row_count": int(item.get("row_count") or 0),
                }
            )
        return listed

    def delete_upload(self, practice_id: str, actor: str, upload_id: str) -> dict[str, int]:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        before = len(row["uploads"])
        row["uploads"] = [item for item in row["uploads"] if item.get("id") != upload_id]
        if len(row["uploads"]) == before:
            raise KeyError(upload_id)
        removed_rows = [item for item in row["rows"] if item.get("upload_id") == upload_id]
        row["rows"] = [item for item in row["rows"] if item.get("upload_id") != upload_id]
        self._audit(
            row,
            actor,
            "delete",
            "upload",
            {"uploads": 1, "rows": len(removed_rows)},
        )
        self._write(payload)
        return {"uploads": 1, "rows": len(removed_rows)}

    def settings_public(self, practice_id: str) -> dict[str, Any]:
        self._require()
        payload = self._read()
        row = self._practice(payload, practice_id)
        return self._settings_body(row)

    def _settings_body(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "phi_mode": False,
            "phi_mode_locked": True,
            "phi_checklist": [
                {"id": item["id"], "label": item["label"], "checked": False, "enabled": False}
                for item in PHI_CHECKLIST
            ],
            "upload_retention_days": int(row.get("upload_retention_days") or UPLOAD_RETENTION_DAYS),
            "row_retention_months": int(row.get("row_retention_months") or ROW_RETENTION_MONTHS),
            "audit_retention_years": AUDIT_RETENTION_YEARS,
            "audit_retention_editable": False,
            "upload_retention_floor_days": UPLOAD_FLOOR_DAYS,
            "row_retention_floor_months": ROW_FLOOR_MONTHS,
            "confirmation_phrase": CONFIRMATION,
            "audit": [
                {
                    "at": item.get("at") or "",
                    "actor": item.get("actor") or "",
                    "action": item.get("action") or "",
                    "what": item.get("what") or "",
                    "counts": item.get("counts") or {},
                }
                for item in (row.get("audit") or [])[-30:]
            ],
        }

    def _would_purge(self, row: dict[str, Any], days: int, months: int) -> tuple[int, int]:
        moment = _now()
        upload_cutoff = moment - timedelta(days=days)
        row_cutoff = _subtract_months(moment, months)
        uploads = 0
        for item in row.get("uploads") or []:
            if not item.get("blob_b64"):
                continue
            try:
                if _parse(str(item.get("uploaded_at") or "")) < upload_cutoff:
                    uploads += 1
            except ValueError:
                continue
        rows = 0
        for item in row.get("rows") or []:
            try:
                if _parse(str(item.get("imported_at") or "")) < row_cutoff:
                    rows += 1
            except ValueError:
                continue
        return uploads, rows

    def update_settings(
        self,
        practice_id: str,
        actor: str,
        *,
        upload_retention_days: int,
        row_retention_months: int,
        confirmation: str,
    ) -> dict[str, Any]:
        self._require()
        if upload_retention_days < UPLOAD_FLOOR_DAYS:
            raise ValueError(f"Original uploads are kept at least {UPLOAD_FLOOR_DAYS} days.")
        if row_retention_months < ROW_FLOOR_MONTHS:
            raise ValueError(f"Claim rows are kept at least {ROW_FLOOR_MONTHS} months.")
        payload = self._read()
        row = self._practice(payload, practice_id)
        previous_days = int(row.get("upload_retention_days") or UPLOAD_RETENTION_DAYS)
        previous_months = int(row.get("row_retention_months") or ROW_RETENTION_MONTHS)
        uploads, rows = self._would_purge(row, upload_retention_days, row_retention_months)
        shortening = upload_retention_days < previous_days or row_retention_months < previous_months
        if shortening and (uploads or rows) and (confirmation or "").strip() != CONFIRMATION:
            raise PermissionError(
                "Type PURGE DENIALS to confirm. This change would remove stored denial files or rows."
            )
        row["upload_retention_days"] = int(upload_retention_days)
        row["row_retention_months"] = int(row_retention_months)
        row["phi_mode"] = False
        removed_uploads, removed_rows = (0, 0)
        if shortening and (confirmation or "").strip() == CONFIRMATION:
            removed_uploads, removed_rows = self._purge_practice(row, _now())
        self._audit(
            row,
            actor,
            "retention",
            "settings",
            {
                "upload_days": int(upload_retention_days),
                "row_months": int(row_retention_months),
                "previous_upload_days": previous_days,
                "previous_row_months": previous_months,
                "uploads_removed": removed_uploads,
                "rows_removed": removed_rows,
            },
        )
        self._write(payload)
        return self._settings_body(row)

    def _purge_practice(self, row: dict[str, Any], moment: datetime) -> tuple[int, int]:
        days = int(row.get("upload_retention_days") or UPLOAD_RETENTION_DAYS)
        months = int(row.get("row_retention_months") or ROW_RETENTION_MONTHS)
        upload_cutoff = moment - timedelta(days=days)
        row_cutoff = _subtract_months(moment, months)
        audit_cutoff = moment.replace(year=moment.year - AUDIT_RETENTION_YEARS)
        kept_uploads = []
        removed_uploads = 0
        for item in row.get("uploads") or []:
            try:
                old = _parse(str(item.get("uploaded_at") or "")) < upload_cutoff
            except ValueError:
                old = False
            if old:
                if item.get("blob_b64"):
                    item.pop("blob_b64", None)
                    removed_uploads += 1
                try:
                    aged_out = _parse(str(item.get("uploaded_at") or "")) < audit_cutoff
                except ValueError:
                    aged_out = False
                if aged_out:
                    continue
            kept_uploads.append(item)
        # Original files past the upload window drop their bytes. The upload
        # record stays until the audit window so the deletion can be counted.
        row["uploads"] = kept_uploads
        kept_rows = []
        removed_rows = 0
        for item in row.get("rows") or []:
            try:
                old = _parse(str(item.get("imported_at") or "")) < row_cutoff
            except ValueError:
                old = False
            if old:
                removed_rows += 1
                continue
            kept_rows.append(item)
        row["rows"] = kept_rows
        kept_audit = []
        for item in row.get("audit") or []:
            try:
                old = _parse(str(item.get("at") or "")) < audit_cutoff
            except ValueError:
                old = False
            if not old:
                kept_audit.append(item)
        row["audit"] = kept_audit
        return removed_uploads, removed_rows

    def purge_if_due(self) -> None:
        """Once per Eastern day. No log line. Does nothing when the key is unset."""
        if not self.configured():
            return
        try:
            payload = self._read()
        except DenialsConfigError:
            return
        moment = _now()
        today = moment.date().isoformat()
        changed = False
        for practice_id, row in list((payload.get("practices") or {}).items()):
            if not isinstance(row, dict):
                continue
            if str(row.get("last_purge_on") or "") == today:
                continue
            removed_uploads, removed_rows = self._purge_practice(row, moment)
            row["last_purge_on"] = today
            row["phi_mode"] = False
            if removed_uploads or removed_rows:
                self._audit(
                    row,
                    "purge",
                    "purge",
                    "daily",
                    {"uploads": removed_uploads, "rows": removed_rows},
                )
            changed = True
            _ = practice_id
        if changed:
            try:
                self._write(payload)
            except DenialsConfigError:
                return

    def empty_dashboard(self) -> dict[str, Any]:
        body = summarize([])
        body["configured"] = False
        body["phi_mode"] = False
        body["compliance_note"] = COMPLIANCE_NOTE
        return body


denials_store = DenialsStore()


def not_configured_detail() -> str:
    return "Denial analysis is not configured."
