"""Billing home and the QuickBooks permission check. No claims and no money movement."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.access.permissions import QUICKBOOKS_MONEY
from app.access.resolve import has_permission, is_owner
from app.auth import CurrentUser
from app.comply.registry import ensure_seeded_clocks
from app.credentialing.digest import digest_enabled, send_weekly_digest
from app.credentialing.reminders import sync_reminders
from app.credentialing.store import credentialing_store
from app.platform.due import due_engine

router = APIRouter(tags=["billing"])

BILLING_CODES = {"o4": "Encounter form reconciliation against notes", "o12": "Claims aging review — 365-day filing bar"}


class DigestBody(BaseModel):
    enabled: bool


def _admin(user: CurrentUser) -> None:
    if not (is_owner(user) or has_permission(user, "capability:manage_roles")):
        raise HTTPException(status_code=403, detail="Only an Admin can change that.")


def _money(user: CurrentUser) -> None:
    if not has_permission(user, QUICKBOOKS_MONEY):
        raise HTTPException(status_code=403, detail="You do not have access to that.")


def _billing_obligations(practice_id: str) -> list[dict[str, Any]]:
    rows = []
    for ob in due_engine.list_open(practice_id):
        if ob.source == "compliance_registry" and ob.source_ref in BILLING_CODES:
            rows.append(
                {
                    "obligation_id": ob.obligation_id,
                    "code": ob.source_ref,
                    "title": ob.title,
                    "due_at": ob.due_at,
                    "status": ob.status,
                    "href": "/#comply",
                }
            )
    rows.sort(key=lambda item: item["code"])
    return rows


def _action_items(practice_id: str) -> list[dict[str, str]]:
    bands = due_engine.bands(practice_id)
    items = []
    for ob in bands["overdue"] + bands["this_week"]:
        if ob.domain != "credentialing":
            continue
        items.append(
            {
                "title": ob.title,
                "due_at": ob.due_at,
                "href": ob.href,
                "band": "overdue" if ob in bands["overdue"] else "this_week",
            }
        )
    return items


@router.get("/billing")
def billing_home(user: CurrentUser) -> dict[str, Any]:
    ensure_seeded_clocks(practice_id=user.practice_id, owner_user_id=user.user_id)
    counts = sync_reminders(user.practice_id, user.user_id)
    return {
        "digest_enabled": digest_enabled(user.practice_id),
        "can_toggle_digest": is_owner(user) or has_permission(user, "capability:manage_roles"),
        "reminder_counts": counts,
        "action_items": _action_items(user.practice_id),
        "obligations": _billing_obligations(user.practice_id),
        "links": [
            {"name": "Credentialing grid", "href": "/#credentialing"},
            {"name": "Eligibility", "href": "/#eligibility"},
        ],
    }


@router.post("/billing/obligations/{obligation_id}/complete")
def complete_billing_obligation(obligation_id: str, user: CurrentUser) -> dict[str, Any]:
    ob = due_engine.get(obligation_id)
    if ob is None or ob.practice_id != user.practice_id or ob.source_ref not in BILLING_CODES:
        raise HTTPException(status_code=404, detail="obligation not found")
    done = due_engine.complete_by_id(obligation_id, practice_id=user.practice_id)
    if done is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    return {"obligation_id": done.obligation_id, "code": done.source_ref, "status": done.status}


@router.put("/billing/digest")
def put_digest(body: DigestBody, user: CurrentUser) -> dict[str, bool]:
    _admin(user)
    credentialing_store.set_digest_enabled(user.practice_id, body.enabled)
    return {"enabled": digest_enabled(user.practice_id)}


@router.post("/billing/digest/preview")
def preview_digest(user: CurrentUser) -> dict[str, str | bool]:
    """Preview only. Does not send."""
    _admin(user)
    result = send_weekly_digest(user.practice_id)
    return result


@router.get("/quickbooks")
def quickbooks_money(user: CurrentUser) -> dict[str, bool]:
    _money(user)
    return {"ok": True}
