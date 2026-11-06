"""Weekly-hours mail transport.

Default is off: nothing is sent and no network call is made. Gmail is a
send-only service-account path (domain-wide delegation) used only when
TIME_MAIL_MODE=gmail. This module never logs credentials, tokens, or bodies.

Exactly-once external delivery is not possible here. See
docs/modules/ops6-weekly-hours.md for the claim/retry rules and the cases
that can still duplicate or drop a message.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Protocol

import httpx

ENV_MAIL_MODE = "TIME_MAIL_MODE"
ENV_MAIL_SENDER = "TIME_MAIL_SENDER"
ENV_SA_JSON = "GOOGLE_SA_JSON"

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

_SAFE_CODE = re.compile(r"[a-z0-9_]{1,40}")
_SAFE_PROVIDER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

MailStatus = Literal["sent", "failed", "uncertain", "not_ready"]


class MailConfigError(RuntimeError):
    """Safe configuration code. The message is the code and nothing else."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def safe_error_code(detail: str, *, fallback: str = "mail_failed") -> str:
    code = (detail or "").strip().lower()
    if _SAFE_CODE.fullmatch(code):
        return code
    return fallback


def safe_provider_id(value: str) -> str:
    token = (value or "").strip()
    if _SAFE_PROVIDER_ID.fullmatch(token):
        return token
    return ""


@dataclass(frozen=True)
class OutboundMail:
    to: str
    subject: str
    text: str
    csv_filename: str = ""
    csv_text: str = ""
    practice_id: str = ""


@dataclass(frozen=True)
class MailResult:
    status: MailStatus
    detail: str = ""
    provider_id: str = ""

    def safe(self) -> "MailResult":
        return MailResult(
            status=self.status,
            detail=safe_error_code(self.detail, fallback="mail_failed" if self.status != "sent" else "ok"),
            provider_id=safe_provider_id(self.provider_id),
        )


class MailSender(Protocol):
    def send(self, message: OutboundMail) -> MailResult: ...

    def check_ready(self) -> Optional[str]: ...


class RecordingMailer:
    """In-memory sender for tests. Records messages and never touches the network."""

    def __init__(
        self,
        result: Optional[MailResult | Callable[[OutboundMail], MailResult]] = None,
    ) -> None:
        self.sent: list[OutboundMail] = []
        self._result = result or MailResult(status="sent", detail="ok", provider_id="test")
        self._ready: Optional[str] = None

    def set_ready(self, problem: Optional[str]) -> None:
        self._ready = problem

    def check_ready(self) -> Optional[str]:
        return self._ready

    def send(self, message: OutboundMail) -> MailResult:
        self.sent.append(message)
        produced = self._result(message) if callable(self._result) else self._result
        return produced.safe()


_UNSET = object()
_mailer_override: Any = _UNSET


def set_mailer_for_tests(mailer: Any) -> None:
    """Install a mailer, or None to force 'not configured'. Pass _UNSET via clear."""
    global _mailer_override
    _mailer_override = mailer


def clear_mailer_for_tests() -> None:
    global _mailer_override
    _mailer_override = _UNSET


def mail_mode() -> str:
    return (os.environ.get(ENV_MAIL_MODE) or "off").strip().lower() or "off"


class ConnectedMailboxMailer:
    """Send scheduled hours mail from the practice's connected Gmail mailbox.

    Returns a normal failure when that mailbox is not connected. It does not
    read TIME_MAIL_MODE and it does not use a service account.
    """

    def check_ready(self) -> Optional[str]:
        return None

    def send(self, message: OutboundMail) -> MailResult:
        from app.hr.gmail_oauth import deliver_self_test_message

        if not message.practice_id:
            return MailResult(status="failed", detail="mail_practice_missing")
        return deliver_self_test_message(message.practice_id, message)


def build_mailer() -> Optional[MailSender]:
    """Return a sender, or None when mail is intentionally off.

    Tests may inject a mailer. ``None`` as the override means off.
    """
    if _mailer_override is not _UNSET:
        return _mailer_override
    if mail_mode() != "gmail":
        return None
    return GmailSender()


def load_service_account(raw: Optional[str] = None) -> dict[str, Any]:
    """Load the service-account JSON from env or an explicit string.

    Accepts a file path or inline JSON (Fly secrets are env vars). Errors are
    codes only — the raw secret is never included in the message.
    """
    value = (os.environ.get(ENV_SA_JSON) or "").strip() if raw is None else raw.strip()
    if not value:
        raise MailConfigError("sa_missing")
    try:
        if value.lstrip().startswith("{"):
            data = json.loads(value)
        else:
            data = json.loads(Path(value).read_text(encoding="utf-8"))
    except Exception as e:
        raise MailConfigError("sa_unreadable") from e
    if not isinstance(data, dict):
        raise MailConfigError("sa_unreadable")
    for key in ("client_email", "private_key", "token_uri"):
        if not str(data.get(key) or "").strip():
            raise MailConfigError("sa_incomplete")
    return data


