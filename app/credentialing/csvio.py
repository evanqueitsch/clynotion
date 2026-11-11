"""Credentialing CSV preview. Headers are accepted verbatim."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

PAYER_HEADERS = ["payer", "family_members", "type", "accepts_licenses", "notes"]
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


def _text(raw: Optional[str]) -> str:
    return (raw or "").lstrip("\ufeff")


def _split_list(value: str) -> list[str]:
    if not value.strip():
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def _reader(text: str) -> csv.DictReader:
    return csv.DictReader(io.StringIO(text))


def _iso(value: str, label: str, errors: list[str]) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        errors.append(f"{label} must be an ISO date (YYYY-MM-DD).")
        return ""
    return raw


def parse_payers(text: Optional[str]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    if text is None:
        return [], [], []
    body = _text(text)
    if not body.strip():
        return [], [], []
    reader = _reader(body)
    errors: list[str] = []
    changes: list[str] = []
    if list(reader.fieldnames or []) != PAYER_HEADERS:
        errors.append("payers.csv headers must be: " + ",".join(PAYER_HEADERS))
        return [], errors, changes
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(reader, start=2):
        name = (row.get("payer") or "").strip()
        if not name:
            errors.append(f"payers.csv row {index}: payer is blank.")
            continue
        if name in seen:
            errors.append(f"payers.csv row {index}: duplicate payer {name}.")
            continue
        seen.add(name)
        kind = (row.get("type") or "").strip().lower()
        if kind not in PAYER_TYPES:
            errors.append(
                f"payers.csv row {index}: type must be one of {', '.join(PAYER_TYPES)}."
            )
            continue
        rows.append(
            {
                "name": name,
                "family_members": _split_list(row.get("family_members") or ""),
                "type": kind,
                "accepts_licenses": _split_list(row.get("accepts_licenses") or ""),
                "notes": (row.get("notes") or "").strip(),
            }
        )
        changes.append(f"Payer {name}: update from payers.csv.")
    return rows, errors, changes


def parse_people(text: Optional[str]) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
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
        changes.append(f"Subject {name}: update from people.csv.")
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
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return "", "", f"cell {raw!r} must be Y, X, an ISO date, ?, or blank."
    return "pending", raw, None


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
            label = submitted or status
            changes.append(f"{payer} / {name}: {label}.")
    return rows, errors, changes


def build_plan(
    *,
    payers_csv: Optional[str],
    people_csv: Optional[str],
    grid_csv: Optional[str],
    existing_payers: set[str],
    existing_subjects: set[str],
) -> ImportPlan:
    plan = ImportPlan()
    if payers_csv is None and people_csv is None and grid_csv is None:
        plan.errors.append("Upload at least one CSV.")
        return plan
    payers, payer_errors, payer_changes = parse_payers(payers_csv)
    people, people_errors, warnings, people_changes = parse_people(people_csv)
    plan.errors.extend(payer_errors)
    plan.errors.extend(people_errors)
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
    return plan
