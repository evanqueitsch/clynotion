"""Send-only Gmail connect for the labelled self-test.

Uses the existing Google OAuth client. The refresh token is stored beside
the Directory grant and is never used for scheduled payroll mail.
Scope is gmail.send plus openid/email so the consented mailbox can be verified.
No inbox read, and no service-account delegation.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.google_oauth import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    _client_id,
    _client_secret,
    allowed_domains,
    public_base_url,
)
from app.hr.time_mail import GMAIL_SEND_SCOPE, MailResult, OutboundMail, safe_error_code
from app.workspace_directory import (
    WorkspaceDirectoryError,
    clear_gmail_send_tokens,
    gmail_send_record,
    gmail_send_status,
    save_gmail_send_tokens,
)

GMAIL_SEND_SCOPES = f"openid email {GMAIL_SEND_SCOPE}"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
USERINFO_URL = "https://openidconnect.googleapis.com/userinfo"


class GmailSendError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def gmail_send_redirect_uri() -> str:
    base = public_base_url()
    if not base:
        raise RuntimeError("ATTUNE_PUBLIC_BASE_URL is required to connect a sending mailbox")
    return f"{base}/auth/google/gmail-send/callback"


def gmail_send_authorization_url(state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": gmail_send_redirect_uri(),
        "response_type": "code",
        "scope": GMAIL_SEND_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent select_account",
        # Do not include previously granted Directory scopes on this token.
        "include_granted_scopes": "false",
    }
    domains = allowed_domains()
    if len(domains) == 1:
        params["hd"] = domains[0]
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def sender_email_allowed(email: str, domains: Optional[list[str]] = None) -> bool:
    allowed = domains if domains is not None else allowed_domains()
    cleaned = (email or "").strip().lower()
    if "@" not in cleaned or not allowed:
        return False
    return cleaned.rsplit("@", 1)[-1] in allowed


def exchange_gmail_send_code(code: str) -> dict[str, str]:
    """Exchange the consent code. Raises GmailSendError with a safe code."""
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": gmail_send_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise GmailSendError("gmail_send_exchange_failed")
        tokens = resp.json() if isinstance(resp.json(), dict) else {}
        access = str(tokens.get("access_token") or "").strip()
        refresh = str(tokens.get("refresh_token") or "").strip()
        if not access:
            raise GmailSendError("gmail_send_token_missing")
        info = client.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        if info.status_code >= 400:
            raise GmailSendError("gmail_send_identity_failed")
        payload = info.json() if isinstance(info.json(), dict) else {}
    email = str(payload.get("email") or "").strip().lower()
    verified = payload.get("email_verified")
    verified_ok = verified is True or str(verified).strip().lower() in ("true", "1")
    if not email or not verified_ok:
        raise GmailSendError("gmail_send_identity_failed")
    if not sender_email_allowed(email):
        raise GmailSendError("gmail_send_domain")
    if not refresh:
        # Without a refresh token the mailbox cannot send after this access token expires.
        raise GmailSendError("gmail_send_refresh_missing")
    return {"access_token": access, "refresh_token": refresh, "email": email}


def _refresh_gmail_access(refresh_token: str) -> str:
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        raise GmailSendError("gmail_send_refresh_failed")
    payload = resp.json() if isinstance(resp.json(), dict) else {}
    access = str(payload.get("access_token") or "").strip()
    if not access:
        raise GmailSendError("gmail_send_refresh_failed")
    return access


def _raw_message(message: OutboundMail, sender: str) -> str:
    mime = EmailMessage()
    mime["To"] = message.to
    mime["From"] = sender
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    if message.csv_text:
        mime.add_attachment(
            message.csv_text.encode("utf-8"),
            subtype="csv",
            filename=message.csv_filename or "weekly-hours-self-test.csv",
        )
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


def deliver_self_test_message(practice_id: str, message: OutboundMail) -> MailResult:
    """Send one already-built self-test message from the connected mailbox."""
    record = gmail_send_record(practice_id)
    refresh = record.get("refresh_token") or ""
    sender = record.get("email") or ""
    if not refresh or not sender_email_allowed(sender):
        return MailResult(status="failed", detail="gmail_not_connected")
    try:
        access = _refresh_gmail_access(refresh)
    except GmailSendError as exc:
        return MailResult(status="failed", detail=safe_error_code(exc.code))
    raw = _raw_message(message, sender)
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            resp = client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {access}"},
            )
    except httpx.ConnectError:
        return MailResult(status="failed", detail="gmail_connect")
    except httpx.ConnectTimeout:
        return MailResult(status="failed", detail="gmail_connect_timeout")
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.HTTPError):
        return MailResult(status="uncertain", detail="gmail_uncertain")
    if 200 <= resp.status_code < 300:
        return MailResult(status="sent", detail="gmail_accepted")
    return MailResult(status="failed", detail=f"gmail_http_{resp.status_code}")


def connect_status(practice_id: str) -> dict[str, Any]:
    return gmail_send_status(practice_id)


def disconnect_sender(practice_id: str) -> None:
    clear_gmail_send_tokens(practice_id)


__all__ = [
    "GMAIL_SEND_SCOPES",
    "GmailSendError",
    "WorkspaceDirectoryError",
    "connect_status",
    "deliver_self_test_message",
    "disconnect_sender",
    "exchange_gmail_send_code",
    "gmail_send_authorization_url",
    "gmail_send_redirect_uri",
    "save_gmail_send_tokens",
    "sender_email_allowed",
]
