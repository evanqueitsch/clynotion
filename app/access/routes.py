"""Role editor and assignment APIs. Admin or break-glass only, except /access/me."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.access.permissions import permission_registry
from app.access.resolve import (
    assigned_role_id,
    has_permission,
    is_owner,
    landing_for,
    permissions_for,
)
from app.access.store import access_store
from app.auth import CurrentUser, User
from app.clinicians import clinician_store

router = APIRouter(prefix="/access", tags=["access"])


class RolePermissionsBody(BaseModel):
    permissions: list[str] = Field(default_factory=list)


class AssignmentBody(BaseModel):
    email: str
    role_id: str = ""


def _admin(user: CurrentUser) -> None:
    if not (is_owner(user) or has_permission(user, "capability:manage_roles")):
        raise HTTPException(status_code=403, detail="Only an Admin can manage roles.")


@router.get("/me")
def access_me(user: CurrentUser) -> dict[str, Any]:
    perms = sorted(permissions_for(user))
    return {
        "landing": landing_for(user),
        "role_id": assigned_role_id(user),
        "permissions": perms,
        "can_manage_roles": "capability:manage_roles" in perms,
        "can_payroll_dashboard": "capability:payroll_dashboard" in perms,
        "can_correct_payroll": "capability:payroll_correct" in perms,
    }


@router.get("/effective")
def effective_permissions(email: str, user: CurrentUser) -> dict[str, Any]:
    """Admin check of one mailbox. Does not sign in as that person."""
    _admin(user)
    cleaned = (email or "").strip().lower()
    if "@" not in cleaned:
        raise HTTPException(status_code=422, detail="A staff email is required.")
    subject = User(user_id="access-check", username="", practice_id=user.practice_id, email=cleaned)
    return {
        "email": cleaned,
        "role_id": assigned_role_id(subject),
        "permissions": sorted(permissions_for(subject)),
    }


@router.get("/roles")
def list_roles(user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return {"roles": access_store.roles_public(user.practice_id), "permissions": permission_registry()}


@router.put("/roles/{role_id}")
def update_role(role_id: str, body: RolePermissionsBody, user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    try:
        return access_store.update_role_permissions(
            user.practice_id, role_id, body.permissions, actor_email=user.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assignments")
def list_assignments(user: CurrentUser) -> dict[str, Any]:
    _admin(user)
    return {"assignments": access_store.assignments_public(user.practice_id)}


@router.put("/assignments")
def put_assignment(body: AssignmentBody, user: CurrentUser) -> dict[str, bool]:
    _admin(user)
    try:
        access_store.set_assignment(
            user.practice_id, body.email, body.role_id, actor_email=user.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/staff")
def staff_for_roles(user: CurrentUser) -> list[dict[str, str]]:
    """Workspace roster emails an Admin can assign. No tokens."""
    _admin(user)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        from app.workspace_directory import list_directory_users

        for person in list_directory_users(user.practice_id):
            email = (person.email or "").strip().lower()
            if not email or email in seen or person.suspended:
                continue
            seen.add(email)
            rows.append({"email": email, "display_name": person.display_name or email, "source": "directory"})
    except Exception:
        pass
    if not rows:
        for clin in clinician_store.list_for_practice(user.practice_id):
            email = (clin.email or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            rows.append({"email": email, "display_name": clin.display_name or email, "source": "roster"})
    assignments = {row["email"]: row["role_id"] for row in access_store.assignments_public(user.practice_id)}
    for row in rows:
        row["role_id"] = assignments.get(row["email"], "")
    rows.sort(key=lambda item: item["display_name"].casefold())
    return rows
