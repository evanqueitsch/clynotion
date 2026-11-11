"""Credentialing HTTP routes. The guard checks the tool; writes check capabilities."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.access.permissions import (
    CREDENTIALING_MANAGE,
    CREDENTIALING_PAYERS,
    CREDENTIALING_SENSITIVE,
)
from app.access.resolve import has_permission, is_owner
from app.auth import CurrentUser
from app.credentialing.csvio import build_plan
from app.credentialing.store import SECRET_FIELDS, credentialing_store

router = APIRouter(prefix="/credentialing", tags=["credentialing"])


class SubjectBody(BaseModel):
    id: str = ""
    name: str
    kind: str = "clinician"
    license_type: str = ""
    hire_date: str = ""
    device: str = ""
    license_state: str = ""
    license_expires_on: str = ""
    caqh_attested_on: str = ""
    supervisor_id: str = ""
    legal_name: str = ""
    service_address: str = ""
    active: bool = True
    npi: Optional[str] = None
    tin: Optional[str] = None
    license_number: Optional[str] = None
    caqh_id: Optional[str] = None
    taxonomy: Optional[str] = None
    promise_id: Optional[str] = None


class PayerBody(BaseModel):
    id: str = ""
    name: str
    type: str
    family_members: list[str] = Field(default_factory=list)
    accepts_licenses: list[str] = Field(default_factory=list)
    notes: str = ""
    parent_payer: str = ""
    counties: list[str] = Field(default_factory=list)


class EnrollmentBody(BaseModel):
    subject_id: str
    payer_id: str
    status: str = "unknown"
    submitted_on: str = ""
    effective_on: str = ""
    next_follow_up: str = ""
    note: str = ""
    payer_provider_id: Optional[str] = None


class RevealBody(BaseModel):
    subject_id: str
    field: str
    payer_id: str = ""


def _can(user: CurrentUser, permission: str) -> bool:
    return is_owner(user) or has_permission(user, permission)


def _need(user: CurrentUser, permission: str) -> None:
    if not _can(user, permission):
        raise HTTPException(status_code=403, detail="You do not have access to that.")


def _flags(user: CurrentUser) -> dict[str, bool]:
    return {
        "can_manage": _can(user, CREDENTIALING_MANAGE),
        "can_payers": _can(user, CREDENTIALING_PAYERS),
        "can_sensitive": _can(user, CREDENTIALING_SENSITIVE),
    }


def _value_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


async def _upload_text(upload: Optional[UploadFile]) -> Optional[str]:
    if upload is None or not (upload.filename or "").strip():
        return None
    raw = await upload.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="CSV files must be UTF-8.") from exc


@router.get("/grid")
def credentialing_grid(user: CurrentUser) -> dict[str, Any]:
    return {**_flags(user), **credentialing_store.public_grid(user.practice_id)}


@router.get("/audit")
def credentialing_audit(user: CurrentUser, subject_id: str = "") -> dict[str, Any]:
    return {"events": credentialing_store.audit_for(user.practice_id, subject_id)}


@router.put("/subjects")
def put_subject(body: SubjectBody, user: CurrentUser) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    try:
        saved = credentialing_store.save_subject(
            user.practice_id, body.model_dump(), actor=user.email or user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Subject not found.") from exc
    except ValueError as exc:
        raise _value_error(exc) from exc
    return saved


@router.post("/payers")
def post_payer(body: PayerBody, user: CurrentUser) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    _need(user, CREDENTIALING_PAYERS)
    try:
        return credentialing_store.save_payer(
            user.practice_id, body.model_dump(), actor=user.email or user.username
        )
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.put("/payers/{payer_id}")
def put_payer(payer_id: str, body: PayerBody, user: CurrentUser) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    _need(user, CREDENTIALING_PAYERS)
    payload = body.model_dump()
    payload["id"] = payer_id
    try:
        return credentialing_store.save_payer(
            user.practice_id, payload, actor=user.email or user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Payer not found.") from exc
    except ValueError as exc:
        raise _value_error(exc) from exc


@router.post("/payers/{payer_id}/archive")
def archive_payer(payer_id: str, user: CurrentUser) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    _need(user, CREDENTIALING_PAYERS)
    try:
        return credentialing_store.archive_payer(
            user.practice_id, payer_id, actor=user.email or user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Payer not found.") from exc


@router.put("/enrollments")
def put_enrollment(body: EnrollmentBody, user: CurrentUser) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    try:
        return credentialing_store.save_enrollment(
            user.practice_id, body.model_dump(), actor=user.email or user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Enrollment not found.") from exc
    except ValueError as exc:
        raise _value_error(exc) from exc


def _reveal(user: CurrentUser, body: RevealBody, action: str) -> dict[str, str]:
    _need(user, CREDENTIALING_SENSITIVE)
    if body.field not in SECRET_FIELDS:
        raise HTTPException(status_code=422, detail="Unknown field.")
    try:
        value = credentialing_store.reveal(
            user.practice_id,
            actor=user.email or user.username,
            subject_id=body.subject_id,
            field=body.field,
            payer_id=body.payer_id,
            action=action,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That field is not on file.") from exc
    return {"value": value}


@router.post("/reveal")
def reveal_value(body: RevealBody, user: CurrentUser) -> dict[str, str]:
    return _reveal(user, body, "reveal")


@router.post("/copy")
def copy_value(body: RevealBody, user: CurrentUser) -> dict[str, str]:
    return _reveal(user, body, "copy")


@router.post("/import/preview")
async def import_preview(
    user: CurrentUser,
    payers: Optional[UploadFile] = File(None),
    people: Optional[UploadFile] = File(None),
    grid: Optional[UploadFile] = File(None),
) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    return await _plan_response(user, payers, people, grid)


@router.post("/import/commit")
async def import_commit(
    user: CurrentUser,
    payers: Optional[UploadFile] = File(None),
    people: Optional[UploadFile] = File(None),
    grid: Optional[UploadFile] = File(None),
) -> dict[str, Any]:
    _need(user, CREDENTIALING_MANAGE)
    plan, response = await _parsed(user, payers, people, grid)
    if plan.errors:
        raise HTTPException(status_code=422, detail=response)
    backup = credentialing_store.commit_import(
        user.practice_id, plan, actor=user.email or user.username
    )
    return {**response, "backup": backup, "ok": True}


@router.get("/export/{kind}")
def export_csv(kind: str, user: CurrentUser) -> PlainTextResponse:
    _need(user, CREDENTIALING_MANAGE)
    texts = credentialing_store.export_texts(
        user.practice_id, include_secrets=_can(user, CREDENTIALING_SENSITIVE)
    )
    if kind not in texts:
        raise HTTPException(status_code=404, detail="Unknown export.")
    filename = {"payers": "payers.csv", "people": "people.csv", "grid": "grid.csv"}[kind]
    return PlainTextResponse(
        texts[kind],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _plan_response(
    user: CurrentUser,
    payers: Optional[UploadFile],
    people: Optional[UploadFile],
    grid: Optional[UploadFile],
) -> dict[str, Any]:
    _, response = await _parsed(user, payers, people, grid)
    return response


async def _parsed(
    user: CurrentUser,
    payers: Optional[UploadFile],
    people: Optional[UploadFile],
    grid: Optional[UploadFile],
) -> tuple[Any, dict[str, Any]]:
    current = credentialing_store.public_grid(user.practice_id)
    plan = build_plan(
        payers_csv=await _upload_text(payers),
        people_csv=await _upload_text(people),
        grid_csv=await _upload_text(grid),
        existing_payers={row["name"] for row in current["payers"] if not row.get("archived")},
        existing_subjects={row["name"] for row in current["subjects"]},
    )
    return plan, {"errors": plan.errors, "warnings": plan.warnings, "changes": plan.changes}
