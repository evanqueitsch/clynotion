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
    prior = practices.get(practice_id) if isinstance(practices.get(practice_id), dict) else {}
    row = {
        "refresh_token": refresh_token,
        "access_token": access_token,
        "email": email,
    }
    # Reconnecting Directory must not drop the separate send-only mailbox grant.
    if isinstance(prior, dict) and isinstance(prior.get("gmail_send"), dict):
        row["gmail_send"] = prior["gmail_send"]
    practices[practice_id] = row
    _save_token_store(store)


def clear_directory_tokens(practice_id: str) -> None:
    store = _load_token_store()
    practices = store.get("practices") or {}
    prior = practices.get(practice_id) if isinstance(practices.get(practice_id), dict) else None
    if not isinstance(prior, dict):
        return
    gmail = prior.get("gmail_send") if isinstance(prior.get("gmail_send"), dict) else None
    if gmail:
        practices[practice_id] = {"gmail_send": gmail}
    else:
        del practices[practice_id]
    store["practices"] = practices
    _save_token_store(store)


def save_gmail_send_tokens(
    practice_id: str,
    *,
    refresh_token: str,
    access_token: str = "",
    email: str = "",
) -> None:
    """Store the send-only mailbox grant without touching Directory tokens."""
    store = _load_token_store()
    practices = store.setdefault("practices", {})
    prior = practices.get(practice_id) if isinstance(practices.get(practice_id), dict) else {}
    row = dict(prior)
    row["gmail_send"] = {
        "refresh_token": refresh_token,
        "access_token": access_token,
        "email": email.strip().lower(),
    }
    practices[practice_id] = row
    _save_token_store(store)


def clear_gmail_send_tokens(practice_id: str) -> None:
    store = _load_token_store()
    practices = store.get("practices") or {}
    prior = practices.get(practice_id) if isinstance(practices.get(practice_id), dict) else None
    if not isinstance(prior, dict) or "gmail_send" not in prior:
        return
    row = dict(prior)
    row.pop("gmail_send", None)
    if row:
        practices[practice_id] = row
    else:
        del practices[practice_id]
    store["practices"] = practices
    _save_token_store(store)


def gmail_send_record(practice_id: str) -> dict[str, str]:
    store = _load_token_store()
    row = (store.get("practices") or {}).get(practice_id) or {}
    mail = row.get("gmail_send") if isinstance(row, dict) else None
    if not isinstance(mail, dict):
        return {}
    return {
        "refresh_token": str(mail.get("refresh_token") or ""),
        "access_token": str(mail.get("access_token") or ""),
        "email": str(mail.get("email") or "").strip().lower(),
    }


def gmail_send_status(practice_id: str) -> dict[str, Any]:
    mail = gmail_send_record(practice_id)
    email = mail.get("email") or ""
    connected = bool(mail.get("refresh_token")) and bool(email)
    return {
        "connected": connected,
        "sender_email": email if connected else "",
        "connect_url": "/auth/google/gmail-send/start",
        "scheduled_mail": "off",
    }


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


_RESOURCE_OU_SEGMENTS = frozenset(
    {
        "resources",
        "resource",
        "rooms",
        "room",
        "calendars",
        "conference rooms",
        "shared",
        "shared mailboxes",
        "service accounts",
    }
)


@dataclass
class DirectoryFetch:
    """One directory read. `complete` is true only when every page was read."""

    users: list[WorkspaceUser]
    skipped: dict[str, int]
    pages: int
    complete: bool
    error_code: str
    domain: str


def _ou_segments(path: str) -> list[str]:
    return [part.strip().lower() for part in (path or "").split("/") if part.strip()]


