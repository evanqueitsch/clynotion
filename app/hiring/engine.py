"""Hiring workflow. Encrypted onboarding.enc, created empty on first use.

History records actions and ids only. Field values, letter text, codes, and
tokens are not written to history or logs.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.crypto import decrypt_utf8, encrypt_utf8
from app.hiring.checklist import (
    BLOCKED_DONE,
    I9_KEYS,
    PAYROLL_KEYS,
    STATUS_ONLY,
    build_items,
    clearance_state,
    packet_due,
    clinician_title,
    closed_set,
    add_business_days,
    parse_iso,
    parse_mdy,
    previous_business_day,
    to_iso,
)
from app.hiring.fill import (
    OfferFillError,
    _normalize_date,
    asset_hashes,
    load_fields_spec,
    render_docx,
    supervisor_name_default,
)
from app.hiring.forms_catalog import FORMS, form_path
from app.hiring.mailer import code_body, deliver, offer_body, public_queue_row, reminder_body

logger = logging.getLogger("app.hiring.engine")
from app.hiring.pdf import PdfRenderError, append_certificate, render_offer_pdf
from app.hiring.storage import StorageKey, file_store

ENV_MODE = "ATTUNE_ONBOARDING_PERSISTENCE"
ENV_PATH = "ATTUNE_ONBOARDING_DATA_PATH"
DEFAULT_PATH = ".attune_data/onboarding.enc"
EASTERN = ZoneInfo("America/New_York")
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TERMINAL = {"accepted", "declined", "withdrawn", "expired"}
OPEN = {"draft", "sent", "viewed"}
DECLINE_REASONS = {"", "accepted_another", "pay", "schedule", "location", "other"}
MAX_UPLOAD = 15 * 1024 * 1024
_lock = threading.RLock()
_DRIVE_OFFER_KEYS = (
    "drive_folder_id",
    "drive_folder_url",
    "drive_folder_name",
    "drive_status",
    "drive_resume_file_id",
    "drive_resume_name",
    "drive_pdf_file_id",
    "drive_pdf_name",
    "drive_signed_file_id",
    "resume_in_onboarding",
    "pdf_in_onboarding",
)
_scheduler_started = False


def _parse_form_date(value: str, field: str) -> date:
    normalized = _normalize_date(value)
    if not normalized:
        label = "Offer date" if field == "offer_date" else "Response deadline"
        raise HiringError(400, f"{label} must be a real date.", field)
    return datetime.strptime(normalized, "%m/%d/%Y").date()


class HiringError(Exception):
    def __init__(self, status: int, detail: str, field: str = "") -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.field = field


def hire_cell_status(payer: dict[str, Any], credentials: str) -> str:
    """Eligible payers stay unknown (not started). A restricted payer is not eligible without a matching credential.

    Blank credentials do not match a payer that lists licenses, including one that says masters.
    A payer with no license list, or one that accepts all, stays unknown.
    """
    rules = [str(item).strip() for item in (payer.get("accepts_licenses") or []) if str(item).strip()]
    folded = {item.casefold() for item in rules}
    if not rules or "all" in folded:
        return "unknown"
    from app.credentialing.store import MASTERS

    tokens = {part.casefold() for part in re.split(r"[^A-Za-z]+", credentials or "") if part}
    if not tokens:
        return "not_eligible"
    accepted = {item for item in folded if item not in {"all", "masters"}}
    if "masters" in folded:
        accepted.update(MASTERS)
    if tokens & accepted:
        return "unknown"
    return "not_eligible"


def _drive_ok(offer: dict[str, Any], display_name: str) -> bool:
    from app.hiring.personnel_drive import offer_letter_filename

    if offer.get("drive_status") != "connected" or not offer.get("drive_folder_id"):
        return False
    file_id = str(offer.get("drive_pdf_file_id") or "")
    if not file_id:
        return True
    return str(offer.get("drive_pdf_name") or "") == offer_letter_filename(display_name)


def drive_prompt(offer: dict[str, Any], display_name: str) -> tuple[str, bool]:
    """Status line and whether Retry should show. Working is progress only."""
    status = str(offer.get("drive_status") or "")
    if not status:
        status = "connected" if offer.get("drive_folder_id") else "not_connected"
    if status == "working":
        return "Creating Drive folders…", False
    if status == "trashed":
        return "Folder trashed", False
    if _drive_ok(offer, display_name):
        return "", False
    if status == "not_connected":
        return "Drive not connected", True
    return "Drive needs another try", True


def _drive_async() -> bool:
    flag = (os.environ.get("ATTUNE_HIRING_DRIVE_ASYNC") or "").strip().lower()
    if flag in {"0", "false", "no"}:
        return False
    if flag in {"1", "true", "yes"}:
        return True
    from app.config import is_real_mode

    return is_real_mode()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


def _today() -> date:
    return datetime.now(EASTERN).date()


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:12]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _path() -> Path:
    return Path((os.environ.get(ENV_PATH) or DEFAULT_PATH).strip())


def _file_mode() -> bool:
    return os.environ.get(ENV_MODE) == "file"


def _empty() -> dict[str, Any]:
    return {"version": 1, "practices": {}}


def _default_settings() -> dict[str, Any]:
    return {
        "offer_response_days": 30,
        "admin_reminder_days_before": 7,
        "candidate_emails_enabled": False,
        "candidate_reminder_enabled": False,
        "candidate_reminder_days_before": 7,
        "packet_link_days": 14,
        "qa_email_allowlist": [],
        "qa_portal_bypass": False,
        "offer_supervisor_name_default": supervisor_name_default(),
        "offer_supervisor_title_default": "Director",
        "business_closed_days": [],
        "i9_copy_documents": False,
        "vault_min_days_personnel": 2555,
        "vault_min_days_i9": 1095,
        "hiring_rollout": False,
        "supervision_agreement_approved": False,
    }


class HiringEngine:
    def __init__(self) -> None:
        self._memory: Optional[dict[str, Any]] = None
        self._hits: dict[str, list[float]] = {}
        self._otp_hits: dict[str, list[float]] = {}

    def reset(self) -> None:
        self._memory = None
        self._hits.clear()
        self._otp_hits.clear()
        if not _file_mode():
            self._memory = _empty()
        from app.hiring.storage import reset_file_store

        reset_file_store()

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

    def _practice(self, payload: dict[str, Any], practice_id: str) -> dict[str, Any]:
        practices = payload.setdefault("practices", {})
        row = practices.get(practice_id)
        if not isinstance(row, dict):
            row = {}
            practices[practice_id] = row
        row.setdefault("settings", _default_settings())
        for key, value in _default_settings().items():
            row["settings"].setdefault(key, value)
        for bucket in (
            "candidates",
            "offers",
            "offer_versions",
            "acceptances",
            "portal_tokens",
            "otp_challenges",
            "portal_sessions",
            "hires",
            "checklist_items",
            "document_refs",
            "forms_library",
            "resumes",
        ):
            row.setdefault(bucket, {})
        row.setdefault("history", [])
        row.setdefault("mail_queue", [])
        self._ensure_forms(row)
        return row

    def _ensure_forms(self, row: dict[str, Any]) -> None:
        library = row.setdefault("forms_library", {})
        today = to_iso(_today())
        for spec in FORMS:
            if any(item.get("form_key") == spec["form_key"] and not item.get("retired_at") for item in library.values()):
                continue
            library[_new_id("form-")] = {
                "id": "",
                "form_key": spec["form_key"],
                "title": spec["title"],
                "publisher": spec["publisher"],
                "edition": spec["edition"],
                "expires_on": spec.get("expires_on") or "",
                "source_url": spec.get("source_url") or "",
                "filename": spec.get("filename") or "",
                "applies_to": spec.get("applies_to") or "",
                "january_check": bool(spec.get("january_check")),
                "last_checked_on": today,
                "retired_at": "",
            }
        for item in library.values():
            if not item.get("id"):
                item["id"] = next(key for key, value in library.items() if value is item)

    def _history(self, row: dict[str, Any], entity: str, entity_id: str, action: str, actor: str, summary: str, ip: str = "") -> None:
        row["history"].append(
            {
                "id": _new_id("hist-"),
                "entity": entity,
                "entity_id": entity_id,
                "action": action,
                "actor": actor,
                "at": _iso(),
                "ip": ip,
                "summary": summary,
            }
        )

    def _expire_row(self, row: dict[str, Any]) -> int:
        count = 0
        today = _today()
        for offer in row["offers"].values():
            if offer.get("status") not in {"sent", "viewed"}:
                continue
            deadline = offer.get("response_deadline") or ""
            if not deadline:
                continue
            if parse_iso(deadline) < today:
                offer["status"] = "expired"
                offer["closed_at"] = _iso()
                self._void_tokens(row, "offer", offer["id"], "expired")
                self._history(row, "offer", offer["id"], "expired", "scheduler", "Offer expired")
                count += 1
        return count

    def _void_tokens(self, row: dict[str, Any], subject: str, subject_id: str, reason: str) -> None:
        for token in row["portal_tokens"].values():
            if token.get("subject") == subject and token.get("subject_id") == subject_id and not token.get("revoked_at"):
                token["revoked_at"] = _iso()
                token["revoke_reason"] = reason

    def rollout_enabled(self, practice_id: str) -> bool:
        with _lock:
            row = self._practice(self._read(), practice_id)
            return bool(row["settings"].get("hiring_rollout"))

    def tick(self) -> dict[str, int]:
        with _lock:
            payload = self._read()
            expired = 0
            reminded = 0
            for practice_id, row in (payload.get("practices") or {}).items():
                if not isinstance(row, dict) or "offers" not in row:
                    continue
                expired += self._expire_row(row)
                reminded += self._remind(row, str(practice_id))
            self._save(payload)
            return {"expired": expired, "reminded": reminded}

    def _email_allowed(self, row: dict[str, Any], address: str) -> bool:
        settings = row["settings"]
        if settings.get("candidate_emails_enabled"):
            return True
        allow = {str(item).strip().lower() for item in settings.get("qa_email_allowlist") or []}
        return address.strip().lower() in allow

    def _remind(self, row: dict[str, Any], practice_id: str) -> int:
        settings = row["settings"]
        if not (settings.get("candidate_emails_enabled") and settings.get("candidate_reminder_enabled")):
            return 0
        sent = 0
        today = _today()
        window = int(settings.get("candidate_reminder_days_before") or 7)
        base = (os.environ.get("ATTUNE_PUBLIC_BASE_URL") or "https://clynotion.com").rstrip("/")
        for offer in row["offers"].values():
            if offer.get("status") not in {"sent", "viewed"} or offer.get("reminder_sent"):
                continue
            if offer.get("sent_channel") != "portal":
                continue
            deadline = parse_iso(offer["response_deadline"])
            if (deadline - today).days != window:
                continue
            candidate = row["candidates"].get(offer["candidate_id"]) or {}
            address = str(candidate.get("personal_email") or "")
            if not self._email_allowed(row, address):
                continue
            # The raw token is only a hash at rest, so the one reminder carries a
            # fresh link and the previous link stops working.
            raw, _token = self._mint_token(row, "offer", offer["id"], _end_of_deadline(offer["response_deadline"]))
            status = deliver(
                practice_id,
                address,
                "Your offer deadline",
                reminder_body(f"{base}/join/{raw}", _mdy(deadline)),
            )
            row["mail_queue"].append(public_queue_row("reminder", offer["id"], _iso(), status))
            if status == "sent":
                offer["reminder_sent"] = True
                sent += 1
                self._history(row, "offer", offer["id"], "reminded", "scheduler", "Candidate reminder sent")
        return sent

    def _active_token(self, row: dict[str, Any], subject: str, subject_id: str) -> Optional[dict[str, Any]]:
        for token in row["portal_tokens"].values():
            if token.get("subject") == subject and token.get("subject_id") == subject_id and not token.get("revoked_at"):
                return token
        return None

    def settings_public(self, practice_id: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._save(payload)
            return dict(row["settings"])

    def update_settings(self, practice_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        allowed = set(_default_settings())
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            settings = row["settings"]
            for key, value in patch.items():
                if key not in allowed:
                    raise HiringError(400, "Unknown setting.")
                if key in {
                    "offer_response_days",
                    "admin_reminder_days_before",
                    "candidate_reminder_days_before",
                    "packet_link_days",
                    "vault_min_days_personnel",
                    "vault_min_days_i9",
                }:
                    number = int(value)
                    if number < 1 or number > 3650:
                        raise HiringError(400, "That number is out of range.")
                    settings[key] = number
                elif key in {
                    "candidate_emails_enabled",
                    "candidate_reminder_enabled",
                    "qa_portal_bypass",
                    "i9_copy_documents",
                    "hiring_rollout",
                    "supervision_agreement_approved",
                }:
                    settings[key] = bool(value)
                elif key == "qa_email_allowlist":
                    if not isinstance(value, list):
                        raise HiringError(400, "The allowlist must be a list.")
                    cleaned = []
                    for item in value:
                        text = str(item or "").strip().lower()
                        if text and not EMAIL_RX.match(text):
                            raise HiringError(400, "Allowlist entries must be email addresses.")
                        if text and text not in cleaned:
                            cleaned.append(text)
                    settings[key] = cleaned
                elif key == "business_closed_days":
                    if not isinstance(value, list):
                        raise HiringError(400, "Closed days must be a list of dates.")
                    days = []
                    for item in value:
                        text = str(item or "").strip()
                        if not text:
                            continue
                        days.append(date.fromisoformat(text[:10]).isoformat())
                    settings[key] = days
                elif key in {"offer_supervisor_name_default", "offer_supervisor_title_default"}:
                    settings[key] = str(value or "").strip()[:120]
            self._history(row, "settings", practice_id, "updated", actor, "Settings updated")
            self._save(payload)
            return dict(settings)

    def _apply_defaults(self, row: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        settings = row["settings"]
        merged = dict(body)
        if not str(merged.get("supervisor_title") or "").strip():
            merged["supervisor_title"] = settings.get("offer_supervisor_title_default") or "Director"
        if not str(merged.get("supervisor_name") or "").strip():
            merged["supervisor_name"] = settings.get("offer_supervisor_name_default") or ""
        if not str(merged.get("offer_date") or "").strip():
            merged["offer_date"] = _today().strftime("%m/%d/%Y")
        offer_day = _parse_form_date(str(merged["offer_date"]), "offer_date")
        merged["offer_date"] = offer_day.strftime("%m/%d/%Y")
        if not str(merged.get("response_deadline") or "").strip():
            deadline = offer_day + timedelta(days=int(settings.get("offer_response_days") or 30))
            merged["response_deadline"] = deadline.strftime("%m/%d/%Y")
            merged["deadline_overridden"] = False
        else:
            computed = offer_day + timedelta(days=int(settings.get("offer_response_days") or 30))
            given = _parse_form_date(str(merged["response_deadline"]), "response_deadline")
            merged["response_deadline"] = given.strftime("%m/%d/%Y")
            merged["deadline_overridden"] = given != computed
        return merged

    def _check_deadline(self, value: str) -> date:
        day = _parse_form_date(value, "response_deadline")
        if day < _today():
            raise HiringError(400, "The response deadline cannot be in the past.", "response_deadline")
        return day

    def create_offer(self, practice_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._expire_row(row)
            resume_id = str(body.get("resume_id") or "").strip()
            if resume_id and resume_id not in (row.get("resumes") or {}):
                raise HiringError(400, "Upload the resume again.")
            offer = self._write_version(row, None, body, actor)
            if resume_id:
                self._link_resume(row, offer, resume_id)
            self._history(row, "offer", offer["id"], "drafted", actor, "Offer drafted")
            self._queue_drive(payload, practice_id, offer["id"], actor, pdf=False)
            return self._offer_public(row, offer)

    def update_offer(self, practice_id: str, offer_id: str, body: dict[str, Any], actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._expire_row(row)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            if offer["status"] in TERMINAL:
                raise HiringError(409, "That offer can no longer be edited.")
            if offer["status"] == "draft":
                self._write_version(row, offer, body, actor, replace=True)
                self._link_resume(row, offer, str(body.get("resume_id") or "").strip())
                self._history(row, "offer", offer_id, "revised", actor, "Draft updated")
            else:
                self._void_tokens(row, "offer", offer_id, "new_version")
                offer["status"] = "draft"
                self._write_version(row, offer, body, actor, replace=False)
                self._link_resume(row, offer, str(body.get("resume_id") or "").strip())
                self._history(row, "offer", offer_id, "versioned", actor, "New version started")
            self._queue_drive(payload, practice_id, offer_id, actor, pdf=False)
            return self._offer_public(row, offer)

    def _write_version(
        self,
        row: dict[str, Any],
        offer: Optional[dict[str, Any]],
        body: dict[str, Any],
        actor: str,
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        merged = self._apply_defaults(row, body)
        self._check_deadline(str(merged.get("response_deadline") or ""))
        email = str(merged.get("personal_email") or "").strip().lower()
        name = str(merged.get("display_name") or merged.get("candidate_full_name") or "").strip()
        if not EMAIL_RX.match(email):
            raise HiringError(400, "A candidate email is required.", "personal_email")
        if not name:
            raise HiringError(400, "A candidate name is required.", "candidate_full_name")
        try:
            values, docx = render_docx(
                {
                    "employment_type": merged.get("employment_type"),
                    "offer_date": merged.get("offer_date"),
                    "candidate_full_name": merged.get("candidate_full_name") or name,
                    "candidate_credentials": merged.get("candidate_credentials") or "",
                    "candidate_first_name": merged.get("candidate_first_name") or "",
                    "position_title": merged.get("position_title") or "",
                    "supervisor_title": merged.get("supervisor_title") or "",
                    "supervisor_name": merged.get("supervisor_name") or "",
                    "hourly_rate": merged.get("hourly_rate") or "",
                    "licensed_rate": merged.get("licensed_rate") or "",
                    "caseload_range": merged.get("caseload_range") or "",
                    "qsehra_monthly_amount": merged.get("qsehra_monthly_amount") or "",
                    "qsehra_wait_days": merged.get("qsehra_wait_days") or "",
                    "start_date": merged.get("start_date") or "",
                    "response_deadline": merged.get("response_deadline") or "",
                }
            )
        except OfferFillError as exc:
            raise HiringError(400, str(exc), exc.field) from exc
        try:
            pdf = render_offer_pdf(docx)
        except PdfRenderError as exc:
            raise HiringError(500, "The letter could not be rendered.") from exc
        candidate = self._candidate(row, email, name)
        if offer is None:
            for other in row["offers"].values():
                if other.get("candidate_id") == candidate["id"] and other.get("status") in OPEN:
                    raise HiringError(409, "This candidate already has an open offer.")
            offer = {
                "id": _new_id("off-"),
                "candidate_id": candidate["id"],
                "status": "draft",
                "employment_type": values["employment_type"],
                "current_version_id": "",
                "response_deadline": parse_mdy(values["response_deadline"]).isoformat(),
                "deadline_overridden": bool(merged.get("deadline_overridden")),
                "direct_client_access": self._access_flag(merged, values["position_title"]),
                "sent_at": "",
                "sent_channel": "",
                "viewed_at": "",
                "closed_at": "",
                "decline_reason": "",
                "decline_note": "",
                "withdraw_reason": "",
                "superseded_by_offer_id": "",
                "reissued_from_offer_id": str(merged.get("reissued_from_offer_id") or ""),
                "reminder_sent": False,
            }
            row["offers"][offer["id"]] = offer
        else:
            offer["employment_type"] = values["employment_type"]
            offer["response_deadline"] = parse_mdy(values["response_deadline"]).isoformat()
            offer["deadline_overridden"] = bool(merged.get("deadline_overridden"))
            offer["direct_client_access"] = self._access_flag(merged, values["position_title"])
            candidate["display_name"] = name
            candidate["personal_email"] = email
            offer["candidate_id"] = candidate["id"]
        store = file_store()
        store.ensure_tree(offer["id"])
        number = 1
        if replace and offer.get("current_version_id"):
            current = row["offer_versions"].get(offer["current_version_id"]) or {}
            number = int(current.get("n") or 1)
            version_id = offer["current_version_id"]
        else:
            number = 1 + max(
                [int(item.get("n") or 0) for item in row["offer_versions"].values() if item.get("offer_id") == offer["id"]]
                or [0]
            )
            version_id = _new_id("ver-")
        template_sha, fields_sha = asset_hashes()
        docx_ref = store.put(
            StorageKey("personnel", offer["id"], "01-onboarding", "offer-letter-docx", number),
            docx,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        pdf_ref = store.put(
            StorageKey("personnel", offer["id"], "01-onboarding", "offer-letter-pdf", number),
            pdf,
            "application/pdf",
        )
        public_values = {key: value for key, value in values.items() if not str(key).startswith("_")}
        typed_credentials = str(merged.get("candidate_credentials") or "").strip()[:120]
        public_values["candidate_credentials"] = typed_credentials
        row["offer_versions"][version_id] = {
            "id": version_id,
            "offer_id": offer["id"],
            "n": number,
            "field_values": public_values,
            "template_sha256": template_sha,
            "fields_config_sha256": fields_sha,
            "docx_object_key": docx_ref.object_key,
            "pdf_object_key": pdf_ref.object_key,
            "docx_backend": docx_ref.backend,
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "created_by": actor,
            "created_at": _iso(),
            "frozen": False,
            "voided_at": "",
        }
        offer["current_version_id"] = version_id
        return offer

    def _access_flag(self, body: dict[str, Any], position: str) -> bool:
        if "direct_client_access" in body and body.get("direct_client_access") is not None:
            return bool(body.get("direct_client_access"))
        return clinician_title(position)

    def _candidate(self, row: dict[str, Any], email: str, name: str) -> dict[str, Any]:
        for candidate in row["candidates"].values():
            if str(candidate.get("personal_email") or "").lower() == email and not candidate.get("archived_at"):
                candidate["display_name"] = name
                return candidate
        candidate = {
            "id": _new_id("cand-"),
            "display_name": name,
            "personal_email": email,
            "created_at": _iso(),
            "archived_at": "",
        }
        row["candidates"][candidate["id"]] = candidate
        return candidate

    def delete_draft(self, practice_id: str, offer_id: str, actor: str) -> None:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            if offer["status"] != "draft":
                raise HiringError(409, "Only a draft can be deleted.")
            self._drop_offer_resumes(row, offer)
            row["offers"].pop(offer_id, None)
            self._history(row, "offer", offer_id, "deleted", actor, "Draft deleted")
            self._save(payload)

    def delete_offer(self, practice_id: str, offer_id: str, confirm: str, actor: str) -> None:
        """Remove an offer after the caller types the candidate name. Any status."""
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
            version = row["offer_versions"].get(offer.get("current_version_id") or "") or {}
            legal = str((version.get("field_values") or {}).get("candidate_full_name") or "").strip()
            expected = str(candidate.get("display_name") or "").strip()
            confirm_name = str(confirm or "").strip()
            allowed = {name for name in (expected, legal) if name}
            if not confirm_name or confirm_name not in allowed:
                raise HiringError(400, "Type the candidate's name to confirm.")
            hire_ids = [hire["id"] for hire in row["hires"].values() if hire.get("offer_id") == offer_id]
            store = file_store()
            for version in list(row["offer_versions"].values()):
                if version.get("offer_id") != offer_id:
                    continue
                for key in ("pdf_object_key", "docx_object_key"):
                    _delete_stored(store, version.get(key) or "")
                row["offer_versions"].pop(version["id"], None)
            for acceptance in list(row["acceptances"].values()):
                if acceptance.get("offer_id") != offer_id:
                    continue
                _delete_stored(store, acceptance.get("certificate_object_key") or "")
                row["acceptances"].pop(acceptance["id"], None)
            for ref_id, ref in list(row["document_refs"].items()):
                if ref.get("hire_id") not in hire_ids:
                    continue
                _delete_stored(store, ref.get("object_key") or "")
                row["document_refs"].pop(ref_id, None)
            for item_id, item in list(row["checklist_items"].items()):
                if item.get("hire_id") in hire_ids:
                    row["checklist_items"].pop(item_id, None)
            for hire_id in hire_ids:
                self._void_tokens(row, "hire", hire_id, "deleted")
                row["hires"].pop(hire_id, None)
            self._void_tokens(row, "offer", offer_id, "deleted")
            self._drop_offer_resumes(row, offer)
            self._trash_drive_ids([str(offer.get("drive_folder_id") or "")])
            row["offers"].pop(offer_id, None)
            self._history(row, "offer", offer_id, "deleted", actor, "Offer deleted")
            self._save(payload)

    def send_offer(self, practice_id: str, offer_id: str, channel: str, actor: str, base_url: str) -> dict[str, Any]:
        if channel not in {"portal", "outside"}:
            raise HiringError(400, "Choose portal or outside.")
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._expire_row(row)
            offer = row["offers"].get(offer_id)
            if not offer or offer["status"] != "draft":
                raise HiringError(409, "Only a draft can be sent.")
            version = row["offer_versions"][offer["current_version_id"]]
            version["frozen"] = True
            if channel == "portal":
                candidate = row["candidates"][offer["candidate_id"]]
                address = candidate["personal_email"]
                if not self._email_allowed(row, address):
                    raise HiringError(409, "Candidate email is turned off. Mark the offer sent outside Clynotion.")
                raw, _token = self._mint_token(row, "offer", offer_id, _end_of_deadline(offer["response_deadline"]))
                status = deliver(
                    practice_id,
                    address,
                    "Your offer from Heart for Change",
                    offer_body(base_url.rstrip("/") + "/join/" + raw, _mdy(parse_iso(offer["response_deadline"]))),
                )
                row["mail_queue"].append(public_queue_row("offer_link", offer_id, _iso(), status))
                if status != "sent":
                    version["frozen"] = False
                    raise HiringError(409, "The offer email was not sent. The sending mailbox may be disconnected.")
                self._history(row, "offer", offer_id, "sent", actor, "Offer sent through the portal")
            else:
                self._mint_token(row, "offer", offer_id, _end_of_deadline(offer["response_deadline"]))
                self._history(row, "offer", offer_id, "sent", actor, "Offer marked sent outside Clynotion")
            offer["status"] = "sent"
            offer["sent_at"] = _iso()
            offer["sent_channel"] = channel
            offer["reminder_sent"] = False
            self._queue_drive(payload, practice_id, offer_id, actor, pdf=True)
            return self._offer_public(row, offer)

    def _mint_token(self, row: dict[str, Any], subject: str, subject_id: str, expires_at: str) -> tuple[str, dict[str, Any]]:
        self._void_tokens(row, subject, subject_id, "replaced")
        raw = secrets.token_urlsafe(32)
        token = {
            "id": _new_id("tok-"),
            "subject": subject,
            "subject_id": subject_id,
            "token_sha256": _sha(raw),
            "expires_at": expires_at,
            "revoked_at": "",
            "revoke_reason": "",
            "raw_for_reminder": "",
        }
        row["portal_tokens"][token["id"]] = token
        return raw, token

    def withdraw(self, practice_id: str, offer_id: str, reason: str, actor: str) -> dict[str, Any]:
        if not str(reason or "").strip():
            raise HiringError(400, "A reason is required to withdraw an offer.")
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = self._require_open(row, offer_id, allow_draft=True)
            offer["status"] = "withdrawn"
            offer["withdraw_reason"] = str(reason).strip()[:300]
            offer["closed_at"] = _iso()
            self._void_tokens(row, "offer", offer_id, "withdrawn")
            self._history(row, "offer", offer_id, "withdrawn", actor, "Offer withdrawn")
            self._save(payload)
            return self._offer_public(row, offer)

    def admin_decline(self, practice_id: str, offer_id: str, reason: str, note: str, actor: str) -> dict[str, Any]:
        if reason not in DECLINE_REASONS:
            raise HiringError(400, "Unknown decline reason.")
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = self._require_open(row, offer_id, allow_draft=False)
            offer["status"] = "declined"
            offer["decline_reason"] = reason
            offer["decline_note"] = str(note or "").strip()[:300]
            offer["closed_at"] = _iso()
            self._void_tokens(row, "offer", offer_id, "declined")
            self._history(row, "offer", offer_id, "declined", actor, "Decline recorded")
            self._save(payload)
            return self._offer_public(row, offer)

    def _require_open(self, row: dict[str, Any], offer_id: str, *, allow_draft: bool) -> dict[str, Any]:
        self._expire_row(row)
        offer = row["offers"].get(offer_id)
        if not offer:
            raise HiringError(404, "Offer not found.")
        allowed = {"sent", "viewed"} if not allow_draft else {"draft", "sent", "viewed"}
        if offer["status"] not in allowed:
            raise HiringError(409, "That offer cannot take that step.")
        return offer

    def paper_accept(
        self,
        practice_id: str,
        offer_id: str,
        typed_name: str,
        actor: str,
        ip: str,
        user_agent: str,
        upload: bytes | None,
        accepted_on: str = "",
        *,
        require_upload: bool = False,
        require_date: bool = False,
    ) -> dict[str, Any]:
        if require_upload and not upload:
            raise HiringError(400, "Upload the signed offer PDF.")
        if upload and not upload.startswith(b"%PDF"):
            raise HiringError(400, "Upload the signed offer as a PDF.")
        if len(upload or b"") > MAX_UPLOAD:
            raise HiringError(400, "That file is larger than 15 MB.")
        if require_date and not str(accepted_on or "").strip():
            raise HiringError(400, "Set the acceptance date.", "accepted_on")
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = self._require_open(row, offer_id, allow_draft=False)
            hire = self._accept(
                row,
                offer,
                typed_name,
                method="paper",
                actor=actor,
                ip=ip,
                user_agent=user_agent,
                upload=upload,
                practice_id=practice_id,
                accepted_on=accepted_on,
            )
            self._queue_drive(payload, practice_id, offer["id"], actor, pdf=True, filing=True)
            return {"offer": self._offer_public(row, offer), "hire": self._hire_public(row, hire)}

    def _accept(
        self,
        row: dict[str, Any],
        offer: dict[str, Any],
        typed_name: str,
        *,
        method: str,
        actor: str,
        ip: str,
        user_agent: str,
        upload: bytes | None,
        practice_id: str,
        accepted_on: str = "",
    ) -> dict[str, Any]:
        version = row["offer_versions"][offer["current_version_id"]]
        expected = str(version["field_values"].get("candidate_full_name") or "")
        if upload:
            if typed_name.strip() and _norm(typed_name) != _norm(expected):
                raise HiringError(400, "Type the full legal name as it appears on the letter.")
        elif _norm(typed_name) != _norm(expected):
            raise HiringError(400, "Type the full legal name as it appears on the letter.")
        accepted = _acceptance_day(accepted_on)
        store = file_store()
        pdf = store.get(_ref(version["pdf_object_key"], version.get("docx_backend") or "local", version["pdf_sha256"]))
        digest = hashlib.sha256(pdf).hexdigest()
        if digest != version["pdf_sha256"]:
            raise HiringError(409, "The letter file no longer matches the version that was sent.")
        when = _now()
        eastern = when.astimezone(EASTERN).strftime("%m/%d/%Y %I:%M %p %Z")
        certificate = append_certificate(
            pdf,
            [
                "Acceptance certificate",
                f"Name: {typed_name.strip()}",
                f"Time: {eastern}",
                f"IP: {ip or 'unknown'}",
                f"Letter SHA-256: {digest}",
                f"Method: {method}",
            ],
        )
        acceptance = {
            "id": _new_id("acc-"),
            "offer_id": offer["id"],
            "offer_version_id": version["id"],
            "typed_name": typed_name.strip(),
            "accepted_at": when.isoformat(),
            "ip": ip,
            "user_agent": (user_agent or "")[:300],
            "pdf_sha256": digest,
            "method": method,
            "signed_upload_ref": "",
            "accepted_on": accepted.isoformat(),
        }
        offer["status"] = "accepted"
        offer["closed_at"] = when.isoformat()
        self._void_tokens(row, "offer", offer["id"], "accepted")
        hire = self._create_hire(row, offer, version, practice_id, accepted)
        store.ensure_tree(hire["id"])
        filed = store.put(
            StorageKey("personnel", hire["id"], "01-onboarding", "signed-offer", 1),
            certificate,
            "application/pdf",
        )
        ref = self._doc_ref(row, hire["id"], "", "signed-offer", filed, actor, sensitive=False)
        acceptance["certificate_object_key"] = filed.object_key
        if upload:
            mime = _sniff(upload)
            uploaded = store.put(
                StorageKey("personnel", hire["id"], "01-onboarding", "signed-upload", 1),
                upload,
                mime,
            )
            acceptance["signed_upload_ref"] = self._doc_ref(row, hire["id"], "", "signed-upload", uploaded, actor, sensitive=False)["id"]
        row["acceptances"][acceptance["id"]] = acceptance
        self._history(row, "offer", offer["id"], "accepted", actor, "Offer accepted", ip=ip)
        self._history(row, "hire", hire["id"], "created", "clynotion", "Onboarding record created")
        i9_ready = bool((os.environ.get("DRIVE_I9_SHARED_DRIVE_ID") or "").strip())
        hire["i9_drive"] = "ready" if i9_ready else "pending"
        if not i9_ready:
            self._history(row, "hire", hire["id"], "i9_pending", "clynotion", "I-9 drive is not configured")
        _ = ref
        return hire

    def _create_hire(
        self,
        row: dict[str, Any],
        offer: dict[str, Any],
        version: dict[str, Any],
        practice_id: str,
        accepted: date | None = None,
    ) -> dict[str, Any]:
        values = version["field_values"]
        start = parse_mdy(values["start_date"])
        accepted = accepted or _today()
        hire_id = _new_id("hire-")
        closed = closed_set(row["settings"].get("business_closed_days") or [])
        needs_supervision = bool(row["settings"].get("supervision_agreement_approved")) and "lsw" in str(values.get("position_title") or "").casefold()
        items = build_items(
            hire_id=hire_id,
            employment_type=values["employment_type"],
            start=start,
            accepted=accepted,
            direct_client_access=bool(offer.get("direct_client_access")),
            needs_supervision=needs_supervision,
            closed=closed,
            new_id=_new_id,
        )
        for item in items:
            row["checklist_items"][item["id"]] = item
        candidate = row["candidates"][offer["candidate_id"]]
        hire = {
            "id": hire_id,
            "candidate_id": candidate["id"],
            "offer_id": offer["id"],
            "employment_type": values["employment_type"],
            "position": values.get("position_title") or "",
            "start_date": start.isoformat(),
            "supervisor_title": values.get("supervisor_title") or "",
            "supervisor_name": values.get("supervisor_name") or "",
            "direct_client_access": bool(offer.get("direct_client_access")),
            "status": "onboarding",
            "first_day_worked_for_pay": "",
            "activated_at": "",
            "employee_view_id": "",
            "work_email": "",
            "folders": file_store().ensure_tree(hire_id),
            "drive_subfolders": {},
            "i9_drive": "pending",
        }
        row["hires"][hire_id] = hire
        _ = practice_id
        return hire

    def _doc_ref(self, row, hire_id, item_id, doc_type, stored, actor, *, sensitive: bool) -> dict[str, Any]:
        ref = {
            "id": _new_id("doc-"),
            "hire_id": hire_id,
            "checklist_item_id": item_id,
            "doc_type": doc_type,
            "version": 1,
            "backend": stored.backend,
            "object_key": stored.object_key,
            "drive_file_id": stored.drive_file_id,
            "drive_revision_id": stored.drive_revision_id,
            "sha256": stored.sha256,
            "size": stored.size,
            "mime": stored.mime,
            "uploaded_by": actor,
            "uploaded_at": _iso(),
            "retention_date": "",
            "sensitive": sensitive,
            "deleted_at": "",
            "deletion_note": "",
        }
        if doc_type == "hipaa-ack":
            ref["retention_date"] = to_iso(_today() + timedelta(days=365 * 6))
        row["document_refs"][ref["id"]] = ref
        return ref

    def reissue(self, practice_id: str, offer_id: str, actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._expire_row(row)
            offer = row["offers"].get(offer_id)
            if not offer or offer["status"] not in TERMINAL:
                raise HiringError(409, "Send a new offer from a closed offer.")
            if offer["status"] != "expired" and offer["status"] not in TERMINAL:
                raise HiringError(409, "That offer is still open.")
            version = row["offer_versions"][offer["current_version_id"]]
            candidate = row["candidates"][offer["candidate_id"]]
            body = dict(version["field_values"])
            body["display_name"] = candidate["display_name"]
            body["personal_email"] = candidate["personal_email"]
            body["response_deadline"] = ""
            body["direct_client_access"] = offer.get("direct_client_access")
            body["reissued_from_offer_id"] = offer_id
            created = self._write_version(row, None, body, actor)
            self._history(row, "offer", created["id"], "reissued", actor, "New offer drafted from a closed offer")
            self._save(payload)
            public = self._offer_public(row, created)
            public["reissued_from_offer_id"] = offer_id
            return public

    def force_expire(self, practice_id: str, offer_id: str, actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = row["offers"].get(offer_id)
            if not offer or offer["status"] not in {"sent", "viewed"}:
                raise HiringError(409, "Only a sent offer can be expired.")
            offer["response_deadline"] = (_today() - timedelta(days=1)).isoformat()
            self._expire_row(row)
            self._history(row, "offer", offer_id, "force_expired", actor, "Expiry forced")
            self._save(payload)
            return self._offer_public(row, offer)

    def list_offers(self, practice_id: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            changed = self._expire_row(row)
            if changed:
                self._save(payload)
            offers = [self._offer_public(row, offer) for offer in row["offers"].values()]
            offers.sort(key=lambda item: item.get("created_at") or "", reverse=True)
            hires = [self._hire_public(row, hire) for hire in row["hires"].values()]
            return {
                "settings": dict(row["settings"]),
                "offers": offers,
                "hires": hires,
                "forms": self._forms_public(row),
                "form_flags": self._form_flags(row),
                "history": list(reversed(row["history"][-40:])),
                "mail_queue": list(row["mail_queue"]),
            }

    def get_offer(self, practice_id: str, offer_id: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._expire_row(row)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            self._save(payload)
            return self._offer_public(row, offer)

    def offer_bytes(self, practice_id: str, offer_id: str, kind: str, actor: str) -> tuple[bytes, str, str]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            version = row["offer_versions"][offer["current_version_id"]]
            if kind == "resume":
                return self._resume_download(row, offer, actor, payload)
            key = "pdf_object_key" if kind == "pdf" else "docx_object_key"
            mime = "application/pdf" if kind == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            data = file_store().get(_ref(version[key], version.get("docx_backend") or "local", version.get("pdf_sha256") or ""))
            self._history(row, "offer", offer_id, "downloaded", actor, "Offer file opened")
            self._save(payload)
            filename = f"{offer_id}-offer.{kind if kind == 'pdf' else 'docx'}"
            return data, mime, filename

    def upload_resume(self, practice_id: str, data: bytes, filename: str, actor: str) -> dict[str, Any]:
        from app.hiring.resume import ResumeReadError, read_resume_for_upload, resume_mime

        try:
            mime = resume_mime(data, filename)
        except ResumeReadError as exc:
            raise HiringError(400, str(exc)) from exc
        if len(data) > 10 * 1024 * 1024:
            raise HiringError(400, "That resume is larger than 10 MB.")
        warning = ""
        fields = {
            "candidate_full_name": "",
            "candidate_first_name": "",
            "personal_email": "",
            "phone": "",
            "candidate_credentials": "",
            "position_title": "",
        }
        try:
            fields = read_resume_for_upload(data, filename)
        except ResumeReadError as exc:
            warning = str(exc)
        resume_id = _new_id("res-")
        store = file_store()
        store.ensure_tree(resume_id)
        ref = store.put(StorageKey("personnel", resume_id, "01-onboarding", "resume", 1), data, mime)
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            row["resumes"][resume_id] = {
                "id": resume_id,
                "object_key": ref.object_key,
                "sha256": ref.sha256,
                "mime": mime,
                "size": len(data),
                "backend": ref.backend,
                "offer_id": "",
                "created_at": _iso(),
            }
            self._history(row, "resume", resume_id, "uploaded", actor, "Resume uploaded")
            self._save(payload)
        return {"resume_id": resume_id, "fields": fields, "warning": warning}

    def _link_resume(self, row: dict[str, Any], offer: dict[str, Any], resume_id: str) -> None:
        if not resume_id:
            return
        resume = (row.get("resumes") or {}).get(resume_id)
        if not resume:
            raise HiringError(400, "Upload the resume again.")
        owner = str(resume.get("offer_id") or "")
        if owner and owner != offer["id"]:
            raise HiringError(409, "That resume is already on another offer.")
        previous = str(offer.get("resume_id") or "")
        if previous and previous != resume_id:
            self._drop_resume(row, previous)
        resume["offer_id"] = offer["id"]
        offer["resume_id"] = resume_id

    def retry_personnel_folder(self, practice_id: str, offer_id: str, actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            offer = row["offers"].get(offer_id)
            if not offer:
                raise HiringError(404, "Offer not found.")
            filing = offer.get("status") == "accepted"
            self._queue_drive(
                payload,
                practice_id,
                offer_id,
                actor,
                pdf=offer.get("status") in {"sent", "viewed", "accepted"},
                filing=filing,
            )
            return self._offer_public(row, offer)

    def _sync_personnel(
        self,
        row: dict[str, Any],
        offer: dict[str, Any],
        actor: str,
        *,
        pdf: bool,
        audit_unavailable: bool = False,
    ) -> None:
        from app.hiring.personnel_drive import folder_link, folder_name, personnel_client

        client = personnel_client()
        if client is None:
            offer["drive_status"] = "not_connected"
            if audit_unavailable:
                self._history(row, "offer", offer["id"], "drive_unavailable", actor, "Drive not connected")
            return
        try:
            candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
            desired = folder_name(str(candidate.get("display_name") or ""))
            current = str(offer.get("drive_folder_id") or "")
            if current:
                if offer.get("drive_folder_name") != desired and hasattr(client, "rename"):
                    client.rename(current, desired)
                    if offer.get("drive_folder_name"):
                        self._history(row, "offer", offer["id"], "drive_renamed", actor, "Personnel folder renamed")
                folder_id, url = current, folder_link(current)
            else:
                folder_id, url = client.ensure_folder("", desired)
                if folder_id:
                    self._history(row, "offer", offer["id"], "drive_folder", actor, "Personnel folder created")
            offer["drive_folder_id"] = folder_id
            offer["drive_folder_url"] = url
            offer["drive_folder_name"] = desired
            offer["drive_status"] = "connected"
            self._copy_resume_to_drive(row, offer, client, actor)
            if pdf:
                self._copy_pdf_to_drive(row, offer, client, actor)
        except Exception:
            offer["drive_status"] = "error"
            self._history(row, "offer", offer["id"], "drive_failed", actor, "Personnel folder was not created")

    def _copy_resume_to_drive(self, row: dict[str, Any], offer: dict[str, Any], client: Any, actor: str) -> None:
        resume = (row.get("resumes") or {}).get(offer.get("resume_id") or "")
        folder_id = str(offer.get("drive_folder_id") or "")
        if not resume or not folder_id:
            return
        mime = str(resume.get("mime") or "application/octet-stream")
        candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
        from app.hiring.personnel_drive import resume_filename

        filename = resume_filename(str(candidate.get("display_name") or ""), mime)
        file_id = str(offer.get("drive_resume_file_id") or "")
        if file_id:
            if offer.get("drive_resume_name") != filename and hasattr(client, "rename"):
                client.rename(file_id, filename)
                if offer.get("drive_resume_name"):
                    self._history(row, "offer", offer["id"], "drive_resume_renamed", actor, "Resume file renamed")
            offer["drive_resume_name"] = filename
            return
        data = file_store().get(
            _ref(str(resume.get("object_key") or ""), str(resume.get("backend") or "local"), str(resume.get("sha256") or ""))
        )
        offer["drive_resume_file_id"] = client.upload(folder_id, filename, data, mime)
        offer["drive_resume_name"] = filename
        self._history(row, "offer", offer["id"], "drive_resume", actor, "Resume copied to the personnel folder")

    def _copy_pdf_to_drive(self, row: dict[str, Any], offer: dict[str, Any], client: Any, actor: str) -> None:
        from app.hiring.personnel_drive import offer_letter_filename

        folder_id = str(offer.get("drive_folder_id") or "")
        if not folder_id:
            return
        candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
        filename = offer_letter_filename(str(candidate.get("display_name") or ""))
        file_id = str(offer.get("drive_pdf_file_id") or "")
        if file_id:
            if offer.get("drive_pdf_name") != filename and hasattr(client, "rename"):
                client.rename(file_id, filename)
            offer["drive_pdf_name"] = filename
            return
        version = row["offer_versions"].get(offer.get("current_version_id") or "")
        if not version:
            return
        data = file_store().get(
            _ref(
                str(version.get("pdf_object_key") or ""),
                str(version.get("docx_backend") or "local"),
                str(version.get("pdf_sha256") or ""),
            )
        )
        offer["drive_pdf_file_id"] = client.upload(folder_id, filename, data, "application/pdf")
        offer["drive_pdf_name"] = filename
        self._history(row, "offer", offer["id"], "drive_pdf", actor, "Offer PDF copied to the personnel folder")

    def _queue_drive(
        self,
        payload: dict[str, Any],
        practice_id: str,
        offer_id: str,
        actor: str,
        *,
        pdf: bool,
        filing: bool = False,
    ) -> None:
        from app.hiring.personnel_drive import personnel_client

        row = self._practice(payload, practice_id)
        offer = row["offers"].get(offer_id)
        if offer is None:
            self._save(payload)
            return
        if _drive_async() and personnel_client() is not None:
            offer["drive_status"] = "working"
            self._save(payload)
            threading.Thread(
                target=self._drive_job,
                args=(practice_id, offer_id, actor, pdf, filing),
                daemon=True,
                name="clynotion-drive",
            ).start()
            return
        self._apply_drive(row, offer, actor, pdf=pdf, filing=filing)
        self._save(payload)

    def _drive_job(self, practice_id: str, offer_id: str, actor: str, pdf: bool, filing: bool) -> None:
        """Drive HTTP stays outside the request lock so the page can load."""
        try:
            with _lock:
                payload = self._read()
                row = copy.deepcopy(self._practice(payload, practice_id))
            offer = row["offers"].get(offer_id)
            if not offer:
                return
            before = len(row.get("history") or [])
            self._apply_drive(row, offer, actor, pdf=pdf, filing=filing)
            history = (row.get("history") or [])[before:]
            hire_state = {}
            for hire in row["hires"].values():
                if hire.get("offer_id") == offer_id:
                    hire_state = {
                        "drive_subfolders": dict(hire.get("drive_subfolders") or {}),
                        "i9_drive": hire.get("i9_drive") or "",
                    }
            with _lock:
                payload = self._read()
                live = self._practice(payload, practice_id)
                live_offer = live["offers"].get(offer_id)
                if not live_offer:
                    return
                for key in _DRIVE_OFFER_KEYS:
                    if key in offer:
                        live_offer[key] = offer[key]
                if hire_state:
                    for hire in live["hires"].values():
                        if hire.get("offer_id") == offer_id:
                            hire.update(hire_state)
                live.setdefault("history", []).extend(history)
                self._save(payload)
        except Exception as exc:
            logger.warning("drive_job_failed exception=%s", type(exc).__name__)
            self._mark_drive_error(practice_id, offer_id)

    def _mark_drive_error(self, practice_id: str, offer_id: str) -> None:
        try:
            with _lock:
                payload = self._read()
                offer = self._practice(payload, practice_id)["offers"].get(offer_id)
                if offer and offer.get("drive_status") == "working":
                    offer["drive_status"] = "error"
                    self._save(payload)
        except Exception as exc:
            logger.warning("drive_job_failed exception=%s", type(exc).__name__)

    def _apply_drive(self, row: dict[str, Any], offer: dict[str, Any], actor: str, *, pdf: bool, filing: bool) -> None:
        hire = next((item for item in row["hires"].values() if item.get("offer_id") == offer["id"]), None)
        if filing and hire is not None:
            self._file_accepted_drive(row, offer, hire, actor, self._signed_bytes(row, offer))
            return
        self._sync_personnel(row, offer, actor, pdf=pdf)

    def _signed_bytes(self, row: dict[str, Any], offer: dict[str, Any]) -> bytes | None:
        acceptance = next((item for item in row["acceptances"].values() if item.get("offer_id") == offer["id"]), None)
        if not acceptance:
            return None
        ref = (row.get("document_refs") or {}).get(acceptance.get("signed_upload_ref") or "")
        if not ref:
            return None
        return file_store().get(
            _ref(str(ref.get("object_key") or ""), str(ref.get("backend") or "local"), str(ref.get("sha256") or ""))
        )

    def _file_accepted_drive(
        self,
        row: dict[str, Any],
        offer: dict[str, Any],
        hire: dict[str, Any],
        actor: str,
        signed: bytes | None,
    ) -> None:
        from app.hiring.personnel_drive import ONBOARDING_FOLDERS, folder_name, personnel_client

        client = personnel_client()
        if client is None:
            offer["drive_status"] = "not_connected"
            return
        try:
            if not offer.get("drive_folder_id"):
                self._sync_personnel(row, offer, actor, pdf=True)
            parent = str(offer.get("drive_folder_id") or "")
            if not parent or not hasattr(client, "ensure_child"):
                if offer.get("drive_status") == "working":
                    offer["drive_status"] = "connected" if parent else "error"
                return
            children = dict(hire.get("drive_subfolders") or {})
            created = False
            for label in ONBOARDING_FOLDERS:
                if not children.get(label):
                    children[label] = client.ensure_child(parent, label)
                    created = True
            hire["drive_subfolders"] = children
            if created:
                self._history(row, "hire", hire["id"], "folders", "clynotion", "Onboarding folders created")
            onboarding = str(children.get("01 Onboarding") or "")
            if not onboarding:
                offer["drive_status"] = "error"
                return
            candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
            name = str(candidate.get("display_name") or folder_name(""))
            self._place_in_onboarding(row, offer, client, onboarding, name, actor, signed)
            offer["drive_status"] = "connected"
        except Exception:
            offer["drive_status"] = "error"
            self._history(row, "hire", hire["id"], "drive_failed", actor, "Onboarding folders were not created")

    def _place_in_onboarding(
        self,
        row: dict[str, Any],
        offer: dict[str, Any],
        client: Any,
        onboarding: str,
        name: str,
        actor: str,
        signed: bytes | None,
    ) -> None:
        from app.hiring.personnel_drive import resume_filename, signed_offer_filename

        resume = (row.get("resumes") or {}).get(offer.get("resume_id") or "")
        if resume and not offer.get("resume_in_onboarding"):
            mime = str(resume.get("mime") or "application/octet-stream")
            filename = resume_filename(name, mime)
            file_id = str(offer.get("drive_resume_file_id") or "")
            if file_id and hasattr(client, "move"):
                client.move(file_id, onboarding, filename)
            elif not file_id:
                data = file_store().get(
                    _ref(str(resume.get("object_key") or ""), str(resume.get("backend") or "local"), str(resume.get("sha256") or ""))
                )
                offer["drive_resume_file_id"] = client.upload(onboarding, filename, data, mime)
            offer["drive_resume_name"] = filename
            offer["resume_in_onboarding"] = True
        from app.hiring.personnel_drive import offer_letter_filename

        letter_name = offer_letter_filename(name)
        pdf_id = str(offer.get("drive_pdf_file_id") or "")
        if not offer.get("pdf_in_onboarding") or offer.get("drive_pdf_name") != letter_name:
            if pdf_id and hasattr(client, "move"):
                client.move(pdf_id, onboarding, letter_name)
                offer["pdf_in_onboarding"] = True
                offer["drive_pdf_name"] = letter_name
            elif not pdf_id:
                version = row["offer_versions"].get(offer.get("current_version_id") or "")
                if version:
                    data = file_store().get(
                        _ref(
                            str(version.get("pdf_object_key") or ""),
                            str(version.get("docx_backend") or "local"),
                            str(version.get("pdf_sha256") or ""),
                        )
                    )
                    offer["drive_pdf_file_id"] = client.upload(onboarding, letter_name, data, "application/pdf")
                    offer["pdf_in_onboarding"] = True
                    offer["drive_pdf_name"] = letter_name
        if signed and not offer.get("drive_signed_file_id"):
            offer["drive_signed_file_id"] = client.upload(
                onboarding, signed_offer_filename(name), signed, "application/pdf"
            )
            self._history(row, "offer", offer["id"], "signed_filed", actor, "Signed offer filed")

    def _drop_offer_resumes(self, row: dict[str, Any], offer: dict[str, Any]) -> None:
        offer_id = offer.get("id") or ""
        linked = str(offer.get("resume_id") or "")
        for resume_id, resume in list((row.get("resumes") or {}).items()):
            if resume_id == linked or resume.get("offer_id") == offer_id:
                self._drop_resume(row, resume_id)

    def _drop_resume(self, row: dict[str, Any], resume_id: str) -> None:
        resume = (row.get("resumes") or {}).pop(resume_id, None)
        if not resume:
            return
        _delete_stored(file_store(), str(resume.get("object_key") or ""))

    def _resume_download(
        self, row: dict[str, Any], offer: dict[str, Any], actor: str, payload: dict[str, Any]
    ) -> tuple[bytes, str, str]:
        resume = (row.get("resumes") or {}).get(offer.get("resume_id") or "")
        if not resume:
            raise HiringError(404, "That resume is not on file.")
        data = file_store().get(
            _ref(str(resume.get("object_key") or ""), str(resume.get("backend") or "local"), str(resume.get("sha256") or ""))
        )
        self._history(row, "resume", str(resume.get("id") or ""), "downloaded", actor, "Resume downloaded")
        self._save(payload)
        mime = str(resume.get("mime") or "application/octet-stream")
        candidate = row["candidates"].get(offer.get("candidate_id") or "") or {}
        from app.hiring.personnel_drive import resume_filename

        return data, mime, resume_filename(str(candidate.get("display_name") or ""), mime)

    def _offer_public(self, row: dict[str, Any], offer: dict[str, Any]) -> dict[str, Any]:
        candidate = row["candidates"].get(offer["candidate_id"]) or {}
        version = row["offer_versions"].get(offer.get("current_version_id") or "") or {}
        acceptance = next((item for item in row["acceptances"].values() if item.get("offer_id") == offer["id"]), None)
        hire = next((item for item in row["hires"].values() if item.get("offer_id") == offer["id"]), None)
        return {
            "id": offer["id"],
            "status": offer["status"],
            "candidate_id": offer["candidate_id"],
            "display_name": candidate.get("display_name") or "",
            "personal_email": candidate.get("personal_email") or "",
            "employment_type": offer.get("employment_type") or "",
            "response_deadline": offer.get("response_deadline") or "",
            "deadline_overridden": bool(offer.get("deadline_overridden")),
            "direct_client_access": bool(offer.get("direct_client_access")),
            "sent_at": offer.get("sent_at") or "",
            "sent_channel": offer.get("sent_channel") or "",
            "viewed_at": offer.get("viewed_at") or "",
            "closed_at": offer.get("closed_at") or "",
            "decline_reason": offer.get("decline_reason") or "",
            "withdraw_reason": offer.get("withdraw_reason") or "",
            "reissued_from_offer_id": offer.get("reissued_from_offer_id") or "",
            "current_version": version.get("n") or 1,
            "fields": dict(version.get("field_values") or {}),
            "pdf_sha256": version.get("pdf_sha256") or "",
            "created_at": version.get("created_at") or "",
            "link_active": self._active_token(row, "offer", offer["id"]) is not None and offer["status"] in {"sent", "viewed"},
            "acceptance": _acceptance_public(acceptance) if acceptance else None,
            "hire_id": hire["id"] if hire else "",
            "resume": bool(offer.get("resume_id")) and offer.get("resume_id") in (row.get("resumes") or {}),
            "drive_connected": _drive_connected(),
            "drive_status": offer.get("drive_status") or ("connected" if offer.get("drive_folder_id") else "not_connected"),
            "drive_ok": _drive_ok(offer, str(candidate.get("display_name") or "")),
            "drive_note": drive_prompt(offer, str(candidate.get("display_name") or ""))[0],
            "drive_retry": drive_prompt(offer, str(candidate.get("display_name") or ""))[1],
            "drive_folder_url": offer.get("drive_folder_url") or "",
        }

    def _hire_public(self, row: dict[str, Any], hire: dict[str, Any]) -> dict[str, Any]:
        candidate = row["candidates"].get(hire["candidate_id"]) or {}
        items = [item for item in row["checklist_items"].values() if item.get("hire_id") == hire["id"]]
        items.sort(key=lambda item: (item.get("stage") or 0, item.get("template_key") or ""))
        return {
            "id": hire["id"],
            "candidate_id": hire["candidate_id"],
            "offer_id": hire["offer_id"],
            "display_name": candidate.get("display_name") or "",
            "position": hire.get("position") or "",
            "employment_type": hire.get("employment_type") or "",
            "start_date": hire.get("start_date") or "",
            "direct_client_access": bool(hire.get("direct_client_access")),
            "status": hire.get("status") or "",
            "first_day_worked_for_pay": hire.get("first_day_worked_for_pay") or "",
            "activated_at": hire.get("activated_at") or "",
            "employee_view_id": hire.get("employee_view_id") or "",
            "i9_drive": hire.get("i9_drive") or "pending",
            "packet_complete": bool(hire.get("packet_completed_at")),
            "welcome": _welcome_text(candidate.get("display_name") or "", hire),
            "items": [self._item_public(row, item) for item in items],
        }

    def _item_public(self, row: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        hire = row["hires"].get(item["hire_id"]) or {}
        warning = False
        if item.get("template_key") == "i9_section2" and item.get("status") == "open":
            first = str(hire.get("first_day_worked_for_pay") or "")
            if first:
                closed = closed_set(row["settings"].get("business_closed_days") or [])
                red_on = add_business_days(parse_iso(first), 2, closed)
                warning = _today() >= red_on
        flags = {"warning": False, "badge": ""}
        if item.get("template_key") in {"clearances", "exclusion_screening"}:
            due_text = str(item.get("due_date") or "")
            flags = clearance_state(
                direct=bool(hire.get("direct_client_access")),
                status=str(item.get("status") or ""),
                due=parse_iso(due_text) if due_text else None,
                today=_today(),
            )
            warning = flags["warning"]
        return {
            "id": item["id"],
            "template_key": item["template_key"],
            "stage": item["stage"],
            "owner_role": item["owner_role"],
            "title": item["title"],
            "kind": item.get("kind") or "",
            "due_date": item.get("due_date") or "",
            "status": item.get("status") or "open",
            "done_at": item.get("done_at") or "",
            "done_by": item.get("done_by") or "",
            "note": item.get("note") or "",
            "dates": dict(item.get("dates") or {}),
            "where_original_lives": item.get("where_original_lives") or "",
            "warning": warning or flags["warning"],
            "badge": flags["badge"],
        }

    def set_item(
        self,
        practice_id: str,
        hire_id: str,
        key: str,
        *,
        status: str,
        actor: str,
        dates: dict[str, str] | None = None,
        where: str = "",
        first_day: str = "",
        note: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"open", "done", "skipped", "n/a"}:
            raise HiringError(400, "Unknown item status.")
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            if not hire:
                raise HiringError(404, "Hire not found.")
            item = next((entry for entry in row["checklist_items"].values() if entry.get("hire_id") == hire_id and entry.get("template_key") == key), None)
            if not item:
                raise HiringError(404, "Checklist item not found.")
            if status == "done" and key in BLOCKED_DONE:
                raise HiringError(409, BLOCKED_DONE[key])
            if status == "done" and key == "supervision_agreement" and not row["settings"].get("supervision_agreement_approved"):
                raise HiringError(409, "That agreement needs counsel approval before it can be marked done.")
            if first_day:
                hire["first_day_worked_for_pay"] = date.fromisoformat(first_day[:10]).isoformat()
                closed = closed_set(row["settings"].get("business_closed_days") or [])
                due = add_business_days(parse_iso(hire["first_day_worked_for_pay"]), 3, closed)
                for entry in row["checklist_items"].values():
                    if entry.get("hire_id") == hire_id and entry.get("template_key") in {"i9_section2", "i9_scan_uploaded"}:
                        entry["due_date"] = to_iso(due)
            if dates:
                allowed = {"i9_section1_on", "i9_docs_examined_on", "i9_section2_completed_on", "examiner"}
                for name, value in dates.items():
                    if name not in allowed:
                        raise HiringError(400, "That date is not tracked.")
                    if name == "examiner":
                        item["dates"][name] = str(value or "").strip()[:120]
                    elif value:
                        item["dates"][name] = date.fromisoformat(str(value)[:10]).isoformat()
                    else:
                        item["dates"][name] = ""
            item["status"] = status
            if note is not None:
                item["note"] = str(note)[:300]
            item["where_original_lives"] = str(where or item.get("where_original_lives") or "")[:200]
            if status in {"done", "n/a"}:
                item["done_at"] = _iso()
                item["done_by"] = actor
            elif status == "open":
                item["done_at"] = ""
                item["done_by"] = ""
                item["note"] = ""
            self._history(row, "checklist", item["id"], "updated", actor, "Checklist item updated")
            self._save(payload)
            return self._hire_public(row, hire)

    def mark_packet_complete(self, practice_id: str, hire_id: str, actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            if not hire:
                raise HiringError(404, "Hire not found.")
            self._payroll_todo(practice_id, row, hire)
            hire["packet_completed_at"] = _iso()
            self._history(row, "hire", hire_id, "packet_complete", actor, "Packet marked complete")
            self._save(payload)
            return self._hire_public(row, hire)

    def undo_packet_complete(self, practice_id: str, hire_id: str, actor: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            if not hire:
                raise HiringError(404, "Hire not found.")
            hire["packet_completed_at"] = ""
            from app.platform.due import due_engine

            due_engine.complete(practice_id=practice_id, source="hiring-payroll", source_ref=str(hire_id) + ":packet")
            self._history(row, "hire", hire_id, "packet_reopened", actor, "Packet completion undone")
            self._save(payload)
            return self._hire_public(row, hire)

    def _payroll_todo(self, practice_id: str, row: dict[str, Any], hire: dict[str, Any]) -> None:
        from app.platform.due import due_engine

        candidate = row["candidates"].get(hire.get("candidate_id") or "") or {}
        name = str(candidate.get("display_name") or "New hire")
        closed = closed_set(row["settings"].get("business_closed_days") or [])
        start = parse_iso(str(hire.get("start_date") or _today().isoformat()))
        due_day = packet_due(start, _today(), closed)
        due_at = datetime(due_day.year, due_day.month, due_day.day, 23, 59, 59, tzinfo=EASTERN).isoformat()
        due_engine.upsert(
            practice_id=practice_id,
            domain="books",
            source="hiring-payroll",
            title=f"{name}: W-4, direct deposit, and the PA new-hire report",
            owner_user_id="billing",
            due_at=due_at,
            href="/#hiring",
            source_ref=str(hire.get("id") or "") + ":packet",
        )

    def upload_item(self, practice_id: str, hire_id: str, key: str, data: bytes, actor: str) -> dict[str, Any]:
        if key in STATUS_ONLY:
            raise HiringError(400, "That item records a status only.")
        mime = _sniff(data)
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            item = next((entry for entry in row["checklist_items"].values() if entry.get("hire_id") == hire_id and entry.get("template_key") == key), None)
            if not hire or not item:
                raise HiringError(404, "Checklist item not found.")
            if key in I9_KEYS or key == "i9_scan_uploaded":
                area, sub, sensitive = "i9", "i9", True
            elif key == "clearances":
                area, sub, sensitive = "personnel", "04-clearances", True
            else:
                area, sub, sensitive = "personnel", "01-onboarding", False
            version = len(item.get("document_ref_ids") or []) + 1
            stored = file_store().put(StorageKey(area, hire_id, sub, key, version), data, mime)
            ref = self._doc_ref(row, hire_id, item["id"], key, stored, actor, sensitive=sensitive)
            ref["version"] = version
            item.setdefault("document_ref_ids", []).append(ref["id"])
            item["status"] = "done"
            item["done_at"] = _iso()
            item["done_by"] = actor
            self._history(row, "document", ref["id"], "uploaded", actor, "Document stored")
            self._save(payload)
            return {"document_id": ref["id"], "hire": self._hire_public(row, hire)}

    def document_bytes(self, practice_id: str, document_id: str, actor: str, *, allow_sensitive: bool) -> tuple[bytes, str, str]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            ref = row["document_refs"].get(document_id)
            if not ref or ref.get("deleted_at"):
                raise HiringError(404, "Document not found.")
            if ref.get("sensitive") and not allow_sensitive:
                raise HiringError(403, "You do not have access to that.")
            data = file_store().get(_ref(ref["object_key"], ref.get("backend") or "local", ref.get("sha256") or "", ref.get("drive_file_id") or ""))
            self._history(row, "document", document_id, "opened", actor, "Document opened")
            self._save(payload)
            return data, ref.get("mime") or "application/octet-stream", f"{ref['doc_type']}.bin"

    def payroll_queue(self, practice_id: str) -> list[dict[str, Any]]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            queue = []
            for hire in row["hires"].values():
                if hire.get("status") == "archived":
                    continue
                candidate = row["candidates"].get(hire["candidate_id"]) or {}
                items = [
                    self._item_public(row, item)
                    for item in row["checklist_items"].values()
                    if item.get("hire_id") == hire["id"] and item.get("template_key") in PAYROLL_KEYS
                ]
                queue.append(
                    {
                        "hire_id": hire["id"],
                        "display_name": candidate.get("display_name") or "",
                        "start_date": hire.get("start_date") or "",
                        "items": items,
                    }
                )
            return queue

    def payroll_set(self, practice_id: str, hire_id: str, key: str, status: str, actor: str) -> dict[str, Any]:
        if key not in PAYROLL_KEYS:
            raise HiringError(403, "That item is not on the payroll handoff.")
        return self.set_item(practice_id, hire_id, key, status=status, actor=actor)

    def activate(self, practice_id: str, hire_id: str, actor: str, work_email: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            if not hire or hire.get("status") != "onboarding":
                raise HiringError(409, "That hire cannot be activated.")
            start = parse_iso(hire["start_date"])
            if start > _today():
                raise HiringError(409, "Activation is available on or after the start date.")
            if hire.get("direct_client_access"):
                clearance = next(
                    (
                        item
                        for item in row["checklist_items"].values()
                        if item.get("hire_id") == hire_id and item.get("template_key") == "clearances"
                    ),
                    None,
                )
                if not clearance or clearance.get("status") != "done":
                    raise HiringError(409, "PA clearances have to be marked received before activation.")
            candidate = row["candidates"][hire["candidate_id"]]
            email = (work_email or candidate.get("personal_email") or "").strip().lower()
            if not EMAIL_RX.match(email):
                raise HiringError(400, "A work email is required to activate.")
            offer = row["offers"].get(hire.get("offer_id") or "") or {}
            version = row["offer_versions"].get(offer.get("current_version_id") or "") or {}
            credentials = str((version.get("field_values") or {}).get("candidate_credentials") or "")
            self._handoff(practice_id, hire, candidate, email, actor, credentials)
            hire["status"] = "active"
            hire["activated_at"] = _iso()
            hire["work_email"] = email
            hire["employee_view_id"] = email
            self._void_tokens(row, "hire", hire_id, "activated")
            for item in row["checklist_items"].values():
                if item.get("hire_id") == hire_id and item.get("template_key") == "activate":
                    item["status"] = "done"
                    item["done_at"] = _iso()
                    item["done_by"] = actor
            self._history(row, "hire", hire_id, "activated", actor, "Hire activated")
            self._save(payload)
            return self._hire_public(row, hire)

    def delete_hire(self, practice_id: str, hire_id: str, confirm: str, actor: str) -> None:
        """Remove one hire after the exact name is typed. Other people stay."""
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            hire = row["hires"].get(hire_id)
            if not hire:
                raise HiringError(404, "Hire not found.")
            candidate = row["candidates"].get(hire.get("candidate_id") or "") or {}
            offer = row["offers"].get(hire.get("offer_id") or "") or {}
            version = row["offer_versions"].get(offer.get("current_version_id") or "") or {}
            legal = str((version.get("field_values") or {}).get("candidate_full_name") or "").strip()
            expected = str(candidate.get("display_name") or "").strip()
            confirm_name = str(confirm or "").strip()
            if not confirm_name or confirm_name not in {name for name in (expected, legal) if name}:
                raise HiringError(400, "Type the candidate's name to confirm.")
            self._trash_drive_ids([str(offer.get("drive_folder_id") or "")])
            for key in _DRIVE_OFFER_KEYS:
                if key == "drive_status" or key not in offer:
                    continue
                offer[key] = ""
            offer["drive_status"] = "trashed"
            for ref_id, ref in list((row.get("document_refs") or {}).items()):
                if ref.get("hire_id") != hire_id:
                    continue
                if str(ref.get("backend") or "local") == "local":
                    _delete_stored(file_store(), str(ref.get("object_key") or ""))
                row["document_refs"].pop(ref_id, None)
            for item_id, item in list(row["checklist_items"].items()):
                if item.get("hire_id") == hire_id:
                    row["checklist_items"].pop(item_id, None)
            self._void_tokens(row, "hire", hire_id, "deleted")
            row["hires"].pop(hire_id, None)
            from app.credentialing.store import credentialing_store
            from app.employees.store import employees_store
            from app.platform.due import due_engine

            credentialing_store.remove_created_by_hire(practice_id, hire_id)
            employees_store.remove_created_by_hire(practice_id, hire_id)
            due_engine.complete(practice_id=practice_id, source="hiring-payroll", source_ref=hire_id)
            due_engine.complete(practice_id=practice_id, source="hiring-payroll", source_ref=hire_id + ":packet")
            self._history(row, "hire", hire_id, "deleted", actor, "Hire deleted")
            self._save(payload)

    def _trash_drive_ids(self, file_ids: list[str]) -> None:
        from app.hiring.personnel_drive import personnel_client

        client = personnel_client()
        if client is None or not hasattr(client, "trash"):
            return
        for file_id in file_ids:
            if not file_id:
                continue
            try:
                client.trash(file_id)
            except Exception as exc:
                logger.warning("drive_trash_failed exception=%s", type(exc).__name__)

    def _handoff(
        self,
        practice_id: str,
        hire: dict[str, Any],
        candidate: dict[str, Any],
        email: str,
        actor: str,
        credentials: str = "",
    ) -> None:
        from app.credentialing.store import credentialing_store
        from app.employees.store import employees_store
        from app.platform.due import due_engine

        name = str(candidate.get("display_name") or "New hire")
        existing = None
        for subject in credentialing_store.list_subject_names(practice_id):
            if subject.get("name", "").strip().casefold() == name.casefold():
                existing = subject
                break
        created_keys: list[str] = []
        if existing:
            subject_id = existing["id"]
        else:
            created = credentialing_store.save_subject(
                practice_id,
                {"name": name, "kind": "clinician", "hire_date": hire.get("start_date") or ""},
                actor=actor,
            )
            subject_id = created["id"]
            grid = credentialing_store.public_grid(practice_id)
            subject = {"kind": "clinician", "license_type": ""}
            for cell in grid.get("cells") or []:
                if cell.get("subject_id") != subject_id or cell.get("enrollment_saved"):
                    continue
                payer = next((item for item in grid.get("payers") or [] if item.get("id") == cell.get("payer_id")), None)
                if not payer:
                    continue
                status = hire_cell_status(payer, credentials)
                try:
                    credentialing_store.save_enrollment(
                        practice_id,
                        {"subject_id": subject_id, "payer_id": payer["id"], "status": status, "note": ""},
                        actor=actor,
                    )
                    created_keys.append(f"{subject_id}|{payer['id']}")
                except Exception:
                    continue
            credentialing_store.mark_created_by_hire(practice_id, subject_id, hire["id"], created_keys)
        employee_existed = employees_store.has_record(practice_id, email)
        employees_store.save_contact(
            practice_id,
            email,
            {"preferred_name": name},
            actor,
        )
        employees_store.set_link(practice_id, email, subject_id, actor)
        if not employee_existed:
            employees_store.mark_created_by_hire(practice_id, email, hire["id"])
        hire["activation_created"] = {
            "subject_id": "" if existing else subject_id,
            "employee_email": "" if employee_existed else email,
        }
        due_engine.upsert(
            practice_id=practice_id,
            domain="books",
            source="hiring-payroll",
            title="Payroll handoff",
            owner_user_id=actor,
            due_at=_iso(),
            href="/#hiring-payroll",
            source_ref=hire["id"],
        )

    def form_flags_and_list(self, practice_id: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            self._save(payload)
            return {"forms": self._forms_public(row), "flags": self._form_flags(row)}

    def _forms_public(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": item.get("id") or "",
                "form_key": item.get("form_key") or "",
                "title": item.get("title") or "",
                "publisher": item.get("publisher") or "",
                "edition": item.get("edition") or "",
                "expires_on": item.get("expires_on") or "",
                "source_url": item.get("source_url") or "",
                "last_checked_on": item.get("last_checked_on") or "",
                "retired_at": item.get("retired_at") or "",
                "available": bool(item.get("filename")),
            }
            for item in row["forms_library"].values()
        ]

    def _form_flags(self, row: dict[str, Any]) -> list[str]:
        today = _today()
        flags = []
        for item in row["forms_library"].values():
            expires = str(item.get("expires_on") or "")
            if expires:
                try:
                    if date.fromisoformat(expires) < today:
                        flags.append(item.get("form_key") or "form")
                except ValueError:
                    pass
            checked = str(item.get("last_checked_on") or "")
            if checked:
                try:
                    if (today - date.fromisoformat(checked)).days > 183:
                        flags.append(item.get("form_key") or "form")
                except ValueError:
                    pass
            if item.get("january_check") and today.month == 1:
                flags.append(item.get("form_key") or "form")
        return sorted(set(flags))

    def form_bytes(self, practice_id: str, form_key: str) -> tuple[bytes, str, str]:
        with _lock:
            row = self._practice(self._read(), practice_id)
        match = next((item for item in row["forms_library"].values() if item.get("form_key") == form_key), None)
        if not match or not match.get("filename"):
            raise HiringError(404, "That form is not on file.")
        path = form_path(str(match["filename"]))
        if not path.is_file():
            raise HiringError(404, "That form is not on file.")
        mime = "application/pdf" if path.suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return path.read_bytes(), mime, path.name

    def purge_candidate(self, practice_id: str, candidate_id: str, actor: str) -> dict[str, int]:
        with _lock:
            payload = self._read()
            row = self._practice(payload, practice_id)
            candidate = row["candidates"].get(candidate_id)
            if not candidate:
                raise HiringError(404, "Candidate not found.")
            offer_ids = [offer["id"] for offer in row["offers"].values() if offer.get("candidate_id") == candidate_id]
            for offer in list(row["offers"].values()):
                if offer.get("id") in offer_ids:
                    self._drop_offer_resumes(row, offer)
            hire_ids = [hire["id"] for hire in row["hires"].values() if hire.get("candidate_id") == candidate_id]
            removed_files = 0
            store = file_store()
            for ref in list(row["document_refs"].values()):
                if ref.get("hire_id") in hire_ids or any(ref.get("object_key", "").find(offer_id) >= 0 for offer_id in offer_ids):
                    if not ref.get("deleted_at"):
                        try:
                            store.delete(_ref(ref["object_key"], ref.get("backend") or "local", ref.get("sha256") or ""))
                            removed_files += 1
                        except Exception:
                            pass
                    row["document_refs"].pop(ref["id"], None)
            for bucket, key in (
                ("offers", "id"),
                ("offer_versions", "offer_id"),
                ("acceptances", "offer_id"),
            ):
                for item_id, item in list(row[bucket].items()):
                    if item.get("offer_id") in offer_ids or item.get("id") in offer_ids:
                        row[bucket].pop(item_id, None)
            for item_id, item in list(row["checklist_items"].items()):
                if item.get("hire_id") in hire_ids:
                    row["checklist_items"].pop(item_id, None)
            for hire_id in hire_ids:
                row["hires"].pop(hire_id, None)
                self._void_tokens(row, "hire", hire_id, "purged")
            for offer_id in offer_ids:
                self._void_tokens(row, "offer", offer_id, "purged")
            for token_id, token in list(row["portal_tokens"].items()):
                if token.get("subject_id") in offer_ids or token.get("subject_id") in hire_ids:
                    row["portal_tokens"].pop(token_id, None)
            row["candidates"].pop(candidate_id, None)
            row["history"] = [
                item
                for item in row["history"]
                if item.get("entity_id") not in set(offer_ids + hire_ids + [candidate_id])
            ]
            self._history(row, "candidate", candidate_id, "purged", actor, "Test candidate removed")
            self._save(payload)
            return {"offers": len(offer_ids), "hires": len(hire_ids), "files": removed_files}

    def documents_for_email(self, practice_id: str, email: str) -> list[dict[str, str]]:
        address = (email or "").strip().lower()
        with _lock:
            row = self._practice(self._read(), practice_id)
        groups = {
            "signed-offer": "Offer and agreements",
            "packet_signed": "Offer and agreements",
            "license_verification": "Education and license",
            "resume_degree": "Education and license",
            "clearances": "Clearances and training",
            "act31_training": "Clearances and training",
            "liability_cert": "Insurance",
        }
        found = []
        for hire in row["hires"].values():
            if str(hire.get("work_email") or "").lower() != address and str(hire.get("employee_view_id") or "").lower() != address:
                continue
            for ref in row["document_refs"].values():
                if ref.get("hire_id") != hire["id"] or ref.get("sensitive") or ref.get("deleted_at"):
                    continue
                found.append(
                    {
                        "id": ref["id"],
                        "group": groups.get(ref.get("doc_type") or "", "Offer and agreements"),
                        "doc_type": ref.get("doc_type") or "",
                    }
                )
        return found

    # --- portal ---

    def rate_limit(self, key: str, *, limit: int, window: int) -> None:
        now = _now().timestamp()
        hits = [item for item in self._hits.get(key, []) if now - item < window]
        if len(hits) >= limit:
            raise HiringError(429, "Too many attempts. Wait a few minutes.")
        hits.append(now)
        self._hits[key] = hits

    def open_token(self, raw_token: str, ip: str, user_agent: str) -> str:
        digest = _sha(raw_token)
        with _lock:
            payload = self._read()
            for practice_id, row in payload.get("practices", {}).items():
                if not isinstance(row, dict):
                    continue
                self._expire_row(row)
                token = next((item for item in row.get("portal_tokens", {}).values() if item.get("token_sha256") == digest), None)
                if not token:
                    continue
                if token.get("revoked_at"):
                    raise HiringError(410, "This link is no longer active.")
                if token.get("expires_at") and _parse_dt(token["expires_at"]) < _now():
                    token["revoked_at"] = _iso()
                    token["revoke_reason"] = "expired"
                    self._save(payload)
                    raise HiringError(410, "This link is no longer active.")
                secret = secrets.token_urlsafe(32)
                session = {
                    "id": _new_id("ses-"),
                    "secret_sha256": _sha(secret),
                    "practice_id": practice_id,
                    "token_id": token["id"],
                    "subject": token["subject"],
                    "subject_id": token["subject_id"],
                    "verified": bool(row["settings"].get("qa_portal_bypass")),
                    "idle_until": (_now() + timedelta(minutes=15)).isoformat(),
                    "ip": ip,
                    "user_agent": (user_agent or "")[:300],
                }
                row["portal_sessions"][session["id"]] = session
                self._save(payload)
                return secret
        raise HiringError(404, "This link is not valid.")

    def _session(self, payload: dict[str, Any], secret: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        digest = _sha(secret)
        for practice_id, row in payload.get("practices", {}).items():
            if not isinstance(row, dict):
                continue
            for session in row.get("portal_sessions", {}).values():
                if session.get("secret_sha256") == digest:
                    if session.get("idle_until") and session["idle_until"] < _iso():
                        raise HiringError(401, "That session timed out. Open the link again.")
                    session["idle_until"] = (_now() + timedelta(minutes=15)).isoformat()
                    return str(practice_id), row, session
        raise HiringError(401, "Open the link again.")

    def request_code(self, secret: str, base_url: str) -> None:
        _ = base_url
        with _lock:
            payload = self._read()
            practice_id, row, session = self._session(payload, secret)
            if session.get("verified"):
                self._save(payload)
                return
            token = row["portal_tokens"].get(session["token_id"]) or {}
            if token.get("revoked_at"):
                raise HiringError(410, "This link is no longer active.")
            offer = self._subject_offer(row, session)
            candidate = row["candidates"].get(offer["candidate_id"]) if offer else None
            if session["subject"] == "hire":
                hire = row["hires"].get(session["subject_id"]) or {}
                candidate = row["candidates"].get(hire.get("candidate_id") or "")
            address = str((candidate or {}).get("personal_email") or "")
            if not self._email_allowed(row, address):
                raise HiringError(409, "Email confirmation is turned off. Ask the office to record your answer.")
            locked = session.get("locked_until") or ""
            if locked and locked > _iso():
                raise HiringError(429, "Too many attempts. Try again in a few minutes.")
            code = f"{secrets.randbelow(1000000):06d}"
            challenge = {
                "id": _new_id("otp-"),
                "token_id": session["token_id"],
                "session_id": session["id"],
                "code_hash": _sha(code),
                "expires_at": (_now() + timedelta(minutes=10)).isoformat(),
                "attempts": 0,
                "locked_until": "",
            }
            row["otp_challenges"][challenge["id"]] = challenge
            session["challenge_id"] = challenge["id"]
            status = deliver(practice_id, address, "Your sign-in code", code_body(code))
            row["mail_queue"].append(public_queue_row("otp", session["subject_id"], _iso(), status))
            if status != "sent":
                raise HiringError(409, "The sign-in code could not be sent.")
            self._save(payload)

    def verify_code(self, secret: str, code: str) -> None:
        with _lock:
            payload = self._read()
            _practice, row, session = self._session(payload, secret)
            challenge = row["otp_challenges"].get(session.get("challenge_id") or "")
            if not challenge:
                raise HiringError(400, "Request a code first.")
            if challenge.get("locked_until") and challenge["locked_until"] > _iso():
                raise HiringError(429, "Too many attempts. Try again in a few minutes.")
            if challenge.get("expires_at") < _iso():
                raise HiringError(400, "That code expired. Request a new one.")
            if not hmac.compare_digest(challenge["code_hash"], _sha(str(code or "").strip())):
                challenge["attempts"] = int(challenge.get("attempts") or 0) + 1
                if challenge["attempts"] >= 5:
                    challenge["locked_until"] = (_now() + timedelta(minutes=15)).isoformat()
                    session["locked_until"] = challenge["locked_until"]
                self._save(payload)
                raise HiringError(400, "That code is not right.")
            session["verified"] = True
            self._mark_viewed(row, session)
            self._save(payload)

    def _mark_viewed(self, row: dict[str, Any], session: dict[str, Any]) -> None:
        if session.get("subject") != "offer":
            return
        offer = row["offers"].get(session.get("subject_id"))
        if offer and offer.get("status") == "sent":
            offer["status"] = "viewed"
            offer["viewed_at"] = _iso()
            self._history(row, "offer", offer["id"], "viewed", "candidate", "Offer opened", ip=session.get("ip") or "")

    def _subject_offer(self, row: dict[str, Any], session: dict[str, Any]) -> Optional[dict[str, Any]]:
        if session.get("subject") == "offer":
            return row["offers"].get(session.get("subject_id"))
        return None

    def portal_state(self, secret: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            _practice, row, session = self._session(payload, secret)
            self._expire_row(row)
            self._save(payload)
            if not session.get("verified"):
                return {"verified": False, "needs_code": True}
            if session.get("subject") == "offer":
                offer = row["offers"].get(session["subject_id"])
                if not offer or offer["status"] not in {"sent", "viewed"}:
                    raise HiringError(410, "This link is no longer active.")
                candidate = row["candidates"].get(offer["candidate_id"]) or {}
                version = row["offer_versions"].get(offer["current_version_id"]) or {}
                return {
                    "verified": True,
                    "kind": "offer",
                    "display_name": candidate.get("display_name") or "",
                    "deadline": offer.get("response_deadline") or "",
                    "position": (version.get("field_values") or {}).get("position_title") or "",
                }
            hire = row["hires"].get(session["subject_id"])
            if not hire:
                raise HiringError(410, "This link is no longer active.")
            candidate = row["candidates"].get(hire["candidate_id"]) or {}
            items = [
                self._item_public(row, item)
                for item in row["checklist_items"].values()
                if item.get("hire_id") == hire["id"] and item.get("owner_role") == "candidate" and item.get("template_key") not in I9_KEYS
            ]
            return {
                "verified": True,
                "kind": "hire",
                "display_name": candidate.get("display_name") or "",
                "start_date": hire.get("start_date") or "",
                "items": items,
            }

    def portal_pdf(self, secret: str) -> tuple[bytes, str]:
        with _lock:
            payload = self._read()
            _practice, row, session = self._session(payload, secret)
            if not session.get("verified"):
                raise HiringError(401, "Enter the sign-in code first.")
            offer = self._subject_offer(row, session)
            if not offer:
                raise HiringError(404, "The letter is not available.")
            version = row["offer_versions"][offer["current_version_id"]]
            data = file_store().get(_ref(version["pdf_object_key"], "local", version["pdf_sha256"]))
            return data, f"{offer['id']}.pdf"

    def portal_accept(self, secret: str, typed_name: str, ip: str, user_agent: str) -> dict[str, Any]:
        with _lock:
            payload = self._read()
            practice_id, row, session = self._session(payload, secret)
            if not session.get("verified"):
                raise HiringError(401, "Enter the sign-in code first.")
            offer = self._subject_offer(row, session)
            if not offer or offer["status"] not in {"sent", "viewed"}:
                raise HiringError(410, "This link is no longer active.")
            hire = self._accept(
                row,
                offer,
                typed_name,
                method="portal",
                actor="candidate",
                ip=ip,
                user_agent=user_agent,
                upload=None,
                practice_id=practice_id,
            )
            session["subject"] = "hire"
            session["subject_id"] = hire["id"]
            self._queue_drive(payload, practice_id, offer["id"], "candidate", pdf=True, filing=True)
            return {"ok": True, "hire_id": hire["id"]}

    def portal_decline(self, secret: str, reason: str, note: str, ip: str) -> None:
        if reason not in DECLINE_REASONS:
            raise HiringError(400, "Unknown decline reason.")
        with _lock:
            payload = self._read()
            _practice, row, session = self._session(payload, secret)
            if not session.get("verified"):
                raise HiringError(401, "Enter the sign-in code first.")
            offer = self._subject_offer(row, session)
            if not offer or offer["status"] not in {"sent", "viewed"}:
                raise HiringError(410, "This link is no longer active.")
            offer["status"] = "declined"
            offer["decline_reason"] = reason
            offer["decline_note"] = str(note or "").strip()[:300]
            offer["closed_at"] = _iso()
            self._void_tokens(row, "offer", offer["id"], "declined")
            self._history(row, "offer", offer["id"], "declined", "candidate", "Offer declined", ip=ip)
            self._save(payload)

    def counts(self, practice_id: str) -> dict[str, int]:
        with _lock:
            row = self._practice(self._read(), practice_id)
            return {
                "candidates": len(row["candidates"]),
                "offers": len(row["offers"]),
                "hires": len(row["hires"]),
                "files": len(row["document_refs"]),
            }


def _norm(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _mdy(day: date) -> str:
    return day.strftime("%m/%d/%Y")


def _end_of_deadline(iso_date: str) -> str:
    day = date.fromisoformat(iso_date[:10])
    end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=EASTERN)
    return end.astimezone(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _drive_connected() -> bool:
    from app.hiring.personnel_drive import personnel_configured

    return personnel_configured()


def _delete_stored(store: Any, object_key: str) -> None:
    if not object_key or "/" not in object_key:
        return
    try:
        store.delete(_ref(object_key, "local", ""))
    except Exception:
        return


def _ref(object_key: str, backend: str, sha: str, drive_file_id: str = ""):
    from app.hiring.storage import StoredRef

    return StoredRef(
        backend=backend or "local",
        object_key=object_key,
        sha256=sha,
        size=0,
        mime="",
        drive_file_id=drive_file_id,
    )


def _sniff(data: bytes) -> str:
    if len(data) > MAX_UPLOAD:
        raise HiringError(400, "That file is larger than 15 MB.")
    if data.startswith(b"%PDF"):
        if b"/JavaScript" in data or b"/JS" in data:
            raise HiringError(400, "That PDF cannot be stored.")
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise HiringError(400, "Upload a PDF, JPG, or PNG.")


def _acceptance_public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id") or "",
        "typed_name": item.get("typed_name") or "",
        "accepted_at": item.get("accepted_at") or "",
        "ip": item.get("ip") or "",
        "user_agent": item.get("user_agent") or "",
        "pdf_sha256": item.get("pdf_sha256") or "",
        "method": item.get("method") or "",
        "offer_version_id": item.get("offer_version_id") or "",
        "accepted_on": item.get("accepted_on") or "",
    }


def _acceptance_day(value: str) -> date:
    if not str(value or "").strip():
        return _today()
    normalized = _normalize_date(value)
    if not normalized:
        raise HiringError(400, "Acceptance date must be a real date.", "accepted_on")
    return datetime.strptime(normalized, "%m/%d/%Y").date()


def _welcome_text(name: str, hire: dict[str, Any]) -> str:
    who = name.strip() or "there"
    start = hire.get("start_date") or "[start date]"
    position = hire.get("position") or "[position]"
    return (
        f"Hello {who},\n\n"
        f"Welcome. Your start date is {start} and your position is {position}.\n"
        "The new-hire packet is attached for you to complete and return. "
        "Payroll will send a separate invite for the W-4 and direct deposit.\n"
        "Bring the identity documents you choose for the Form I-9. "
        "We will not tell you which documents to bring.\n"
        "Heart for Change will never ask for a Social Security number or bank details by email."
    )


hiring = HiringEngine()


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started or os.environ.get("ATTUNE_HIRING_SCHEDULER") == "off":
        return
    _scheduler_started = True

    def _loop() -> None:
        while True:
            threading.Event().wait(3600)
            try:
                result = hiring.tick()
                print(
                    f"hiring_tick expired={result['expired']} reminded={result['reminded']}",
                    flush=True,
                )
            except Exception as exc:
                print(f"hiring_tick_error {type(exc).__name__}", flush=True)

    threading.Thread(target=_loop, name="hiring-tick", daemon=True).start()
