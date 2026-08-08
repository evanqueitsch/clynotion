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


class GoogleOAuthError(RuntimeError):
    """Safe, non-secret failure code for redirects / logs."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    hd: Optional[str]


def _clean_env(value: str) -> str:
    """Strip whitespace and accidental wrapping quotes from Fly/dashboard pastes."""
    return value.strip().strip('"').strip("'").strip()


def google_configured() -> bool:
    return bool(
        _clean_env(os.environ.get(ENV_CLIENT_ID, ""))
        and _clean_env(os.environ.get(ENV_CLIENT_SECRET, ""))
    )


def allowed_domains() -> list[str]:
    raw = _clean_env(os.environ.get(ENV_ALLOWED_DOMAINS, ""))
    if not raw:
        return []
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def public_base_url() -> str:
    return _clean_env(os.environ.get(ENV_PUBLIC_BASE_URL, "")).rstrip("/")


def default_practice_id() -> str:
    return _clean_env(os.environ.get(ENV_DEFAULT_PRACTICE, "")) or DEFAULT_PRACTICE_ID


def redirect_uri() -> str:
    base = public_base_url()
    if not base:
        raise RuntimeError(
            f"{ENV_PUBLIC_BASE_URL} is required for Google OAuth "
            "(e.g. https://clynotion.fly.dev)"
        )
    return f"{base}/auth/google/callback"


def _client_id() -> str:
    value = _clean_env(os.environ.get(ENV_CLIENT_ID, ""))
    if not value:
        raise RuntimeError(f"{ENV_CLIENT_ID} is required for Google OAuth")
    return value


def _client_secret() -> str:
    value = _clean_env(os.environ.get(ENV_CLIENT_SECRET, ""))
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


def _map_token_error(payload: dict) -> str:
    err = str(payload.get("error") or "").strip().lower()
    if err == "redirect_uri_mismatch":
        return "google_redirect"
    if err == "invalid_client":
        return "google_client"
    if err == "invalid_grant":
        return "google_grant"
    if err:
        return "google_exchange"
    return "google_exchange"


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
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code >= 400:
            try:
                payload = token_resp.json()
            except Exception:
                payload = {}
            mapped = _map_token_error(payload if isinstance(payload, dict) else {})
            # Log non-secret Google error fields for Fly dashboard debugging.
            print(
                "google_token_exchange_failed",
                {
                    "status": token_resp.status_code,
                    "error": (payload or {}).get("error") if isinstance(payload, dict) else None,
                    "error_description": (
                        (payload or {}).get("error_description")
                        if isinstance(payload, dict)
                        else None
                    ),
                    "redirect_uri": redirect_uri(),
                },
                flush=True,
            )
            raise GoogleOAuthError(mapped)

        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise GoogleOAuthError("google_exchange", detail="token_missing")

        info_resp = client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if info_resp.status_code >= 400:
            print(
                "google_userinfo_failed",
                {"status": info_resp.status_code},
                flush=True,
            )
            raise GoogleOAuthError("google_exchange", detail="userinfo_failed")
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


def oauth_public_diagnostics() -> dict[str, object]:
    """Non-secret config snapshot for /auth/config debugging."""
    cid = _clean_env(os.environ.get(ENV_CLIENT_ID, ""))
    csec = _clean_env(os.environ.get(ENV_CLIENT_SECRET, ""))
    base = public_base_url()
    return {
        "google_configured": bool(cid and csec),
        "client_id_suffix": cid[-12:] if len(cid) >= 12 else "",
        "client_secret_set": bool(csec),
        "client_secret_len": len(csec),
        "redirect_uri": f"{base}/auth/google/callback" if base else "",
        "allowed_domains": allowed_domains(),
        "public_base_url": base,
    }


def probe_token_endpoint() -> dict[str, object]:
    """
    POST a deliberately invalid code to Google's token endpoint.
    Interprets the error to validate client id/secret + redirect_uri without
    exposing secret values.
    - invalid_grant => client + redirect look accepted
    - invalid_client => bad Client ID/Secret pair
    - redirect_uri_mismatch => Google Console redirect URI wrong
    """
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": "attune-diagnostic-invalid-code",
                    "client_id": _client_id(),
                    "client_secret": _client_secret(),
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        err = str((payload or {}).get("error") or "")
        desc = str((payload or {}).get("error_description") or "")
        verdict = "unknown"
        if err == "invalid_grant":
            verdict = "client_and_redirect_ok"
        elif err == "invalid_client":
            verdict = "bad_client_id_or_secret"
        elif err == "redirect_uri_mismatch":
            verdict = "redirect_uri_mismatch"
        elif err == "unauthorized_client":
            verdict = "unauthorized_client"
        return {
            "http_status": resp.status_code,
            "error": err,
            "error_description": desc[:300],
            "verdict": verdict,
            "redirect_uri": redirect_uri(),
            "client_id_suffix": _client_id()[-20:],
            "client_secret_len": len(_client_secret()),
        }
    except Exception as e:
        return {
            "http_status": 0,
            "error": "probe_failed",
            "error_description": type(e).__name__,
            "verdict": "probe_failed",
            "redirect_uri": public_base_url() and redirect_uri() or "",
        }
