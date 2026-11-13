"""Totals and rates from de-identified claim rows. No row text leaves this shape."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_DENIED = {"denied", "rejected"}


def _rate(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.0000"
    value = (Decimal(part) / Decimal(whole)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return f"{value}"


def _money(value: str) -> Decimal:
    try:
        return Decimal(value or "0").quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _bucket(rows: list[dict[str, str]], key_of) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = key_of(row) or "—"
        groups.setdefault(key, []).append(row)
    out = []
    for key, items in groups.items():
        denied = sum(1 for item in items if item.get("claim_status") in _DENIED)
        out.append(
            {
                "key": key,
                "claims": len(items),
                "denied": denied,
                "rate": _rate(denied, len(items)),
            }
        )
    out.sort(key=lambda item: (-int(item["denied"]), str(item["key"])))
    return out


def summarize(rows: list[dict[str, str]]) -> dict[str, Any]:
    denied_rows = [row for row in rows if row.get("claim_status") in _DENIED]
    billed = sum((_money(row.get("billed", "")) for row in rows), Decimal("0.00"))
    paid = sum((_money(row.get("paid", "")) for row in rows), Decimal("0.00"))
    denied_gap = sum(
        (max(_money(row.get("billed", "")) - _money(row.get("paid", "")), Decimal("0.00")) for row in denied_rows),
        Decimal("0.00"),
    )
    denied_count = len(denied_rows)
    months: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        month = row.get("service_month") or row.get("status_date") or "—"
        months.setdefault(month, []).append(row)
    trend = []
    for month, items in months.items():
        denied = sum(1 for item in items if item.get("claim_status") in _DENIED)
        trend.append(
            {
                "month": month,
                "claims": len(items),
                "denied": denied,
                "rate": _rate(denied, len(items)),
            }
        )
    trend.sort(key=lambda item: item["month"])
    category_rows = _bucket(denied_rows, lambda row: row.get("category") or "unspecified")
    carc_rows = _bucket(denied_rows, lambda row: row.get("carc") or "(none)")
    for table in (category_rows, carc_rows):
        for item in table:
            item["rate"] = _rate(int(item["denied"]), denied_count)
    return {
        "totals": {
            "claims": len(rows),
            "denied": sum(1 for row in rows if row.get("claim_status") == "denied"),
            "rejected": sum(1 for row in rows if row.get("claim_status") == "rejected"),
            "denied_or_rejected": denied_count,
            "billed": f"{billed.quantize(Decimal('0.01'))}",
            "paid": f"{paid.quantize(Decimal('0.01'))}",
            "denial_rate": _rate(denied_count, len(rows)),
            "dollar_denial_rate": _rate(int(denied_gap * 100), int(billed * 100)) if billed else "0.0000",
        },
        "by_payer": _bucket(rows, lambda row: row.get("payer") or "—"),
        "by_cpt": _bucket(rows, lambda row: row.get("cpt") or "—"),
        "by_modifier_pos": _bucket(
            rows,
            lambda row: f"{row.get('modifiers') or '—'} / {row.get('pos') or '—'}",
        ),
        "by_category": category_rows,
        "by_carc": carc_rows,
        "trend": trend,
    }
