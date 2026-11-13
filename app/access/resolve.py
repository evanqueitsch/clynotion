"""Who a signed-in user is allowed to do.

Break-glass owners (dev alice, ATTUNE_HR_ADMIN_EMAILS, ATTUNE_OWNER_EMAILS)
keep Admin even if the role file is empty. Roster default_role no longer
grants that. Post-cutoff correction is Melissa's email, not a role.
"""

from __future__ import annotations

import os

from app.auth import User
from app.hr.time_entry import MELISSA_EMAIL, is_melissa

from app.access.permissions import (
    MANAGE_ROLES,
    PAYROLL_CORRECT,
    PAYROLL_DASHBOARD,
    TIME_ENTRY,
    admin_permissions,
)
from app.access.store import access_store


# The production Google user for Evan. ATTUNE_HR_ADMIN_EMAILS is empty on
# the live app, and the roster also has a shared hr@ admin mailbox that
# must not inherit this.
BREAK_GLASS_OWNER_EMAILS = frozenset({"operations@heart4change.org"})


def _owner_emails() -> set[str]:
    raw = ",".join(
        [
            os.environ.get("ATTUNE_HR_ADMIN_EMAILS") or "",
            os.environ.get("ATTUNE_OWNER_EMAILS") or "",
        ]
    )
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_owner(user: User) -> bool:
    """Break-glass. Does not include Melissa and does not read roster roles."""
    if (user.username or "").strip() == "alice":
        return True
    email = (user.email or "").strip().lower()
    return bool(email) and (email in _owner_emails() or email in BREAK_GLASS_OWNER_EMAILS)


def assigned_role_id(user: User) -> str:
    if is_owner(user):
        return "admin"
    return access_store.role_id_for(user.practice_id, user.email)


def permissions_for(user: User) -> set[str]:
    """Role grants plus mailbox grants.

    Melissa's mailbox always keeps time entry, the payroll dashboard, and
    post-cutoff correction. Those three are not stored on a role. HR record
    view, manage, and sensitive are not added here; manage and sensitive stay
    off Practice Owner unless a previous role already granted them, in which
    case the migration copied them onto the mailbox.
    """
    if is_owner(user):
        perms = set(admin_permissions())
    else:
        perms = set(access_store.permissions_for_role(user.practice_id, assigned_role_id(user)))
        perms.update(access_store.account_permissions_for(user.practice_id, user.email))
    if is_melissa(user):
        perms.add(TIME_ENTRY)
        perms.add(PAYROLL_DASHBOARD)
        perms.add(PAYROLL_CORRECT)
    else:
        perms.discard(PAYROLL_CORRECT)
    return perms


def has_permission(user: User, permission: str) -> bool:
    if permission == PAYROLL_CORRECT:
        return is_melissa(user)
    perms = permissions_for(user)
    if permission in perms:
        return True
    if permission.startswith("tool:"):
        from app.platform.catalog import CATALOG_ITEMS

        tool_id = permission.split(":", 1)[1]
        item = next((row for row in CATALOG_ITEMS if row.get("id") == tool_id), None)
        unit_id = str(item.get("unit") or "") if item else ""
        if (
            item
            and item.get("status") == "live"
            and not item.get("requires_explicit_grant")
            and unit_id
            and f"unit:{unit_id}" in perms
        ):
            return True
    return False


def sees_every_module(user: User) -> bool:
    """Admin and break-glass owners see built and unbuilt modules."""
    return is_owner(user) or assigned_role_id(user) == "admin"


def landing_for(user: User) -> str:
    """Where the UI should open. ``none`` hides the product."""
    perms = permissions_for(user)
    if not perms:
        return "none"
    product = {item for item in perms if item.startswith("tool:") or item.startswith("unit:")}
    if product <= {TIME_ENTRY} and MANAGE_ROLES not in perms and PAYROLL_DASHBOARD not in perms:
        return "time"
    if is_melissa(user) and MANAGE_ROLES not in perms and product <= {TIME_ENTRY}:
        return "time"
    return "home"
