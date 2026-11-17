"""Candidate email. Nothing is sent unless candidate emails are enabled.

Delivery uses the practice's already-connected Gmail mailbox (gmail.send).
No new scope, service account, or domain-wide delegation grant is requested.
Bodies are not logged. Tests capture messages in memory.
"""

from __future__ import annotations

import os
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


STAFF_HANDOFF_SUBJECT = "New hire ready for payroll: {name}"
STAFF_HANDOFF_BODY = (
    "{name}\n"
    "Position: {position}\n"
    "Schedule: {employment}\n"
    "Start date: {start_date}\n"
    "Open the hire: {link}\n"
)
CANDIDATE_PACKET_SUBJECT = "Welcome to Heart for Change, {name}"
CANDIDATE_PACKET_BODY = (
    "Hello {name},\n\n"
    "Welcome. Your start date is {start_date} and your position is {position}.\n"
    "The new-hire packet is attached for you to complete and return. "
    "Payroll will send a separate invite for the W-4 and direct deposit.\n"
    "Bring the identity documents you choose for the Form I-9. "
    "We will not tell you which documents to bring.\n"
    "Heart for Change will never ask for a Social Security number or bank details by email."
)
_PLACEHOLDERS = ("name", "first_name", "position", "employment", "start_date", "link")


def render_template(template: str, values: dict[str, str]) -> str:
    text = template or ""
    for key in _PLACEHOLDERS:
        text = text.replace("{" + key + "}", values.get(key) or "")
    return text


def payroll_handoff_body(name: str, position: str, employment: str, start: str, link: str) -> str:
    return render_template(
        STAFF_HANDOFF_BODY,
        {
            "name": name,
            "position": position or "—",
            "employment": employment or "—",
            "start_date": start or "—",
            "link": link,
        },
    )


def candidate_packet_body(welcome: str) -> str:
    return f"{welcome.rstrip()}\n\n{FOOTER}\n"


def deliver(
    practice_id: str,
    to: str,
    subject: str,
    text: str,
    attachments: tuple[tuple[str, str, bytes], ...] = (),
) -> str:
    """Return sent, failed, or suppressed. Does not log the address or body."""
    message = OutboundMail(
        to=to,
        subject=subject,
        text=text,
        practice_id=practice_id,
        attachments=attachments,
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
