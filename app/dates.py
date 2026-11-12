"""Calendar dates for credentialing and compliance clocks.

The form shows month/day/year, the same way a US date control paints a stored
date (11/09/2026). Save accepts that display value. The file stores the
year-month-day form only, with no time and no zone, so a due date cannot
shift into the previous evening.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# What the field shows. Do not tell the user to type the stored form.
DATE_HINT = "Use Nov 9, 2026 as 11/09/2026."

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_ISO_STAMP = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[T ].+$")
_YEAR_FIRST = re.compile(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})$")
_MONTH_FIRST = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})$")
_MONTH_FIRST_DASH = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{2}|\d{4})$")


def parse_calendar_date(value: str) -> str:
    """Return a year-month-day string, or '' when the field is blank.

    Blank stays blank so clearing a date does not keep the previous value.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = _parse(raw)
    if parsed is None:
        raise ValueError(DATE_HINT)
    return parsed.isoformat()


def calendar_date(value: str) -> date | None:
    """Read a stored or submitted date. Invalid text is absent, not an error."""
    raw = (value or "").strip()
    if not raw:
        return None
    return _parse(raw)


def shift_calendar_date(value: str, days: int) -> str:
    parsed = calendar_date(value)
    if parsed is None:
        return ""
    return (parsed + timedelta(days=days)).isoformat()


def _parse(raw: str) -> date | None:
    match = _ISO.match(raw) or _ISO_STAMP.match(raw) or _YEAR_FIRST.match(raw)
    if match is not None:
        return _make(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = _MONTH_FIRST.match(raw) or _MONTH_FIRST_DASH.match(raw)
    if match is None:
        return None
    return _make(_year(match.group(3)), int(match.group(1)), int(match.group(2)))


def _year(token: str) -> int:
    number = int(token)
    if len(token) == 2:
        return 2000 + number if number <= 68 else 1900 + number
    return number


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
