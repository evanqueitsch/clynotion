"""Employee View routes. Rollout defaults off. Responses never include audit values."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.access.permissions import (
    CREDENTIALING_MANAGE,
    HR_RECORDS_MANAGE,
    HR_RECORDS_VIEW,
    HR_SENSITIVE,
)
from app.access.resolve import has_permission, sees_every_module
from app.auth import CurrentUser
from app.employees.store import CONTACT_FIELDS, employees_store

router = APIRouter(prefix="/employee", tags=["employee"])


class ContactBody(BaseModel):
    preferred_name: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    personal_phone: str = ""
    personal_email: str = ""
    work_phone: str = ""
    emergency_name: str = ""
    emergency_phone: str = ""


class SpecialtiesBody(BaseModel):
    names: list[str] = Field(default_factory=list)


class TrainingItem(BaseModel):
    name: str = ""
    completed_on: str = ""


class TrainingsBody(BaseModel):
    items: list[TrainingItem] = Field(default_factory=list)


class LinkBody(BaseModel):
    subject_id: str = ""


class WorkspaceLinkBody(BaseModel):
    email: str = ""


class CertificationItem(BaseModel):
    name: str = ""
    issued_on: str = ""
    expires_on: str = ""


class CertificationsBody(BaseModel):
    items: list[CertificationItem] = Field(default_factory=list)


class ChangeBody(BaseModel):
    field: str
    value: str = ""


class RolloutBody(BaseModel):
    enabled: bool = False


class QualificationTypeBody(BaseModel):
    id: str = ""
    name: str = ""
    kind: str = "certification"


class QualificationTypesBody(BaseModel):
    items: list[QualificationTypeBody] = Field(default_factory=list)


def _actor(user: CurrentUser) -> str:
    return (user.email or "").strip().lower()


def _open(user: CurrentUser) -> None:
    if employees_store.rollout_enabled(user.practice_id):
        return
    if sees_every_module(user):
        return
    raise HTTPException(status_code=403, detail="Employee View is off.")


def _manage(user: CurrentUser) -> None:
    if sees_every_module(user) or has_permission(user, HR_RECORDS_MANAGE) or has_permission(user, CREDENTIALING_MANAGE):
        return
    raise HTTPException(status_code=403, detail="You do not have access to that.")


def _call(fn):
    try:
        return fn()
    except KeyError:
        raise HTTPException(status_code=404, detail="Record not found.")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/me")
def employee_me(user: CurrentUser) -> dict[str, Any]:
    _open(user)
    return employees_store.own(user.practice_id, _actor(user), user.username or "")


@router.put("/me/contact")
def save_my_contact(body: ContactBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    fields = {field: getattr(body, field) for field in CONTACT_FIELDS}
    return _call(lambda: employees_store.save_contact(user.practice_id, _actor(user), fields, _actor(user)))


@router.put("/me/specialties")
def save_my_specialties(body: SpecialtiesBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    return _call(
        lambda: employees_store.save_specialties(user.practice_id, _actor(user), body.names, _actor(user))
    )


@router.put("/me/trainings")
def save_my_trainings(body: TrainingsBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    items = [item.model_dump() for item in body.items]
    return _call(lambda: employees_store.save_trainings(user.practice_id, _actor(user), items, _actor(user)))


@router.put("/me/certifications")
def save_my_certifications(body: CertificationsBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    items = [item.model_dump() for item in body.items]
    return _call(
        lambda: employees_store.save_certifications(user.practice_id, _actor(user), items, _actor(user))
    )


@router.put("/me/link")
def save_my_link(body: LinkBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    return _call(
        lambda: employees_store.set_link(user.practice_id, _actor(user), body.subject_id, _actor(user))
    )


@router.post("/me/changes")
def request_my_change(body: ChangeBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    return _call(
        lambda: employees_store.request_change(
            user.practice_id, _actor(user), body.field, body.value, _actor(user)
        )
    )


@router.get("/directory")
def employee_directory(user: CurrentUser) -> dict[str, Any]:
    _open(user)
    if not (sees_every_module(user) or has_permission(user, HR_RECORDS_VIEW)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    return {
        "rollout_enabled": employees_store.rollout_enabled(user.practice_id),
        "can_rollout": sees_every_module(user),
        "people": employees_store.directory(user.practice_id, _actor(user)),
    }


@router.get("/people/{key}")
def employee_person(key: str, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    if not (sees_every_module(user) or has_permission(user, HR_RECORDS_VIEW)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    reveal = sees_every_module(user) or has_permission(user, HR_SENSITIVE)
    return _call(lambda: employees_store.view_record(user.practice_id, key, _actor(user), reveal=reveal))


@router.put("/people/{key}/workspace")
def link_workspace(key: str, body: WorkspaceLinkBody, user: CurrentUser) -> dict[str, str]:
    _open(user)
    _manage(user)
    return _call(
        lambda: employees_store.link_workspace_account(user.practice_id, key, body.email, _actor(user))
    )


@router.put("/people/{email}/profile")
def link_profile(email: str, body: LinkBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    _manage(user)
    return _call(
        lambda: employees_store.set_link(user.practice_id, email, body.subject_id, _actor(user))
    )


@router.post("/people/{email}/reveal")
def reveal_person(email: str, user: CurrentUser) -> dict[str, str]:
    _open(user)
    if not (sees_every_module(user) or has_permission(user, HR_SENSITIVE)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    return _call(lambda: employees_store.reveal_contact(user.practice_id, email, _actor(user)))


@router.put("/people/{email}/contact")
def correct_contact(email: str, body: ContactBody, user: CurrentUser) -> dict[str, Any]:
    _open(user)
    _manage(user)
    fields = {field: getattr(body, field) for field in CONTACT_FIELDS}
    return _call(lambda: employees_store.save_contact(user.practice_id, email, fields, _actor(user)))


@router.get("/export.csv")
def export_people(user: CurrentUser) -> Response:
    _open(user)
    if not (sees_every_module(user) or has_permission(user, HR_RECORDS_VIEW)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    sensitive = sees_every_module(user) or has_permission(user, HR_SENSITIVE)
    text = employees_store.export_csv(user.practice_id, _actor(user), sensitive=sensitive)
    return Response(content=text, media_type="text/csv")


@router.get("/audit")
def employee_audit(user: CurrentUser) -> dict[str, Any]:
    _open(user)
    if not (sees_every_module(user) or has_permission(user, HR_RECORDS_VIEW)):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    return {"events": employees_store.audit_events(user.practice_id)}


@router.put("/rollout")
def put_rollout(body: RolloutBody, user: CurrentUser) -> dict[str, bool]:
    if not sees_every_module(user):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    enabled = employees_store.set_rollout(user.practice_id, body.enabled, _actor(user))
    return {"enabled": enabled}


@router.put("/qualification-types")
def put_qualification_types(body: QualificationTypesBody, user: CurrentUser) -> dict[str, Any]:
    if not sees_every_module(user):
        raise HTTPException(status_code=403, detail="You do not have access to that.")
    items = employees_store.set_qualification_types(
        user.practice_id,
        [item.model_dump() for item in body.items],
        _actor(user),
    )
    return {"items": items}
