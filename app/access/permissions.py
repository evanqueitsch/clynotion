"""Permission ids generated from the product catalog.

Roles store these ids. Adding a catalog tool creates a permission without a
code change in the role editor.
"""

from __future__ import annotations

from typing import Any

from app.platform.catalog import BUSINESS_UNITS, CATALOG_ITEMS

PAYROLL_CORRECT = "capability:payroll_correct"
PAYROLL_DASHBOARD = "capability:payroll_dashboard"
MANAGE_ROLES = "capability:manage_roles"
TIME_ENTRY = "tool:time_entry"
CREDENTIALING_TOOL = "tool:credentialing"
CREDENTIALING_MANAGE = "capability:credentialing_manage"
CREDENTIALING_PAYERS = "capability:credentialing_payers"
CREDENTIALING_SENSITIVE = "capability:credentialing_sensitive"
BILLING_TOOL = "tool:billing"
HR_RECORDS_VIEW = "capability:hr_records_view"
HR_RECORDS_MANAGE = "capability:hr_records_manage"
HR_SENSITIVE = "capability:hr_sensitive"
# Not applied in this deploy. When an Admin turns Employee View on, Roles &
# access can add these to Billing. Manage stays off that list on purpose.
BILLING_HR_WHEN_ROLLOUT = (
    "tool:employee_view",
    HR_RECORDS_VIEW,
    HR_SENSITIVE,
)
QUICKBOOKS_MONEY = "capability:quickbooks_money"
PAYROLL_QUICKBOOKS_PUSH = "capability:payroll_quickbooks_push"
DENIALS_SUMMARY = "tool:denials_summary"
DENIALS_SUMMARY_NAME = "Denial summaries (de-identified totals and trends)"
DENIALS_TOOL = "tool:denials"
DENIALS_TOOL_NAME = "Denial analysis (de-identified claims)"
PHI_CLAIMS = "capability:phi_claims"
# Bound to this mailbox the way post-cutoff correction is bound to Melissa.
# Not a Billing-role grant.
CHRISTINE_EMAIL = "billing@heart4change.org"

# Grantable by name. No role receives these unless an Admin saves the grant.
SENSITIVE_GRANT_IDS = frozenset({QUICKBOOKS_MONEY, PAYROLL_QUICKBOOKS_PUSH})

# Opens the payroll week (/hr/time/week and the Payroll week tile).
# capability:payroll_correct is post-cutoff editing and is not this key.
PRACTICE_OWNER_PERMISSIONS = (
    "tool:employee_view",
    HR_RECORDS_VIEW,
    CREDENTIALING_TOOL,
    PAYROLL_DASHBOARD,
    DENIALS_SUMMARY,
)

# Added to the Billing role by the one-time grants migration. Never removed.
NEW_BILLING_GRANTS = (
    CREDENTIALING_TOOL,
    CREDENTIALING_MANAGE,
    CREDENTIALING_PAYERS,
    CREDENTIALING_SENSITIVE,
    BILLING_TOOL,
)


def _tool_permission(item_id: str) -> str:
    return f"tool:{item_id}"


def _unit_permission(unit_id: str) -> str:
    return f"unit:{unit_id}"


def permission_registry() -> list[dict[str, Any]]:
    """Every grantable permission, plus named capabilities that are not roles."""
    rows: list[dict[str, Any]] = []
    for unit in BUSINESS_UNITS:
        rows.append(
            {
                "id": _unit_permission(unit["id"]),
                "name": unit["name"],
                "group": "section",
                "grantable": True,
            }
        )
    for item in CATALOG_ITEMS:
        if item.get("kind") != "tool" or item.get("status") == "retired":
            continue
        tool_id = str(item["id"])
        rows.append(
            {
                "id": _tool_permission(tool_id),
                "name": item["name"],
                "group": item.get("unit") or "tool",
                "grantable": tool_id != "denials",
                "sensitive": tool_id == "denials",
                "status": item.get("status") or "",
                "note": (
                    "Bound to the billing mailbox. Admin has it. Not a role grant."
                    if tool_id == "denials"
                    else ""
                ),
            }
        )
    rows.extend(
        [
            {
                "id": MANAGE_ROLES,
                "name": "Manage roles and assignments",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": PAYROLL_DASHBOARD,
                "name": "Payroll week dashboard",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": PAYROLL_CORRECT,
                "name": "Post-cutoff payroll corrections",
                "group": "capability",
                "grantable": False,
                "note": "Bound to Melissa's account. Not granted by a role.",
            },
            {
                "id": CREDENTIALING_MANAGE,
                "name": "Credentialing changes",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": CREDENTIALING_PAYERS,
                "name": "Credentialing payers",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": CREDENTIALING_SENSITIVE,
                "name": "Credentialing sensitive IDs",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": QUICKBOOKS_MONEY,
                "name": "QuickBooks money movement",
                "group": "capability",
                "grantable": True,
                "sensitive": True,
                "note": "No route uses this yet. Grant it by name.",
            },
            {
                "id": PAYROLL_QUICKBOOKS_PUSH,
                "name": "Send payroll hours to QuickBooks",
                "group": "capability",
                "grantable": True,
                "sensitive": True,
                "note": "No route uses this yet. Grant it by name.",
            },
            {
                "id": HR_RECORDS_VIEW,
                "name": "View staff HR records",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": HR_RECORDS_MANAGE,
                "name": "Edit staff HR records",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": HR_SENSITIVE,
                "name": "See and export HR addresses and contacts",
                "group": "capability",
                "grantable": True,
            },
            {
                "id": DENIALS_SUMMARY,
                "name": DENIALS_SUMMARY_NAME,
                "group": "capability",
                "grantable": True,
                "sensitive": True,
                "note": "Aggregate totals and trends only. No claim rows.",
            },
            {
                "id": PHI_CLAIMS,
                "name": "Claim-level PHI",
                "group": "capability",
                "grantable": False,
                "sensitive": True,
                "note": "Catalog only. Gates future PHI features. Bound to the billing mailbox. Off in this phase.",
            },
        ]
    )
    return rows


def grantable_ids() -> list[str]:
    return [row["id"] for row in permission_registry() if row.get("grantable")]


def billing_role_permissions() -> list[str]:
    """Built-in Billing role. Does not include money-movement capabilities."""
    return [TIME_ENTRY, *NEW_BILLING_GRANTS]


def admin_permissions() -> list[str]:
    """Admin sees and controls the product, except Melissa-only corrections and sensitive grants.

    Denial summaries stay off this automatic set. Practice Owner lists that
    key itself, so adding it does not change anyone else's effective permissions.
    """
    rows = [
        item
        for item in grantable_ids()
        if item != PAYROLL_CORRECT and item not in SENSITIVE_GRANT_IDS and item != DENIALS_SUMMARY
    ]
    for item in (DENIALS_TOOL, PHI_CLAIMS):
        if item not in rows:
            rows.append(item)
    return rows
