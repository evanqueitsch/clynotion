"""HTTP routes for exclusion screening and the audit log.

Screening changes and match resolution are Admin only. Billing can read a
hire's exclusion status. The audit log is Admin, plus Practice Owner read-only.
There is no route that edits or deletes an audit entry or a screening result.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.access.resolve import assigned_role_id, has_permission, is_owner
from app.auth import CurrentUser
from app.compliance.audit_chain import note, public_entry, show_et, verify_chain
from app.compliance.masking import scrub_text
from app.compliance.pdftext import text_pdf
from app.compliance.schedule import run_scheduled
from app.compliance.screening import (
    ScreeningError,
    filtered_screenings,
    gate,
    public_screening,
    record_manual_medicheck,
    record_test_match,
    resolve_match,
    roster,
    run_screening,
    sam_status,
)
from app.compliance.store import compliance_store

router = APIRouter(tags=["compliance"])


class SettingsBody(BaseModel):
    monthly_screening_enabled: Optional[bool] = None
    integrity_check_enabled: Optional[bool] = None
    drive_folder_id: Optional[str] = None


class ScreenBody(BaseModel):
    person_id: str = ""
    confirm: str = ""


class FixtureBody(BaseModel):
    person_id: str
    list_name: str = "leie"
    csv_text: str = ""


class ResolveBody(BaseModel):
    decision: str
    note: str = ""


class SupervisorBody(BaseModel):
    name: str = ""
    active: Optional[bool] = None
    id: str = ""


def _admin(user: CurrentUser) -> None:
    if is_owner(user) or assigned_role_id(user) == "admin":
        return
    raise HTTPException(status_code=403, detail="You do not have access to that.")


def _audit_reader(user: CurrentUser) -> None:
    role = assigned_role_id(user)
    if is_owner(user) or role in {"admin", "practice_owner"}:
        return
    raise HTTPException(status_code=403, detail="You do not have access to that.")


def _hire_reader(user: CurrentUser) -> None:
    if is_owner(user) or assigned_role_id(user) == "admin":
        return
    if has_permission(user, "tool:billing") or has_permission(user, "capability:hiring_payroll") or has_permission(user, "tool:hiring_onboarding"):
        return
    raise HTTPException(status_code=403, detail="You do not have access to that.")


def _actor(user: CurrentUser) -> str:
    return (user.email or user.username or "").strip()


def _call(fn):
    try:
        return fn()
    except ScreeningError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


def _settings_public(practice_id: str) -> dict[str, Any]:
    settings = compliance_store.settings(practice_id)
    folder = str(settings.get("drive_folder_id") or "").strip()
    return {
        "monthly_screening_enabled": bool(settings.get("monthly_screening_enabled")),
        "integrity_check_enabled": bool(settings.get("integrity_check_enabled")),
        "drive_folder_id": folder,
        "drive_status": "Configured" if folder else "Not configured",
        "sam_status": sam_status(),
        "monthly_detail": "Off. The run is the first business day of the month at 7:00 AM Eastern, and only after this is turned on.",
        "integrity_detail": "Off. The daily check runs only after this is turned on. Verify log runs when you press the button.",
        "drive_detail": (
            "No folder is set, so the daily copy does not run."
            if not folder
            else "A daily copy is sent to the saved Shared Drive folder."
        ),
    }


@router.get("/compliance/settings")
def get_settings(user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return _settings_public(user.practice_id)


@router.put("/compliance/settings")
def put_settings(body: SettingsBody, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    before = compliance_store.settings(user.practice_id)
    updated = compliance_store.save_settings(user.practice_id, body.model_dump(exclude_none=True))
    note(
        user.practice_id,
        action="settings_change",
        object_type="compliance_settings",
        object_id=user.practice_id,
        actor=_actor(user),
        before={key: before.get(key) for key in ("monthly_screening_enabled", "integrity_check_enabled", "drive_folder_id")},
        after={key: updated.get(key) for key in ("monthly_screening_enabled", "integrity_check_enabled", "drive_folder_id")},
    )
    return _settings_public(user.practice_id)


@router.get("/compliance/roster")
def get_roster(user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    from app.compliance.screening import test_people

    people = []
    for item in roster(user.practice_id):
        people.append({"person_id": item["person_id"], "name": item["name"], "kind": item["kind"], "test_record": False})
    tests = []
    for item in test_people(user.practice_id):
        tests.append({"person_id": item["person_id"], "name": item["name"], "kind": "test", "test_record": True})
    return {"people": people, "test_people": tests, "sam_status": sam_status()}


@router.post("/compliance/test-hires")
def post_test_hire(user: CurrentUser) -> dict[str, Any]:
    """Create one flagged test hire. No email, no Drive folder, no personnel record."""
    _admin(user)
    from app.hiring.engine import HiringError, hiring

    try:
        return hiring.create_test_hire(user.practice_id, _actor(user))
    except HiringError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.post("/compliance/screening/run")
def post_run(body: ScreenBody, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    person_ids = [body.person_id] if body.person_id else None
    return _call(
        lambda: run_screening(
            user.practice_id,
            _actor(user),
            person_ids=person_ids,
            purpose="manual",
            confirm=body.confirm,
        )
    )


@router.post("/compliance/screening/fixture")
def post_fixture(body: FixtureBody, user: CurrentUser) -> dict[str, Any]:
    """Record a synthetic TEST possible match. The pasted text is ignored."""
    _admin(user)
    _ = body.csv_text
    _ = body.list_name
    return _call(lambda: record_test_match(user.practice_id, _actor(user), person_id=body.person_id))


@router.get("/compliance/screening")
def list_screenings(
    user: CurrentUser,
    person_id: str = "",
    month: str = "",
    list_name: str = "",
    include_test: bool = False,
) -> dict[str, Any]:
    _admin(user)
    rows = filtered_screenings(
        user.practice_id,
        person_id=person_id,
        month=month,
        list_name=list_name,
        include_test=include_test,
    )
    resolutions = compliance_store.resolutions(user.practice_id)
    return {"results": rows, "resolutions": resolutions}


@router.get("/compliance/screening/hire/{hire_id}")
def hire_status(hire_id: str, user: CurrentUser) -> dict[str, Any]:
    _hire_reader(user)
    return gate(user.practice_id, f"hire:{hire_id}")


@router.post("/compliance/screening/hire/{hire_id}")
def screen_hire(hire_id: str, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return _call(
        lambda: run_screening(
            user.practice_id,
            _actor(user),
            person_ids=[f"hire:{hire_id}"],
            purpose="pre_hire",
        )
    )


@router.post("/compliance/reviews/{result_id}")
def post_review(result_id: str, body: ResolveBody, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return _call(
        lambda: resolve_match(
            user.practice_id,
            result_id,
            decision=body.decision,
            note_text=body.note,
            actor=_actor(user),
        )
    )


@router.post("/compliance/medicheck/manual")
async def post_manual(
    user: CurrentUser,
    person_id: str = Form(...),
    checked_on: str = Form(...),
    comment: str = Form(""),
    upload: UploadFile = File(...),
) -> dict[str, Any]:
    _admin(user)
    data = await upload.read()
    return _call(
        lambda: record_manual_medicheck(
            user.practice_id,
            person_id=person_id,
            checked_on=checked_on,
            filename=upload.filename or "medicheck.pdf",
            data=data,
            actor=_actor(user),
            comment=comment,
        )
    )


@router.post("/compliance/supervisors")
def post_supervisor(body: SupervisorBody, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    try:
        if body.id:
            stored = compliance_store.save_outside_supervisor(
                user.practice_id, {"id": body.id, "active": bool(body.active)}
            )
        else:
            stored = compliance_store.save_outside_supervisor(
                user.practice_id, {"name": body.name, "paid": True, "active": True}
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That supervisor was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    note(
        user.practice_id,
        action="update" if body.id else "create",
        object_type="outside_supervisor",
        object_id=str(stored.get("id") or ""),
        actor=_actor(user),
        diff={"active": stored.get("active"), "paid": stored.get("paid")},
    )
    return {"id": stored.get("id"), "name": stored.get("name"), "active": stored.get("active")}


def _screening_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["Exclusion screening evidence"]
    for item in rows:
        lines.append(
            " | ".join(
                [
                    item.get("checked_at_et") or "",
                    item.get("display_name") or "",
                    item.get("list_name") or "",
                    item.get("result") or "",
                    item.get("list_updated_on") or "",
                    item.get("source_sha256") or "",
                ]
            )
        )
    return lines


def _log_export(user: CurrentUser, kind: str, object_type: str, count: int) -> None:
    note(
        user.practice_id,
        action="export",
        object_type=object_type,
        object_id=kind,
        actor=_actor(user),
        diff={"format": kind, "rows": count},
    )


@router.get("/compliance/screening/export.csv")
def export_screening_csv(
    user: CurrentUser,
    person_id: str = "",
    month: str = "",
    list_name: str = "",
) -> Response:
    _admin(user)
    rows = filtered_screenings(user.practice_id, person_id=person_id, month=month, list_name=list_name)
    _log_export(user, "csv", "exclusion_screening", len(rows))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["checked_at_et", "person_id", "display_name", "list", "result", "list_updated_on", "source_sha256", "detail"])
    for item in rows:
        writer.writerow(
            [
                item["checked_at_et"],
                item["person_id"],
                item["display_name"],
                item["list_name"],
                item["result"],
                item["list_updated_on"],
                item["source_sha256"],
                scrub_text(item["detail"]),
            ]
        )
    return Response(content=buffer.getvalue(), media_type="text/csv")


@router.get("/compliance/screening/export.pdf")
def export_screening_pdf(
    user: CurrentUser,
    person_id: str = "",
    month: str = "",
    list_name: str = "",
) -> Response:
    _admin(user)
    rows = filtered_screenings(user.practice_id, person_id=person_id, month=month, list_name=list_name)
    _log_export(user, "pdf", "exclusion_screening", len(rows))
    return Response(content=text_pdf(_screening_lines(rows), title="Exclusion screening"), media_type="application/pdf")


def _filtered_audit(
    practice_id: str,
    *,
    person: str = "",
    action: str = "",
    object_type: str = "",
    start: str = "",
    end: str = "",
) -> list[dict[str, Any]]:
    rows = [public_entry(item) for item in compliance_store.audit_entries(practice_id)]
    if person:
        needle = person.casefold()
        rows = [
            item
            for item in rows
            if needle in str(item["actor"]).casefold() or needle in str(item["object_id"]).casefold()
        ]
    if action:
        rows = [item for item in rows if item["action"] == action]
    if object_type:
        rows = [item for item in rows if item["object_type"] == object_type]
    if start:
        rows = [item for item in rows if str(item["ts"])[:10] >= start]
    if end:
        rows = [item for item in rows if str(item["ts"])[:10] <= end]
    return rows


@router.get("/compliance/audit")
def list_audit(
    user: CurrentUser,
    person: str = "",
    action: str = "",
    object_type: str = "",
    start: str = "",
    end: str = "",
) -> dict[str, Any]:
    _audit_reader(user)
    rows = _filtered_audit(user.practice_id, person=person, action=action, object_type=object_type, start=start, end=end)
    checks = compliance_store.integrity(user.practice_id)
    latest = checks[-1] if checks else None
    return {
        "entries": rows,
        "integrity": (
            {
                "at": latest.get("at") or "",
                "at_et": show_et(str(latest.get("at") or "")),
                "ok": bool(latest.get("ok")),
                "checked": latest.get("checked") or 0,
                "head_hash": latest.get("head_hash") or "",
            }
            if latest
            else None
        ),
        "read_only": assigned_role_id(user) == "practice_owner" and not is_owner(user),
    }


@router.post("/compliance/audit/verify")
def post_verify(user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return verify_chain(user.practice_id, actor=_actor(user), actor_role=assigned_role_id(user))


@router.api_route("/compliance/audit/{entry_id}", methods=["PUT", "PATCH", "DELETE"])
def reject_audit_edit(entry_id: str, user: CurrentUser) -> None:
    _admin(user)
    raise HTTPException(status_code=405, detail="Audit entries cannot be changed or deleted.")


@router.api_route("/compliance/screening/{result_id}", methods=["PUT", "PATCH", "DELETE"])
def reject_screening_edit(result_id: str, user: CurrentUser) -> None:
    _admin(user)
    raise HTTPException(status_code=405, detail="Screening results cannot be changed or deleted.")


@router.get("/compliance/audit/export.csv")
def export_audit_csv(
    user: CurrentUser,
    person: str = "",
    action: str = "",
    object_type: str = "",
    start: str = "",
    end: str = "",
) -> Response:
    _audit_reader(user)
    prior = _filtered_audit(user.practice_id, person=person, action=action, object_type=object_type, start=start, end=end)
    _log_export(user, "csv", "audit_chain", len(prior) + 1)
    rows = _filtered_audit(user.practice_id, person=person, action=action, object_type=object_type, start=start, end=end)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ts_et", "actor", "actor_role", "action", "object_type", "object_id", "ip", "hash"])
    for item in rows:
        writer.writerow(
            [
                item["ts_et"],
                item["actor"],
                item["actor_role"],
                item["action"],
                item["object_type"],
                item["object_id"],
                item["ip"],
                item["hash"],
            ]
        )
    return Response(content=buffer.getvalue(), media_type="text/csv")


@router.get("/compliance/audit/export.pdf")
def export_audit_pdf(
    user: CurrentUser,
    person: str = "",
    action: str = "",
    object_type: str = "",
    start: str = "",
    end: str = "",
) -> Response:
    _audit_reader(user)
    prior = _filtered_audit(user.practice_id, person=person, action=action, object_type=object_type, start=start, end=end)
    _log_export(user, "pdf", "audit_chain", len(prior) + 1)
    rows = _filtered_audit(user.practice_id, person=person, action=action, object_type=object_type, start=start, end=end)
    lines = ["Audit log"]
    for item in rows:
        lines.append(
            " | ".join(
                [
                    item["ts_et"],
                    item["actor"],
                    item["actor_role"],
                    item["action"],
                    item["object_type"],
                    item["object_id"],
                ]
            )
        )
    return Response(content=text_pdf(lines, title="Audit log"), media_type="application/pdf")


@router.post("/internal/compliance/tick")
def compliance_tick(request: Request) -> dict[str, str]:
    """Same cron secret as weekly hours. Does nothing while the toggles are off."""
    from app.hr.time_routes import _require_cron

    _require_cron(request)
    return run_scheduled()
