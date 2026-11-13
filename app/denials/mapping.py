"""Column mapping for a de-identified SimplePractice-style export."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.denials.deidentify import norm_header, scrub_remark

FILE_TYPES = ("filed_claims", "payment_reports")

FIELDS: tuple[dict[str, object], ...] = (
    {"id": "payer", "label": "Payer", "required": False},
    {"id": "cpt", "label": "CPT / HCPCS", "required": False},
    {"id": "modifiers", "label": "Modifiers", "required": False},
    {"id": "pos", "label": "Place of service", "required": False},
    {"id": "diagnosis", "label": "Diagnosis code", "required": False},
    {"id": "service_month", "label": "Service month (YYYY-MM)", "required": False},
    {"id": "billed", "label": "Billed amount", "required": False},
    {"id": "allowed", "label": "Allowed amount", "required": False},
    {"id": "paid", "label": "Paid amount", "required": False},
    {"id": "claim_status", "label": "Claim status", "required": True},
    {"id": "carc", "label": "CARC", "required": False},
    {"id": "rarc", "label": "RARC", "required": False},
    {"id": "remark", "label": "Remark text", "required": False},
    {"id": "clinician_code", "label": "Clinician code", "required": False},
    {"id": "claim_key", "label": "Claim key", "required": True},
    {"id": "status_date", "label": "Status date (YYYY-MM)", "required": False},
)

_SYNONYMS: dict[str, str] = {
    "payer": "payer",
    "payer name": "payer",
    "insurance": "payer",
    "insurance company": "payer",
    "plan": "payer",
    "plan name": "payer",
    "carrier": "payer",
    "cpt": "cpt",
    "hcpcs": "cpt",
    "cpt hcpcs": "cpt",
    "procedure": "cpt",
    "procedure code": "cpt",
    "billing code": "cpt",
    "service code": "cpt",
    "modifier": "modifiers",
    "modifiers": "modifiers",
    "mod": "modifiers",
    "pos": "pos",
    "place of service": "pos",
    "place of service code": "pos",
    "diagnosis": "diagnosis",
    "diagnosis code": "diagnosis",
    "dx": "diagnosis",
    "icd": "diagnosis",
    "icd 10": "diagnosis",
    "service month": "service_month",
    "month of service": "service_month",
    "dos month": "service_month",
    "billed": "billed",
    "billed amount": "billed",
    "charge": "billed",
    "charges": "billed",
    "billed charges": "billed",
    "allowed": "allowed",
    "allowed amount": "allowed",
    "paid": "paid",
    "paid amount": "paid",
    "payment": "paid",
    "insurance payment": "paid",
    "claim status": "claim_status",
    "status": "claim_status",
    "carc": "carc",
    "reason code": "carc",
    "adjustment reason": "carc",
    "denial code": "carc",
    "rarc": "rarc",
    "remark code": "rarc",
    "remark": "remark",
    "remark text": "remark",
    "denial reason": "remark",
    "message": "remark",
    "clinician": "clinician_code",
    "clinician code": "clinician_code",
    "provider code": "clinician_code",
    "rendering provider code": "clinician_code",
    "claim key": "claim_key",
    "claim id": "claim_key",
    "claim number": "claim_key",
    "claim": "claim_key",
    "status date": "status_date",
    "payment date": "status_date",
    "adjudication date": "status_date",
}

_FIELD_IDS = {str(item["id"]) for item in FIELDS}
_MONEY = {"billed", "allowed", "paid"}
_MONTH = {"service_month", "status_date"}


def suggest(headers: list[str]) -> dict[str, str]:
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for header in headers:
        field = _SYNONYMS.get(norm_header(header), "")
        if field and field not in used:
            mapping[header] = field
            used.add(field)
    return mapping


def clean_mapping(raw: dict[str, str] | None, headers: list[str]) -> dict[str, str]:
    """Keep only known fields for columns that exist on this file."""
    incoming = raw or {}
    cleaned: dict[str, str] = {}
    used: set[str] = set()
    allowed_headers = set(headers)
    for header, field in incoming.items():
        name = str(header)
        target = str(field or "").strip()
        if name not in allowed_headers or target not in _FIELD_IDS or target in used:
            continue
        cleaned[name] = target
        used.add(target)
    missing = [item["label"] for item in FIELDS if item["required"] and item["id"] not in used]
    if missing:
        raise ValueError("Map " + " and ".join(str(item).lower() for item in missing) + " before loading.")
    return cleaned


def _money(value: str) -> str:
    text = (value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return "0.00"
    try:
        return f"{Decimal(text).quantize(Decimal('0.01'))}"
    except InvalidOperation:
        return "0.00"


def _month(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 7 and text[4] == "-" and text[:4].isdigit() and text[5:7].isdigit():
        return text[:7]
    return ""


def normalize_status(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"denied", "denial", "denied claim"}:
        return "denied"
    if "reject" in text:
        return "rejected"
    if text in {"paid", "payment", "processed", "accepted"}:
        return "paid"
    return text[:40]


def apply_mapping(headers: list[str], rows: list[list[str]], mapping: dict[str, str]) -> list[dict[str, str]]:
    index = {header: position for position, header in enumerate(headers)}
    parsed: list[dict[str, str]] = []
    for row in rows:
        item = {str(field["id"]): "" for field in FIELDS}
        for header, field in mapping.items():
            position = index.get(header)
            if position is None or position >= len(row):
                continue
            raw = row[position]
            if field in _MONEY:
                item[field] = _money(raw)
            elif field in _MONTH:
                item[field] = _month(raw)
            elif field == "remark":
                item[field] = scrub_remark(raw)
            elif field == "claim_status":
                item[field] = normalize_status(raw)
            else:
                item[field] = str(raw or "").strip()[:80]
        if not item["claim_key"] or not item["claim_status"]:
            continue
        parsed.append(item)
    return parsed
