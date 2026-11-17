"""Offer-letter merge. Port of the fill_offer prototype, plus a real-date rule
and an optional licensed rate.

The signer's signature image is never rewritten. Placeholders split across
Word runs are joined into the first run so its formatting stays.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lxml import etree

ASSETS = Path(__file__).resolve().parent / "assets"
TEMPLATE_PATH = ASSETS / "H4C_Offer_Letter_TEMPLATE.docx"
FIELDS_PATH = ASSETS / "offer_fields.json"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
PH = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
LEFTOVER = re.compile(r"\{\{[^{}]{0,80}\}\}")
DATE_RX = re.compile(r"^\d{2}/\d{2}/\d{4}$")
LABELS = {
    "employment_type": "Employment type",
    "offer_date": "Offer date",
    "candidate_full_name": "Full legal name",
    "candidate_credentials": "Credentials",
    "candidate_first_name": "First name",
    "position_title": "Position",
    "supervisor_title": "Supervisor title",
    "supervisor_name": "Supervisor name",
    "hourly_rate": "Hourly rate",
    "licensed_rate": "Licensed rate",
    "caseload_range": "Caseload range",
    "qsehra_monthly_amount": "QSEHRA monthly amount",
    "qsehra_wait_days": "QSEHRA wait days",
    "start_date": "Start date",
    "response_deadline": "Response deadline",
}
MONEY_WITH_CENTS = {"hourly_rate", "licensed_rate"}
FORMATS = {
    "hourly_rate": r"^\$\d{1,3}(,\d{3})*\.\d{2}$",
    "licensed_rate": r"^\$\d{1,3}(,\d{3})*\.\d{2}$",
    "qsehra_monthly_amount": r"^\$\d{1,3}(,\d{3})*(\.\d{2})?$",
    "qsehra_wait_days": r"^\d{1,3}$",
    "offer_date": r"^\d{2}/\d{2}/\d{4}$",
    "start_date": r"^\d{2}/\d{2}/\d{4}$",
    "response_deadline": r"^\d{2}/\d{2}/\d{4}$",
    "caseload_range": r"^\d{1,2}–\d{1,2}$",
}
BANNED_KEYS = re.compile(
    r"ssn|social_security|date_of_birth|\bdob\b|birth_date|routing|"
    r"account_number|passport|driver_license|id_number|bank_account",
    re.IGNORECASE,
)
SSN_SHAPE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


class OfferFillError(ValueError):
    """A letter cannot be generated. The message is safe to show to Admin."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


def _label(name: str) -> str:
    return LABELS.get(name, "That field")


def _die(message: str, field: str = "") -> None:
    raise OfferFillError(message, field)


def load_fields_spec(path: Path | None = None) -> dict[str, Any]:
    import json

    raw = (path or FIELDS_PATH).read_text(encoding="utf-8")
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        _die("fields spec must be an object")
    return spec


def supervisor_name_default(spec: dict[str, Any] | None = None) -> str:
    """The template's current supervisor default. Not hard-coded in Python."""
    data = spec or load_fields_spec()
    for field in data.get("fields") or []:
        if isinstance(field, dict) and field.get("name") == "supervisor_name":
            return str(field.get("default") or "")
    return ""


def asset_hashes() -> tuple[str, str]:
    return (
        hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest(),
        hashlib.sha256(FIELDS_PATH.read_bytes()).hexdigest(),
    )


