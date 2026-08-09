"""Google Workspace Directory sync — identity/roster only (not session PHI)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.crypto import decrypt_utf8, encrypt_utf8
from app.google_oauth import (
    ENV_PUBLIC_BASE_URL,
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    _clean_env,
    _client_id,
    _client_secret,
    allowed_domains,
    make_state,
    public_base_url,
    verify_state,
)

DIRECTORY_SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
DIRECTORY_SCOPES = f"openid email profile {DIRECTORY_SCOPE}"
DIRECTORY_USERS_URL = "https://admin.googleapis.com/admin/directory/v1/users"

ENV_TOKEN_PATH = "ATTUNE_WORKSPACE_TOKEN_PATH"
DEFAULT_TOKEN_PATH = ".attune_data/workspace_tokens.enc"
ENV_DIRECTORY_MODE = "ATTUNE_WORKSPACE_DIRECTORY"


@dataclass(frozen=True)
class WorkspaceUser:
    google_id: str
    email: str
    display_name: str
    suspended: bool = False
    org_unit: str = ""


class WorkspaceDirectoryError(RuntimeError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def directory_mode() -> str:
    """live|mock|off — mock returns synthetic directory users for local/dev."""
    raw = _clean_env(os.environ.get(ENV_DIRECTORY_MODE, "")).lower()
    if raw in ("live", "mock", "off"):
        return raw
    # Default: mock in non-google auth, live when google auth is configured.
    from app.auth import auth_mode

    return "live" if auth_mode() == "google" else "mock"


def directory_redirect_uri() -> str:
    base = public_base_url()
    if not base:
        raise RuntimeError(
            f"{ENV_PUBLIC_BASE_URL} is required for Workspace directory OAuth"
        )
    return f"{base}/auth/google/directory/callback"


def directory_authorization_url(state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": directory_redirect_uri(),
        "response_type": "code",
        "scope": DIRECTORY_SCOPES,
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent select_account",
    }
    domains = allowed_domains()
    if len(domains) == 1:
        params["hd"] = domains[0]
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _token_path() -> Path:
    raw = _clean_env(os.environ.get(ENV_TOKEN_PATH, "")) or DEFAULT_TOKEN_PATH
    return Path(raw)


def _load_token_store() -> dict[str, Any]:
    path = _token_path()
    if not path.is_file():
        return {"version": 1, "practices": {}}
    try:
        payload = json.loads(decrypt_utf8(path.read_bytes()))
    except (ValueError, json.JSONDecodeError, OSError):
        return {"version": 1, "practices": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "practices": {}}
    practices = payload.get("practices")
    if not isinstance(practices, dict):
        payload["practices"] = {}
    return payload


def _save_token_store(payload: dict[str, Any]) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_utf8(json.dumps(payload)))


def save_directory_tokens(
    practice_id: str,
    *,
    refresh_token: str,
    access_token: str = "",
    email: str = "",
) -> None:
    store = _load_token_store()
    practices = store.setdefault("practices", {})
    practices[practice_id] = {
        "refresh_token": refresh_token,
        "access_token": access_token,
        "email": email,
    }
    _save_token_store(store)


def clear_directory_tokens(practice_id: str) -> None:
    store = _load_token_store()
    practices = store.get("practices") or {}
    if practice_id in practices:
        del practices[practice_id]
        store["practices"] = practices
        _save_token_store(store)


def directory_connection_status(practice_id: str) -> dict[str, Any]:
    mode = directory_mode()
    store = _load_token_store()
    row = (store.get("practices") or {}).get(practice_id) or {}
    connected = mode == "mock" or bool(row.get("refresh_token") or row.get("access_token"))
    return {
        "mode": mode,
        "connected": connected,
        "admin_email": str(row.get("email") or ""),
        "domain": (allowed_domains()[0] if allowed_domains() else ""),
        "connect_url": "/auth/google/directory/start",
    }


def exchange_directory_code(code: str) -> dict[str, str]:
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": directory_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise WorkspaceDirectoryError("directory_token_exchange_failed")
        tokens = resp.json()
    access = str(tokens.get("access_token") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    if not access and not refresh:
        raise WorkspaceDirectoryError("directory_token_missing")
    email = ""
    if access:
        try:
            with httpx.Client(timeout=15.0) as client:
                info = client.get(
                    "https://openidconnect.googleapis.com/userinfo",
                    headers={"Authorization": f"Bearer {access}"},
                )
                if info.status_code < 400:
                    email = str((info.json() or {}).get("email") or "")
        except Exception:
            email = ""
    return {"access_token": access, "refresh_token": refresh, "email": email}


def _refresh_access_token(refresh_token: str) -> str:
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
            raise WorkspaceDirectoryError("directory_refresh_failed")
        access = str((resp.json() or {}).get("access_token") or "").strip()
    if not access:
        raise WorkspaceDirectoryError("directory_refresh_failed")
    return access


def _access_token_for_practice(practice_id: str) -> str:
    store = _load_token_store()
    row = (store.get("practices") or {}).get(practice_id) or {}
    refresh = str(row.get("refresh_token") or "").strip()
    access = str(row.get("access_token") or "").strip()
    if refresh:
        access = _refresh_access_token(refresh)
        row["access_token"] = access
        store.setdefault("practices", {})[practice_id] = row
        _save_token_store(store)
        return access
    if access:
        return access
    raise WorkspaceDirectoryError("directory_not_connected")


def _mock_directory_users() -> list[WorkspaceUser]:
    domain = allowed_domains()[0] if allowed_domains() else "example.com"
    return [
        WorkspaceUser(
            google_id="mock-1",
            email=f"alex.supervisor@{domain}",
            display_name="Alex Supervisor",
        ),
        WorkspaceUser(
            google_id="mock-2",
            email=f"jamie.clinician@{domain}",
            display_name="Jamie Clinician",
        ),
        WorkspaceUser(
            google_id="mock-3",
            email=f"riley.clinician@{domain}",
            display_name="Riley Clinician",
        ),
    ]


def list_directory_users(practice_id: str) -> list[WorkspaceUser]:
    mode = directory_mode()
    if mode == "off":
        raise WorkspaceDirectoryError("directory_off")
    if mode == "mock":
        return _mock_directory_users()

    access = _access_token_for_practice(practice_id)
    domains = allowed_domains()
    params: dict[str, str] = {
        "maxResults": "200",
        "orderBy": "email",
        "projection": "basic",
    }
    if domains:
        params["domain"] = domains[0]
    else:
        params["customer"] = "my_customer"

    out: list[WorkspaceUser] = []
    page_token: Optional[str] = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            q = dict(params)
            if page_token:
                q["pageToken"] = page_token
            resp = client.get(
                DIRECTORY_USERS_URL,
                params=q,
                headers={"Authorization": f"Bearer {access}"},
            )
            if resp.status_code == 403:
                raise WorkspaceDirectoryError(
                    "directory_forbidden",
                    detail="Workspace admin consent required for Directory API",
                )
            if resp.status_code >= 400:
                raise WorkspaceDirectoryError(
                    "directory_list_failed",
                    detail=f"status_{resp.status_code}",
                )
            payload = resp.json() or {}
            for row in payload.get("users") or []:
                if not isinstance(row, dict):
                    continue
                email = str(row.get("primaryEmail") or "").strip().lower()
                google_id = str(row.get("id") or "").strip()
                name_obj = row.get("name") or {}
                display = ""
                if isinstance(name_obj, dict):
                    display = str(name_obj.get("fullName") or "").strip()
                if not display:
                    display = email.split("@", 1)[0] if email else google_id
                if not email or not google_id:
                    continue
                # Domain allowlist defense in depth.
                if domains and email.rsplit("@", 1)[-1] not in domains:
                    continue
                out.append(
                    WorkspaceUser(
                        google_id=google_id,
                        email=email,
                        display_name=display,
                        suspended=bool(row.get("suspended")),
                        org_unit=str(row.get("orgUnitPath") or ""),
                    )
                )
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
    return out


# Re-export helpers used by main for directory OAuth state cookies.
__all__ = [
    "DIRECTORY_SCOPE",
    "WorkspaceDirectoryError",
    "WorkspaceUser",
    "clear_directory_tokens",
    "directory_authorization_url",
    "directory_connection_status",
    "directory_mode",
    "directory_redirect_uri",
    "exchange_directory_code",
    "list_directory_users",
    "make_state",
    "save_directory_tokens",
    "verify_state",
]
