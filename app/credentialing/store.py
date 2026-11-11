"""Encrypted credentialing file. Atomic replace, same pattern as access roles."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.credentialing.csvio import ImportPlan
from app.crypto import decrypt_utf8, encrypt_utf8

ENV_PATH = "ATTUNE_CREDENTIALING_DATA_PATH"
ENV_MODE = "ATTUNE_CREDENTIALING_PERSISTENCE"
DEFAULT_PATH = ".attune_data/credentialing.enc"

PAYER_TYPES = ("commercial", "medicaid_mco", "chip", "medicare", "tricare", "eap")
STATUSES = ("unknown", "pending", "in_network", "not_eligible", "needs_review")
SECRET_FIELDS = (
    "npi",
    "tin",
    "license_number",
    "caqh_id",
    "taxonomy",
    "promise_id",
    "payer_provider_id",
)
MASTERS = {"lcsw", "lsw", "lpc", "lapc", "lmft"}


def mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    last = raw[-4:] if len(raw) > 4 else raw
    return "•••••• " + last


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "row"


def _path() -> Path:
    raw = (os.environ.get(ENV_PATH) or DEFAULT_PATH).strip()
    return Path(raw)


def _file_mode() -> bool:
    return os.environ.get(ENV_MODE, "file") != "memory"


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


def _practice_blob() -> dict[str, Any]:
    return {
        "subjects": {},
        "payers": {},
        "enrollments": {},
        "reveal_audit": [],
    }


def license_conflict(payer: dict[str, Any], subject: dict[str, Any]) -> str:
    """Reason a clinician should be marked not eligible, or empty when there is no suggestion."""
    if subject.get("kind") != "clinician":
        return ""
    license_type = str(subject.get("license_type") or "").strip()
    rules = [str(item).strip() for item in (payer.get("accepts_licenses") or []) if str(item).strip()]
    if not license_type or not rules:
        return ""
    folded = {item.casefold() for item in rules}
    if "all" in folded:
        return ""
    accepted = {item.casefold() for item in rules if item.casefold() not in {"all", "masters"}}
    if "masters" in folded:
        accepted.update(MASTERS)
    if license_type.casefold() in accepted:
        return ""
    return f"{payer.get('name') or 'This payer'} does not accept {license_type}."


def _secret_delta(old: str, new: str) -> Optional[dict[str, str]]:
    if (old or "") == (new or ""):
        return None
    if not (new or ""):
        return {"old": "set" if old else "", "new": ""}
    if not (old or ""):
        return {"old": "", "new": "set"}
    return {"old": "set", "new": "changed"}


class CredentialingStore:
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
        payload = json.loads(decrypt_utf8(path.read_bytes()))
        if not isinstance(payload, dict):
            return _empty()
        payload.setdefault("practices", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        if not _file_mode():
            self._memory = payload
            return
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = encrypt_utf8(json.dumps(payload))
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)

    def _backup(self) -> str:
        if not _file_mode():
            return ""
        path = _path()
        if not path.is_file():
            return ""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = path.with_name(path.name + f".bak-{stamp}")
        shutil.copy2(path, dest)
        return dest.name

    def _practice(self, practice_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        practices = payload.setdefault("practices", {})
        row = practices.get(practice_id)
        if not isinstance(row, dict):
            row = _practice_blob()
            practices[practice_id] = row
        row.setdefault("subjects", {})
        row.setdefault("payers", {})
        row.setdefault("enrollments", {})
        row.setdefault("reveal_audit", [])
        return row

    def _load_practice(self, practice_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._read()
        return payload, self._practice(practice_id, payload)

    def _unique_id(self, bucket: dict[str, Any], name: str) -> str:
        base = _slug(name)
        candidate = base
        suffix = 2
        while candidate in bucket:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _by_name(self, bucket: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
        for row in bucket.values():
            if isinstance(row, dict) and row.get("name") == name and not row.get("archived"):
                return row
        for row in bucket.values():
            if isinstance(row, dict) and row.get("name") == name:
                return row
        return None

    def public_grid(self, practice_id: str) -> dict[str, Any]:
        _, row = self._load_practice(practice_id)
        subjects = [
            _public_subject(item)
            for item in row["subjects"].values()
            if isinstance(item, dict)
        ]
        subjects.sort(key=lambda item: (0 if item["kind"] == "practice" else 1, item["name"].casefold()))
        payers = [_public_payer(item) for item in row["payers"].values() if isinstance(item, dict)]
        payers.sort(key=lambda item: item["name"].casefold())
        cells = []
        for payer in row["payers"].values():
            if not isinstance(payer, dict) or payer.get("archived"):
                continue
            for subject in row["subjects"].values():
                if not isinstance(subject, dict) or subject.get("archived"):
                    continue
                cells.append(_public_cell(row, subject, payer))
        return {"subjects": subjects, "payers": payers, "cells": cells}

    def reveal(
        self,
        practice_id: str,
        *,
        actor: str,
        subject_id: str,
        field: str,
        payer_id: str = "",
        action: str,
    ) -> str:
        if field not in SECRET_FIELDS:
            raise KeyError(field)
        payload, row = self._load_practice(practice_id)
        if field == "payer_provider_id":
            key = f"{subject_id}|{payer_id}"
            enrollment = row["enrollments"].get(key) or {}
            value = str(enrollment.get("payer_provider_id") or "")
        else:
            subject = row["subjects"].get(subject_id)
            if not isinstance(subject, dict):
                raise KeyError(subject_id)
            value = str(subject.get(field) or "")
        audit = row.setdefault("reveal_audit", [])
        audit.append(
            {
                "at": _now(),
                "actor": (actor or "").strip().lower(),
                "subject_id": subject_id,
                "payer_id": payer_id,
                "field": field,
                "action": action,
            }
        )
        if len(audit) > 500:
            del audit[:-500]
        self._save(payload)
        return value

    def audit_for(self, practice_id: str, subject_id: str) -> list[dict[str, str]]:
        _, row = self._load_practice(practice_id)
        return [
            {
                "at": str(item.get("at") or ""),
                "actor": str(item.get("actor") or ""),
                "subject_id": str(item.get("subject_id") or ""),
                "payer_id": str(item.get("payer_id") or ""),
                "field": str(item.get("field") or ""),
                "action": str(item.get("action") or ""),
            }
            for item in row.get("reveal_audit") or []
            if isinstance(item, dict) and (not subject_id or item.get("subject_id") == subject_id)
        ]

    def save_subject(self, practice_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Name is required.")
        kind = str(body.get("kind") or "clinician").strip().lower()
        if kind not in {"practice", "clinician"}:
            raise ValueError("Kind must be practice or clinician.")
        subject_id = str(body.get("id") or "").strip()
        current = row["subjects"].get(subject_id) if subject_id else None
        if subject_id and not isinstance(current, dict):
            raise KeyError(subject_id)
        if current is None:
            subject_id = self._unique_id(row["subjects"], name)
            current = {"id": subject_id, "history": []}
            row["subjects"][subject_id] = current
        changes: dict[str, Any] = {}
        plain = {
            "name": name,
            "kind": kind,
            "license_type": str(body.get("license_type") or "").strip(),
            "hire_date": _date_or_blank(str(body.get("hire_date") or "")),
            "device": str(body.get("device") or "").strip(),
            "license_state": str(body.get("license_state") or "").strip(),
            "license_expires_on": _date_or_blank(str(body.get("license_expires_on") or "")),
            "caqh_attested_on": _date_or_blank(str(body.get("caqh_attested_on") or "")),
            "supervisor_id": str(body.get("supervisor_id") or "").strip(),
            "legal_name": str(body.get("legal_name") or "").strip(),
            "service_address": str(body.get("service_address") or "").strip(),
            "active": bool(body.get("active", True)),
        }
        for key, value in plain.items():
            if current.get(key) != value:
                changes[key] = {"old": current.get(key) or "", "new": value}
            current[key] = value
        for key in ("npi", "tin", "license_number", "caqh_id", "taxonomy", "promise_id"):
            if key not in body or body.get(key) is None:
                continue
            new_value = str(body.get(key) or "").strip()
            delta = _secret_delta(str(current.get(key) or ""), new_value)
            if delta:
                changes[key] = delta
            current[key] = new_value
        current["id"] = subject_id
        current["updated_by"] = actor
        current["updated_at"] = _now()
        if changes:
            current.setdefault("history", []).append(
                {"at": current["updated_at"], "by": actor, "source": "manual", "changes": changes}
            )
        self._save(payload)
        return _public_subject(current)

    def save_payer(self, practice_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("Payer name is required.")
        kind = str(body.get("type") or "").strip().lower()
        if kind not in PAYER_TYPES:
            raise ValueError("Payer type is not one of the allowed values.")
        payer_id = str(body.get("id") or "").strip()
        current = row["payers"].get(payer_id) if payer_id else None
        if payer_id and not isinstance(current, dict):
            raise KeyError(payer_id)
        if current is None:
            payer_id = self._unique_id(row["payers"], name)
            current = {"id": payer_id}
            row["payers"][payer_id] = current
        family = body.get("family_members") or []
        licenses = body.get("accepts_licenses") or []
        counties = body.get("counties") or []
        current.update(
            {
                "id": payer_id,
                "name": name,
                "type": kind,
                "family_members": [str(item).strip() for item in family if str(item).strip()],
                "accepts_licenses": [str(item).strip() for item in licenses if str(item).strip()],
                "notes": str(body.get("notes") or "").strip(),
                "parent_payer": str(body.get("parent_payer") or "").strip(),
                "counties": [str(item).strip() for item in counties if str(item).strip()],
                "archived": bool(current.get("archived")),
                "updated_by": actor,
                "updated_at": _now(),
            }
        )
        self._save(payload)
        return _public_payer(current)

    def archive_subject(self, practice_id: str, subject_id: str, *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        current = row["subjects"].get(subject_id)
        if not isinstance(current, dict):
            raise KeyError(subject_id)
        current["archived"] = True
        current["updated_by"] = actor
        current["updated_at"] = _now()
        self._save(payload)
        return _public_subject(current)

    def restore_subject(self, practice_id: str, subject_id: str, *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        current = row["subjects"].get(subject_id)
        if not isinstance(current, dict):
            raise KeyError(subject_id)
        current["archived"] = False
        current["updated_by"] = actor
        current["updated_at"] = _now()
        self._save(payload)
        return _public_subject(current)

    def archive_payer(self, practice_id: str, payer_id: str, *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        current = row["payers"].get(payer_id)
        if not isinstance(current, dict):
            raise KeyError(payer_id)
        current["archived"] = True
        current["updated_by"] = actor
        current["updated_at"] = _now()
        self._save(payload)
        return _public_payer(current)

    def restore_payer(self, practice_id: str, payer_id: str, *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        current = row["payers"].get(payer_id)
        if not isinstance(current, dict):
            raise KeyError(payer_id)
        current["archived"] = False
        current["updated_by"] = actor
        current["updated_at"] = _now()
        self._save(payload)
        return _public_payer(current)

    def record_people_export(self, practice_id: str, *, actor: str) -> None:
        """Log a sensitive people.csv export. The values themselves are not stored."""
        payload, row = self._load_practice(practice_id)
        audit = row.setdefault("reveal_audit", [])
        now = _now()
        actor_email = (actor or "").strip().lower()
        for subject in row["subjects"].values():
            if not isinstance(subject, dict) or subject.get("archived"):
                continue
            audit.append(
                {
                    "at": now,
                    "actor": actor_email,
                    "subject_id": subject.get("id") or "",
                    "payer_id": "",
                    "field": "people.csv",
                    "action": "export",
                }
            )
        if len(audit) > 500:
            del audit[:-500]
        self._save(payload)

    def save_enrollment(self, practice_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        payload, row = self._load_practice(practice_id)
        subject = row["subjects"].get(str(body.get("subject_id") or ""))
        payer = row["payers"].get(str(body.get("payer_id") or ""))
        if not isinstance(subject, dict) or not isinstance(payer, dict):
            raise KeyError("subject or payer")
        status = str(body.get("status") or "unknown").strip()
        if status not in STATUSES:
            raise ValueError("Unknown enrollment status.")
        note = str(body.get("note") or "")
        conflict = license_conflict(payer, subject)
        if conflict and status not in {"not_eligible", "unknown"} and not note.strip():
            raise ValueError("Overriding not eligible requires a note.")
        key = f"{subject['id']}|{payer['id']}"
        current = row["enrollments"].get(key)
        if not isinstance(current, dict):
            current = {
                "subject_id": subject["id"],
                "payer_id": payer["id"],
                "status": "unknown",
                "submitted_on": "",
                "effective_on": "",
                "payer_provider_id": "",
                "next_follow_up": "",
                "note": "",
                "history": [],
            }
            row["enrollments"][key] = current
        updated = {
            "status": status,
            "submitted_on": _date_or_blank(str(body.get("submitted_on") or "")),
            "effective_on": _date_or_blank(str(body.get("effective_on") or "")),
            "next_follow_up": _date_or_blank(str(body.get("next_follow_up") or "")),
            "note": note.strip(),
        }
        changes: dict[str, Any] = {}
        for field, value in updated.items():
            if current.get(field) != value:
                changes[field] = {"old": current.get(field) or "", "new": value}
            current[field] = value
        if "payer_provider_id" in body and body.get("payer_provider_id") is not None:
            new_secret = str(body.get("payer_provider_id") or "").strip()
            delta = _secret_delta(str(current.get("payer_provider_id") or ""), new_secret)
            if delta:
                changes["payer_provider_id"] = delta
            current["payer_provider_id"] = new_secret
        current["updated_by"] = actor
        current["updated_at"] = _now()
        if changes:
            current.setdefault("history", []).append(
                {"at": current["updated_at"], "by": actor, "source": "manual", "changes": changes}
            )
        self._save(payload)
        return _public_cell(row, subject, payer)

    def commit_import(self, practice_id: str, plan: ImportPlan, *, actor: str) -> str:
        if plan.errors:
            raise ValueError("Fix the import errors before confirming.")
        backup = self._backup()
        payload, row = self._load_practice(practice_id)
        when = _now()
        for item in plan.payers:
            self._upsert_payer(row, item, actor=actor, when=when)
        for item in plan.subjects:
            self._upsert_subject(row, item, actor=actor, when=when)
        for item in plan.cells:
            self._upsert_cell(row, item, actor=actor, when=when)
        self._save(payload)
        return backup

    def _upsert_payer(self, row: dict[str, Any], item: dict[str, Any], *, actor: str, when: str) -> None:
        current = self._by_name(row["payers"], item["name"])
        if current is None:
            payer_id = self._unique_id(row["payers"], item["name"])
            current = {"id": payer_id, "archived": False}
            row["payers"][payer_id] = current
        current.update(
            {
                "name": item["name"],
                "family_members": list(item["family_members"]),
                "type": item["type"],
                "accepts_licenses": list(item["accepts_licenses"]),
                "notes": item["notes"],
                "parent_payer": str(current.get("parent_payer") or ""),
                "counties": list(current.get("counties") or []),
                "archived": False,
                "updated_by": actor,
                "updated_at": when,
            }
        )
        current.setdefault("id", self._unique_id(row["payers"], item["name"]))

    def _upsert_subject(self, row: dict[str, Any], item: dict[str, Any], *, actor: str, when: str) -> None:
        current = self._by_name(row["subjects"], item["name"])
        changes: dict[str, Any] = {}
        if current is None:
            subject_id = self._unique_id(row["subjects"], item["name"])
            current = {"id": subject_id, "history": [], "active": True, "archived": False}
            row["subjects"][subject_id] = current
        fields = {
            "name": item["name"],
            "kind": item["kind"],
            "license_type": item["license_type"],
            "hire_date": item["hire_date"],
            "device": item["device"],
            "npi": item["npi"],
            "tin": item["tin"],
            "license_number": item["license_number"],
            "caqh_id": item["caqh_id"],
        }
        for key, value in fields.items():
            old = str(current.get(key) or "")
            new = str(value or "")
            if old == new:
                continue
            if key in SECRET_FIELDS:
                delta = _secret_delta(old, new)
                if delta:
                    changes[key] = delta
            else:
                changes[key] = {"old": old, "new": new}
            current[key] = new
        current["updated_by"] = actor
        current["updated_at"] = when
        current.setdefault("active", True)
        if changes:
            current.setdefault("history", []).append(
                {"at": when, "by": actor, "source": "import", "changes": changes}
            )

    def _upsert_cell(self, row: dict[str, Any], item: dict[str, Any], *, actor: str, when: str) -> None:
        payer = self._by_name(row["payers"], item["payer"])
        subject = self._by_name(row["subjects"], item["subject"])
        if payer is None or subject is None:
            raise ValueError("Import row does not match a payer and subject.")
        key = f"{subject['id']}|{payer['id']}"
        current = row["enrollments"].get(key)
        if not isinstance(current, dict):
            current = {
                "subject_id": subject["id"],
                "payer_id": payer["id"],
                "status": "unknown",
                "submitted_on": "",
                "effective_on": "",
                "payer_provider_id": "",
                "next_follow_up": "",
                "note": "",
                "history": [],
            }
            row["enrollments"][key] = current
        changes: dict[str, Any] = {}
        for field, value in (
            ("status", item["status"]),
            ("submitted_on", item["submitted_on"]),
        ):
            if current.get(field) != value:
                changes[field] = {"old": current.get(field) or "", "new": value}
            current[field] = value
        current["updated_by"] = actor
        current["updated_at"] = when
        if changes:
            current.setdefault("history", []).append(
                {"at": when, "by": actor, "source": "import", "changes": changes}
            )

    def export_texts(self, practice_id: str, *, include_secrets: bool) -> dict[str, str]:
        _, row = self._load_practice(practice_id)
        payers = [item for item in row["payers"].values() if isinstance(item, dict) and not item.get("archived")]
        subjects = [item for item in row["subjects"].values() if isinstance(item, dict) and not item.get("archived")]
        payers.sort(key=lambda item: str(item.get("name") or "").casefold())
        subjects.sort(key=lambda item: (0 if item.get("kind") == "practice" else 1, str(item.get("name") or "").casefold()))
        payer_lines = ["payer,family_members,type,accepts_licenses,notes"]
        for payer in payers:
            payer_lines.append(
                ",".join(
                    [
                        _csv(payer.get("name")),
                        _csv("; ".join(payer.get("family_members") or [])),
                        _csv(payer.get("type")),
                        _csv("; ".join(payer.get("accepts_licenses") or [])),
                        _csv(payer.get("notes")),
                    ]
                )
            )
        people_header = "subject,kind,license_type,npi,tin,license_number,caqh_id,hire_date,device"
        people_lines = []
        if not include_secrets:
            people_lines.append(
                "# npi, tin, license_number, and caqh_id were withheld"
            )
        people_lines.append(people_header)
        for subject in subjects:
            def secret(field: str, person: dict[str, Any] = subject) -> str:
                raw = str(person.get(field) or "")
                return raw if include_secrets else ""

            people_lines.append(
                ",".join(
                    [
                        _csv(subject.get("name")),
                        _csv(subject.get("kind")),
                        _csv(subject.get("license_type")),
                        _csv(secret("npi")),
                        _csv(secret("tin")),
                        _csv(secret("license_number")),
                        _csv(secret("caqh_id")),
                        _csv(subject.get("hire_date")),
                        _csv(subject.get("device")),
                    ]
                )
            )
        grid = ["payer," + ",".join(_csv(item.get("name")) for item in subjects)]
        for payer in payers:
            values = [_csv(payer.get("name"))]
            for subject in subjects:
                key = f"{subject['id']}|{payer['id']}"
                enrollment = row["enrollments"].get(key) or {}
                values.append(_csv(_grid_cell(enrollment)))
            grid.append(",".join(values))
        return {
            "payers": "\n".join(payer_lines) + "\n",
            "people": "\n".join(people_lines) + "\n",
            "grid": "\n".join(grid) + "\n",
        }


def _date_or_blank(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    datetime.strptime(raw, "%Y-%m-%d")
    return raw


def _csv(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def _grid_cell(enrollment: dict[str, Any]) -> str:
    status = str(enrollment.get("status") or "unknown")
    if status == "in_network":
        return "Y"
    if status == "not_eligible":
        return "X"
    if status == "needs_review":
        return "?"
    if status == "pending":
        return str(enrollment.get("submitted_on") or "")
    return ""


def _public_subject(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or "",
        "name": row.get("name") or "",
        "kind": row.get("kind") or "",
        "license_type": row.get("license_type") or "",
        "hire_date": row.get("hire_date") or "",
        "device": row.get("device") or "",
        "license_state": row.get("license_state") or "",
        "license_expires_on": row.get("license_expires_on") or "",
        "caqh_attested_on": row.get("caqh_attested_on") or "",
        "supervisor_id": row.get("supervisor_id") or "",
        "legal_name": row.get("legal_name") or "",
        "service_address": row.get("service_address") or "",
        "active": bool(row.get("active", True)),
        "archived": bool(row.get("archived")),
        "npi_masked": mask_secret(str(row.get("npi") or "")),
        "tin_masked": mask_secret(str(row.get("tin") or "")),
        "license_number_masked": mask_secret(str(row.get("license_number") or "")),
        "caqh_id_masked": mask_secret(str(row.get("caqh_id") or "")),
        "taxonomy_masked": mask_secret(str(row.get("taxonomy") or "")),
        "promise_id_masked": mask_secret(str(row.get("promise_id") or "")),
        "history": _public_history(row.get("history") or []),
    }


def _public_payer(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id") or "",
        "name": row.get("name") or "",
        "type": row.get("type") or "",
        "family_members": list(row.get("family_members") or []),
        "accepts_licenses": list(row.get("accepts_licenses") or []),
        "notes": row.get("notes") or "",
        "parent_payer": row.get("parent_payer") or "",
        "counties": list(row.get("counties") or []),
        "archived": bool(row.get("archived")),
    }


def _public_history(events: list[Any]) -> list[dict[str, Any]]:
    out = []
    for event in events:
        if not isinstance(event, dict):
            continue
        out.append(
            {
                "at": event.get("at") or "",
                "by": event.get("by") or "",
                "source": event.get("source") or "",
                "changes": event.get("changes") or {},
            }
        )
    return out


def _public_cell(row: dict[str, Any], subject: dict[str, Any], payer: dict[str, Any]) -> dict[str, Any]:
    key = f"{subject['id']}|{payer['id']}"
    enrollment = row["enrollments"].get(key) or {}
    reason = license_conflict(payer, subject)
    status = str(enrollment.get("status") or "unknown")
    blank = status in {"", "unknown"}
    confirmed = status == "not_eligible"
    if reason and blank:
        display = "suggested"
    elif reason and not confirmed:
        display = "warning"
    else:
        display = "plain"
    return {
        "subject_id": subject["id"],
        "payer_id": payer["id"],
        "status": status,
        "submitted_on": enrollment.get("submitted_on") or "",
        "effective_on": enrollment.get("effective_on") or "",
        "next_follow_up": enrollment.get("next_follow_up") or "",
        "note": enrollment.get("note") or "",
        "payer_provider_id_masked": mask_secret(str(enrollment.get("payer_provider_id") or "")),
        "updated_by": enrollment.get("updated_by") or "",
        "updated_at": enrollment.get("updated_at") or "",
        "suggestion_reason": reason if blank else "",
        "license_warning": reason if reason and not blank and not confirmed else "",
        "display": display,
        "history": _public_history(enrollment.get("history") or []),
    }


credentialing_store = CredentialingStore()