def classify_directory_row(
    row: dict[str, Any],
    domains: list[str],
    seen_emails: set[str],
) -> tuple[Optional[WorkspaceUser], str]:
    """Keep an active person, or a skip reason. Directory admin flags are ignored."""
    if not isinstance(row, dict):
        return None, "incomplete"
    if row.get("suspended"):
        return None, "suspended"
    if row.get("archived"):
        return None, "archived"
    if row.get("deletionTime"):
        return None, "deleted"
    if any(part in _RESOURCE_OU_SEGMENTS for part in _ou_segments(str(row.get("orgUnitPath") or ""))):
        return None, "shared_or_resource"
    email = str(row.get("primaryEmail") or "").strip().lower()
    google_id = str(row.get("id") or "").strip()
    if not email or not google_id or "@" not in email:
        return None, "incomplete"
    domain = email.rsplit("@", 1)[-1]
    if domains and domain not in domains:
        return None, "out_of_domain"
    if email in seen_emails:
        return None, "duplicate"
    name_obj = row.get("name") or {}
    display = ""
    if isinstance(name_obj, dict):
        display = str(name_obj.get("fullName") or "").strip()
    if not display:
        display = email.split("@", 1)[0]
    seen_emails.add(email)
    return (
        WorkspaceUser(
            google_id=google_id,
            email=email,
            display_name=display,
            suspended=False,
            org_unit=str(row.get("orgUnitPath") or ""),
        ),
        "",
    )


def consume_directory_pages(
    pages: list[dict[str, Any]],
    domains: list[str],
    *,
    max_pages: int = 50,
) -> DirectoryFetch:
    """Fold API pages into one list. Stops incomplete if a page token remains."""
    users: list[WorkspaceUser] = []
    skipped: dict[str, int] = {}
    seen: set[str] = set()
    domain = domains[0] if domains else ""
    consumed = 0
    leftover = False
    for page in pages:
        if consumed >= max_pages:
            leftover = True
            break
        consumed += 1
        if not isinstance(page, dict):
            return DirectoryFetch(users, skipped, consumed, False, "directory_list_failed", domain)
        for row in page.get("users") or []:
            person, reason = classify_directory_row(row if isinstance(row, dict) else {}, domains, seen)
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            if person is not None:
                users.append(person)
        if page.get("nextPageToken"):
            leftover = True
        else:
            leftover = False
            break
    complete = consumed > 0 and not leftover
    error = "" if complete else "directory_page_limit"
    users.sort(key=lambda item: (item.display_name.casefold(), item.email))
    return DirectoryFetch(users, skipped, consumed, complete, error, domain)


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


def list_directory_fetch(practice_id: str) -> DirectoryFetch:
    """Read every Directory page for this practice's allowed domain.

    Scope is admin.directory.user.readonly. A Workspace admin must connect
    once; this does not use domain-wide delegation or Gmail contacts.
    """
    mode = directory_mode()
    domains = allowed_domains()
    domain = domains[0] if domains else ""
    if mode == "off":
        raise WorkspaceDirectoryError("directory_off")
    if mode == "mock":
        users = [
            user
            for user in _mock_directory_users()
            if not user.suspended and (not domains or user.email.rsplit("@", 1)[-1] in domains)
        ]
        users.sort(key=lambda item: (item.display_name.casefold(), item.email))
        return DirectoryFetch(users, {}, 1, True, "", domain)

    access = _access_token_for_practice(practice_id)
    params: dict[str, str] = {
        "maxResults": "200",
        "orderBy": "email",
        "projection": "full",
    }
    if domains:
        params["domain"] = domains[0]
    else:
        params["customer"] = "my_customer"

    pages: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    seen_tokens: set[str] = set()
    with httpx.Client(timeout=30.0) as client:
        while len(pages) < 50:
            query = dict(params)
            if page_token:
                query["pageToken"] = page_token
            resp = client.get(
                DIRECTORY_USERS_URL,
                params=query,
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
            if not isinstance(payload, dict):
                raise WorkspaceDirectoryError("directory_list_failed")
            pages.append(payload)
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or page_token in seen_tokens:
                break
            seen_tokens.add(page_token)
    return consume_directory_pages(pages, domains)


def list_directory_users(practice_id: str) -> list[WorkspaceUser]:
    return list_directory_fetch(practice_id).users


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
