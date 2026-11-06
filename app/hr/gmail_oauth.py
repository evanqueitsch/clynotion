"""Send-only Gmail connect for the labelled self-test.

Uses the existing Google OAuth client. The refresh token is stored beside
the Directory grant and is never used for scheduled payroll mail.
Scope is gmail.send plus openid/email so the consented mailbox can be verified.
No inbox read, and no service-account delegation.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.google_oauth import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    GoogleOAuthError,
    _claims_from_id_token,
    _client_id,
    _client_secret,
    _identity_from_claims,
    allowed_domains,
    public_base_url,
)
from app.hr.time_mail import GMAIL_SEND_SCOPE, MailResult, OutboundMail, safe_error_code
from app.workspace_directory import (
    WorkspaceDirectoryError,
    _access_token_usable,
    clear_gmail_send_tokens,
    gmail_send_record,
    gmail_send_status,
    note_gmail_send_error,
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


def _log_google_token_error(status: int, payload: dict[str, Any]) -> None:
    """Log Google's error name only. Descriptions can echo an address or a code."""
    err = str(payload.get("error") or "")[:80]
    print("gmail_send_exchange", {"status": status, "error": err}, flush=True)


def _google_token_error_code(payload: dict[str, Any]) -> str:
    err = str(payload.get("error") or "").strip().lower()
    return {
        "redirect_uri_mismatch": "gmail_send_redirect",
        "invalid_scope": "gmail_send_scope",
        "access_denied": "gmail_send_denied",
        "admin_policy_enforced": "gmail_send_policy",
        "org_internal": "gmail_send_policy",
    }.get(err, "gmail_send_exchange_failed")


def _email_from_userinfo(client: httpx.Client, access: str) -> str:
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
    return email


def _email_from_id_token(id_token: str) -> str:
    try:
        identity = _identity_from_claims(_claims_from_id_token(id_token))
    except PermissionError as exc:
        if "domain" in str(exc):
            raise GmailSendError("gmail_send_domain") from exc
        raise GmailSendError("gmail_send_identity_failed") from exc
    except GoogleOAuthError as exc:
        raise GmailSendError("gmail_send_identity_failed") from exc
    if not sender_email_allowed(identity.email):
        raise GmailSendError("gmail_send_domain")
    return identity.email


def exchange_gmail_send_code(code: str) -> dict[str, str]:
    """Exchange the consent code. Raises GmailSendError with a safe code.

    Identity comes from the OpenID id_token when Google sends one, and from
    userinfo only if that token is absent. A verified practice mailbox is
    stored even when Google omits a refresh token; the access token can send
    until it expires.
    """
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
        try:
            body = resp.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if resp.status_code >= 400:
            _log_google_token_error(resp.status_code, body)
            raise GmailSendError(_google_token_error_code(body))
        access = str(body.get("access_token") or "").strip()
        refresh = str(body.get("refresh_token") or "").strip()
        id_token = str(body.get("id_token") or "").strip()
        if not access and not refresh:
            raise GmailSendError("gmail_send_token_missing")
        scope = str(body.get("scope") or "").strip()
        if scope and GMAIL_SEND_SCOPE not in scope.split():
            raise GmailSendError("gmail_send_scope")
        if id_token:
            email = _email_from_id_token(id_token)
        elif access:
            email = _email_from_userinfo(client, access)
        else:
            raise GmailSendError("gmail_send_identity_failed")
    try:
        expires_in = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    return {
        "access_token": access,
        "refresh_token": refresh,
        "email": email,
        "expires_in": str(expires_in),
    }


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
        # Bytes need an explicit main type. subtype alone raises TypeError
        # before any network call.
        mime.add_attachment(
            message.csv_text,
            subtype="csv",
            charset="utf-8",
            filename=message.csv_filename or "weekly-hours-self-test.csv",
        )
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


def _gmail_failure_code(status: int, body: dict[str, Any]) -> str:
    """Map a Gmail error to a safe code. Do not keep the provider message text."""
    reasons: list[str] = []
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        if err.get("status"):
            reasons.append(str(err.get("status")))
        for item in list(err.get("errors") or []) + list(err.get("details") or []):
            if isinstance(item, dict) and item.get("reason"):
                reasons.append(str(item.get("reason")))
    blob = " ".join(reasons).lower()
    if "accessnotconfigured" in blob or "service_disabled" in blob:
        return "gmail_api_disabled"
    if "insufficient" in blob or "scope" in blob:
        return "gmail_scope"
    if 400 <= status <= 599:
        return f"gmail_http_{status}"
    return "gmail_http_error"


