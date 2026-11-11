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
        if item.get("kind") != "tool":
            continue
        rows.append(
            {
                "id": _tool_permission(item["id"]),
                "name": item["name"],
                "group": item.get("unit") or "tool",
                "grantable": True,
                "status": item.get("status") or "",
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
        ]
    )
    return rows


def grantable_ids() -> list[str]:
    return [row["id"] for row in permission_registry() if row.get("grantable")]


def admin_permissions() -> list[str]:
    """Admin sees and controls the product, except Melissa-only corrections."""
    return [item for item in grantable_ids() if item != PAYROLL_CORRECT]
