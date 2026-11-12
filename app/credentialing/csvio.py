"""Credentialing CSV preview.

Payers-only import is additive playbook data. It never creates subjects or
enrollments and never reads NPI, TIN, license numbers, CAQH, or addresses.

Preferred ``payers.csv`` header (the first five columns still parse alone):

    payer,family_members,type,accepts_licenses,notes,electronic_payer_id,practice_network,practice_network_note

Sheet-shaped headers are accepted as aliases: ``Insurance Co`` for the payer
name, ``Licenses`` / ``License requirements``, ``How-to`` / ``Instructions``,
``Electronic payer ID`` / ``Payer ID``, and ``In network``. Unrecognized
columns are ignored with a warning. They are not written onto people.

Blank cells do not erase stored values. A row with a blank payer name and
how-to text, or with process text in the in-network column (for example
``emailed 8/14 faxed 8/26``), continues the previous payer. That text is
appended to how-to notes. It is not a new payer and it is not ``in_network``.

In-network cell, after trim and case-fold: ``Y`` -> ``in_network``; ``need MA
1st`` / ``Need MA 1st`` -> ``needs_ma_first``; ``STARTED`` or text that starts
with ``SENT IN`` -> ``in_progress`` and the raw text is the network note;
blank -> leave the stored status (new payers stay ``unknown``). Any other
text is ``in_progress``, the raw text is kept as the note, and a warning is
added. A blank cell is never ``not_in_network``. Values such as ``??61101??``
are kept as written.

License cells split on commas, semicolons, or periods (``LSW. LCSW`` and
``LSW, LCSW``). Tokens such as ``masters`` stay whole.

Type is inferred only when creating a payer and the type cell is empty. ``EAP``
-> ``eap``; ``CHIP`` -> ``chip``; ``Tricare`` -> ``tricare``; a clear Medicaid /
MA name (``MA``, ``Medicaid``, ``... (MA)``) -> ``medicaid_mco``. Medicaid and
Medicare together stay ``commercial`` with a warning. Anything else is
``commercial``. An existing type is left as-is; a different incoming type is a
warning, not an overwrite.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.dates import DATE_HINT, parse_calendar_date

PAYER_HEADERS = ["payer", "family_members", "type", "accepts_licenses", "notes"]
PAYER_HEADERS_EXTENDED = PAYER_HEADERS + [
    "electronic_payer_id",
    "practice_network",
    "practice_network_note",
]
PRACTICE_NETWORKS = ("unknown", "in_network", "needs_ma_first", "in_progress")
PEOPLE_HEADERS = [
    "subject",
    "kind",
    "license_type",
    "npi",
    "tin",
    "license_number",
    "caqh_id",
    "hire_date",
    "device",
]
PAYER_TYPES = ("commercial", "medicaid_mco", "chip", "medicare", "tricare", "eap")
_NPI_DIGITS = re.compile(r"^\d{10}$")


@dataclass
class ImportPlan:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    payers: list[dict[str, Any]] = field(default_factory=list)
    subjects: list[dict[str, Any]] = field(default_factory=list)
    cells: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


def _text(raw: Optional[str]) -> str:
    body = (raw or "").lstrip("\ufeff")
    kept = [line for line in body.splitlines() if not line.startswith("#")]
    return "\n".join(kept)


def _split_list(value: str) -> list[str]:
    if not value.strip():
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def normalize_payer_name(name: str) -> str:
    """Match key: trim, collapse whitespace, case-insensitive."""
    return " ".join((name or "").split()).casefold()


def display_payer_name(name: str) -> str:
    """Store the sheet spelling, with surrounding and repeated whitespace collapsed."""
    return " ".join((name or "").split())


def _header_label(value: str) -> str:
    cleaned = (value or "").replace("_", " ").replace("-", " ")
    return " ".join(cleaned.split()).casefold().strip(" .:")


_HEADER_ALIASES = {
    "payer": "payer",
    "insurance co": "payer",
    "insurance company": "payer",
    "insurance": "payer",
    "family members": "family_members",
    "family": "family_members",
    "type": "type",
    "accepts licenses": "accepts_licenses",
    "licenses": "accepts_licenses",
    "license": "accepts_licenses",
    "license requirements": "accepts_licenses",
    "notes": "notes",
    "how to": "notes",
    "howto": "notes",
    "instructions": "notes",
    "electronic payer id": "electronic_payer_id",
    "electronic payer": "electronic_payer_id",
    "payer id": "electronic_payer_id",
    "practice network": "practice_network",
    "in network": "practice_network",
    "in network status": "practice_network",
    "practice network note": "practice_network_note",
    "network note": "practice_network_note",
}


def split_licenses(value: str) -> list[str]:
    """Split a license cell on commas, semicolons, or periods. Keep tokens like masters."""
    if not (value or "").strip():
        return []
    return [part.strip() for part in re.split(r"[,;.]+", value) if part.strip()]


def union_labels(existing: list[Any], incoming: list[Any]) -> list[str]:
    """Case-insensitive union. Existing spelling wins; new tokens are appended."""
    out = [str(item).strip() for item in existing if str(item).strip()]
    seen = {item.casefold() for item in out}
    for item in incoming:
        text = str(item).strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out


def howto_contained(existing: str, incoming: str) -> bool:
    needle = " ".join((incoming or "").split()).casefold()
    if not needle:
        return True
    hay = " ".join((existing or "").split()).casefold()
    return needle in hay


def append_howto(existing: str, incoming: str) -> str:
    """Set notes when empty; otherwise append a how-to that is not already there."""
    extra = (incoming or "").strip()
    current = existing or ""
    if not extra or howto_contained(current, extra):
        return current
    if not current.strip():
        return extra
    return current.rstrip() + "\n\n" + extra


def _looks_like_medicaid(name: str) -> bool:
    folded = " ".join((name or "").split()).casefold()
    if not folded:
        return False
    if folded == "ma" or "medicaid" in folded:
        return True
    return re.search(r"\bma\b", folded) is not None


def infer_payer_type(name: str) -> tuple[str, str]:
    """Type for a new payer when the sheet left type blank. Warning is empty when none."""
    folded = " ".join((name or "").split()).casefold()
    medicaid = _looks_like_medicaid(name)
    medicare = "medicare" in folded
    if medicaid and medicare:
        return (
            "commercial",
            f"{display_payer_name(name)}: name mentions Medicaid and Medicare; imported as commercial.",
        )
    if "eap" in folded:
        return "eap", ""
    if "chip" in folded:
        return "chip", ""
    if "tricare" in folded:
        return "tricare", ""
    if medicaid:
        return "medicaid_mco", ""
    return "commercial", ""


def map_practice_network(raw: str) -> tuple[str, str, str]:
    """Return status, note, warning.

    An empty status means the cell was blank: do not overwrite a stored status.
    The note is raw sheet text when the cell is a qualifier rather than Y / MA.
    """
    text = (raw or "").strip()
    if not text:
        return "", "", ""
    folded = " ".join(text.split()).casefold().rstrip(".")
    if folded in {"y", "in_network", "in network"}:
        return "in_network", "", ""
    if folded in {"need ma 1st", "need ma first", "needs ma first", "needs_ma_first"}:
        return "needs_ma_first", "", ""
    if folded == "unknown":
        return "unknown", "", ""
    if folded in {"in_progress", "in progress"}:
        return "in_progress", "", ""
    if folded == "started" or folded.startswith("sent in"):
        return "in_progress", text, ""
    return (
        "in_progress",
        text,
        f"Unexpected in-network value {text!r}; kept the text and marked in progress.",
    )


def _reader(text: str) -> csv.DictReader:
    return csv.DictReader(io.StringIO(text))


def _iso(value: str, label: str, errors: list[str]) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        return parse_calendar_date(raw)
    except ValueError:
        errors.append(f"{label}: {DATE_HINT}")
        return ""


def _status_words(status: str, submitted: str) -> str:
    if status == "pending":
        return f"pending, submitted {submitted}" if submitted else "pending"
    return {
        "in_network": "in network",
        "not_eligible": "not eligible",
        "needs_review": "needs review",
        "unknown": "unknown",
    }.get(status, status)


def _payer_columns(fieldnames: list[Any]) -> tuple[dict[str, str], list[str], list[str]]:
    """Map original header -> canonical field. Unknown columns warn; they are not imported."""
    columns: dict[str, str] = {}
    used: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for raw in fieldnames:
        if raw is None:
            continue
        label = _header_label(str(raw))
        if not label:
            continue
        canonical = _HEADER_ALIASES.get(label)
        if not canonical:
            warnings.append(f"payers.csv column {str(raw).strip()!r} was ignored.")
            continue
        if canonical in used:
            errors.append(
                f"payers.csv has two columns for {canonical}: {used[canonical]!r} and {str(raw).strip()!r}."
            )
            continue
        used[canonical] = str(raw).strip()
        columns[str(raw)] = canonical
    if "payer" not in used:
        errors.append(
            "payers.csv needs a payer column. Use " + ",".join(PAYER_HEADERS_EXTENDED) + "."
        )
    return columns, errors, warnings


def _payer_cell(row: dict[str, Any], columns: dict[str, str], canonical: str) -> str:
    for original, name in columns.items():
        if name == canonical:
            return str(row.get(original) or "")
    return ""


def _blank_payer_item() -> dict[str, Any]:
    return {
        "name": "",
        "family_members": [],
        "type": "",
        "accepts_licenses": [],
        "notes": "",
        "electronic_payer_id": "",
        "practice_network": "",
        "practice_network_note": "",
    }


def parse_payers(
    text: Optional[str],
    *,
    existing: Optional[set[str]] = None,
    existing_types: Optional[dict[str, str]] = None,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Return payer rows, errors, warnings, and change lines.

    Rows are additive: empty strings and empty lists mean "leave the stored value".
    ``practice_network`` is empty when the cell was blank.
    """
    if text is None:
        return [], [], [], []
    body = _text(text)
    if not body.strip():
        return [], [], [], []
    reader = _reader(body)
    errors: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    columns, header_errors, header_warnings = _payer_columns(list(reader.fieldnames or []))
    errors.extend(header_errors)
    warnings.extend(header_warnings)
    if header_errors:
        return [], errors, warnings, changes
    existing_keys = {normalize_payer_name(name) for name in (existing or set())}
    type_by_key = {
        normalize_payer_name(name): str(kind or "").strip().lower()
        for name, kind in (existing_types or {}).items()
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous: Optional[dict[str, Any]] = None
    for index, row in enumerate(reader, start=2):
        raw_name = _payer_cell(row, columns, "payer")
        name = display_payer_name(raw_name)
        notes = _payer_cell(row, columns, "notes").strip()
        network_raw = _payer_cell(row, columns, "practice_network").strip()
        network_note = _payer_cell(row, columns, "practice_network_note").strip()
        licenses = split_licenses(_payer_cell(row, columns, "accepts_licenses"))
        family = _split_list(_payer_cell(row, columns, "family_members"))
        electronic = _payer_cell(row, columns, "electronic_payer_id").strip()
        if not name:
            extra = [part for part in (notes, network_raw, network_note) if part]
            if not extra and not licenses and not family and not electronic:
                kind_only = _payer_cell(row, columns, "type").strip()
                if kind_only:
                    errors.append(f"payers.csv row {index}: payer is blank.")
                continue
            if previous is None:
                errors.append(f"payers.csv row {index}: payer is blank.")
                continue
            for chunk in extra:
                previous["notes"] = append_howto(str(previous["notes"]), chunk)
            if licenses:
                previous["accepts_licenses"] = union_labels(previous["accepts_licenses"], licenses)
            if family:
                previous["family_members"] = union_labels(previous["family_members"], family)
            if electronic:
                previous["electronic_payer_id"] = electronic
            continue
        key = normalize_payer_name(name)
        if key in seen:
            errors.append(f"payers.csv row {index}: duplicate payer {name}.")
            continue
        seen.add(key)
        incoming_type = _payer_cell(row, columns, "type").strip().lower()
        if incoming_type and incoming_type not in PAYER_TYPES:
            errors.append(
                f"payers.csv row {index}: type must be one of {', '.join(PAYER_TYPES)}."
            )
            continue
        is_update = key in existing_keys
        kind = incoming_type
        if not is_update and not incoming_type:
            kind, inferred = infer_payer_type(name)
            if inferred:
                warnings.append(inferred)
        elif is_update and incoming_type:
            stored = type_by_key.get(key, "")
            if stored and stored != incoming_type:
                warnings.append(
                    f"{name}: incoming type {incoming_type} does not match {stored}; left unchanged."
                )
        status, derived_note, network_warning = map_practice_network(network_raw)
        if network_warning:
            warnings.append(f"payers.csv row {index}: {network_warning}")
        item = _blank_payer_item()
        item.update(
            {
                "name": name,
                "family_members": family,
                "type": kind,
                "accepts_licenses": licenses,
                "notes": notes,
                "electronic_payer_id": electronic,
                "practice_network": status,
                "practice_network_note": network_note or derived_note,
            }
        )
        rows.append(item)
        previous = item
        verb = "update" if is_update else "create"
        changes.append(f"Payer {name}: {verb}.")
    return rows, errors, warnings, changes


def parse_people(
    text: Optional[str], *, existing: Optional[set[str]] = None
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    if text is None:
        return [], [], [], []
    body = _text(text)
    if not body.strip():
        return [], [], [], []
    reader = _reader(body)
    errors: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    if list(reader.fieldnames or []) != PEOPLE_HEADERS:
        errors.append("people.csv headers must be: " + ",".join(PEOPLE_HEADERS))
        return [], errors, warnings, changes
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(reader, start=2):
        name = (row.get("subject") or "").strip()
        kind = (row.get("kind") or "").strip().lower()
        if not name:
            errors.append(f"people.csv row {index}: subject is blank.")
            continue
        if name in seen:
            errors.append(f"people.csv row {index}: duplicate subject {name}.")
            continue
        seen.add(name)
        if kind not in {"practice", "clinician"}:
            errors.append(f"people.csv row {index}: kind must be practice or clinician.")
            continue
        row_errors: list[str] = []
        hire = _iso(row.get("hire_date") or "", f"people.csv row {index} hire_date", row_errors)
        errors.extend(row_errors)
        if row_errors:
            continue
        npi = (row.get("npi") or "").strip()
        if npi and not _NPI_DIGITS.match(npi):
            warnings.append(f"{name}: NPI has {len(npi)} digits; expected 10.")
        rows.append(
            {
                "name": name,
                "kind": kind,
                "license_type": (row.get("license_type") or "").strip(),
                "npi": npi,
                "tin": (row.get("tin") or "").strip(),
                "license_number": (row.get("license_number") or "").strip(),
                "caqh_id": (row.get("caqh_id") or "").strip(),
                "hire_date": hire,
                "device": (row.get("device") or "").strip(),
            }
        )
        verb = "update" if existing and name in existing else "create"
        changes.append(f"Subject {name}: {verb}.")
    return rows, errors, warnings, changes


def _cell(value: str) -> tuple[str, str, Optional[str]]:
    """Return status, submitted_on, and an error."""
    raw = value.strip()
    if not raw:
        return "unknown", "", None
    if raw.upper() == "Y":
        return "in_network", "", None
    if raw.upper() == "X":
        return "not_eligible", "", None
    if raw == "?":
        return "needs_review", "", None
    try:
        stored = parse_calendar_date(raw)
    except ValueError:
        return "", "", f"cell {raw!r} must be Y, X, a date such as 11/09/2026, ?, or blank."
    return "pending", stored, None


def parse_grid(
    text: Optional[str],
    *,
    payer_names: set[str],
    subject_names: set[str],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    if text is None:
        return [], [], []
    body = _text(text)
    if not body.strip():
        return [], [], []
    reader = csv.reader(io.StringIO(body))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["grid.csv is empty."], []
    header = [cell.strip() for cell in header]
    errors: list[str] = []
    changes: list[str] = []
    if not header or header[0] != "payer":
        errors.append("grid.csv first column must be payer.")
        return [], errors, changes
    columns = header[1:]
    if not columns:
        errors.append("grid.csv needs one column per subject.")
        return [], errors, changes
    unknown = [name for name in columns if name not in subject_names]
    if unknown:
        errors.append("grid.csv columns do not match people: " + ", ".join(unknown))
        return [], errors, changes
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        padded = list(row) + [""] * (len(header) - len(row))
        payer = padded[0].strip()
        if not payer:
            errors.append(f"grid.csv row {index}: payer is blank.")
            continue
        if payer in seen:
            errors.append(f"grid.csv row {index}: duplicate payer {payer}.")
            continue
        seen.add(payer)
        if payer not in payer_names:
            errors.append(f"grid.csv row {index}: unknown payer {payer}.")
            continue
        for name, raw in zip(columns, padded[1:]):
            status, submitted, error = _cell(raw)
            if error:
                errors.append(f"grid.csv row {index} {name}: {error}")
                continue
            rows.append(
                {
                    "payer": payer,
                    "subject": name,
                    "status": status,
                    "submitted_on": submitted,
                }
            )
            changes.append(f"{payer} / {name}: {_status_words(status, submitted)}.")
    return rows, errors, changes


def _noun(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def summarize_plan(
    plan: ImportPlan, *, existing_payers: set[str], existing_subjects: set[str]
) -> str:
    """One line for the import preview, for example '3 payers to create, 2 people to create, 6 cells, 1 warning'."""
    parts: list[str] = []
    payer_keys = {normalize_payer_name(name) for name in existing_payers}
    payer_create = sum(
        1 for row in plan.payers if normalize_payer_name(row["name"]) not in payer_keys
    )
    payer_update = len(plan.payers) - payer_create
    person_create = sum(1 for row in plan.subjects if row["name"] not in existing_subjects)
    person_update = len(plan.subjects) - person_create
    if payer_create:
        parts.append(f"{_noun(payer_create, 'payer', 'payers')} to create")
    if payer_update:
        parts.append(f"{_noun(payer_update, 'payer', 'payers')} to update")
    if person_create:
        parts.append(f"{_noun(person_create, 'person', 'people')} to create")
    if person_update:
        parts.append(f"{_noun(person_update, 'person', 'people')} to update")
    parts.append(_noun(len(plan.cells), "cell", "cells"))
    parts.append(_noun(len(plan.warnings), "warning", "warnings"))
    return ", ".join(parts)


def build_plan(
    *,
    payers_csv: Optional[str],
    people_csv: Optional[str],
    grid_csv: Optional[str],
    existing_payers: set[str],
    existing_subjects: set[str],
    existing_payer_types: Optional[dict[str, str]] = None,
) -> ImportPlan:
    plan = ImportPlan()
    if payers_csv is None and people_csv is None and grid_csv is None:
        plan.errors.append("Upload at least one CSV.")
        plan.summary = summarize_plan(
            plan, existing_payers=existing_payers, existing_subjects=existing_subjects
        )
        return plan
    payers, payer_errors, payer_warnings, payer_changes = parse_payers(
        payers_csv, existing=existing_payers, existing_types=existing_payer_types
    )
    people, people_errors, warnings, people_changes = parse_people(people_csv, existing=existing_subjects)
    plan.errors.extend(payer_errors)
    plan.errors.extend(people_errors)
    plan.warnings.extend(payer_warnings)
    plan.warnings.extend(warnings)
    plan.payers = payers
    plan.subjects = people
    plan.changes.extend(payer_changes)
    plan.changes.extend(people_changes)
    payer_names = set(existing_payers) | {row["name"] for row in payers}
    subject_names = set(existing_subjects) | {row["name"] for row in people}
    cells, grid_errors, grid_changes = parse_grid(
        grid_csv, payer_names=payer_names, subject_names=subject_names
    )
    plan.errors.extend(grid_errors)
    plan.cells = cells
    plan.changes.extend(grid_changes)
    plan.summary = summarize_plan(
        plan, existing_payers=existing_payers, existing_subjects=existing_subjects
    )
    return plan
