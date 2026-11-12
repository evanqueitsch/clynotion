"""Human history lines for Employee View and Credentialing.

Stored audit rows stay as they were written. This module only builds a
display sentence at read time. Secret values are masked in that sentence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.platform.catalog import CREDENTIAL_CLOCKS

EASTERN = ZoneInfo("America/New_York")

FIELD_LABELS: dict[str, str] = {
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
    "tin": "TIN",
    "license_number": "License number",
    "caqh_id": "CAQH ID",
    "taxonomy": "Taxonomy",
    "promise_id": "Promise ID",
    "license_expires_on": "License expiration",
    "caqh_attested_on": "CAQH attestation",
    "license_renewal": "License renewal",
    "name": "Name",
    "kind": "Kind",
    "hire_date": "Hire date",
    "device": "Device",
    "license_state": "License state",
    "supervisor_id": "Supervisor",
    "legal_name": "Legal name",
    "service_address": "Service address",
    "active": "Active",
    "status": "Status",
    "submitted_on": "Submitted on",
    "effective_on": "Effective on",
    "expires_on": "Expiration",
    "has_expiration": "Credentials expire",
    "next_follow_up": "Next follow-up",
    "note": "Note",
    "payer_provider_id": "Payer provider ID",
}

for _clock in CREDENTIAL_CLOCKS:
    FIELD_LABELS.setdefault(str(_clock["id"]), str(_clock["name"]))

SECRET_FIELDS = frozenset(
    {
        "npi",
        "tin",
        "license_number",
        "caqh_id",
        "taxonomy",
        "promise_id",
        "payer_provider_id",
        "street",
        "city",
        "state",
        "zip",
        "personal_phone",
        "personal_email",
        "work_phone",
        "emergency_name",
        "emergency_phone",
        "service_address",
    }
)

DATE_FIELDS = frozenset(
    {
        "hire_date",
        "license_expires_on",
        "caqh_attested_on",
        "submitted_on",
        "effective_on",
        "expires_on",
        "next_follow_up",
        *(str(item["id"]) for item in CREDENTIAL_CLOCKS),
    }
)

STATUS_LABELS = {
    "unknown": "Unknown",
    "pending": "Pending",
    "in_network": "In network",
    "not_eligible": "Not eligible",
    "needs_review": "Needs review",
}

_SECRET_TOKENS = {
    "set": "a saved value",
    "changed": "a new saved value",
    "saved": "a saved value",
    "clear": "(empty)",
    "cleared": "(empty)",
}


def field_label(field: str) -> str:
    key = str(field or "").strip()
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else "Record"


def clock_labels() -> dict[str, str]:
    return {str(item["id"]): str(item["name"]) for item in CREDENTIAL_CLOCKS}


def change_fields() -> list[dict[str, str]]:
    """Approval fields with the labels the request form and queue should show."""
    rows = [
        ("license_type", "License type"),
        ("npi", "NPI"),
        ("license_number", "License number"),
        ("caqh_id", "CAQH ID"),
        ("license_expires_on", "License expiration"),
        ("caqh_attested_on", "CAQH attestation"),
    ]
    seen = {item[0] for item in rows}
    for clock in CREDENTIAL_CLOCKS:
        clock_id = str(clock["id"])
        if clock_id in seen:
            continue
        rows.append((clock_id, str(clock["name"])))
        seen.add(clock_id)
    return [{"id": item_id, "label": label} for item_id, label in rows]


def format_when(iso: str) -> str:
    text = str(iso or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    else:
        parsed = parsed.astimezone(EASTERN)
    hour = parsed.hour % 12 or 12
    return f"{parsed.month}/{parsed.day}/{parsed.year}, {hour}:{parsed.strftime('%M %p')} ET"


def format_us_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return ""
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def _mask_secret(value: str) -> str:
    token = value.strip().casefold()
    if token in _SECRET_TOKENS:
        return _SECRET_TOKENS[token]
    if token in {"", "empty", "(empty)"}:
        return "(empty)"
    last = value[-4:] if len(value) > 4 else value
    return "•••••• " + last


def display_value(field: str, value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "(empty)"
    text = str(value).strip()
    if text == "" or text.casefold() in {"none", "null"}:
        return "(empty)"
    if field in SECRET_FIELDS:
        return _mask_secret(text)
    if field == "has_expiration":
        if text.casefold() in {"true", "yes", "1"}:
            return "yes"
        if text.casefold() in {"false", "no", "0"}:
            return "no"
    if field in DATE_FIELDS:
        shown = format_us_date(text)
        return shown or text
    if field == "status":
        return STATUS_LABELS.get(text, text.replace("_", " "))
    if field == "active":
        if text.casefold() in {"true", "yes", "1"}:
            return "yes"
        if text.casefold() in {"false", "no", "0"}:
            return "no"
    if text[:1] in {"{", "["}:
        return "updated"
    return text


def summary_line(at: str, actor: str, summary: str) -> str:
    text = str(summary or "").strip() or "Updated the record"
    readable = text[:1].lower() + text[1:]
    when = format_when(at)
    who = str(actor or "").strip()
    if when and who:
        return f"{when} — {who} {readable}."
    if when:
        return f"{when} — {readable}."
    if who:
        return f"{who} {readable}."
    return readable + "."


def format_history_line(item: dict[str, Any]) -> str:
    """One plain-language entry. Does not change ``item``."""
    changes = item.get("changes")
    when = format_when(str(item.get("at") or ""))
    actor = str(item.get("actor") or item.get("by") or "").strip()
    if isinstance(changes, dict) and changes:
        source = str(item.get("source") or "").strip().lower()
        verb = "imported" if source == "import" else "changed"
        clauses = []
        for key, delta in changes.items():
            if isinstance(delta, dict):
                old_shown = display_value(str(key), delta.get("old"))
                new_shown = display_value(str(key), delta.get("new"))
                if old_shown == new_shown:
                    continue
                clauses.append(f"{field_label(str(key))} from {old_shown} to {new_shown}")
            else:
                clauses.append(f"{field_label(str(key))} updated")
        if len(clauses) == 1:
            detail = f"{verb} {clauses[0]}"
        else:
            detail = verb + " " + "; ".join(clauses)
    else:
        field = str(item.get("field") or "")
        label = field_label(field) if field else "the record"
        action = str(item.get("action") or "updated")
        if action == "cleared":
            detail = f"cleared {label}"
        elif action == "requested":
            detail = f"requested a change to {label}"
        else:
            detail = f"updated {label}"
    if when and actor:
        return f"{when} — {actor} {detail}."
    if when:
        return f"{when} — {detail}."
    if actor:
        return f"{actor} {detail}."
    return detail + "."