def load_values(vals: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """Prototype validation order, then Clynotion's real-date rule."""
    if not isinstance(vals, dict):
        _die("values JSON must be an object")
    for key in vals:
        if BANNED_KEYS.search(str(key)):
            _die("that field is not collected here")
    known = {f["name"]: f for f in spec["fields"]}
    derived = {f["name"] for f in spec.get("derived", [])}
    bad = sorted(set(vals) & derived)
    if bad:
        _die("That value is filled in automatically.")
    unknown = sorted(set(vals) - set(known))
    if unknown:
        _die("That form has a field this letter does not use.")
    out: dict[str, str] = {}
    for name, field in known.items():
        value = vals.get(name)
        value = "" if value is None else str(value).strip()
        if not value and field.get("default") and field["default"] != "from employment_type":
            value = str(field["default"])
        if not value and not field.get("optional") and not field.get("full_time_only"):
            _die(f"{_label(name)} is required.", name)
        if "{{" in value or "}}" in value:
            _die(f"{_label(name)} cannot include braces.", name)
        if value and SSN_SHAPE.search(value):
            _die(f"{_label(name)} cannot include an identity number.", name)
        out[name] = value
    types = spec["employment_types"]
    employment = out["employment_type"]
    if employment not in types:
        _die("Employment type must be full-time or part-time.", "employment_type")
    for name in types[employment].get("fields_not_allowed", []):
        if out.get(name):
            _die(f"{_label(name)} is only for full-time hires.", name)
        out.pop(name, None)
    for name, field in known.items():
        if (
            field.get("full_time_only")
            and name not in types[employment].get("fields_not_allowed", [])
            and not out.get(name)
        ):
            _die(f"{_label(name)} is required for full-time hires.", name)
    if not out["position_title"]:
        out["position_title"] = types[employment]["position_title_default"]
    _normalize_formatted(out)
    for name, pattern in FORMATS.items():
        if out.get(name) and not re.fullmatch(pattern, out[name]):
            _die(_format_message(name), name)
    for label in ("start_date", "response_deadline", "offer_date"):
        if out.get(label):
            _require_real_date(label, out[label])
    schedule = types[employment]["schedule_paragraph"]
    for key in ("caseload_range", "supervisor_title"):
        schedule = schedule.replace("{{" + key + "}}", out[key])
    out["schedule_paragraph"] = schedule
    out["benefit_time_off"] = types[employment]["benefit_time_off"]
    out["benefit_license_ceu"] = types[employment]["benefit_license_ceu"]
    licensed = out.get("licensed_rate") or ""
    if licensed:
        out["licensed_rate_clause"] = (
            f"The pre-licensure rate of {out['hourly_rate']} per hour applies until licensure, "
            f"and the licensed rate of {licensed} per hour applies from then on. "
        )
    else:
        out["licensed_rate_clause"] = ""
    out["_remove"] = list(types[employment].get("remove_paragraphs_starting_with", []))
    return out


def _format_message(name: str) -> str:
    if name in MONEY_WITH_CENTS or name == "qsehra_monthly_amount":
        return f"{_label(name)} must be a dollar amount."
    if name == "qsehra_wait_days":
        return "QSEHRA wait days must be a whole number of days."
    if name == "caseload_range":
        return "Caseload range must be a range such as 25–30."
    if name in {"offer_date", "start_date", "response_deadline"}:
        return f"{_label(name)} must be a real date."
    return f"{_label(name)} is not in the right form."


def _normalize_formatted(out: dict[str, str]) -> None:
    for name in ("hourly_rate", "licensed_rate", "qsehra_monthly_amount"):
        if not out.get(name):
            continue
        normalized = _normalize_money(out[name], cents=name in MONEY_WITH_CENTS)
        if normalized is None:
            _die(_format_message(name), name)
        out[name] = normalized
    if out.get("qsehra_wait_days"):
        days = _normalize_days(out["qsehra_wait_days"])
        if days is None:
            _die(_format_message("qsehra_wait_days"), "qsehra_wait_days")
        out["qsehra_wait_days"] = days
    if out.get("caseload_range"):
        caseload = _normalize_caseload(out["caseload_range"])
        if caseload is None:
            _die(_format_message("caseload_range"), "caseload_range")
        out["caseload_range"] = caseload
    for name in ("offer_date", "start_date", "response_deadline"):
        if not out.get(name):
            continue
        normalized = _normalize_date(out[name])
        if normalized is None:
            _die(_format_message(name), name)
        out[name] = normalized


def _normalize_money(value: str, *, cents: bool) -> str | None:
    text = value.strip().replace("$", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    if cents:
        return "$" + f"{amount.quantize(Decimal('0.01')):,.2f}"
    if amount == amount.to_integral():
        return "$" + f"{int(amount):,}"
    return "$" + f"{amount.quantize(Decimal('0.01')):,.2f}"


def _normalize_days(value: str) -> str | None:
    text = value.strip().lower()
    if text.endswith("days"):
        text = text[: -len("days")].strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    number = int(text)
    if number > 999:
        return None
    return str(number)


def _normalize_caseload(value: str) -> str | None:
    text = value.strip().replace("—", "-").replace("–", "-")
    text = re.sub(r"\s+to\s+", "-", text, flags=re.IGNORECASE)
    match = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", text)
    if not match:
        return None
    return f"{int(match.group(1))}–{int(match.group(2))}"


def _normalize_date(value: str) -> str | None:
    text = value.strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return None
    try:
        parsed = datetime(int(match.group(3)), int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None
    return parsed.strftime("%m/%d/%Y")


def _require_real_date(label: str, value: str) -> None:
    """Clynotion does not accept TBD or ASAP. A real calendar date is required."""
    if not DATE_RX.match(value or ""):
        _die(f"{_label(label)} must be a real date.", label)
    try:
        datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        _die(f"{_label(label)} must be a real date.", label)


def _runs(paragraph: etree._Element) -> list[etree._Element]:
    return [node for node in paragraph.iter(W + "t")]


def _replace_span(paragraph: etree._Element, old: str, new: str) -> None:
    nodes = _runs(paragraph)
    full = "".join(node.text or "" for node in nodes)
    if old not in full:
        return
    start = full.index(old)
    end = start + len(old)
    bounds: list[tuple[int, int]] = []
    pos = 0
    for node in nodes:
        size = len(node.text or "")
        bounds.append((pos, pos + size))
        pos += size
    first = next(i for i, (a, b) in enumerate(bounds) if a <= start < b)
    last = next(i for i, (a, b) in enumerate(bounds) if a < end <= b)
    if first == last:
        text = nodes[first].text or ""
        offset = bounds[first][0]
        nodes[first].text = text[: start - offset] + new + text[end - offset :]
    else:
        offset = bounds[first][0]
        last_offset = bounds[last][0]
        nodes[first].text = (nodes[first].text or "")[: start - offset] + new
        for index in range(first + 1, last):
            nodes[index].text = ""
        nodes[last].text = (nodes[last].text or "")[end - last_offset :]
    _preserve_space(nodes)


def _preserve_space(nodes: list[etree._Element]) -> None:
    for node in nodes:
        text = node.text or ""
        if text and (text[0] == " " or text[-1] == " "):
            node.set(XML_SPACE, "preserve")


def fill_paragraph(paragraph: etree._Element, vals: dict[str, Any]) -> None:
    nodes = _runs(paragraph)
    full = "".join(node.text or "" for node in nodes)
    if "{{" not in full:
        return
    bounds: list[tuple[int, int]] = []
    pos = 0
    for node in nodes:
        size = len(node.text or "")
        bounds.append((pos, pos + size))
        pos += size
    for match in reversed(list(PH.finditer(full))):
        name = match.group(1)
        if name not in vals:
            continue
        start, end = match.span()
        first = next(i for i, (a, b) in enumerate(bounds) if a <= start < b)
        last = next(i for i, (a, b) in enumerate(bounds) if a < end <= b)
        if first == last:
            text = nodes[first].text or ""
            offset = bounds[first][0]
            nodes[first].text = text[: start - offset] + vals[name] + text[end - offset :]
        else:
            offset = bounds[first][0]
            last_offset = bounds[last][0]
            nodes[first].text = (nodes[first].text or "")[: start - offset] + vals[name]
            for index in range(first + 1, last):
                nodes[index].text = ""
            nodes[last].text = (nodes[last].text or "")[end - last_offset :]
        _preserve_space(nodes)
        full = "".join(node.text or "" for node in nodes)
        bounds = []
        pos = 0
        for node in nodes:
            size = len(node.text or "")
            bounds.append((pos, pos + size))
            pos += size


def fill_docx(values: dict[str, Any], *, template: Path | None = None, modified: datetime | None = None) -> bytes:
    """Return a filled docx. Writes nothing if a placeholder remains."""
    path = template or TEMPLATE_PATH
    zin = zipfile.ZipFile(path)
    buf = io.BytesIO()
    zout = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
    leftovers: list[str] = []
    stamp = (modified or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".xml") and item.filename.startswith("word/"):
                root = etree.fromstring(data)
                if item.filename == "word/document.xml":
                    for prefix in values.get("_remove") or []:
                        hits = [
                            paragraph
                            for paragraph in root.iter(W + "p")
                            if "".join(node.text or "" for node in paragraph.iter(W + "t")).startswith(prefix)
                        ]
                        if len(hits) != 1:
                            _die(
                                "template problem: expected exactly one paragraph starting "
                                f"{prefix!r} to remove, found {len(hits)}"
                            )
                        parent = hits[0].getparent()
                        if parent is not None:
                            parent.remove(hits[0])
                for paragraph in root.iter(W + "p"):
                    if not values.get("candidate_credentials"):
                        _replace_span(paragraph, "}}, {{candidate_credentials}}", "}}{{candidate_credentials}}")
                    fill_paragraph(paragraph, values)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            elif item.filename == "docProps/core.xml":
                root = etree.fromstring(data)
                ns = {"dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/"}
                title = root.find("dc:title", ns)
                if title is not None:
                    title.text = "Offer of Employment — " + values["position_title"]
                changed = root.find("dcterms:modified", ns)
                if changed is not None:
                    changed.text = stamp
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            text = data.decode("utf-8", "ignore") if item.filename.endswith((".xml", ".rels")) else ""
            plain = re.sub(r"<[^>]+>", "", text)
            for match in LEFTOVER.finditer(plain):
                leftovers.append(f"{item.filename}: {match.group(0)}")
            zout.writestr(item, data)
    finally:
        zout.close()
        zin.close()
    if leftovers:
        _die("unfilled placeholder(s) remain, output NOT written:\n  " + "\n  ".join(leftovers))
    return buf.getvalue()


def render_docx(raw: dict[str, Any], *, template: Path | None = None, spec: dict[str, Any] | None = None) -> tuple[dict[str, Any], bytes]:
    fields = spec or load_fields_spec()
    values = load_values(raw, fields)
    blob = fill_docx(values, template=template)
    return values, blob
