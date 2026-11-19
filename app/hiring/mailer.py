"""Candidate email. Nothing is sent unless candidate emails are enabled.

Delivery uses the practice's already-connected Gmail mailbox (gmail.send).
No new scope, service account, or domain-wide delegation grant is requested.
Bodies are not logged. Tests capture messages in memory.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
from typing import Any, Optional

from app.hr.time_mail import OutboundMail, RecordingMailer

_capture: Optional[RecordingMailer] = None


def set_capture_for_tests(mailer: Optional[RecordingMailer]) -> None:
    global _capture
    _capture = mailer


def clear_capture_for_tests() -> None:
    global _capture
    _capture = None


def captured() -> list[OutboundMail]:
    if _capture is None:
        return []
    return list(_capture.sent)


FOOTER = (
    "Heart for Change will never ask for a Social Security number or bank details by email."
)


def offer_body(link: str, deadline: str) -> str:
    return (
        "Heart for Change sent you an offer letter.\n"
        f"Open it here: {link}\n"
        f"Please respond by {deadline}.\n\n"
        f"{FOOTER}\n"
    )


def code_body(code: str) -> str:
    return f"Your sign-in code is {code}. It expires in 10 minutes.\n\n{FOOTER}\n"


def reminder_body(link: str, deadline: str) -> str:
    return (
        "A response to your offer is due soon.\n"
        f"Open it here: {link}\n"
        f"The deadline is {deadline}.\n\n"
        f"{FOOTER}\n"
    )


OFFICE_ADDRESS = "200 Willow Valley Sq, Suite 210, Lancaster, PA"
REPLY_TO = "operations@heart4change.org"
WIZARD_FOOTER = (
    "Heart for Change Counseling Services, Inc. · "
    + OFFICE_ADDRESS
    + ". Heart for Change will never ask for your Social Security number or bank details by email."
)
HEADER_TEXT = "Heart for Change Counseling Services, Inc."
_GREEN = "#0f6e56"
_PLACEHOLDERS = (
    "name",
    "first_name",
    "last_name",
    "position",
    "employment",
    "start_date",
    "start_date_long",
    "link",
    "supervisor_name",
    "packet_list",
    "billing_first_name",
)
LEGACY_STAFF_SUBJECT = "New hire ready for payroll: {name}"
LEGACY_CANDIDATE_SUBJECT = "Welcome to Heart for Change, {name}"

STAFF_HANDOFF_SUBJECT = "New Hire Ready for Payroll Setup: {name} (starts {start_date})"
STAFF_HANDOFF_INTRO = (
    "Hi {billing_first_name},\n\n"
    "{name} has accepted an offer and the onboarding packet has been sent. "
    "This hire is now assigned to you in Clynotion to complete payroll setup."
)
LEGACY_STAFF_INTRO = (
    "Hello {billing_first_name},\n\n"
    "{name} has accepted an offer and the onboarding packet has been sent. "
    "This hire is now assigned to you in Clynotion to complete payroll setup."
)
STAFF_HANDOFF_STEPS = (
    "File the Pennsylvania new-hire report.\n"
    "Schedule the in-person Form I-9 and record the appointment and completion dates.\n"
    "[direct] Confirm PA clearances and exclusion screening are received before the start date.\n"
    "[office] Confirm exclusion screening is complete.\n"
    "Activate the hire in Clynotion once everything above is done."
)
OLD_STAFF_SETUP_STEP = "Set up the W-4 and direct deposit in QuickBooks Workforce and send the employee's invitation."
NEW_STAFF_SETUP_STEP = "Confirm the hire completed QuickBooks Workforce self-onboarding (W-4 and direct deposit)."
OLD_CANDIDATE_INVITE_STEP = "Our payroll team will send you a separate secure invitation to set up your W-4 and direct deposit."
PRIOR_CANDIDATE_INVITE_STEP = (
    "You will receive a secure email from QuickBooks Workforce (do_not_reply@intuit.com) "
    "to enter your W-4 tax withholding and direct deposit details. Please complete it before your start date."
)
NEW_CANDIDATE_INVITE_STEP = (
    "You will receive a secure email from QuickBooks Workforce (do_not_reply@intuit.com) "
    "closer to your start date (within 90 days of it) to enter your W-4 tax withholding and direct deposit details. "
    "Please complete it before your start date."
)
PACKET_REVIEW_STEP = "Review the attached new-hire packet ({packet_list}) and complete each form."
STAFF_HANDOFF_CLOSING = (
    "Each step is tracked in Clynotion and appears on your Home page until it is complete. "
    "No personal identifiers are included in this email."
)
STAFF_HANDOFF_SIGNATURE = "Thank you,\n\nClynotion · Heart for Change Operations"

CANDIDATE_PACKET_SUBJECT = "Welcome to Heart for Change — Next Steps Before Your Start Date"
CANDIDATE_PACKET_INTRO = (
    "Dear {first_name},\n\n"
    "On behalf of Heart for Change Counseling Services, we are delighted that you have accepted our offer "
    "and will be joining our team as {position}. We look forward to working with you."
)
CANDIDATE_PACKET_STEPS = (
    "Review the attached new-hire packet ({packet_list}) and complete each form.\n"
    "Bring your completed forms with you on or before your first day. "
    "Please do not email any form that contains your Social Security number or banking information.\n"
    "[direct] Complete your Pennsylvania clearances (PA Child Abuse History, PA State Police Criminal Record Check, "
    "and FBI fingerprinting) and provide the results before your start date.\n"
    "You will receive a secure email from QuickBooks Workforce (do_not_reply@intuit.com) "
    "closer to your start date (within 90 days of it) to enter your W-4 tax withholding and direct deposit details. "
    "Please complete it before your start date.\n"
    "On your first day, bring original, unexpired documents of your choice that establish your identity and "
    "work eligibility for Form I-9. The list of acceptable documents is in the I-9 instructions attached."
)
CANDIDATE_PACKET_CLOSING = (
    "If you have any questions before your start date, simply reply to this email or call our office "
    "at 717-478-3411. We are glad to have you with us."
)
CANDIDATE_PACKET_SIGNATURE = (
    "Warm regards,\n\n"
    "Heart for Change Counseling Services, Inc.\n"
    "Operations\n"
    "717-478-3411 · operations@heart4change.org"
)
ADDITIONAL_DOCUMENTS_SUBJECT = "Additional Onboarding Documents — Heart for Change"
ADDITIONAL_DOCUMENTS_INTRO = (
    "Dear {first_name},\n\n"
    "A few more onboarding forms are attached. Each one is a fillable PDF you can complete and sign on your computer. "
    "Please bring or return the completed forms by your start date, {start_date_long}."
)
ADDITIONAL_DOCUMENTS_CLOSING = (
    "If you have any questions before your start date, reply to this email or call our office at 717-478-3411."
)

WIZARD_FIELDS = (
    "staff_handoff_subject",
    "staff_handoff_intro",
    "staff_handoff_steps",
    "staff_handoff_closing",
    "staff_handoff_signature",
    "candidate_packet_subject",
    "candidate_packet_intro",
    "candidate_packet_steps",
    "candidate_packet_closing",
    "candidate_packet_signature",
    "additional_documents_subject",
    "additional_documents_intro",
    "additional_documents_closing",
)


def migrate_wizard_steps(kind: str, raw: str) -> tuple[str, list[str]]:
    """Replace one outdated step line. Returns the text and the old lines replaced."""
    changed: list[str] = []
    lines: list[str] = []
    for line in (raw or "").splitlines():
        stripped = line.strip()
        prefix = ""
        body = stripped
        if stripped.lower().startswith("[direct]"):
            prefix = "[direct] "
            body = stripped.split("]", 1)[1].strip()
        elif stripped.lower().startswith("[office]"):
            prefix = "[office] "
            body = stripped.split("]", 1)[1].strip()
        folded = body.casefold()
        replacement = ""
        if kind == "candidate" and body in {OLD_CANDIDATE_INVITE_STEP, PRIOR_CANDIDATE_INVITE_STEP}:
            replacement = NEW_CANDIDATE_INVITE_STEP
        elif kind == "candidate" and "w-4" in folded and "packet" in folded and body != PACKET_REVIEW_STEP:
            replacement = PACKET_REVIEW_STEP
        elif kind == "candidate" and "w-4" in folded and "quickbooks workforce" not in folded and any(
            word in folded for word in ("payroll", "invitation", "direct deposit")
        ):
            replacement = NEW_CANDIDATE_INVITE_STEP
        elif kind == "staff" and (
            body in {OLD_STAFF_SETUP_STEP, NEW_STAFF_SETUP_STEP}
            or ("quickbooks" in folded and "self-onboard" in folded)
            or (
                "w-4" in folded
                and "confirm the hire completed" not in folded
                and any(word in folded for word in ("set up", "invitation", "send the employee"))
            )
        ):
            changed.append(body)
            continue
        if replacement and replacement != body:
            lines.append(prefix + replacement)
            changed.append(body)
        else:
            lines.append(line)
    return "\n".join(lines), changed


def wizard_defaults() -> dict[str, str]:
    return {
        "staff_handoff_subject": STAFF_HANDOFF_SUBJECT,
        "staff_handoff_intro": STAFF_HANDOFF_INTRO,
        "staff_handoff_steps": STAFF_HANDOFF_STEPS,
        "staff_handoff_closing": STAFF_HANDOFF_CLOSING,
        "staff_handoff_signature": STAFF_HANDOFF_SIGNATURE,
        "candidate_packet_subject": CANDIDATE_PACKET_SUBJECT,
        "candidate_packet_intro": CANDIDATE_PACKET_INTRO,
        "candidate_packet_steps": CANDIDATE_PACKET_STEPS,
        "candidate_packet_closing": CANDIDATE_PACKET_CLOSING,
        "candidate_packet_signature": CANDIDATE_PACKET_SIGNATURE,
        "additional_documents_subject": ADDITIONAL_DOCUMENTS_SUBJECT,
        "additional_documents_intro": ADDITIONAL_DOCUMENTS_INTRO,
        "additional_documents_closing": ADDITIONAL_DOCUMENTS_CLOSING,
    }


def render_template(template: str, values: dict[str, str], *, html: bool = False) -> str:
    text = template or ""
    for key in _PLACEHOLDERS:
        raw = values.get(key) or ""
        text = text.replace("{" + key + "}", escape(raw) if html else raw)
    return text


PRACTICE_TZ = ZoneInfo("America/New_York")


def practice_calendar_day(value: str):
    """Calendar day in America/New_York.

    A date-only value, including midnight UTC used to store that day, stays on
    that day. Any other timestamp is the Eastern calendar day of that instant.
    """
    text = (value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return datetime.strptime(text, "%Y-%m-%d").date()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00(?:\.0+)?(?:Z|\+00:00)", text):
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    if "T" in text:
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            moment = None
        if moment is not None:
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=ZoneInfo("UTC"))
            return moment.astimezone(PRACTICE_TZ).date()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def short_start_date(value: str) -> str:
    """MM/DD/YYYY. A date-only value is never read as UTC."""
    parsed = _parse_date_only(value)
    if parsed is None:
        return (value or "").strip()
    return parsed.strftime("%m/%d/%Y")


def long_start_date(value: str) -> str:
    """Weekday, Month D, YYYY. A date-only value is never read as UTC."""
    parsed = _parse_date_only(value)
    if parsed is None:
        return (value or "").strip()
    return f"{parsed.strftime('%A, %B')} {parsed.day}, {parsed.year}"


def _parse_date_only(value: str):
    day = practice_calendar_day(value)
    if day is None:
        return None
    return datetime(day.year, day.month, day.day)


def wizard_steps(raw: str, *, direct: bool) -> list[str]:
    steps: list[str] = []
    for line in (raw or "").splitlines():
        text = re.sub(r"^\d+\.\s*", "", line.strip())
        if not text:
            continue
        marker = text.casefold()
        if marker.startswith("[direct]"):
            if not direct:
                continue
            text = text.split("]", 1)[1].strip()
        elif marker.startswith("[office]"):
            if direct:
                continue
            text = text.split("]", 1)[1].strip()
        if text:
            steps.append(text)
    return steps


def _paragraphs(text: str, *, html: bool) -> str:
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    if not html:
        return "\n\n".join(blocks)
    parts = []
    for block in blocks:
        inner = "<br>".join(escape(line) for line in block.split("\n"))
        parts.append(
            '<p style="margin:0 0 14px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:22px;color:#1c2421;">'
            + inner
            + "</p>"
        )
    return "".join(parts)


def _summary_rows(kind: str, values: dict[str, str]) -> list[tuple[str, str]]:
    if kind == "staff":
        return [
            ("New hire", values.get("name") or ""),
            ("Position", values.get("position") or ""),
            ("Schedule", values.get("employment") or ""),
            ("Start date", values.get("start_date_long") or values.get("start_date") or ""),
            ("Direct client access", values.get("direct_client") or "No"),
        ]
    return [
        ("Position", values.get("position") or ""),
        ("Schedule", values.get("employment") or ""),
        ("Start date", values.get("start_date_long") or values.get("start_date") or ""),
        ("Supervisor", values.get("supervisor_name") or ""),
    ]


def compose_additional(
    sections: dict[str, str],
    values: dict[str, str],
    titles: list[str],
) -> tuple[str, str, str]:
    """Subject, plain text, and HTML for documents sent after the first packet."""
    defaults = wizard_defaults()
    filled = {key: values.get(key) or "" for key in _PLACEHOLDERS}
    filled["packet_list"] = ", ".join(titles)

    def section(name: str) -> str:
        text = str(sections.get(name) or "").strip()
        return text or defaults[f"additional_documents_{name}"]

    subject = render_template(section("subject"), filled).replace("\r", " ").replace("\n", " ").strip()
    intro = render_template(section("intro"), filled)
    closing = render_template(section("closing"), filled)
    signature = render_template(CANDIDATE_PACKET_SIGNATURE, filled)
    rows = [("Start date", filled.get("start_date_long") or filled.get("start_date") or "")]
    text_lines = [HEADER_TEXT, "", intro, ""]
    for label, value in rows:
        text_lines.append(f"{label}: {value}")
    text_lines.extend(["", "Documents:"])
    text_lines.extend(f"{index}. {title}" for index, title in enumerate(titles, start=1))
    text_lines.extend(["", closing, "", signature, "", WIZARD_FOOTER])
    plain = "\n".join(text_lines).strip() + "\n"
    html = _wizard_html("candidate", intro, rows, list(titles), closing, signature, "", steps_heading="DOCUMENTS")
    return subject, plain, html


def compose_wizard(
    kind: str,
    sections: dict[str, str],
    values: dict[str, str],
    *,
    direct: bool,
) -> tuple[str, str, str]:
    """Return subject, plain text, and HTML. Placeholders are filled. HTML is escaped."""
    defaults = wizard_defaults()
    prefix = "staff_handoff" if kind == "staff" else "candidate_packet"
    filled = {key: values.get(key) or "" for key in _PLACEHOLDERS}
    filled["direct_client"] = "Yes" if direct else "No"

    def section(name: str) -> str:
        text = str(sections.get(name) or "").strip()
        return text or defaults[f"{prefix}_{name}"]

    subject = render_template(section("subject"), filled).replace("\r", " ").replace("\n", " ").strip()
    intro = render_template(_greeting(section("intro"), filled.get("billing_first_name") or ""), filled)
    closing = render_template(section("closing"), filled)
    signature = render_template(section("signature"), filled)
    steps = [render_template(step, filled) for step in wizard_steps(section("steps"), direct=direct)]
    rows = _summary_rows(kind, filled)
    link = filled.get("link") or ""
    text_lines = [HEADER_TEXT, "", intro, ""]
    for label, value in rows:
        text_lines.append(f"{label}: {value}")
    text_lines.append("")
    if kind == "staff" and link:
        text_lines.extend(["Open in Clynotion: " + link, ""])
    text_lines.append("Next steps:")
    text_lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    text_lines.extend(["", closing, "", signature, "", WIZARD_FOOTER])
    plain = "\n".join(text_lines).strip() + "\n"
    html = _wizard_html(kind, intro, rows, steps, closing, signature, link)
    return subject, plain, html


def _wizard_html(
    kind: str,
    intro: str,
    rows: list[tuple[str, str]],
    steps: list[str],
    closing: str,
    signature: str,
    link: str,
    steps_heading: str = "NEXT STEPS",
) -> str:
    summary = [
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;border-collapse:collapse;">'
    ]
    for label, value in rows:
        summary.append(
            "<tr>"
            f'<td width="168" valign="top" style="width:168px;padding:8px 12px;background:#f7f8f7;border:1px solid #e4e7e4;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:18px;color:#3d4742;font-weight:bold;">{escape(label)}</td>'
            f'<td valign="top" style="padding:8px 12px;border:1px solid #e4e7e4;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#1c2421;">{escape(value)}</td>'
            "</tr>"
        )
    summary.append("</table>")
    button = ""
    if kind == "staff" and link.startswith(("https://", "http://")):
        button = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 20px;">'
            "<tr><td bgcolor=\""
            + _GREEN
            + '" style="border-radius:4px;">'
            f'<a href="{escape(link, quote=True)}" style="display:inline-block;padding:12px 18px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:20px;color:#ffffff;text-decoration:none;font-weight:bold;">Open in Clynotion</a>'
            "</td></tr></table>"
        )
    numbered = [
        '<p style="margin:0 0 8px;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:18px;color:#3d4742;font-weight:bold;letter-spacing:0.04em;">'
        + escape(steps_heading)
        + "</p>",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 8px;">',
    ]
    for index, step in enumerate(steps, start=1):
        numbered.append(
            "<tr>"
            f'<td width="28" valign="top" style="width:28px;padding:0 8px 12px 0;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:22px;color:{_GREEN};font-weight:bold;">{index}.</td>'
            f'<td valign="top" style="padding:0 0 12px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:22px;color:#1c2421;">{escape(step)}</td>'
            "</tr>"
        )
    numbered.append("</table>")
    body = (
        _paragraphs(intro, html=True)
        + "".join(summary)
        + button
        + "".join(numbered)
        + _paragraphs(closing, html=True)
        + _paragraphs(signature, html=True)
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><body style=\"margin:0;padding:0;background:#f3f4f3;\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f3;">'
        '<tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:#ffffff;">'
        "<tr><td style=\"background:"
        + _GREEN
        + ';padding:20px 28px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:22px;color:#ffffff;font-weight:bold;">'
        + HEADER_TEXT
        + "</td></tr>"
        '<tr><td style="padding:28px 28px 8px;">'
        + body
        + "</td></tr>"
        '<tr><td style="padding:8px 28px 22px;border-top:1px solid #e4e7e4;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px;color:#5c6560;">'
        + escape(WIZARD_FOOTER)
        + "</td></tr></table></td></tr></table></body></html>"
    )


def _greeting(intro: str, billing_first: str) -> str:
    """Use Hi Name, and Hello, when no Billing user has a directory name."""
    if billing_first.strip():
        return intro
    return re.sub(r"^(?:Hi|Hello)\s*\{billing_first_name\}\s*,", "Hello,", intro, count=1)


def sample_wizard_values(*, link: str, supervisor: str, packet_list: str, billing_first_name: str = "") -> dict[str, str]:
    """Fake Riley used by the settings preview and the test send. No real hire."""
    return {
        "name": "Riley Sample",
        "first_name": "Riley",
        "last_name": "Sample",
        "position": "Sample position",
        "employment": "Full-time",
        "start_date": short_start_date("12/08/2026"),
        "start_date_long": long_start_date("12/08/2026"),
        "link": link,
        "supervisor_name": supervisor or "Jordan Example",
        "packet_list": packet_list,
        "billing_first_name": billing_first_name,
    }


def payroll_handoff_body(name: str, position: str, employment: str, start: str, link: str) -> str:
    subject, text, _html = compose_wizard(
        "staff",
        {},
        {
            "name": name,
            "position": position or "—",
            "employment": employment or "—",
            "start_date": start or "—",
            "start_date_long": long_start_date(start) or "—",
            "link": link,
            "billing_first_name": "",
        },
        direct=False,
    )
    _ = subject
    return text


def candidate_packet_body(welcome: str) -> str:
    return f"{welcome.rstrip()}\n\n{FOOTER}\n"


def deliver(
    practice_id: str,
    to: str,
    subject: str,
    text: str,
    attachments: tuple[tuple[str, str, bytes], ...] = (),
    *,
    html: str = "",
    reply_to: str = "",
) -> str:
    """Return sent, failed, or suppressed. Does not log the address or body."""
    message = OutboundMail(
        to=to,
        subject=subject,
        text=text,
        practice_id=practice_id,
        attachments=attachments,
        html=html,
        reply_to=reply_to,
    )
    if _capture is not None or os.environ.get("ATTUNE_MODE") != "real":
        box = _capture or RecordingMailer()
        if _capture is None:
            # Tests that forgot to install a capture still must not touch the network.
            box.send(message)
            return "sent"
        result = box.send(message)
        return "sent" if result.status == "sent" else "failed"
    from app.hr.gmail_oauth import deliver_self_test_message

    result = deliver_self_test_message(practice_id, message)
    if result.status == "sent":
        return "sent"
    return "failed"


def public_queue_row(kind: str, offer_id: str, at: str, status: str) -> dict[str, Any]:
    return {"kind": kind, "offer_id": offer_id, "at": at, "status": status}
