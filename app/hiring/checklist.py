"""Per-hire checklist. Due dates use US Eastern business days.

Part-time hires skip the benefits item. Direct client access makes PA
clearances due before the start date. Payroll items belong to Billing.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# template_key, stage, owner, title, kind
# kind: auto | status | upload | i9 | payroll | blocked
ITEMS: tuple[dict[str, Any], ...] = (
    {"key": "record_created", "stage": 1, "owner": "clynotion", "title": "Onboarding record and checklist", "kind": "auto"},
    {"key": "folders_created", "stage": 1, "owner": "clynotion", "title": "Per-hire folders", "kind": "auto"},
    {"key": "offer_filed", "stage": 1, "owner": "clynotion", "title": "Signed offer filed", "kind": "auto"},
    {"key": "welcome_sent", "stage": 2, "owner": "admin", "title": "Welcome message", "kind": "status"},
    {"key": "packet_signed", "stage": 2, "owner": "candidate", "title": "Employee information and HIPAA acknowledgment", "kind": "upload"},
    {"key": "agreements_signed", "stage": 2, "owner": "candidate", "title": "Employment agreement, non-solicitation, and handbook", "kind": "blocked"},
    {"key": "supervision_agreement", "stage": 2, "owner": "practice_owner", "title": "Clinical supervision agreement", "kind": "blocked"},
    {"key": "residency_cert", "stage": 2, "owner": "candidate", "title": "PA residency certification", "kind": "status"},
    {"key": "rev_419", "stage": 2, "owner": "candidate", "title": "REV-419 when an exemption applies", "kind": "status"},
    {"key": "i9_section1", "stage": 2, "owner": "candidate", "title": "I-9 Section 1", "kind": "i9"},
    {"key": "clearances", "stage": 2, "owner": "admin", "title": "PA clearances (Acts 151, 34, and 114)", "kind": "upload"},
    {"key": "act31_training", "stage": 2, "owner": "candidate", "title": "Act 31 training certificate", "kind": "upload"},
    {"key": "license_verification", "stage": 2, "owner": "admin", "title": "PA license verification", "kind": "upload"},
    {"key": "liability_cert", "stage": 2, "owner": "candidate", "title": "Professional liability certificate", "kind": "upload"},
    {"key": "resume_degree", "stage": 2, "owner": "candidate", "title": "Resume and degree", "kind": "upload"},
    {"key": "exclusion_screening", "stage": 2, "owner": "admin", "title": "Exclusion check", "kind": "status"},
    {"key": "workspace_account", "stage": 2, "owner": "admin", "title": "Workspace account", "kind": "status"},
    {"key": "npi_caqh", "stage": 2, "owner": "admin", "title": "NPI and CAQH collected for credentialing", "kind": "status"},
    {"key": "i9_section2", "stage": 3, "owner": "admin", "title": "I-9 Section 2", "kind": "i9"},
    {"key": "i9_scan_uploaded", "stage": 3, "owner": "admin", "title": "I-9 scan uploaded", "kind": "i9"},
    {"key": "simplepractice_ipad", "stage": 3, "owner": "admin", "title": "SimplePractice and iPad", "kind": "status"},
    {"key": "portal_revoked", "stage": 3, "owner": "clynotion", "title": "Portal link revoked", "kind": "status"},
    {"key": "activate", "stage": 3, "owner": "admin", "title": "Activate hire", "kind": "status"},
    {"key": "credentialing_handoff", "stage": 4, "owner": "billing", "title": "Credentialing handoff", "kind": "status"},
    {"key": "pa_new_hire_report", "stage": 4, "owner": "billing", "title": "PA new-hire report", "kind": "payroll"},
    {"key": "website_profile", "stage": 4, "owner": "admin", "title": "Website profile", "kind": "status"},
    {"key": "team_intro", "stage": 4, "owner": "admin", "title": "Team introduction", "kind": "status"},
    {"key": "supervision", "stage": 4, "owner": "practice_owner", "title": "Supervision", "kind": "status"},
    {"key": "benefits", "stage": 4, "owner": "admin", "title": "QSEHRA and SIMPLE IRA", "kind": "status"},
)

PAYROLL_KEYS = frozenset({"pa_new_hire_report"})
BILLING_STEP_KEYS = frozenset(
    {
        "pa_new_hire_report",
        "i9_section2",
        "clearances",
        "exclusion_screening",
    }
)
I9_KEYS = frozenset({"i9_section1", "i9_section2", "i9_scan_uploaded"})
STATUS_ONLY = frozenset(spec["key"] for spec in ITEMS if spec["kind"] in {"status", "payroll", "auto"})
BLOCKED_DONE = {
    "agreements_signed": "Those agreement files are not on file yet, so this item cannot be marked done.",
}


def parse_mdy(value: str) -> date:
    return datetime.strptime(value, "%m/%d/%Y").date()


def parse_iso(value: str) -> date:
    return date.fromisoformat(value[:10])


def to_iso(value: date) -> str:
    return value.isoformat()


def is_business_day(day: date, closed: set[date]) -> bool:
    return day.weekday() < 5 and day not in closed


def packet_due(start: date, packet_on: date, closed: set[date]) -> date:
    """Later of the start date (minus 0 business days) and the packet date plus 3 business days."""
    return max(start, add_business_days(packet_on, 3, closed))


def add_business_days(start: date, count: int, closed: set[date]) -> date:
    if count < 0:
        raise ValueError("count")
    day = start
    left = count
    while left:
        day += timedelta(days=1)
        if is_business_day(day, closed):
            left -= 1
    return day


def previous_business_day(start: date, closed: set[date]) -> date:
    day = start - timedelta(days=1)
    while not is_business_day(day, closed):
        day -= timedelta(days=1)
    return day


def closed_set(raw: list[str]) -> set[date]:
    found: set[date] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        found.add(date.fromisoformat(text[:10]))
    return found


def clinician_title(title: str) -> bool:
    return "clinician" in (title or "").casefold()


def supervision_required(credentials: str) -> bool:
    """Supervision applies unless the hire is fully licensed.

    Blank credentials need supervision. A candidate credential needs supervision.
    LCSW, LPC, LMFT, and psychologist do not.
    """
    text = (credentials or "").casefold()
    if not text.strip() or "candidate" in text:
        return True
    if "psychologist" in text:
        return False
    tokens = {part for part in re.split(r"[^a-z]+", text) if part}
    if tokens & {"lcsw", "lpc", "lmft"}:
        return False
    return True


def license_on_file(credentials: str) -> bool:
    """Blank or an explicit none is not a license. A candidate credential still counts."""
    text = (credentials or "").strip().casefold()
    return text not in {"", "none", "n/a", "na"}


def clearance_state(
    *,
    direct: bool,
    status: str,
    due: date | None,
    today: date,
    start: date | None = None,
    created: date | None = None,
) -> dict[str, Any]:
    """Red when a direct-client clearance is still open on or after its due date.

    The amber badge stays while that item is open, including before the due date.
    When the start date is on or before the hire was created, the creation day
    shows "due now" instead of an overdue warning.
    """
    open_item = bool(direct) and status == "open"
    if open_item and start is not None and created is not None and start <= created and today <= created:
        return {"warning": False, "badge": "due now"}
    late = bool(open_item and due is not None and today >= due)
    return {"warning": late, "badge": "due before start" if open_item else ""}


def build_items(
    *,
    hire_id: str,
    employment_type: str,
    start: date,
    accepted: date,
    direct_client_access: bool,
    needs_supervision: bool,
    has_license: bool = True,
    closed: set[date],
    new_id,
) -> list[dict[str, Any]]:
    before_start = previous_business_day(start, closed)
    floor = add_business_days(accepted, 1, closed)
    rows: list[dict[str, Any]] = []
    for spec in ITEMS:
        key = spec["key"]
        status = "open"
        due = to_iso(before_start)
        if key in {"record_created", "folders_created", "offer_filed"}:
            status = "done"
            due = to_iso(accepted)
        elif key == "welcome_sent":
            due = to_iso(add_business_days(accepted, 1, closed))
        elif key == "rev_419":
            status = "n/a"
        elif key in {"supervision_agreement", "supervision"} and not needs_supervision:
            status = "n/a"
        elif key in {"license_verification", "npi_caqh"} and not has_license:
            status = "n/a"
        elif key == "clearances":
            due = to_iso(before_start)
            if not direct_client_access:
                status = "n/a"
        elif key == "benefits" and employment_type == "part-time":
            status = "skipped"
        elif key == "pa_new_hire_report":
            due = to_iso(start + timedelta(days=20))
        elif key in {"i9_section2", "i9_scan_uploaded"}:
            due = ""
        elif spec["stage"] >= 3:
            due = to_iso(start)
        if due and key not in {"clearances", "exclusion_screening"} and due < to_iso(floor):
            due = to_iso(floor)
        note = "No license on file" if key in {"license_verification", "npi_caqh"} and status == "n/a" else ""
        rows.append(
            {
                "id": new_id("item-"),
                "hire_id": hire_id,
                "template_key": key,
                "stage": spec["stage"],
                "owner_role": spec["owner"],
                "title": spec["title"],
                "kind": spec["kind"],
                "due_date": due,
                "status": status,
                "done_at": "",
                "done_by": "clynotion" if status == "done" else "",
                "dates": {},
                "where_original_lives": "",
                "note": note,
                "document_ref_ids": [],
                "form_id": "",
            }
        )
    return rows
