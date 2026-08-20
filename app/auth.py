"""JWT auth: Google Workspace SSO (prod) + optional dev password store."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import is_real_mode
from app.google_oauth import default_practice_id

ENV_JWT_SECRET = "ATTUNE_JWT_SECRET"
ENV_AUTH_MODE = "ATTUNE_AUTH"
COOKIE_NAME = "attune_token"
_ALGORITHM = "HS256"
_TOKEN_TTL_SEC = 60 * 60 * 12

_security = HTTPBearer(auto_error=False)
_ephemeral_secret: Optional[str] = None


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    practice_id: str
    email: str = ""


class FakeUserStore:
    """In-memory users for local/dev. Passwords are plaintext on purpose (fake store)."""

    def __init__(self) -> None:
        self._users: dict[str, tuple[str, User]] = {
            "alice": (
                "alice-pass",
                User(
                    user_id="user-alice",
                    username="alice",
                    practice_id="practice-a",
                    email="alice@example.com",
                ),
            ),
            "bob": (
                "bob-pass",
                User(
                    user_id="user-bob",
                    username="bob",
                    practice_id="practice-b",
                    email="bob@example.com",
                ),
            ),
        }

    def authenticate(self, username: str, password: str) -> Optional[User]:
        row = self._users.get(username)
        if row is None:
            return None
        expected, user = row
        if password != expected:
            return None
        return user

    def get(self, user_id: str) -> Optional[User]:
        for _, user in self._users.values():
            if user.user_id == user_id:
                return user
        return None

    def clear(self) -> None:
        pass


user_store = FakeUserStore()


def auth_mode() -> str:
    """
    ATTUNE_AUTH=dev|google
    Default: google when GOOGLE_CLIENT_ID is set, else dev.
    """
    raw = (os.environ.get(ENV_AUTH_MODE) or "").strip().lower()
    if raw in ("dev", "google"):
        return raw
    from app.google_oauth import google_configured

    return "google" if google_configured() else "dev"


def password_login_enabled() -> bool:
    return auth_mode() == "dev"


def reset_ephemeral_jwt_secret_for_tests() -> None:
    global _ephemeral_secret
    _ephemeral_secret = None


def _jwt_secret() -> str:
    global _ephemeral_secret
    env = os.environ.get(ENV_JWT_SECRET, "").strip()
    if env:
        return env
    if is_real_mode() or auth_mode() == "google":
        raise RuntimeError(
            f"{ENV_JWT_SECRET} is required when ATTUNE_MODE=real or ATTUNE_AUTH=google; "
            "refusing ephemeral JWT secret"
        )
    if _ephemeral_secret is None:
        _ephemeral_secret = os.urandom(32).hex()
    return _ephemeral_secret


def jwt_signing_secret() -> str:
    return _jwt_secret()


def issue_token(user: User, *, ttl_sec: Optional[int] = None) -> str:
    now = int(time.time())
    ttl = _TOKEN_TTL_SEC if ttl_sec is None else ttl_sec
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "practice_id": user.practice_id,
        "email": user.email,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def user_from_google(*, sub: str, email: str, name: str) -> User:
    local = email.split("@", 1)[0] if "@" in email else email
    username = (name or local or email).strip() or email
    return User(
        user_id=f"google:{sub}",
        username=username,
        practice_id=default_practice_id(),
        email=email.strip().lower(),
    )


def decode_token(token: str) -> User:
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired",
        ) from e
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        ) from e
    user_id = payload.get("sub")
    practice_id = payload.get("practice_id")
    username = payload.get("username", "")
    email = payload.get("email", "")
    if not user_id or not practice_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        )
    user = user_store.get(str(user_id))
    if user is None:
        return User(
            user_id=str(user_id),
            username=str(username),
            practice_id=str(practice_id),
            email=str(email or ""),
        )
    return user


def _token_from_request(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials],
) -> Optional[str]:
    if creds is not None and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    return None


def get_current_user(
    request: Request,
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_security)],
) -> User:
    token = _token_from_request(request, creds)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    return decode_token(token)


def get_optional_user(
    request: Request,
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_security)],
) -> Optional[User]:
    token = _token_from_request(request, creds)
    if not token:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[Optional[User], Depends(get_optional_user)]


def is_hr_admin(user: User) -> bool:
    """Owner / back-office admin gate for HR onboarding (employee PII zone).

    Allow when:
    - username is a known seed owner (alice), or
    - email is in ATTUNE_HR_ADMIN_EMAILS, or
    - roster clinician with same email has default_role admin.
    """
    if user.username in ("alice",):
        return True
    allow = {
        e.strip().lower()
        for e in (os.environ.get("ATTUNE_HR_ADMIN_EMAILS") or "").split(",")
        if e.strip()
    }
    email = (user.email or "").strip().lower()
    if email and email in allow:
        return True
    if email:
        try:
            from app.clinicians import clinician_store

            for clin in clinician_store.list_for_practice(user.practice_id):
                if clin.default_role != "admin":
                    continue
                clin_email = (clin.email or "").strip().lower()
                if email and clin_email == email:
                    return True
                # Directory may not have populated email yet — match display name.
                if (user.username or "").strip().lower() == clin.display_name.strip().lower():
                    return True
        except Exception:
            pass
    return False


def require_hr_admin(user: CurrentUser) -> User:
    if not is_hr_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="HR admin only — onboarding is owner/back-office access",
        )
    return user


HrAdminUser = Annotated[User, Depends(require_hr_admin)]
