"""Encrypted employee records.

Separate file from credentialing. A missing ``ATTUNE_EMPLOYEES_ENCRYPTION_KEY``
falls back to ``ATTUNE_DATA_ENCRYPTION_KEY`` (shared-key risk). Setting the
employees key does not rotate or rewrite credentialing.enc. This store never
prints record values.

Audit rows are kept. Nothing here discards old events after a count.
Supervision audit in ``app.audit`` is still in-memory only.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from app.crypto import decrypt_utf8, encrypt_utf8
from app.platform.catalog import CREDENTIAL_CLOCKS

ENV_PATH = "ATTUNE_EMPLOYEES_DATA_PATH"
ENV_MODE = "ATTUNE_EMPLOYEES_PERSISTENCE"
ENV_KEY = "ATTUNE_EMPLOYEES_ENCRYPTION_KEY"
DEFAULT_PATH = ".attune_data/employees.enc"
_EASTERN = ZoneInfo("America/New_York")

CONTACT_FIELDS = (
    "preferred_name",
    "street",
    "city",
    "state",
    "zip",
    "personal_phone",
    "personal_email",
    "work_phone",
    "emergency_name",
    "emergency_phone",
)
# Values for these fields are never copied into history, audit, or change summaries.
SENSITIVE_CONTACT = frozenset(
    {
        "street",
        "city",
        "state",
        "zip",
        "personal_phone",
        "personal_email",
        "work_phone",
        "emergency_name",
        "emergency_phone",
    }
)
FIELD_LABELS = {
    "preferred_name": "Preferred name",
    "street": "Street",
    "city": "City",
    "state": "State",
    "zip": "ZIP",
    "personal_phone": "Personal phone",
    "personal_email": "Personal email",
    "work_phone": "Work phone",
    "emergency_name": "Emergency contact name",
    "emergency_phone": "Emergency contact phone",
    "specialties": "Specialties",
    "trainings": "Trainings",
    "certifications": "Certifications",
    "credentialing_link": "Credentialing profile",
    "workspace_link": "Workspace account",
    "license_type": "License type",
    "npi": "NPI",
    "license_number": "License number",
    "caqh_id": "CAQH ID",
    "license_expires_on": "License expiration",
    "caqh_attested_on": "CAQH attestation",
    "license_renewal": "License renewal",
}
SECRET_APPROVAL = frozenset({"npi", "license_number", "caqh_id", "tin"})
PLAIN_APPROVAL = frozenset(
    {"license_type", "license_expires_on", "caqh_attested_on"}
)
QUALIFICATION_TYPES = (
    {"id": "lcsw", "name": "LCSW", "kind": "license"},
    {"id": "lsw", "name": "LSW", "kind": "license"},
    {"id": "lpc", "name": "LPC", "kind": "license"},
    {"id": "lapc", "name": "LAPC", "kind": "license"},
    {"id": "lmft", "name": "LMFT", "kind": "license"},
    {"id": "psychologist", "name": "Psychologist", "kind": "license"},
    {"id": "ncc", "name": "NCC", "kind": "certification"},
    {"id": "cadc", "name": "CADC", "kind": "certification"},
    {"id": "cpr", "name": "CPR/First Aid", "kind": "certification"},
    {"id": "mandated_reporter", "name": "Mandated reporter", "kind": "certification"},
    {"id": "other", "name": "Other", "kind": "certification"},
)


def _now() -> str:
    return datetime.now(_EASTERN).isoformat()


def _today() -> date:
    return datetime.now(_EASTERN).date()


def _file_mode() -> bool:
    return os.environ.get(ENV_MODE) == "file"


def _path():
    from pathlib import Path

    return Path(os.environ.get(ENV_PATH) or DEFAULT_PATH)


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


def _fernet() -> Optional[Fernet]:
    raw = (os.environ.get(ENV_KEY) or "").strip()
    if not raw:
        return None
    return Fernet(raw.encode())


def _encrypt(text: str) -> bytes:
    box = _fernet()
    if box is None:
        return encrypt_utf8(text)
    return box.encrypt(text.encode())


def _decrypt(blob: bytes) -> str:
    box = _fernet()
    if box is None:
        return decrypt_utf8(blob)
    return box.decrypt(blob).decode()


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def _clip(value: str) -> str:
    text = str(value or "").strip()
    if len(text) > 200:
        raise ValueError("That value is too long.")
    return text


def _date_or_blank(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Use a YYYY-MM-DD date.") from exc
    return text


def _business_days_since(start_iso: str, today: date) -> int:
    try:
        start = date.fromisoformat(str(start_iso)[:10])
    except ValueError:
        return 0
    days = 0
    cursor = start
    while cursor < today:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def _clock_ids() -> set[str]:
    return {str(item["id"]) for item in CREDENTIAL_CLOCKS}


def _format_when(iso: str) -> str:
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_EASTERN)
    else:
        parsed = parsed.astimezone(_EASTERN)
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}, {hour}:{parsed.strftime('%M %p')} ET"


def _history_line(item: dict[str, Any]) -> str:
    """Readable history. Field labels only — never a street, phone, email, or ID value."""
    field = str(item.get("field") or "")
    label = FIELD_LABELS.get(field) or field.replace("_", " ").strip().capitalize() or "Record"
    action = str(item.get("action") or "updated")
    if action == "cleared":
        verb = f"cleared {label}"
    elif action == "requested":
        verb = f"requested a change to {label}"
    else:
        verb = f"updated {label}"
    actor = str(item.get("actor") or "").strip().lower()
    when = _format_when(str(item.get("at") or ""))
    parts = [part for part in (when, actor, verb) if part]
    return " — ".join(parts)


def _public_history(items: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "at": str(item.get("at") or ""),
                "actor": str(item.get("actor") or ""),
                "action": str(item.get("action") or ""),
                "field": str(item.get("field") or ""),
                "line": _history_line(item),
            }
        )
    return out


class EmployeesStore:
    def __init__(self) -> None:
        self._memory: Optional[dict[str, Any]] = None

    def reset(self) -> None:
        self._memory = None
        if not _file_mode():
            self._memory = _empty()

    def _read(self) -> dict[str, Any]:
        if not _file_mode():
            if self._memory is None:
                self._memory = _empty()
            return self._memory
        path = _path()
        if not path.is_file():
            return _empty()
        return json.loads(_decrypt(path.read_bytes()))

    def _save(self, payload: dict[str, Any]) -> None:
        if not _file_mode():
            self._memory = payload
            return
        from app.credentialing.backup import backup_once

        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        backup_once(path)
        data = _encrypt(json.dumps(payload))
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def _practice(self, practice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        practices = payload.setdefault("practices", {})
        row = practices.get(practice_id)
        if not isinstance(row, dict):
            row = {}
            practices[practice_id] = row
        row.setdefault("rollout_enabled", False)
        row.setdefault("records", {})
        row.setdefault("audit", [])
        row.setdefault("changes", [])
        row.setdefault("pending", [])
        if "qualification_types" not in row:
            row["qualification_types"] = [dict(item) for item in QUALIFICATION_TYPES]
        return row

    def _load(self, practice_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._read()
        return payload, self._practice(practice_id, payload)

    def _audit(
        self,
        row: dict[str, Any],
        actor: str,
        action: str,
        record_id: str,
        field: str = "",
    ) -> None:
        events = row.setdefault("audit", [])
        if not isinstance(events, list):
            events = []
            row["audit"] = events
        events.append(
            {
                "at": _now(),
                "actor": (actor or "").strip().lower(),
                "action": action,
                "record_id": record_id,
                "field": field,
            }
        )

    def _change(
        self,
        row: dict[str, Any],
        *,
        actor: str,
        record_email: str,
        summary: str,
    ) -> None:
        changes = row.setdefault("changes", [])
        if not isinstance(changes, list):
            changes = []
            row["changes"] = changes
        changes.append(
            {
                "id": _new_id("chg-"),
                "at": _now(),
                "actor": (actor or "").strip().lower(),
                "record_email": (record_email or "").strip().lower(),
                "summary": summary,
                "reviewed": False,
            }
        )

    def rollout_enabled(self, practice_id: str) -> bool:
        _, row = self._load(practice_id)
        return bool(row.get("rollout_enabled"))

    def set_rollout(self, practice_id: str, enabled: bool, actor: str) -> bool:
        payload, row = self._load(practice_id)
        row["rollout_enabled"] = bool(enabled)
        self._audit(row, actor, "rollout", practice_id, "on" if enabled else "off")
        self._save(payload)
        return bool(row["rollout_enabled"])

    def qualification_types(self, practice_id: str) -> list[dict[str, str]]:
        _, row = self._load(practice_id)
        return [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "kind": str(item.get("kind") or ""),
            }
            for item in (row.get("qualification_types") or [])
            if isinstance(item, dict)
        ]

    def set_qualification_types(
        self, practice_id: str, items: list[dict[str, str]], actor: str
    ) -> list[dict[str, str]]:
        payload, row = self._load(practice_id)
        cleaned = []
        for item in items:
            name = _clip(str(item.get("name") or ""))
            if not name:
                continue
            cleaned.append(
                {
                    "id": _clip(str(item.get("id") or name.casefold().replace(" ", "_"))),
                    "name": name,
                    "kind": "license" if str(item.get("kind") or "") == "license" else "certification",
                }
            )
        if not cleaned:
            raise ValueError("Keep at least one qualification type.")
        row["qualification_types"] = cleaned
        self._audit(row, actor, "qualification_types", practice_id, "")
        self._save(payload)
        return self.qualification_types(practice_id)

    def _record(self, row: dict[str, Any], email: str) -> dict[str, Any]:
        key = (email or "").strip().lower()
        records = row.setdefault("records", {})
        current = records.get(key)
        if not isinstance(current, dict):
            current = {"email": key, "history": []}
            records[key] = current
        for field in CONTACT_FIELDS:
            current.setdefault(field, "")
        current.setdefault("subject_id", "")
        current.setdefault("specialties", [])
        current.setdefault("trainings", [])
        current.setdefault("certifications", [])
        current.setdefault("history", [])
        current["email"] = key
        return current

    def _history(self, record: dict[str, Any], actor: str, action: str, field: str) -> None:
        record.setdefault("history", []).append(
            {
                "at": _now(),
                "actor": (actor or "").strip().lower(),
                "action": action,
                "field": field,
            }
        )

    def _summary_for_contact(self, old: dict[str, str], new: dict[str, str]) -> str:
        changed = [field for field in CONTACT_FIELDS if (old.get(field) or "") != (new.get(field) or "")]
        if not changed:
            return ""
        sensitive = [field for field in changed if field in SENSITIVE_CONTACT]
        if sensitive and any(not (new.get(field) or "") and (old.get(field) or "") for field in sensitive):
            if all(not (new.get(field) or "") for field in changed):
                return "Cleared contact details"
            return "Updated contact details"
        if any(field in SENSITIVE_CONTACT for field in changed):
            return "Updated contact details"
        if changed == ["preferred_name"] and not (new.get("preferred_name") or ""):
            return "Cleared preferred name"
        if changed == ["preferred_name"]:
            return "Updated preferred name"
        return "Updated contact details"

    def save_contact(
        self, practice_id: str, email: str, fields: dict[str, str], actor: str
    ) -> dict[str, Any]:
        payload, row = self._load(practice_id)
        record = self._record(row, email)
        old = {field: str(record.get(field) or "") for field in CONTACT_FIELDS}
        new = {field: _clip(str(fields.get(field) or "")) for field in CONTACT_FIELDS}
        for field, value in new.items():
            record[field] = value
        summary = self._summary_for_contact(old, new)
        if summary:
            action = "cleared" if summary.startswith("Cleared") else "updated"
            for field in CONTACT_FIELDS:
                if old[field] != new[field]:
                    self._history(record, actor, "cleared" if not new[field] else action, field)
            self._change(row, actor=actor, record_email=email, summary=summary)
        self._save(payload)
        return self._own_record(practice_id, email, actor)

    def save_specialties(
        self, practice_id: str, email: str, names: list[str], actor: str
    ) -> dict[str, Any]:
        from app.credentialing.store import credentialing_store

        payload, row = self._load(practice_id)
        record = self._record(row, email)
        cleaned: list[str] = []
        for name in names:
            text = _clip(name)
            if text and text not in cleaned:
                cleaned.append(text)
        old = [str(item) for item in (record.get("specialties") or [])]
        record["specialties"] = cleaned
        if old != cleaned:
            self._history(record, actor, "cleared" if not cleaned else "updated", "specialties")
            shown = ", ".join(cleaned) if cleaned else "cleared"
            self._change(
                row,
                actor=actor,
                record_email=email,
                summary="Updated specialties: " + shown if cleaned else "Cleared specialties",
            )
        subject_id = str(record.get("subject_id") or "")
        self._save(payload)
        if subject_id:
            credentialing_store.set_specialties(practice_id, subject_id, cleaned, actor=actor)
        return self._own_record(practice_id, email, actor)

    def save_trainings(
        self, practice_id: str, email: str, items: list[dict[str, str]], actor: str
    ) -> dict[str, Any]:
        payload, row = self._load(practice_id)
        record = self._record(row, email)
        cleaned = []
        for item in items:
            name = _clip(str(item.get("name") or ""))
            if not name:
                continue
            cleaned.append({"name": name, "completed_on": _date_or_blank(str(item.get("completed_on") or ""))})
        old = record.get("trainings") or []
        record["trainings"] = cleaned
        if old != cleaned:
            self._history(record, actor, "cleared" if not cleaned else "updated", "trainings")
            names = ", ".join(item["name"] for item in cleaned)
            self._change(
                row,
                actor=actor,
                record_email=email,
                summary="Cleared trainings" if not cleaned else "Updated trainings: " + names,
            )
        self._save(payload)
        return self._own_record(practice_id, email, actor)

    def save_certifications(
        self, practice_id: str, email: str, items: list[dict[str, str]], actor: str
    ) -> dict[str, Any]:
        """Employee-side certifications. They do not change the credentialing record."""
        payload, row = self._load(practice_id)
        record = self._record(row, email)
        if not self._is_clinician(practice_id, str(record.get("subject_id") or "")):
            raise ValueError("Link a clinician credentialing profile first.")
        cleaned = []
        for item in items:
            name = _clip(str(item.get("name") or ""))
            if not name:
                continue
            cleaned.append(
                {
                    "name": name,
                    "issued_on": _date_or_blank(str(item.get("issued_on") or "")),
                    "expires_on": _date_or_blank(str(item.get("expires_on") or "")),
                }
            )
        old = record.get("certifications") or []
        record["certifications"] = cleaned
        if old != cleaned:
            self._history(record, actor, "cleared" if not cleaned else "updated", "certifications")
            names = ", ".join(item["name"] for item in cleaned)
            self._change(
                row,
                actor=actor,
                record_email=email,
                summary="Cleared certifications" if not cleaned else "Updated certifications: " + names,
            )
        self._save(payload)
        return self._own_record(practice_id, email, actor)

    def set_link(self, practice_id: str, email: str, subject_id: str, actor: str) -> dict[str, Any]:
        from app.credentialing.store import credentialing_store

        payload, row = self._load(practice_id)
        record = self._record(row, email)
        subject_id = str(subject_id or "").strip()
        if subject_id:
            known = {item["id"] for item in credentialing_store.list_subject_names(practice_id)}
            if subject_id not in known:
                raise ValueError("That credentialing profile was not found.")
        record["subject_id"] = subject_id
        self._history(record, actor, "cleared" if not subject_id else "updated", "credentialing_link")
        self._change(
            row,
            actor=actor,
            record_email=email,
            summary="Cleared credentialing link" if not subject_id else "Linked credentialing profile",
        )
        self._save(payload)
        return self._own_record(practice_id, email, actor)

    def request_change(
        self, practice_id: str, email: str, field: str, value: str, actor: str
    ) -> dict[str, Any]:
        field = str(field or "").strip()
        allowed = SECRET_APPROVAL | PLAIN_APPROVAL | _clock_ids()
        if field not in allowed:
            raise ValueError("That field has to be approved on the credentialing record.")
        payload, row = self._load(practice_id)
        record = self._record(row, email)
        subject_id = str(record.get("subject_id") or "")
        if not subject_id or not self._is_clinician(practice_id, subject_id):
            raise ValueError("Link a clinician credentialing profile first.")
        text = _date_or_blank(value) if field.endswith("_on") or field in _clock_ids() else _clip(value)
        pending = row.setdefault("pending", [])
        pending.append(
            {
                "id": _new_id("pend-"),
                "at": _now(),
                "actor": (actor or "").strip().lower(),
                "record_email": (email or "").strip().lower(),
                "subject_id": subject_id,
                "field": field,
                "value": text,
                "status": "pending",
                "note": "",
                "decided_by": "",
                "decided_at": "",
            }
        )
        self._history(record, actor, "requested", field)
        self._save(payload)
        return self._own_record(practice_id, email)

    def _is_clinician(self, practice_id: str, subject_id: str) -> bool:
        from app.credentialing.store import credentialing_store

        for item in credentialing_store.list_subject_names(practice_id):
            if item["id"] == subject_id:
                return item["kind"] == "clinician"
        return False

    def _suggest(self, practice_id: str, email: str, username: str) -> dict[str, str]:
        from app.credentialing.store import credentialing_store

        tokens = {
            (username or "").strip().casefold(),
            (email or "").split("@")[0].strip().casefold(),
        }
        tokens.discard("")
        hits = []
        for item in credentialing_store.list_subject_names(practice_id):
            if item["kind"] != "clinician":
                continue
            if item["name"].strip().casefold() in tokens:
                hits.append(item)
        unique = {item["id"]: item for item in hits}
        if len(unique) != 1:
            return {"id": "", "name": ""}
        found = next(iter(unique.values()))
        return {"id": found["id"], "name": found["name"]}

    def _contact_public(self, record: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
        if reveal:
            return {field: str(record.get(field) or "") for field in CONTACT_FIELDS}
        return {
            field: ("saved" if str(record.get(field) or "") else "empty")
            for field in CONTACT_FIELDS
        }

    def _blank(self, email: str) -> dict[str, Any]:
        blank: dict[str, Any] = {
            "email": (email or "").strip().lower(),
            "subject_id": "",
            "specialties": [],
            "trainings": [],
            "certifications": [],
            "history": [],
        }
        for field in CONTACT_FIELDS:
            blank[field] = ""
        return blank

    def _existing(self, row: dict[str, Any], email: str) -> dict[str, Any]:
        current = (row.get("records") or {}).get((email or "").strip().lower())
        if isinstance(current, dict):
            return current
        return self._blank(email)

    def _own_record(self, practice_id: str, email: str, username: str = "") -> dict[str, Any]:
        from app.credentialing.store import credentialing_store

        _, row = self._load(practice_id)
        record = self._existing(row, email)
        subject_id = str(record.get("subject_id") or "")
        clinician = bool(subject_id) and self._is_clinician(practice_id, subject_id)
        body: dict[str, Any] = {
            "email": record["email"],
            "rollout_enabled": bool(row.get("rollout_enabled")),
            "contact": self._contact_public(record, reveal=True),
            "specialties": list(record.get("specialties") or []),
            "trainings": list(record.get("trainings") or []),
            "certifications": list(record.get("certifications") or []) if clinician else [],
            "subject_id": subject_id,
            "is_clinician": clinician,
            "suggested_subject": self._suggest(practice_id, email, username or email.split("@")[0]),
            "candidates": [
                item
                for item in credentialing_store.list_subject_names(practice_id)
                if item["kind"] == "clinician"
            ],
            "history": _public_history(record.get("history") or []),
            "pending": self._pending_for(row, record["email"], reveal=False),
            "qualification_types": self.qualification_types(practice_id) if clinician else [],
        }
        if clinician:
            snapshot = credentialing_store.public_subject(practice_id, subject_id) or {}
            body["credentialing"] = {
                "id": snapshot.get("id") or "",
                "name": snapshot.get("name") or "",
                "license_type": snapshot.get("license_type") or "",
                "license_expires_on": snapshot.get("license_expires_on") or "",
                "caqh_attested_on": snapshot.get("caqh_attested_on") or "",
                "hire_date": snapshot.get("hire_date") or "",
                "device": snapshot.get("device") or "",
                "supervisor_id": snapshot.get("supervisor_id") or "",
                "npi_masked": snapshot.get("npi_masked") or "",
                "license_number_masked": snapshot.get("license_number_masked") or "",
                "caqh_id_masked": snapshot.get("caqh_id_masked") or "",
                "clocks": snapshot.get("clocks") or {},
                "specialties": snapshot.get("specialties") or [],
            }
        return body

    def own(self, practice_id: str, email: str, username: str) -> dict[str, Any]:
        return self._own_record(practice_id, email, username)

    def _pending_for(self, row: dict[str, Any], email: str, *, reveal: bool) -> list[dict[str, Any]]:
        found = []
        for item in row.get("pending") or []:
            if not isinstance(item, dict) or item.get("status") != "pending":
                continue
            if email and str(item.get("record_email") or "") != email:
                continue
            found.append(self._public_pending(item, reveal=reveal))
        return found

    def _public_pending(self, item: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
        field = str(item.get("field") or "")
        value = str(item.get("value") or "")
        overdue = _business_days_since(str(item.get("at") or ""), _today()) > 3
        shown = ""
        if field in SECRET_APPROVAL:
            shown = "set" if value else "clear"
            if reveal and value:
                shown = value
        elif reveal or field not in SECRET_APPROVAL:
            shown = value
        return {
            "id": str(item.get("id") or ""),
            "at": str(item.get("at") or ""),
            "actor": str(item.get("actor") or ""),
            "record_email": str(item.get("record_email") or ""),
            "field": field,
            "proposed": shown,
            "overdue": overdue,
            "status": str(item.get("status") or ""),
        }

    def _workspace_people(self, practice_id: str) -> list[dict[str, str]]:
        """Workspace roster for the directory. Falls back to the local roster. No logging."""
        found: list[dict[str, str]] = []
        seen: set[str] = set()
        try:
            from app.workspace_directory import list_directory_users

            for person in list_directory_users(practice_id):
                if getattr(person, "suspended", False):
                    continue
                email = str(getattr(person, "email", "") or "").strip().lower()
                if not email or email in seen:
                    continue
                seen.add(email)
                name = str(getattr(person, "display_name", "") or email).strip() or email
                found.append({"email": email, "name": name})
        except Exception:
            found = []
            seen = set()
        if found:
            return found
        from app.clinicians import clinician_store

        for clin in clinician_store.list_for_practice(practice_id):
            email = str(getattr(clin, "email", "") or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            name = str(getattr(clin, "display_name", "") or email).strip() or email
            found.append({"email": email, "name": name})
        return found

    def _tokens(self, value: str) -> tuple[str, ...]:
        parts: list[str] = []
        current: list[str] = []
        for char in str(value or "").casefold():
            if char.isalnum():
                current.append(char)
            elif current:
                parts.append("".join(current))
                current = []
        if current:
            parts.append("".join(current))
        return tuple(parts)

    def _match_workspace_person(
        self, subject_name: str, people: list[dict[str, str]]
    ) -> Optional[dict[str, str]]:
        """Unique name match or unique email-local match. Disagreeing matches stay unmatched."""
        name_key = str(subject_name or "").strip().casefold()
        subject_tokens = self._tokens(subject_name)
        if not name_key:
            return None
        name_hits = [person for person in people if person["name"].strip().casefold() == name_key]
        email_hits = [
            person
            for person in people
            if subject_tokens and self._tokens(person["email"].split("@", 1)[0]) == subject_tokens
        ]
        chosen: list[dict[str, str]] = []
        if len(name_hits) == 1:
            chosen.append(name_hits[0])
        if len(email_hits) == 1 and all(person["email"] != email_hits[0]["email"] for person in chosen):
            chosen.append(email_hits[0])
        if len(chosen) == 1:
            return chosen[0]
        return None

    def _linked_subjects(self, row: dict[str, Any]) -> dict[str, str]:
        found: dict[str, str] = {}
        for email, record in (row.get("records") or {}).items():
            if not isinstance(record, dict):
                continue
            subject_id = str(record.get("subject_id") or "")
            if subject_id:
                found[subject_id] = str(record.get("email") or email)
        return found

    def directory(self, practice_id: str, actor: str) -> list[dict[str, Any]]:
        from app.credentialing.store import credentialing_store

        payload, row = self._load(practice_id)
        self._audit(row, actor, "view_directory", "directory", "")
        self._save(payload)
        _, row = self._load(practice_id)
        workspace = self._workspace_people(practice_id)
        linked = self._linked_subjects(row)
        used_emails = {email for email in linked.values() if email}
        available = [person for person in workspace if person["email"] not in used_emails]
        rows: dict[str, dict[str, Any]] = {}
        for item in credentialing_store.list_subject_names(practice_id):
            if item["kind"] != "clinician":
                continue
            subject_id = item["id"]
            linked_email = linked.get(subject_id, "")
            if not linked_email:
                match = self._match_workspace_person(item["name"], available)
                if match:
                    used_emails.add(match["email"])
                    available = [person for person in available if person["email"] != match["email"]]
            rows[subject_id] = {
                "key": subject_id,
                "name": item["name"],
                "email": linked_email,
                "specialties": [],
                "clinician": True,
            }
        records = row.get("records") or {}
        for person in workspace:
            if person["email"] in used_emails:
                continue
            record = records.get(person["email"]) if isinstance(records.get(person["email"]), dict) else {}
            preferred = str(record.get("preferred_name") or "").strip()
            rows[person["email"]] = {
                "key": person["email"],
                "name": preferred or person["name"],
                "email": person["email"],
                "specialties": list(record.get("specialties") or []),
                "clinician": False,
            }
        for email, record in records.items():
            if not isinstance(record, dict) or email in rows or email in used_emails:
                continue
            subject_id = str(record.get("subject_id") or "")
            if subject_id and subject_id in rows:
                continue
            preferred = str(record.get("preferred_name") or "").strip()
            rows[email] = {
                "key": email,
                "name": preferred or email,
                "email": email,
                "specialties": list(record.get("specialties") or []),
                "clinician": self._is_clinician(practice_id, subject_id) if subject_id else False,
            }
        found = list(rows.values())
        found.sort(key=lambda item: item["name"].casefold())
        return found

    def view_record(self, practice_id: str, key: str, actor: str, *, reveal: bool) -> dict[str, Any]:
        from app.credentialing.store import credentialing_store

        payload, row = self._load(practice_id)
        key = (key or "").strip()
        snapshot = credentialing_store.public_subject(practice_id, key) if "@" not in key else None
        if isinstance(snapshot, dict) and snapshot.get("kind") == "clinician":
            self._audit(row, actor, "view_contact", key, "contact")
            self._save(payload)
            linked = self._linked_subjects(row)
            linked_email = linked.get(key, "")
            taken = {email for subject_id, email in linked.items() if subject_id != key and email}
            available = [
                person
                for person in self._workspace_people(practice_id)
                if person["email"] not in taken
            ]
            match = None if linked_email else self._match_workspace_person(str(snapshot.get("name") or ""), available)
            return {
                "email": "",
                "key": key,
                "name": snapshot.get("name") or "",
                "is_clinician": True,
                "contact": {field: "empty" for field in CONTACT_FIELDS},
                "specialties": list(snapshot.get("specialties") or []),
                "trainings": [],
                "credentialing": {
                    "name": snapshot.get("name") or "",
                    "license_type": snapshot.get("license_type") or "",
                    "npi_masked": snapshot.get("npi_masked") or "",
                    "license_number_masked": snapshot.get("license_number_masked") or "",
                    "caqh_id_masked": snapshot.get("caqh_id_masked") or "",
                },
                "workspace_link": {
                    "linked_email": linked_email,
                    "suggested_email": "" if match is None else match["email"],
                    "suggested_name": "" if match is None else match["name"],
                    "accounts": self._workspace_people(practice_id),
                },
            }
        email = key.lower() if "@" in key else ""
        known_accounts = {person["email"] for person in self._workspace_people(practice_id)}
        if not email or (email not in (row.get("records") or {}) and email not in known_accounts):
            raise KeyError(key)
        if email != (actor or "").strip().lower():
            self._audit(row, actor, "view_contact", email, "contact")
            self._save(payload)
        body = self._own_record(practice_id, email)
        for person in self._workspace_people(practice_id):
            if person["email"] == email:
                body["name"] = person["name"]
                break
        body["workspace_account"] = email
        if not reveal and email != (actor or "").strip().lower():
            _, row = self._load(practice_id)
            record = self._existing(row, email)
            body["contact"] = self._contact_public(record, reveal=False)
        return body

    def link_workspace_account(
        self, practice_id: str, subject_id: str, email: str, actor: str
    ) -> dict[str, str]:
        """Confirm or clear the Workspace account on a credentialing profile.

        Passing an email saves that link. An empty email unlinks. A suggested
        name is never written unless the caller sends it as the confirmed email.
        """
        from app.credentialing.store import credentialing_store

        known = {
            item["id"]: item
            for item in credentialing_store.list_subject_names(practice_id)
            if item["kind"] == "clinician"
        }
        if subject_id not in known:
            raise KeyError(subject_id)
        payload, row = self._load(practice_id)
        email = (email or "").strip().lower()
        accounts = {person["email"] for person in self._workspace_people(practice_id)}
        if email and email not in accounts and email not in (row.get("records") or {}):
            raise ValueError("That Workspace account is not on the roster.")
        for record in (row.get("records") or {}).values():
            if not isinstance(record, dict):
                continue
            if str(record.get("subject_id") or "") != subject_id:
                continue
            if email and str(record.get("email") or "") == email:
                continue
            record["subject_id"] = ""
            self._history(record, actor, "cleared", "workspace_link")
        if email:
            record = self._record(row, email)
            record["subject_id"] = subject_id
            self._history(record, actor, "updated", "workspace_link")
            self._change(row, actor=actor, record_email=email, summary="Linked Workspace account")
        self._save(payload)
        return {"subject_id": subject_id, "email": email}

    def reveal_contact(self, practice_id: str, email: str, actor: str) -> dict[str, str]:
        payload, row = self._load(practice_id)
        record = (row.get("records") or {}).get((email or "").strip().lower())
        if not isinstance(record, dict):
            raise KeyError(email)
        self._audit(row, actor, "reveal", record["email"], "contact")
        self._save(payload)
        return {field: str(record.get(field) or "") for field in CONTACT_FIELDS}

    def recent_changes(self, practice_id: str) -> list[dict[str, Any]]:
        _, row = self._load(practice_id)
        return [
            {
                "id": str(item.get("id") or ""),
                "at": str(item.get("at") or ""),
                "actor": str(item.get("actor") or ""),
                "record_email": str(item.get("record_email") or ""),
                "summary": str(item.get("summary") or ""),
            }
            for item in (row.get("changes") or [])
            if isinstance(item, dict) and not item.get("reviewed")
        ]

    def pending_queue(self, practice_id: str, *, reveal: bool) -> list[dict[str, Any]]:
        _, row = self._load(practice_id)
        return [
            self._public_pending(item, reveal=reveal)
            for item in (row.get("pending") or [])
            if isinstance(item, dict) and item.get("status") == "pending"
        ]

    def mark_reviewed(self, practice_id: str, change_id: str, actor: str) -> None:
        payload, row = self._load(practice_id)
        found = False
        for item in row.get("changes") or []:
            if isinstance(item, dict) and item.get("id") == change_id:
                item["reviewed"] = True
                item["reviewed_by"] = (actor or "").strip().lower()
                item["reviewed_at"] = _now()
                found = True
                break
        if not found:
            raise KeyError(change_id)
        self._audit(row, actor, "review", change_id, "")
        self._save(payload)

    def approve(self, practice_id: str, pending_id: str, actor: str) -> None:
        from app.credentialing.store import credentialing_store

        payload, row = self._load(practice_id)
        item = next(
            (
                row_item
                for row_item in (row.get("pending") or [])
                if isinstance(row_item, dict) and row_item.get("id") == pending_id
            ),
            None,
        )
        if item is None or item.get("status") != "pending":
            raise KeyError(pending_id)
        credentialing_store.apply_approved_field(
            practice_id,
            str(item.get("subject_id") or ""),
            str(item.get("field") or ""),
            str(item.get("value") or ""),
            actor=actor,
        )
        item["status"] = "approved"
        item["decided_by"] = (actor or "").strip().lower()
        item["decided_at"] = _now()
        if str(item.get("field") or "") in SECRET_APPROVAL:
            item["value"] = ""
        self._audit(row, actor, "approve", str(item.get("record_email") or ""), str(item.get("field") or ""))
        self._save(payload)

    def return_pending(self, practice_id: str, pending_id: str, actor: str, note: str) -> None:
        text = str(note or "").strip()
        if not text:
            raise ValueError("A note is required to return a change.")
        if len(text) > 500:
            raise ValueError("That note is too long.")
        payload, row = self._load(practice_id)
        item = next(
            (
                row_item
                for row_item in (row.get("pending") or [])
                if isinstance(row_item, dict) and row_item.get("id") == pending_id
            ),
            None,
        )
        if item is None or item.get("status") != "pending":
            raise KeyError(pending_id)
        item["status"] = "returned"
        item["note"] = text
        item["decided_by"] = (actor or "").strip().lower()
        item["decided_at"] = _now()
        if str(item.get("field") or "") in SECRET_APPROVAL:
            item["value"] = ""
        self._audit(row, actor, "return", str(item.get("record_email") or ""), str(item.get("field") or ""))
        self._save(payload)

    def reveal_pending(self, practice_id: str, pending_id: str, actor: str) -> dict[str, str]:
        payload, row = self._load(practice_id)
        item = next(
            (
                row_item
                for row_item in (row.get("pending") or [])
                if isinstance(row_item, dict) and row_item.get("id") == pending_id
            ),
            None,
        )
        if item is None:
            raise KeyError(pending_id)
        self._audit(row, actor, "reveal", str(item.get("record_email") or ""), str(item.get("field") or ""))
        self._save(payload)
        return {"field": str(item.get("field") or ""), "value": str(item.get("value") or "")}

    def audit_events(self, practice_id: str) -> list[dict[str, str]]:
        _, row = self._load(practice_id)
        return [
            {
                "at": str(item.get("at") or ""),
                "actor": str(item.get("actor") or ""),
                "action": str(item.get("action") or ""),
                "record_id": str(item.get("record_id") or ""),
                "field": str(item.get("field") or ""),
            }
            for item in (row.get("audit") or [])
            if isinstance(item, dict)
        ]

    def record_audit(
        self, practice_id: str, actor: str, action: str, record_id: str, field: str = ""
    ) -> None:
        payload, row = self._load(practice_id)
        self._audit(row, actor, action, record_id, field)
        self._save(payload)

    def export_csv(self, practice_id: str, actor: str, *, sensitive: bool) -> str:
        payload, row = self._load(practice_id)
        records = [item for item in (row.get("records") or {}).values() if isinstance(item, dict)]
        for record in records:
            self._audit(row, actor, "export", str(record.get("email") or ""), "csv")
        self._save(payload)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        header = ["email", "preferred_name", "specialties", "trainings"]
        if sensitive:
            header.extend(
                [
                    "street",
                    "city",
                    "state",
                    "zip",
                    "personal_phone",
                    "personal_email",
                    "work_phone",
                    "emergency_name",
                    "emergency_phone",
                ]
            )
        writer.writerow(header)
        for record in records:
            specialties = " | ".join(str(item) for item in (record.get("specialties") or []))
            trainings = " | ".join(
                str(item.get("name") or "")
                for item in (record.get("trainings") or [])
                if isinstance(item, dict)
            )
            row_out = [
                record.get("email") or "",
                record.get("preferred_name") or "",
                specialties,
                trainings,
            ]
            if sensitive:
                row_out.extend(str(record.get(field) or "") for field in (
                    "street",
                    "city",
                    "state",
                    "zip",
                    "personal_phone",
                    "personal_email",
                    "work_phone",
                    "emergency_name",
                    "emergency_phone",
                ))
            writer.writerow([_csv_cell(value) for value in row_out])
        if not sensitive:
            writer.writerow(["", "", "", "Addresses, personal phones, and emergency contacts withheld"])
        return buffer.getvalue()


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


employees_store = EmployeesStore()
