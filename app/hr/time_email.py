"""Styled weekly-hours mail. Matches the onboarding wizard shell.

Scheduled sends keep their recipients. A test copy is a separate message and
is addressed only to operations@heart4change.org.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from app.hiring.mailer import HEADER_TEXT, WIZARD_FOOTER, _GREEN, practice_calendar_day

OPERATIONS_TEST_TO = "operations@heart4change.org"
TIME_ENTRY_URL = "https://clynotion.com/#time"

_FONT = "font-family:Arial,Helvetica,sans-serif;"
_INK = "color:#1c2421;"
_MUTED = "color:#3d4742;"


def sample_rows() -> list[dict[str, Any]]:
    """Fake names for an Admin test copy. The week itself comes from the live schedule."""
    return [
        {
            "display_name": "Riley Sample",
            "email": "riley.sample@example.com",
            "status": "submitted",
            "decimal_hours": "32.50",
            "rounded_display": "32:30",
            "updated_at": "2026-11-19T04:30:00+00:00",
        },
        {
            "display_name": "Jordan Example",
            "email": "jordan.example@example.com",
            "status": "missing",
            "decimal_hours": "",
            "rounded_display": "",
            "updated_at": "",
        },
    ]


def sample_reminder_person(slot: str) -> dict[str, str]:
    if slot == "due":
        return {"display_name": "Jordan Example", "email": "jordan.example@example.com"}
    return {"display_name": "Riley Sample", "email": "riley.sample@example.com"}


def eastern_day_label(value: str) -> str:
    """Calendar day in America/New_York. A date-only value does not shift."""
    day = practice_calendar_day(value)
    if day is None:
        return ""
    return f"{day.strftime('%B')} {day.day}, {day.year}"


def hours_label(row: dict[str, Any]) -> str:
    decimal = str(row.get("decimal_hours") or "").strip()
    if decimal:
        return f"{decimal} hours"
    rounded = str(row.get("rounded_display") or "").strip()
    return rounded


def count_line(submitted: int, missing: int, label: str) -> str:
    if submitted == 0 and missing == 0:
        return f"Nobody is enabled for hours for {label}."
    if missing <= 0:
        return f"Everyone has submitted hours for {label}."
    still = "1 still needs to submit." if missing == 1 else f"{missing} still need to submit."
    return f"{submitted} of {submitted + missing} people have submitted hours for {label}. {still}"


def compose_summary(report: dict[str, Any]) -> tuple[str, str, str]:
    """Subject, plain text, and HTML for the payroll-week summary."""
    label = str(report.get("label") or "")
    subject = f"Payroll hours are ready — {label}"
    submitted_rows = [row for row in report.get("rows") or [] if row.get("status") == "submitted"]
    missing_rows = [row for row in report.get("rows") or [] if row.get("status") != "submitted"]
    submitted = len(submitted_rows)
    missing = len(missing_rows)
    counts = count_line(submitted, missing, label)
    link = str(report.get("dashboard_url") or "")
    payroll = str(report.get("payroll_label") or "")
    intro = f"{_greeting(str(report.get('recipient_name') or ''))}\n\n{counts}"
    text_lines = [HEADER_TEXT, "", intro, "", "Submitted"]
    text_lines.extend(_plain_people(submitted_rows, submitted=True) or ["None"])
    text_lines.extend(["", "Not yet submitted"])
    text_lines.extend(_plain_people(missing_rows, submitted=False) or ["None"])
    if payroll:
        text_lines.extend(["", f"Payroll run: {payroll}"])
    if link:
        text_lines.extend(["", f"Open this payroll week: {link}"])
    text_lines.extend(["", WIZARD_FOOTER])
    html = _shell(
        label,
        _paragraphs(intro)
        + _people_table("Submitted", submitted_rows, submitted=True)
        + _people_table("Not yet submitted", missing_rows, submitted=False)
        + (_paragraphs(f"Payroll run: {payroll}") if payroll else "")
        + _button(link, "Review hours"),
    )
    return subject, "\n".join(text_lines).strip() + "\n", html


def compose_reminder(
    *,
    week_label: str,
    payroll_label: str,
    deadline_label: str,
    weekday_phrase: str,
    coverage_note: str,
    display_name: str = "",
    link: str = TIME_ENTRY_URL,
) -> tuple[str, str, str]:
    """Subject, plain text, and HTML for a staff reminder."""
    subject = weekday_phrase
    greeting = _greeting(display_name)
    intro = "\n\n".join(
        part
        for part in (
            greeting,
            subject,
            f"Workweek: {week_label}",
            f"Payroll run: {payroll_label}" if payroll_label else "",
            f"Deadline: {deadline_label}" if deadline_label else "",
            coverage_note,
        )
        if part
    )
    text_lines = [HEADER_TEXT, "", intro, "", f"Enter hours: {link}", "", WIZARD_FOOTER]
    html = _shell(
        week_label,
        _paragraphs(intro) + _button(link, "Enter hours"),
    )
    return subject, "\n".join(text_lines).strip() + "\n", html


def as_test_copy(subject: str, text: str, html: str) -> tuple[str, str, str]:
    """Prefix the subject and say this copy is not the scheduled send."""
    note = (
        f"Test copy addressed only to {OPERATIONS_TEST_TO}. "
        "It does not go to staff or to the payroll recipient, and it does not run the schedule."
    )
    tested_subject = subject if subject.startswith("[TEST]") else f"[TEST] {subject}"
    tested_text = f"{note}\n\n{text}"
    banner = (
        '<p style="margin:0 0 14px;'
        + _FONT
        + 'font-size:13px;line-height:18px;color:#6b4a00;background:#fff6e5;border:1px solid #ead7a3;padding:10px 12px;">'
        + escape(note)
        + "</p>"
    )
    return tested_subject, tested_text, html.replace("<td style=\"padding:28px 28px 8px;\">", "<td style=\"padding:28px 28px 8px;\">" + banner, 1)


def _greeting(display_name: str) -> str:
    first = str(display_name or "").strip().split(" ", 1)[0]
    if not first or "@" in first:
        return "Hello,"
    return f"Hi {first},"


def _plain_people(rows: list[dict[str, Any]], *, submitted: bool) -> list[str]:
    lines = []
    for row in rows:
        name = str(row.get("display_name") or row.get("email") or "Enabled employee")
        if not submitted:
            lines.append(f"- {name}")
            continue
        bits = [name]
        hours = hours_label(row)
        if hours:
            bits.append(hours)
        submitted_on = eastern_day_label(str(row.get("updated_at") or ""))
        if submitted_on:
            bits.append(f"submitted {submitted_on}")
        lines.append("- " + " — ".join(bits))
    return lines


def _people_table(heading: str, rows: list[dict[str, Any]], *, submitted: bool) -> str:
    body = [
        '<p style="margin:16px 0 8px;'
        + _FONT
        + 'font-size:13px;line-height:18px;'
        + _MUTED
        + 'font-weight:bold;letter-spacing:0.04em;">'
        + escape(heading.upper())
        + "</p>",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px;border-collapse:collapse;">',
    ]
    if not rows:
        body.append(
            '<tr><td style="padding:8px 12px;border:1px solid #e4e7e4;'
            + _FONT
            + "font-size:14px;line-height:20px;"
            + _INK
            + '">None</td></tr>'
        )
    for row in rows:
        name = str(row.get("display_name") or row.get("email") or "Enabled employee")
        detail = "Not yet submitted"
        if submitted:
            parts = []
            hours = hours_label(row)
            if hours:
                parts.append(hours)
            submitted_on = eastern_day_label(str(row.get("updated_at") or ""))
            if submitted_on:
                parts.append(f"Submitted {submitted_on}")
            detail = " · ".join(parts) if parts else "Submitted"
        body.append(
            "<tr>"
            f'<td valign="top" style="padding:8px 12px;background:#f7f8f7;border:1px solid #e4e7e4;{_FONT}font-size:14px;line-height:20px;{_INK}font-weight:bold;">{escape(name)}</td>'
            f'<td valign="top" style="padding:8px 12px;border:1px solid #e4e7e4;{_FONT}font-size:14px;line-height:20px;{_INK}">{escape(detail)}</td>'
            "</tr>"
        )
    body.append("</table>")
    return "".join(body)


def _button(link: str, label: str) -> str:
    if not str(link).startswith(("https://", "http://")):
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 20px;">'
        f'<tr><td bgcolor="{_GREEN}" style="border-radius:4px;">'
        f'<a href="{escape(link, quote=True)}" style="display:inline-block;padding:12px 18px;{_FONT}font-size:15px;line-height:20px;color:#ffffff;text-decoration:none;font-weight:bold;">{escape(label)}</a>'
        "</td></tr></table>"
    )


def _paragraphs(text: str) -> str:
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    parts = []
    for block in blocks:
        inner = "<br>".join(escape(line) for line in block.split("\n"))
        parts.append(
            f'<p style="margin:0 0 14px;{_FONT}font-size:15px;line-height:22px;{_INK}">{inner}</p>'
        )
    return "".join(parts)


def _shell(week_label: str, inner: str) -> str:
    header = escape(HEADER_TEXT)
    week = escape(week_label)
    return (
        '<!DOCTYPE html><html lang="en"><body style="margin:0;padding:0;background:#f3f4f3;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f3;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;">'
        f'<tr><td style="background:{_GREEN};padding:20px 28px;{_FONT}color:#ffffff;">'
        f'<div style="font-size:16px;line-height:22px;font-weight:bold;">{header}</div>'
        f'<div style="font-size:14px;line-height:20px;margin-top:4px;">{week}</div>'
        "</td></tr>"
        '<tr><td style="padding:28px 28px 8px;">'
        + inner
        + "</td></tr>"
        '<tr><td style="padding:8px 28px 22px;border-top:1px solid #e4e7e4;'
        + _FONT
        + 'font-size:12px;line-height:18px;color:#5c6560;">'
        + escape(WIZARD_FOOTER)
        + "</td></tr></table></td></tr></table></body></html>"
    )


def coverage_sentence(saturday: date, sunday: date) -> str:
    return (
        "Enter the hours you already worked that Monday through Friday. "
        f"Hours from {_long(saturday)} and {_long(sunday)} belong in this same total if you worked them."
    )


def _long(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"
