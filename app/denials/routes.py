"""Denial analysis HTTP API. De-identified rows only. No model calls."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app.access.permissions import DENIALS_SUMMARY, DENIALS_TOOL
from app.access.resolve import has_permission, is_owner
from app.auth import CurrentUser
from app.denials.deidentify import DeidentifyError
from app.denials.store import DenialsConfigError, denials_store, not_configured_detail
from app.denials.triage import COMPLIANCE_NOTE

router = APIRouter(prefix="/denials", tags=["denials"])


class WorkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = ""
    work_status: str = "open"


class RetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_retention_days: int = Field(ge=1, le=3650)
    row_retention_months: int = Field(ge=1, le=240)
    confirmation: str = ""


class PhiBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phi_mode: bool = False


def _full(user: CurrentUser) -> None:
    if not (is_owner(user) or has_permission(user, DENIALS_TOOL)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")


def _summary(user: CurrentUser) -> str:
    """Return 'full' or 'summary'. Reject everyone else."""
    if is_owner(user) or has_permission(user, DENIALS_TOOL):
        return "full"
    if has_permission(user, DENIALS_SUMMARY):
        return "summary"
    raise HTTPException(status_code=403, detail="You do not have access to that.")


def _admin(user: CurrentUser) -> None:
    if not (is_owner(user) or has_permission(user, "capability:manage_roles")):
        raise HTTPException(status_code=403, detail="Only an Admin can change that.")


def _actor(user: CurrentUser) -> str:
    return (user.email or user.username or "").strip().lower()


def _config_error(exc: DenialsConfigError) -> HTTPException:
    if str(exc) == "unreadable":
        return HTTPException(status_code=409, detail="Denial analysis storage could not be read.")
    return HTTPException(status_code=409, detail=not_configured_detail())


@router.get("")
def denials_home(user: CurrentUser) -> dict:
    access = _summary(user)
    return {
        "configured": denials_store.configured(),
        "phi_mode": False,
        "access": access,
        "compliance_note": COMPLIANCE_NOTE,
    }


@router.get("/dashboard")
def denials_dashboard(user: CurrentUser) -> dict:
    access = _summary(user)
    if not denials_store.configured():
        body = denials_store.empty_dashboard()
        body["access"] = access
        return body
    try:
        body = denials_store.dashboard(user.practice_id, _actor(user))
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    body["access"] = access
    return body


@router.get("/worklist")
def denials_worklist(user: CurrentUser) -> dict:
    _full(user)
    if not denials_store.configured():
        return {
            "configured": False,
            "phi_mode": False,
            "compliance_note": COMPLIANCE_NOTE,
            "items": [],
        }
    try:
        return denials_store.worklist(user.practice_id, _actor(user))
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc


@router.post("/inspect")
async def denials_inspect(
    user: CurrentUser,
    file_type: str = Form(...),
    upload: UploadFile = File(...),
) -> dict:
    _full(user)
    data = await upload.read()
    try:
        return denials_store.inspect(file_type, upload.filename or "", data, user.practice_id)
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    except DeidentifyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/uploads")
async def denials_upload(
    user: CurrentUser,
    file_type: str = Form(...),
    mapping: str = Form(...),
    upload: UploadFile = File(...),
) -> dict:
    _full(user)
    try:
        parsed = json.loads(mapping or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="The column mapping is not valid.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="The column mapping is not valid.")
    data = await upload.read()
    try:
        return denials_store.upload(
            practice_id=user.practice_id,
            actor=_actor(user),
            file_type=file_type,
            filename=upload.filename or "",
            data=data,
            mapping={str(key): str(value) for key, value in parsed.items()},
        )
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    except DeidentifyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/work/{row_id}")
def denials_update_work(row_id: str, body: WorkBody, user: CurrentUser) -> dict:
    _full(user)
    try:
        return denials_store.update_work(
            user.practice_id,
            _actor(user),
            row_id,
            owner=body.owner,
            work_status=body.work_status,
        )
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That work item was not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/export")
def denials_export(user: CurrentUser) -> Response:
    _full(user)
    if not denials_store.configured():
        raise HTTPException(status_code=409, detail=not_configured_detail())
    try:
        text = denials_store.export_csv(user.practice_id, _actor(user))
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=denials-worklist.csv"},
    )


@router.get("/uploads")
def denials_uploads(user: CurrentUser) -> dict:
    _full(user)
    if not denials_store.configured():
        return {"configured": False, "uploads": []}
    try:
        return {"configured": True, "uploads": denials_store.list_uploads(user.practice_id)}
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc


@router.delete("/uploads/{upload_id}")
def denials_delete_upload(upload_id: str, user: CurrentUser) -> dict:
    _full(user)
    try:
        return denials_store.delete_upload(user.practice_id, _actor(user), upload_id)
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That upload was not found.") from exc


@router.get("/settings")
def denials_settings(user: CurrentUser) -> dict:
    _admin(user)
    if not denials_store.configured():
        return {
            "configured": False,
            "phi_mode": False,
            "phi_mode_locked": True,
            "phi_checklist": [
                {"id": "baa", "label": "A signed BAA covers every system that would store claim PHI.", "checked": False, "enabled": False},
                {"id": "minimum", "label": "The minimum-necessary access list for claim PHI is approved.", "checked": False, "enabled": False},
                {"id": "procedure", "label": "The practice has accepted storing claim-level PHI in Clynotion.", "checked": False, "enabled": False},
                {"id": "owner", "label": "An owner has approved turning PHI mode on.", "checked": False, "enabled": False},
            ],
            "upload_retention_days": 90,
            "row_retention_months": 24,
            "audit_retention_years": 6,
            "audit_retention_editable": False,
            "detail": not_configured_detail(),
        }
    try:
        body = denials_store.settings_public(user.practice_id)
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    body["configured"] = True
    return body


@router.put("/settings")
def denials_update_settings(body: RetentionBody, user: CurrentUser) -> dict:
    _admin(user)
    try:
        saved = denials_store.update_settings(
            user.practice_id,
            _actor(user),
            upload_retention_days=body.upload_retention_days,
            row_retention_months=body.row_retention_months,
            confirmation=body.confirmation,
        )
    except DenialsConfigError as exc:
        raise _config_error(exc) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved["configured"] = True
    return saved


@router.post("/phi-mode")
def denials_phi_mode(body: PhiBody, user: CurrentUser) -> dict:
    _admin(user)
    _ = body
    raise HTTPException(
        status_code=409,
        detail="PHI mode is off and cannot be turned on in this phase.",
    )