def _sender_address() -> str:
    sender = (os.environ.get(ENV_MAIL_SENDER) or "").strip().lower()
    if "@" not in sender:
        raise MailConfigError("sender_missing")
    return sender


class GmailSender:
    """Send-only Gmail API using a service account with domain-wide delegation.

    The service account impersonates TIME_MAIL_SENDER (operations@heart4change.org
    in the intended setup). Scope is gmail.send only. Tokens stay in memory.
    """

    def __init__(
        self,
        *,
        client_factory: Optional[Callable[[], httpx.Client]] = None,
        sa_loader: Optional[Callable[[], dict[str, Any]]] = None,
    ) -> None:
        self._client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))
        )
        self._sa_loader = sa_loader or (lambda: load_service_account())
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    def check_ready(self) -> Optional[str]:
        try:
            self._sa_loader()
            _sender_address()
        except MailConfigError as e:
            return e.code
        return None

    def send(self, message: OutboundMail) -> MailResult:
        try:
            token = self._access_token()
            sender = _sender_address()
        except MailConfigError as e:
            return MailResult(status="not_ready", detail=e.code)
        raw = _raw_mime(message, sender=sender)
        try:
            response = self._post_send(token, raw)
        except httpx.ConnectError:
            return MailResult(status="failed", detail="gmail_connect")
        except httpx.ConnectTimeout:
            return MailResult(status="failed", detail="gmail_connect_timeout")
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError):
            # The request may have reached Gmail. Do not retry automatically.
            return MailResult(status="uncertain", detail="gmail_uncertain")
        except httpx.TimeoutException:
            return MailResult(status="uncertain", detail="gmail_timeout")
        except httpx.HTTPError:
            return MailResult(status="uncertain", detail="gmail_uncertain")
        if 200 <= response.status_code < 300:
            provider_id = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    provider_id = safe_provider_id(str(body.get("id") or ""))
            except Exception:
                provider_id = ""
            return MailResult(status="sent", detail="gmail_accepted", provider_id=provider_id)
        return MailResult(status="failed", detail=f"gmail_http_{response.status_code}")

    def _access_token(self) -> str:
        import time

        now = time.time()
        if self._token and now < self._token_exp - 60:
            return self._token
        sa = self._sa_loader()
        sender = _sender_address()
        assertion = _sign_assertion(sa, sender=sender, now=int(now))
        try:
            response = self._post_token(assertion)
        except httpx.ConnectError as e:
            raise MailConfigError("gmail_token_connect") from e
        except httpx.ConnectTimeout as e:
            raise MailConfigError("gmail_token_connect") from e
        except httpx.HTTPError as e:
            # Token call is before the Gmail send, so a timeout here did not send mail.
            raise MailConfigError("gmail_token_unreachable") from e
        if response.status_code >= 400:
            raise MailConfigError(f"gmail_token_http_{response.status_code}")
        try:
            payload = response.json()
        except Exception as e:
            raise MailConfigError("gmail_token_unreadable") from e
        token = str((payload or {}).get("access_token") or "").strip() if isinstance(payload, dict) else ""
        if not token:
            raise MailConfigError("gmail_token_missing")
        expires_in = 3600
        if isinstance(payload, dict):
            try:
                expires_in = int(payload.get("expires_in") or 3600)
            except (TypeError, ValueError):
                expires_in = 3600
        self._token = token
        self._token_exp = now + max(60, expires_in)
        return token

    def _post_token(self, assertion: str) -> httpx.Response:
        with self._client_factory() as client:
            return client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

    def _post_send(self, token: str, raw: str) -> httpx.Response:
        with self._client_factory() as client:
            return client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {token}"},
            )


def _sign_assertion(sa: dict[str, Any], *, sender: str, now: int) -> str:
    import jwt

    payload = {
        "iss": str(sa["client_email"]),
        "sub": sender,
        "scope": GMAIL_SEND_SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    return jwt.encode(payload, str(sa["private_key"]), algorithm="RS256")


def _raw_mime(message: OutboundMail, *, sender: str) -> str:
    mime = EmailMessage()
    mime["To"] = message.to
    mime["From"] = sender
    mime["Subject"] = message.subject
    mime.set_content(message.text)
    if message.csv_text:
        mime.add_attachment(
            message.csv_text.encode("utf-8"),
            subtype="csv",
            filename=message.csv_filename or "weekly-hours.csv",
        )
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


def delivery_flags() -> dict[str, Any]:
    """Non-secret operator flags for the settings screen."""
    mode = mail_mode()
    sender = (os.environ.get(ENV_MAIL_SENDER) or "").strip().lower()
    if mode != "gmail":
        mode = "off"
    return {
        "mail_mode": mode,
        "sender": sender if mode == "gmail" else "",
        "sa_present": bool((os.environ.get(ENV_SA_JSON) or "").strip()),
        "cron_secret_set": bool((os.environ.get("CRON_SECRET") or "").strip()),
        "sends_mail": mode == "gmail",
    }
