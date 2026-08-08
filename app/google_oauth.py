"""Google OAuth / Workspace SSO (identity only — no PHI in this path)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/userinfo"

ENV_CLIENT_ID = "GOOGLE_CLIENT_ID"
ENV_CLIENT_SECRET = "GOOGLE_CLIENT_SECRET"
ENV_ALLOWED_DOMAINS = "GOOGLE_ALLOWED_DOMAINS"
ENV_PUBLIC_BASE_URL = "ATTUNE_PUBLIC_BASE_URL"
ENV_DEFAULT_PRACTICE = "ATTUNE_DEFAULT_PRACTICE_ID"

DEFAULT_PRACTICE_ID = "practice-hfc"
OAUTH_SCOPES = "openid email profile"


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    hd: Optional[str]


def google_configured() -> bool:
    return bool(
        os.environ.get(ENV_CLIENT_ID, "").strip()
        and os.environ.get(ENV_CLIENT_SECRET, "").strip()
    )


def allowed_domains() -> list[str]:
    raw = os.environ.get(ENV_ALLOWED_DOMAINS, "").strip()
    if not raw:
        return []
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def public_base_url() -> str:
    return os.environ.get(ENV_PUBLIC_BASE_URL, "").strip().rstrip("/")


def default_practice_id() -> str:
    return (
        os.environ.get(ENV_DEFAULT_PRACTICE, "").strip() or DEFAULT_PRACTICE_ID
    )


def redirect_uri() -> str:
    base = public_base_url()
    if not base:
        raise RuntimeError(
            f"{ENV_PUBLIC_BASE_URL} is required for Google OAuth "
            "(e.g. https://clynotion.fly.dev)"
        )
    return f"{base}/auth/google/callback"


def _client_id() -> str:
    value = os.environ.get(ENV_CLIENT_ID, "").strip()
    if not value:
        raise RuntimeError(f"{ENV_CLIENT_ID} is required for Google OAuth")
    return value


def _client_secret() -> str:
    value = os.environ.get(ENV_CLIENT_SECRET, "").strip()
    if not value:
        raise RuntimeError(f"{ENV_CLIENT_SECRET} is required for Google OAuth")
    return value


def make_state(signing_secret: str) -> str:
    nonce = secrets.token_urlsafe(24)
    ts = str(int(time.time()))
    msg = f"{nonce}.{ts}".encode("utf-8")
    sig = hmac.new(signing_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{nonce}.{ts}.{sig}"


def verify_state(state: str, signing_secret: str, *, max_age_sec: int = 600) -> bool:
    parts = (state or "").split(".")
    if len(parts) != 3:
        return False
    nonce, ts, sig = parts
    msg = f"{nonce}.{ts}".encode("utf-8")
    expected = hmac.new(
        signing_secret.encode("utf-8"), msg, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        issued = int(ts)
    except ValueError:
        return False
    return 0 <= (time.time() - issued) <= max_age_sec


def authorization_url(state: str) -> str:
    domains = allowed_domains()
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "access_type": "online",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    # Workspace SSO hint — Google shows the org account picker when set.
    if len(domains) == 1:
        params["hd"] = domains[0]
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _email_allowed(email: str, hd: Optional[str]) -> bool:
    domains = allowed_domains()
    if not domains:
        return False
    email_l = email.strip().lower()
    if "@" not in email_l:
        return False
    domain = email_l.rsplit("@", 1)[1]
    if domain not in domains:
        return False
    # Prefer Workspace-hosted accounts when Google returns hd.
    if hd is not None and hd.strip().lower() not in domains:
        return False
    return True


def exchange_code(code: str) -> GoogleIdentity:
    with httpx.Client(timeout=20.0) as client:
        token_resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code >= 400:
            raise RuntimeError("google_token_exchange_failed")
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise RuntimeError("google_token_missing")

        info_resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if info_resp.status_code >= 400:
            raise RuntimeError("google_userinfo_failed")
        info = info_resp.json()

    email = str(info.get("email") or "").strip().lower()
    sub = str(info.get("sub") or "").strip()
    name = str(info.get("name") or info.get("given_name") or email).strip()
    hd = info.get("hd")
    hd_s = str(hd).strip().lower() if hd else None
    verified = bool(info.get("email_verified"))

    if not email or not sub or not verified:
        raise PermissionError("google_email_unverified")
    if not _email_allowed(email, hd_s):
        raise PermissionError("google_domain_not_allowed")

    return GoogleIdentity(sub=sub, email=email, name=name, hd=hd_s)
