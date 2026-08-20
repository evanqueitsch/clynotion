"""Employee onboarding + clearance/license tracking + LSW supervision hours.

Design of record: docs/modules/ops2-onboarding-compliance-scope.md
(Master scope §12.9). Manual-entry v1 — no QuickBooks/Drive API.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional
from uuid import uuid4

from app.crypto import decrypt_utf8, encrypt_utf8
from app.platform.due import due_engine

ENV_HR_PERSISTENCE = "ATTUNE_HR_PERSISTENCE"
ENV_HR_DATA_PATH = "ATTUNE_HR_DATA_PATH"
DEFAULT_HR_DATA_PATH = ".attune_data/hr_onboarding.enc"

HR_SOURCE = "hr_onboarding"
EXPIRY_SOON_DAYS_DEFAULT = 60
PA_LCSW_HOURS_TARGET_DEFAULT = 3000  # confirm with PA Board (open question)

LicenseType = Literal["LCSW", "LSW", "LPC", "admin", "other"]
StepStatus = Literal["NotStarted", "InProgress", "Complete", "NA"]
ComplianceType = Literal["Act151", "Act34", "Act114", "License", "Other"]
SupervisionFormat = Literal["InPerson", "Telehealth"]

# Forbidden payload keys — hard rule (PII minimization).
_FORBIDDEN_KEY_RE = re.compile(
    r"(ssn|social.?security|bank|routing|account.?number|dob|date.?of.?birth|passport)",
    re.I,
)

ONBOARDING_STEPS: tuple[str, ...] = (
    "offerSigned",
    "employeeInfoForm",
    "hipaaAgreement",
    "i9Section1",
    "i9Section2Verified",
    "w4_quickbooks",
    "directDeposit_quickbooks",
    "credentialingApp",
    "clearanceChildAbuse_act151",
    "clearanceCriminal_act34",
    "clearanceFbiFingerprint_act114",
    "driveFolderCreated",
    "welcomeEmailSent",
    "supervisionAgreement",  # LSW only
)

STEP_LABELS: dict[str, str] = {
    "offerSigned": "Offer signed",
    "employeeInfoForm": "Employee info form",
    "hipaaAgreement": "HIPAA agreement",
    "i9Section1": "I-9 Section 1",
    "i9Section2Verified": "I-9 Section 2 verified",
    "w4_quickbooks": "W-4 (QuickBooks)",
    "directDeposit_quickbooks": "Direct deposit (QuickBooks)",
    "credentialingApp": "Credentialing application",
    "clearanceChildAbuse_act151": "Child abuse clearance (Act 151)",
    "clearanceCriminal_act34": "Criminal clearance (Act 34)",
    "clearanceFbiFingerprint_act114": "FBI fingerprint (Act 114)",
    "driveFolderCreated": "Drive folder created",
    "welcomeEmailSent": "Welcome email sent",
    "supervisionAgreement": "Supervision agreement",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().date()


def _parse_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _add_business_days(start: date, days: int) -> date:
    d = start
    left = days
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            left -= 1
    return d


def assert_no_forbidden_fields(payload: dict[str, Any]) -> None:
    for key in payload.keys():
        if _FORBIDDEN_KEY_RE.search(str(key)):
            raise ValueError(
                f"forbidden field {key!r} — Clynotion stores status/dates/links only"
            )


def _default_steps(*, license_type: str) -> dict[str, str]:
    steps = {s: "NotStarted" for s in ONBOARDING_STEPS}
    if license_type != "LSW":
        steps["supervisionAgreement"] = "NA"
    return steps


@dataclass
class Employee:
    employee_id: str
    practice_id: str
    display_name: str
    title: str = ""
    license_type: LicenseType = "other"
    license_number: str = ""
    license_expiry: str = ""
    supervisor_id: str = ""
    start_date: str = ""
    drive_folder_url: str = ""
    active: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "practice_id": self.practice_id,
            "display_name": self.display_name,
            "title": self.title,
            "license_type": self.license_type,
            "license_number": self.license_number,
            "license_expiry": self.license_expiry,
            "supervisor_id": self.supervisor_id,
            "start_date": self.start_date,
            "drive_folder_url": self.drive_folder_url,
            "active": self.active,
        }


@dataclass
class OnboardingRecord:
    employee_id: str
    practice_id: str
    steps: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    last_updated_by: str = ""
    last_updated_at: str = ""
    completed_at: str = ""

    def applicable_steps(self, license_type: str) -> list[str]:
        if license_type == "LSW":
            return list(ONBOARDING_STEPS)
        return [s for s in ONBOARDING_STEPS if s != "supervisionAgreement"]

    def percent_complete(self, license_type: str) -> float:
        keys = self.applicable_steps(license_type)
        if not keys:
            return 100.0
        done = sum(1 for k in keys if self.steps.get(k) in ("Complete", "NA"))
        return round(100.0 * done / len(keys), 1)

    def overall_status(self, license_type: str) -> str:
        keys = self.applicable_steps(license_type)
        vals = [self.steps.get(k, "NotStarted") for k in keys]
        if all(v in ("Complete", "NA") for v in vals):
            return "Complete"
        if any(v in ("InProgress", "Complete") for v in vals):
            return "InProgress"
        return "NotStarted"

    def i9_section2_overdue(self, start_date: str) -> bool:
        if self.steps.get("i9Section2Verified") == "Complete":
            return False
        sd = _parse_date(start_date)
        if sd is None:
            return False
        return _today() > _add_business_days(sd, 3)

    def to_public_dict(self, *, license_type: str, start_date: str) -> dict[str, Any]:
        return {
            "employee_id": self.employee_id,
            "practice_id": self.practice_id,
            "steps": dict(self.steps),
            "step_labels": dict(STEP_LABELS),
            "notes": self.notes,
            "last_updated_by": self.last_updated_by,
            "last_updated_at": self.last_updated_at,
            "completed_at": self.completed_at,
            "percent_complete": self.percent_complete(license_type),
            "overall_status": self.overall_status(license_type),
            "i9_section2_overdue": self.i9_section2_overdue(start_date),
        }


@dataclass
class ComplianceItem:
    item_id: str
    employee_id: str
    practice_id: str
    type: ComplianceType
    issue_date: str = ""
    expiry_date: str = ""
    renewal_interval_months: int = 60
    document_drive_url: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "employee_id": self.employee_id,
            "practice_id": self.practice_id,
            "type": self.type,
            "issue_date": self.issue_date,
            "expiry_date": self.expiry_date,
            "renewal_interval_months": self.renewal_interval_months,
            "document_drive_url": self.document_drive_url,
        }


@dataclass
class SupervisionLogEntry:
    entry_id: str
    practice_id: str
    supervisee_id: str
    supervisor_id: str
    date: str
    duration_minutes: int
    format: SupervisionFormat = "Telehealth"
    notes: str = ""
    signed_by_both: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "practice_id": self.practice_id,
            "supervisee_id": self.supervisee_id,
            "supervisor_id": self.supervisor_id,
            "date": self.date,
            "duration_minutes": self.duration_minutes,
            "format": self.format,
            "notes": self.notes,
            "signed_by_both": self.signed_by_both,
        }


class OnboardingStore:
    def __init__(self) -> None:
        self._employees: dict[str, Employee] = {}
        self._onboarding: dict[str, OnboardingRecord] = {}
        self._compliance: dict[str, ComplianceItem] = {}
        self._supervision: dict[str, SupervisionLogEntry] = {}
        self._path = Path(
            (os.environ.get(ENV_HR_DATA_PATH) or DEFAULT_HR_DATA_PATH).strip()
        )
        self._mode = (os.environ.get(ENV_HR_PERSISTENCE) or "memory").strip().lower()
        self._memory_blob: Optional[bytes] = None
        self._seeded_practices: set[str] = set()
        self._load()

    def reset(self) -> None:
        self._employees.clear()
        self._onboarding.clear()
        self._compliance.clear()
        self._supervision.clear()
        self._seeded_practices.clear()
        self._memory_blob = None
        if self._mode == "file" and self._path.is_file():
            self._path.unlink()

    def _flush(self) -> None:
        payload = {
            "employees": [e.to_public_dict() for e in self._employees.values()],
            "onboarding": [
                {
                    "employee_id": o.employee_id,
                    "practice_id": o.practice_id,
                    "steps": o.steps,
                    "notes": o.notes,
                    "last_updated_by": o.last_updated_by,
                    "last_updated_at": o.last_updated_at,
                    "completed_at": o.completed_at,
                }
                for o in self._onboarding.values()
            ],
            "compliance": [c.to_public_dict() for c in self._compliance.values()],
            "supervision": [s.to_public_dict() for s in self._supervision.values()],
            "seeded_practices": sorted(self._seeded_practices),
        }
        blob = encrypt_utf8(json.dumps(payload))
        if self._mode == "file":
            import base64

            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(
                    {"version": 1, "blob": base64.b64encode(blob).decode("ascii")}
                ),
                encoding="utf-8",
            )
        else:
            self._memory_blob = blob

    def _load(self) -> None:
        raw: Optional[bytes] = None
        if self._mode == "file" and self._path.is_file():
            try:
                import base64

                wrapper = json.loads(self._path.read_text(encoding="utf-8"))
                raw = base64.b64decode(str(wrapper.get("blob") or ""))
            except (ValueError, OSError, TypeError):
                return
        elif self._memory_blob is not None:
            raw = self._memory_blob
        if not raw:
            return
        try:
            payload = json.loads(decrypt_utf8(raw))
        except ValueError:
            return
        for item in payload.get("employees") or []:
            try:
                emp = Employee(
                    employee_id=str(item["employee_id"]),
                    practice_id=str(item["practice_id"]),
                    display_name=str(item["display_name"]),
                    title=str(item.get("title") or ""),
                    license_type=str(item.get("license_type") or "other"),  # type: ignore[arg-type]
                    license_number=str(item.get("license_number") or ""),
                    license_expiry=str(item.get("license_expiry") or ""),
                    supervisor_id=str(item.get("supervisor_id") or ""),
                    start_date=str(item.get("start_date") or ""),
                    drive_folder_url=str(item.get("drive_folder_url") or ""),
                    active=bool(item.get("active", True)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._employees[emp.employee_id] = emp
        for item in payload.get("onboarding") or []:
            try:
                rec = OnboardingRecord(
                    employee_id=str(item["employee_id"]),
                    practice_id=str(item["practice_id"]),
                    steps=dict(item.get("steps") or {}),
                    notes=str(item.get("notes") or ""),
                    last_updated_by=str(item.get("last_updated_by") or ""),
                    last_updated_at=str(item.get("last_updated_at") or ""),
                    completed_at=str(item.get("completed_at") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._onboarding[rec.employee_id] = rec
        for item in payload.get("compliance") or []:
            try:
                ci = ComplianceItem(
                    item_id=str(item["item_id"]),
                    employee_id=str(item["employee_id"]),
                    practice_id=str(item["practice_id"]),
                    type=str(item["type"]),  # type: ignore[arg-type]
                    issue_date=str(item.get("issue_date") or ""),
                    expiry_date=str(item.get("expiry_date") or ""),
                    renewal_interval_months=int(
                        item.get("renewal_interval_months") or 60
                    ),
                    document_drive_url=str(item.get("document_drive_url") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._compliance[ci.item_id] = ci
        for item in payload.get("supervision") or []:
            try:
                se = SupervisionLogEntry(
                    entry_id=str(item["entry_id"]),
                    practice_id=str(item["practice_id"]),
                    supervisee_id=str(item["supervisee_id"]),
                    supervisor_id=str(item["supervisor_id"]),
                    date=str(item["date"]),
                    duration_minutes=int(item["duration_minutes"]),
                    format=str(item.get("format") or "Telehealth"),  # type: ignore[arg-type]
                    notes=str(item.get("notes") or ""),
                    signed_by_both=bool(item.get("signed_by_both", False)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._supervision[se.entry_id] = se
        self._seeded_practices = set(payload.get("seeded_practices") or [])

    def ensure_seeded(self, practice_id: str) -> None:
        """Seed current-hire vignette rows once per practice (no real PHI)."""
        if practice_id in self._seeded_practices:
            return
        if any(e.practice_id == practice_id for e in self._employees.values()):
            self._seeded_practices.add(practice_id)
            self._flush()
            return
        seeds = [
            ("Alex Mistovich", "Clinician", "LCSW"),
            ("Nathan Sterry", "Clinician", "LSW"),
            ("Kayleigh", "Clinician", "LSW"),
        ]
        for name, title, lic in seeds:
            self.create_employee(
                practice_id=practice_id,
                display_name=name,
                title=title,
                license_type=lic,  # type: ignore[arg-type]
                start_date=_today().isoformat(),
                updated_by="seed",
            )
        self._seeded_practices.add(practice_id)
        self._flush()

    def create_employee(
        self,
        *,
        practice_id: str,
        display_name: str,
        title: str = "",
        license_type: LicenseType = "other",
        license_number: str = "",
        license_expiry: str = "",
        supervisor_id: str = "",
        start_date: str = "",
        drive_folder_url: str = "",
        updated_by: str = "",
    ) -> Employee:
        assert_no_forbidden_fields(
            {
                "display_name": display_name,
                "license_number": license_number,
                "drive_folder_url": drive_folder_url,
            }
        )
        if license_type not in ("LCSW", "LSW", "LPC", "admin", "other"):
            raise ValueError(f"unsupported license_type: {license_type}")
        emp = Employee(
            employee_id=str(uuid4()),
            practice_id=practice_id,
            display_name=display_name.strip(),
            title=title.strip(),
            license_type=license_type,
            license_number=license_number.strip(),
            license_expiry=license_expiry.strip(),
            supervisor_id=supervisor_id.strip(),
            start_date=start_date.strip() or _today().isoformat(),
            drive_folder_url=drive_folder_url.strip(),
        )
        self._employees[emp.employee_id] = emp
        self._onboarding[emp.employee_id] = OnboardingRecord(
            employee_id=emp.employee_id,
            practice_id=practice_id,
            steps=_default_steps(license_type=license_type),
            last_updated_by=updated_by,
            last_updated_at=_now().isoformat(),
        )
        self._flush()
        self.reconcile_due(practice_id=practice_id, owner_user_id=updated_by or "system")
        return emp

    def get_employee(self, practice_id: str, employee_id: str) -> Optional[Employee]:
        emp = self._employees.get(employee_id)
        if emp is None or emp.practice_id != practice_id:
            return None
        return emp

    def list_employees(self, practice_id: str) -> list[Employee]:
        rows = [e for e in self._employees.values() if e.practice_id == practice_id]
        rows.sort(key=lambda e: e.display_name.lower())
        return rows

    def patch_employee(
        self, practice_id: str, employee_id: str, patch: dict[str, Any]
    ) -> Employee:
        assert_no_forbidden_fields(patch)
        emp = self.get_employee(practice_id, employee_id)
        if emp is None:
            raise KeyError(employee_id)
        if "display_name" in patch and patch["display_name"] is not None:
            emp.display_name = str(patch["display_name"]).strip()
        if "title" in patch and patch["title"] is not None:
            emp.title = str(patch["title"]).strip()
        if "license_type" in patch and patch["license_type"] is not None:
            lt = str(patch["license_type"])
            if lt not in ("LCSW", "LSW", "LPC", "admin", "other"):
                raise ValueError(f"unsupported license_type: {lt}")
            emp.license_type = lt  # type: ignore[assignment]
            rec = self._onboarding.get(employee_id)
            if rec:
                if lt == "LSW" and rec.steps.get("supervisionAgreement") == "NA":
                    rec.steps["supervisionAgreement"] = "NotStarted"
                if lt != "LSW":
                    rec.steps["supervisionAgreement"] = "NA"
        if "license_number" in patch and patch["license_number"] is not None:
            emp.license_number = str(patch["license_number"]).strip()
        if "license_expiry" in patch and patch["license_expiry"] is not None:
            emp.license_expiry = str(patch["license_expiry"]).strip()
        if "supervisor_id" in patch and patch["supervisor_id"] is not None:
            emp.supervisor_id = str(patch["supervisor_id"]).strip()
        if "start_date" in patch and patch["start_date"] is not None:
            emp.start_date = str(patch["start_date"]).strip()
        if "drive_folder_url" in patch and patch["drive_folder_url"] is not None:
            emp.drive_folder_url = str(patch["drive_folder_url"]).strip()
        if "active" in patch and patch["active"] is not None:
            emp.active = bool(patch["active"])
        self._flush()
        return emp

    def get_onboarding(
        self, practice_id: str, employee_id: str
    ) -> Optional[OnboardingRecord]:
        emp = self.get_employee(practice_id, employee_id)
        if emp is None:
            return None
        return self._onboarding.get(employee_id)

    def patch_onboarding(
        self,
        practice_id: str,
        employee_id: str,
        *,
        steps: Optional[dict[str, str]] = None,
        notes: Optional[str] = None,
        updated_by: str = "",
    ) -> OnboardingRecord:
        emp = self.get_employee(practice_id, employee_id)
        if emp is None:
            raise KeyError(employee_id)
        rec = self._onboarding.get(employee_id)
        if rec is None:
            raise KeyError(employee_id)
        if steps:
            assert_no_forbidden_fields(steps)
            for key, val in steps.items():
                if key not in ONBOARDING_STEPS:
                    raise ValueError(f"unknown step: {key}")
                if val not in ("NotStarted", "InProgress", "Complete", "NA"):
                    raise ValueError(f"invalid step status: {val}")
                if key == "supervisionAgreement" and emp.license_type != "LSW":
                    rec.steps[key] = "NA"
                else:
                    rec.steps[key] = val
        if notes is not None:
            if len(notes) > 500:
                raise ValueError("notes too long (max 500 chars)")
            rec.notes = notes.strip()
        rec.last_updated_by = updated_by
        rec.last_updated_at = _now().isoformat()
        if rec.overall_status(emp.license_type) == "Complete" and not rec.completed_at:
            rec.completed_at = rec.last_updated_at
        if rec.overall_status(emp.license_type) != "Complete":
            rec.completed_at = ""
        self._flush()
        self.reconcile_due(practice_id=practice_id, owner_user_id=updated_by or "system")
        return rec

    def roster_rows(self, practice_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for emp in self.list_employees(practice_id):
            rec = self._onboarding.get(emp.employee_id)
            if rec is None:
                continue
            expiring = self._expiring_for_employee(emp, within_days=EXPIRY_SOON_DAYS_DEFAULT)
            rows.append(
                {
                    **emp.to_public_dict(),
                    "percent_complete": rec.percent_complete(emp.license_type),
                    "overall_status": rec.overall_status(emp.license_type),
                    "i9_section2_overdue": rec.i9_section2_overdue(emp.start_date),
                    "expiring_soon_count": len(expiring),
                }
            )
        return rows

    def hire_detail(self, practice_id: str, employee_id: str) -> dict[str, Any]:
        emp = self.get_employee(practice_id, employee_id)
        if emp is None:
            raise KeyError(employee_id)
        rec = self._onboarding[employee_id]
        items = [
            c.to_public_dict()
            for c in self._compliance.values()
            if c.practice_id == practice_id and c.employee_id == employee_id
        ]
        hours = self.supervision_hours(practice_id, employee_id)
        return {
            "employee": emp.to_public_dict(),
            "onboarding": rec.to_public_dict(
                license_type=emp.license_type, start_date=emp.start_date
            ),
            "compliance_items": items,
            "supervision": hours,
        }

    def add_compliance_item(
        self,
        *,
        practice_id: str,
        employee_id: str,
        type: ComplianceType,
        issue_date: str = "",
        expiry_date: str = "",
        renewal_interval_months: int = 60,
        document_drive_url: str = "",
        updated_by: str = "",
    ) -> ComplianceItem:
        assert_no_forbidden_fields({"document_drive_url": document_drive_url})
        if self.get_employee(practice_id, employee_id) is None:
            raise KeyError(employee_id)
        if type not in ("Act151", "Act34", "Act114", "License", "Other"):
            raise ValueError(f"unsupported compliance type: {type}")
        issue = issue_date.strip()
        expiry = expiry_date.strip()
        # PA clearances: auto expiry = issue + 60 months
        if type in ("Act151", "Act34", "Act114") and issue and not expiry:
            d = _parse_date(issue)
            if d is not None:
                # approximate 60 months
                month = d.month - 1 + 60
                year = d.year + month // 12
                month = month % 12 + 1
                day = min(d.day, 28)
                expiry = date(year, month, day).isoformat()
                renewal_interval_months = 60
        item = ComplianceItem(
            item_id=str(uuid4()),
            employee_id=employee_id,
            practice_id=practice_id,
            type=type,
            issue_date=issue,
            expiry_date=expiry,
            renewal_interval_months=renewal_interval_months,
            document_drive_url=document_drive_url.strip(),
        )
        self._compliance[item.item_id] = item
        self._flush()
        self.reconcile_due(practice_id=practice_id, owner_user_id=updated_by or "system")
        return item

    def _expiring_for_employee(
        self, emp: Employee, *, within_days: int
    ) -> list[dict[str, Any]]:
        horizon = _today() + timedelta(days=within_days)
        out: list[dict[str, Any]] = []
        if emp.license_expiry:
            ed = _parse_date(emp.license_expiry)
            if ed is not None and ed <= horizon:
                out.append(
                    {
                        "kind": "license",
                        "employee_id": emp.employee_id,
                        "display_name": emp.display_name,
                        "label": f"License ({emp.license_type})",
                        "expiry_date": emp.license_expiry,
                    }
                )
        for c in self._compliance.values():
            if c.employee_id != emp.employee_id or c.practice_id != emp.practice_id:
                continue
            ed = _parse_date(c.expiry_date)
            if ed is not None and ed <= horizon:
                out.append(
                    {
                        "kind": "compliance",
                        "employee_id": emp.employee_id,
                        "display_name": emp.display_name,
                        "label": c.type,
                        "expiry_date": c.expiry_date,
                        "item_id": c.item_id,
                    }
                )
        return out

    def compliance_dashboard(
        self, practice_id: str, *, within_days: int = EXPIRY_SOON_DAYS_DEFAULT
    ) -> dict[str, Any]:
        expiring: list[dict[str, Any]] = []
        i9_overdue: list[dict[str, Any]] = []
        for emp in self.list_employees(practice_id):
            expiring.extend(self._expiring_for_employee(emp, within_days=within_days))
            rec = self._onboarding.get(emp.employee_id)
            if rec and rec.i9_section2_overdue(emp.start_date):
                i9_overdue.append(
                    {
                        "employee_id": emp.employee_id,
                        "display_name": emp.display_name,
                        "start_date": emp.start_date,
                    }
                )
        expiring.sort(key=lambda r: r.get("expiry_date") or "")
        return {
            "within_days": within_days,
            "expiring_soon": expiring,
            "i9_section2_overdue": i9_overdue,
        }

    def add_supervision_entry(
        self,
        *,
        practice_id: str,
        supervisee_id: str,
        supervisor_id: str,
        date_str: str,
        duration_minutes: int,
        format: SupervisionFormat = "Telehealth",
        notes: str = "",
        signed_by_both: bool = False,
    ) -> SupervisionLogEntry:
        emp = self.get_employee(practice_id, supervisee_id)
        if emp is None:
            raise KeyError(supervisee_id)
        if emp.license_type != "LSW":
            raise ValueError("supervision log is only for LSW hires")
        if duration_minutes <= 0 or duration_minutes > 24 * 60:
            raise ValueError("duration_minutes out of range")
        if len(notes) > 500:
            raise ValueError("notes too long (max 500 chars)")
        entry = SupervisionLogEntry(
            entry_id=str(uuid4()),
            practice_id=practice_id,
            supervisee_id=supervisee_id,
            supervisor_id=supervisor_id.strip(),
            date=date_str.strip() or _today().isoformat(),
            duration_minutes=int(duration_minutes),
            format=format,
            notes=notes.strip(),
            signed_by_both=bool(signed_by_both),
        )
        self._supervision[entry.entry_id] = entry
        self._flush()
        return entry

    def supervision_hours(self, practice_id: str, supervisee_id: str) -> dict[str, Any]:
        target = int(
            os.environ.get("ATTUNE_PA_LCSW_HOURS_TARGET") or PA_LCSW_HOURS_TARGET_DEFAULT
        )
        entries = [
            s
            for s in self._supervision.values()
            if s.practice_id == practice_id and s.supervisee_id == supervisee_id
        ]
        entries.sort(key=lambda e: e.date, reverse=True)
        minutes = sum(e.duration_minutes for e in entries)
        hours = round(minutes / 60.0, 2)
        return {
            "supervisee_id": supervisee_id,
            "hours_to_date": hours,
            "minutes_to_date": minutes,
            "target_hours": target,
            "progress_pct": round(min(100.0, 100.0 * hours / target), 1) if target else 0.0,
            "entries": [e.to_public_dict() for e in entries],
        }

    def reconcile_due(self, *, practice_id: str, owner_user_id: str) -> None:
        """Surface I-9 overdue + expiring credentials on Home (people domain)."""
        dash = self.compliance_dashboard(practice_id)
        # I-9
        overdue_ids = {r["employee_id"] for r in dash["i9_section2_overdue"]}
        for emp in self.list_employees(practice_id):
            ref = f"i9:{emp.employee_id}"
            if emp.employee_id in overdue_ids:
                due_engine.upsert(
                    practice_id=practice_id,
                    domain="people",
                    source=HR_SOURCE,
                    title=f"I-9 Section 2 overdue — {emp.display_name}",
                    owner_user_id=owner_user_id,
                    due_at=_now().isoformat(),
                    href="/#onboarding",
                    source_ref=ref,
                    status="open",
                )
            else:
                due_engine.complete(
                    practice_id=practice_id, source=HR_SOURCE, source_ref=ref
                )
        # Expiring
        seen_refs: set[str] = set()
        for row in dash["expiring_soon"]:
            ref = f"expiry:{row.get('item_id') or row['employee_id']}:{row['label']}"
            seen_refs.add(ref)
            due_engine.upsert(
                practice_id=practice_id,
                domain="people",
                source=HR_SOURCE,
                title=f"Expiring soon — {row['display_name']} · {row['label']}",
                owner_user_id=owner_user_id,
                due_at=(row.get("expiry_date") or _now().date().isoformat())
                + "T00:00:00+00:00",
                href="/#onboarding",
                source_ref=ref,
                status="open",
            )
        # Complete stale expiry refs for this practice
        for ob in due_engine.list_open(practice_id):
            if ob.source != HR_SOURCE or not ob.source_ref.startswith("expiry:"):
                continue
            if ob.source_ref not in seen_refs:
                due_engine.complete(
                    practice_id=practice_id,
                    source=HR_SOURCE,
                    source_ref=ob.source_ref,
                )


onboarding_store = OnboardingStore()