def deliver_self_test_message(practice_id: str, message: OutboundMail) -> MailResult:
    """Send one already-built self-test message from the connected mailbox."""
    record = gmail_send_record(practice_id)
    refresh = record.get("refresh_token") or ""
    sender = record.get("email") or ""
    if not sender_email_allowed(sender):
        return MailResult(status="failed", detail="gmail_not_connected")
    try:
        if refresh:
            access = _refresh_gmail_access(refresh)
        elif _access_token_usable(record):
            access = record.get("access_token") or ""
        elif record.get("access_token"):
            return MailResult(status="failed", detail="gmail_send_access_expired")
        else:
            return MailResult(status="failed", detail="gmail_not_connected")
    except GmailSendError as exc:
        return MailResult(status="failed", detail=safe_error_code(exc.code))
    if not access:
        return MailResult(status="failed", detail="gmail_not_connected")
    try:
        raw = _raw_message(message, sender)
    except Exception as exc:
        print("self_test_send_error", {"phase": "mime", "type": type(exc).__name__}, flush=True)
        return MailResult(status="failed", detail="gmail_mime_failed")
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
            resp = client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {access}"},
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
        return MailResult(status="failed", detail="gmail_connect")
    except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError):
        # The request may already have been received.
        return MailResult(status="uncertain", detail="gmail_uncertain")
    except Exception as exc:
        print("self_test_send_error", {"phase": "after_post", "type": type(exc).__name__}, flush=True)
        return MailResult(status="uncertain", detail="gmail_uncertain")
    if 200 <= resp.status_code < 300:
        message_id = ""
        try:
            payload = resp.json() if isinstance(resp.json(), dict) else {}
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            candidate = str(payload.get("id") or "")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate):
                message_id = candidate
        return MailResult(status="sent", detail="gmail_accepted", provider_id=message_id)
    try:
        body = resp.json() if isinstance(resp.json(), dict) else {}
    except Exception:
        body = {}
    return MailResult(status="failed", detail=_gmail_failure_code(resp.status_code, body))


_STATUS_DETAIL = {
    "gmail_send_domain": "That Google account is not on heart4change.org. Personal Gmail cannot send. Nothing was saved.",
    "gmail_send_identity_failed": "Google did not return a verified mailbox address, so the mailbox was not saved.",
    "gmail_send_exchange_failed": "Google returned to Clynotion, but the token exchange failed, so the mailbox was not saved.",
    "gmail_send_token_missing": "Google did not return a token, so the mailbox was not saved.",
    "gmail_send_scope": "Google did not grant send-only access (gmail.send). Allow that permission, or ask a Workspace admin to permit it for this app.",
    "gmail_send_policy": "Workspace policy blocked send access. A Workspace admin must allow this app to send mail, then connect again.",
    "gmail_send_redirect": "Google rejected the callback address. The existing OAuth client must include https://clynotion.com/auth/google/gmail-send/callback.",
    "gmail_send_denied": "Google denied send access before a mailbox could be saved.",
    "gmail_send_state": "The connect attempt failed the security check. Use Connect sending mailbox again from this page.",
    "gmail_send_session": "Your sign-in was missing when Google returned. Stay signed in and use Connect sending mailbox again.",
    "gmail_send_save_failed": "Google authorized the mailbox, but Clynotion could not store it. Nothing was sent.",
    "gmail_send_missing": "Google returned without an authorization code. Use Connect sending mailbox again.",
    "gmail_send_access_expired": "The sending grant expired and Google did not return a refresh token. Connect the mailbox again.",
    "gmail_send_refresh_missing": "Google did not return a lasting grant. Connect again and approve the prompt.",
}


def connect_status(practice_id: str) -> dict[str, Any]:
    """Public mailbox status. Scheduled mail stays off even when this is connected."""
    status = gmail_send_status(practice_id)
    email = str(status.get("sender_email") or "")
    if status.get("connected") and not sender_email_allowed(email):
        status["connected"] = False
        status["sender_email"] = ""
        status["refresh_saved"] = False
        status["last_error"] = "gmail_send_domain"
    error = str(status.get("last_error") or "")
    if status.get("connected"):
        warning = ""
        if not status.get("refresh_saved"):
            warning = " Connected with a short-lived grant. If Send fails, connect the mailbox again."
        status["status_detail"] = (
            f"Verified From address: {status.get('sender_email')}. "
            "Scheduled payroll mail stays off." + warning
        )
    else:
        status["status_detail"] = _STATUS_DETAIL.get(
            error,
            "Sending mailbox is not connected. Choose the heart4change.org mailbox in the Google prompt. "
            "operations@heart4change.org is the intended mailbox only if that is the account you authorize.",
        )
    status["scheduled_mail"] = "off"
    return status


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
    "note_gmail_send_error",
    "gmail_send_authorization_url",
    "gmail_send_redirect_uri",
    "save_gmail_send_tokens",
    "sender_email_allowed",
]
