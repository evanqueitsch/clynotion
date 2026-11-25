"""Admin hiring routes and the narrow Billing payroll handoff."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from app.access.permissions import HIRING_PAYROLL, OFFER_MANAGE, ONBOARDING_SENSITIVE
from app.access.resolve import has_permission, is_owner, sees_every_module
from app.auth import CurrentUser
from app.hiring.engine import HiringError, hiring

router = APIRouter()


class SettingsBody(BaseModel):
    offer_response_days: Optional[int] = None
    admin_reminder_days_before: Optional[int] = None
    candidate_emails_enabled: Optional[bool] = None
    candidate_reminder_enabled: Optional[bool] = None
    candidate_reminder_days_before: Optional[int] = None
    packet_link_days: Optional[int] = None
    qa_email_allowlist: Optional[list[str]] = None
    qa_portal_bypass: Optional[bool] = None
    offer_supervisor_name_default: Optional[str] = None
    offer_supervisor_title_default: Optional[str] = None
    business_closed_days: Optional[list[str]] = None
    i9_copy_documents: Optional[bool] = None
    vault_min_days_personnel: Optional[int] = None
    vault_min_days_i9: Optional[int] = None
    hiring_rollout: Optional[bool] = None
    supervision_agreement_approved: Optional[bool] = None
    staff_handoff_emails_enabled: Optional[bool] = None
    wizard_email_override: Optional[str] = None
    staff_greeting_name: Optional[str] = None
    staff_handoff_subject: Optional[str] = None
    staff_handoff_intro: Optional[str] = None
    staff_handoff_steps: Optional[str] = None
    staff_handoff_closing: Optional[str] = None
    staff_handoff_signature: Optional[str] = None
    candidate_packet_subject: Optional[str] = None
    candidate_packet_intro: Optional[str] = None
    candidate_packet_steps: Optional[str] = None
    candidate_packet_closing: Optional[str] = None
    candidate_packet_signature: Optional[str] = None
    additional_documents_subject: Optional[str] = None
    additional_documents_intro: Optional[str] = None
    additional_documents_closing: Optional[str] = None


class OfferBody(BaseModel):
    display_name: str = ""
    personal_email: str = ""
    employment_type: str = ""
    offer_date: str = ""
    candidate_full_name: str = ""
    candidate_credentials: str = ""
    candidate_first_name: str = ""
    position_title: str = ""
    supervisor_title: str = ""
    supervisor_name: str = ""
    hourly_rate: str = ""
    licensed_rate: str = ""
    caseload_range: str = ""
    qsehra_monthly_amount: str = ""
    qsehra_wait_days: str = ""
    start_date: str = ""
    response_deadline: str = ""
    direct_client_access: Optional[bool] = None
    resume_id: str = ""


class SendBody(BaseModel):
    channel: str


class ReasonBody(BaseModel):
    reason: str = ""
    note: str = ""


class AcceptBody(BaseModel):
    typed_name: str = ""


class ItemBody(BaseModel):
    status: str
    dates: dict[str, str] = Field(default_factory=dict)
    where_original_lives: str = ""
    first_day_worked_for_pay: str = ""
    note: Optional[str] = None


class ActivateBody(BaseModel):
    work_email: str = ""


class PayrollBody(BaseModel):
    status: str


class HandoffBody(BaseModel):
    form_keys: list[str] = Field(default_factory=list)


def _fail(exc: HiringError) -> None:
    if exc.field:
        raise HTTPException(
            status_code=exc.status,
            detail={"message": exc.detail, "field": exc.field},
        )
    raise HTTPException(status_code=exc.status, detail=exc.detail)


def _actor(user: CurrentUser) -> str:
    return (user.email or user.username or "admin").strip().lower()


def _offer_admin(user: CurrentUser) -> bool:
    if sees_every_module(user) or is_owner(user):
        return has_permission(user, OFFER_MANAGE) or is_owner(user)
    return hiring.rollout_enabled(user.practice_id) and has_permission(user, OFFER_MANAGE)


def _require_admin(user: CurrentUser) -> None:
    if not _offer_admin(user):
        raise HTTPException(status_code=403, detail="You do not have access to that.")


def _payroll_actor(user: CurrentUser) -> bool:
    return is_owner(user) or has_permission(user, HIRING_PAYROLL)


def _base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/hiring/bootstrap")
def bootstrap(response: Response, user: CurrentUser) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        if _offer_admin(user):
            return hiring.list_offers(user.practice_id)
        if _payroll_actor(user):
            return hiring.list_billing(user.practice_id)
    except HiringError as exc:
        _fail(exc)
    raise HTTPException(status_code=403, detail="You do not have access to that.")


@router.put("/hiring/settings")
def put_settings(body: SettingsBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    patch = body.model_dump(exclude_none=True)
    try:
        return hiring.update_settings(user.practice_id, patch, _actor(user))
    except HiringError as exc:
        _fail(exc)


class WizardPreviewBody(BaseModel):
    kind: str = "candidate"
    subject: str = ""
    intro: str = ""
    steps: str = ""
    closing: str = ""
    signature: str = ""
    staff_greeting_name: Optional[str] = None


@router.post("/hiring/wizard-email-preview")
def wizard_email_preview(body: WizardPreviewBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.preview_wizard_email(
            user.practice_id,
            body.kind,
            {
                "subject": body.subject,
                "intro": body.intro,
                "steps": body.steps,
                "closing": body.closing,
                "signature": body.signature,
                **(
                    {"staff_greeting_name": body.staff_greeting_name}
                    if body.staff_greeting_name is not None
                    else {}
                ),
            },
        )
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/wizard-email-test")
def wizard_email_test(user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.send_test_wizard_emails(user.practice_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/resume-reader/health")
def resume_reader_health(user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    from app.hiring.resume import reader_health

    return reader_health()


@router.post("/hiring/resumes")
async def post_resume(user: CurrentUser, resume: UploadFile = File(...)) -> dict[str, Any]:
    _require_admin(user)
    data = await resume.read()
    try:
        return hiring.upload_resume(user.practice_id, data, resume.filename or "", _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers")
def post_offer(body: OfferBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.create_offer(user.practice_id, body.model_dump(), _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/offers/{offer_id}")
def get_offer(offer_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.get_offer(user.practice_id, offer_id)
    except HiringError as exc:
        _fail(exc)


@router.put("/hiring/offers/{offer_id}")
def put_offer(offer_id: str, body: OfferBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.update_offer(user.practice_id, offer_id, body.model_dump(), _actor(user))
    except HiringError as exc:
        _fail(exc)


class DeleteBody(BaseModel):
    confirm: str = ""

    @field_validator("confirm", mode="before")
    @classmethod
    def blank_when_missing(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


@router.post("/hiring/offers/{offer_id}/delete")
def delete_offer(offer_id: str, body: DeleteBody, response: Response, user: CurrentUser) -> dict[str, bool]:
    _require_admin(user)
    response.headers["Cache-Control"] = "no-store"
    try:
        hiring.delete_offer(user.practice_id, offer_id, body.confirm, _actor(user))
    except HiringError as exc:
        _fail(exc)
    return {"deleted": True}


@router.post("/hiring/offers/{offer_id}/drive")
def retry_drive(offer_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.retry_personnel_folder(user.practice_id, offer_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/revise")
def revise_offer(offer_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.revise_offer(user.practice_id, offer_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/send")
def send_offer(offer_id: str, body: SendBody, request: Request, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.send_offer(user.practice_id, offer_id, body.channel, _actor(user), _base(request))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/withdraw")
def withdraw(offer_id: str, body: ReasonBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.withdraw(user.practice_id, offer_id, body.reason, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/decline")
def decline(offer_id: str, body: ReasonBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.admin_decline(user.practice_id, offer_id, body.reason, body.note, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/accept")
def accept_offer(offer_id: str, body: AcceptBody, request: Request, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.paper_accept(
            user.practice_id,
            offer_id,
            body.typed_name,
            _actor(user),
            request.client.host if request.client else "",
            request.headers.get("user-agent", ""),
            None,
        )
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/accept-paper")
async def accept_paper(
    offer_id: str,
    request: Request,
    user: CurrentUser,
    accepted_on: str = Form(""),
    typed_name: str = Form(""),
    upload: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    _require_admin(user)
    data = await upload.read() if upload is not None else None
    try:
        return hiring.paper_accept(
            user.practice_id,
            offer_id,
            typed_name,
            _actor(user),
            request.client.host if request.client else "",
            request.headers.get("user-agent", ""),
            data,
            accepted_on,
            require_upload=True,
            require_date=True,
        )
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/reissue")
def reissue(offer_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.reissue(user.practice_id, offer_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/offers/{offer_id}/force-expire")
def force_expire(offer_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.force_expire(user.practice_id, offer_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/offers/{offer_id}/{kind}")
def download_offer(offer_id: str, kind: str, user: CurrentUser) -> Response:
    _require_admin(user)
    if kind not in {"pdf", "docx", "resume"}:
        raise HTTPException(status_code=404, detail="Not found.")
    try:
        data, mime, name = hiring.offer_bytes(user.practice_id, offer_id, kind, _actor(user))
    except HiringError as exc:
        _fail(exc)
    from app.compliance.audit_chain import note_from_request

    note_from_request(
        user.practice_id,
        action="download",
        object_type="offer_file",
        object_id=offer_id,
        actor=_actor(user),
        diff={"kind": kind},
    )
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/hiring/hires/{hire_id}/items/{key}")
def set_item(hire_id: str, key: str, body: ItemBody, user: CurrentUser) -> dict[str, Any]:
    admin = _offer_admin(user)
    if not admin and not _payroll_actor(user):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    if key in {"i9_section1", "i9_section2", "i9_scan_uploaded"} and not admin:
        if key != "i9_section2" or not has_permission(user, HIRING_PAYROLL):
            raise HTTPException(status_code=403, detail="You do not have access to that.")
    try:
        return hiring.set_item(
            user.practice_id,
            hire_id,
            key,
            status=body.status,
            actor=_actor(user),
            dates=body.dates,
            where=body.where_original_lives,
            first_day=body.first_day_worked_for_pay,
            note=body.note,
            billing=not admin,
        )
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/items/{key}/file")
async def upload_item(hire_id: str, key: str, user: CurrentUser, upload: UploadFile = File(...)) -> dict[str, Any]:
    _require_admin(user)
    if key in {"i9_section1", "i9_section2", "i9_scan_uploaded"} and not has_permission(user, ONBOARDING_SENSITIVE):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    data = await upload.read()
    try:
        return hiring.upload_item(user.practice_id, hire_id, key, data, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/documents/{document_id}")
def download_document(document_id: str, user: CurrentUser) -> Response:
    _require_admin(user)
    try:
        data, mime, name = hiring.document_bytes(
            user.practice_id,
            document_id,
            _actor(user),
            allow_sensitive=has_permission(user, ONBOARDING_SENSITIVE) or is_owner(user),
        )
    except HiringError as exc:
        _fail(exc)
    from app.compliance.audit_chain import note_from_request

    note_from_request(
        user.practice_id,
        action="download",
        object_type="personnel_document",
        object_id=document_id,
        actor=_actor(user),
        diff={"kind": "file"},
    )
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/hiring/hires/{hire_id}/packet-complete")
def packet_complete(hire_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.mark_packet_complete(user.practice_id, hire_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/packet-undo")
def packet_undo(hire_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.undo_packet_complete(user.practice_id, hire_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/delete")
def delete_hire(hire_id: str, body: DeleteBody, user: CurrentUser) -> dict[str, bool]:
    _require_admin(user)
    try:
        hiring.delete_hire(user.practice_id, hire_id, body.confirm, _actor(user))
    except HiringError as exc:
        _fail(exc)
    return {"deleted": True}


@router.post("/hiring/hires/{hire_id}/activate")
def activate(hire_id: str, body: ActivateBody, user: CurrentUser) -> dict[str, Any]:
    admin = _offer_admin(user)
    if not admin and not _payroll_actor(user):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    try:
        return hiring.activate(user.practice_id, hire_id, _actor(user), body.work_email, billing=not admin)
    except HiringError as exc:
        _fail(exc)


class QbInviteBody(BaseModel):
    sent: bool = True


@router.post("/hiring/hires/{hire_id}/qb-invite")
def qb_invite(hire_id: str, body: QbInviteBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.mark_qb_invite(user.practice_id, hire_id, body.sent, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/additional-documents/preview")
def additional_preview(hire_id: str, body: HandoffBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.preview_additional_documents(user.practice_id, hire_id, body.form_keys)
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/additional-documents")
def additional_documents(hire_id: str, body: HandoffBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.send_additional_documents(user.practice_id, hire_id, body.form_keys, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/handoff")
def handoff(hire_id: str, body: HandoffBody, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.handoff(user.practice_id, hire_id, body.form_keys, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/recall")
def recall(hire_id: str, user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.recall_handoff(user.practice_id, hire_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/hires/{hire_id}/packet.zip")
def packet_zip(hire_id: str, body: HandoffBody, user: CurrentUser) -> Response:
    _require_admin(user)
    try:
        data, name = hiring.packet_zip(user.practice_id, hire_id, body.form_keys)
    except HiringError as exc:
        _fail(exc)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/hiring/forms")
def forms(user: CurrentUser) -> dict[str, Any]:
    _require_admin(user)
    try:
        return hiring.form_flags_and_list(user.practice_id)
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/forms/{form_key}")
def form_file(form_key: str, user: CurrentUser) -> Response:
    _require_admin(user)
    try:
        data, mime, name = hiring.form_bytes(user.practice_id, form_key)
    except HiringError as exc:
        _fail(exc)
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.delete("/hiring/candidates/{candidate_id}")
def purge(candidate_id: str, user: CurrentUser) -> dict[str, int]:
    _require_admin(user)
    try:
        return hiring.purge_candidate(user.practice_id, candidate_id, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/hiring/payroll")
def payroll(user: CurrentUser) -> dict[str, Any]:
    if not (is_owner(user) or has_permission(user, HIRING_PAYROLL)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    try:
        return {"hires": hiring.payroll_queue(user.practice_id)}
    except HiringError as exc:
        _fail(exc)


@router.post("/hiring/payroll/hires/{hire_id}/items/{key}")
def payroll_item(hire_id: str, key: str, body: PayrollBody, user: CurrentUser) -> dict[str, Any]:
    if not (is_owner(user) or has_permission(user, HIRING_PAYROLL)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    try:
        return hiring.payroll_set(user.practice_id, hire_id, key, body.status, _actor(user))
    except HiringError as exc:
        _fail(exc)


@router.get("/employee/me/hiring-documents")
def my_documents(user: CurrentUser) -> dict[str, Any]:
    try:
        return {"documents": hiring.documents_for_email(user.practice_id, user.email)}
    except HiringError as exc:
        _fail(exc)
