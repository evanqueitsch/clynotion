"""JWT bearer auth + fake user/practice store (dev/MOCK — no external IdP)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import is_real_mode

ENV_JWT_SECRET = "ATTUNE_JWT_SECRET"
_ALGORITHM = "HS256"
_TOKEN_TTL_SEC = 60 * 60 * 12

_security = HTTPBearer(auto_error=False)
_ephemeral_secret: Optional[str] = None


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    practice_id: str


class FakeUserStore:
    """In-memory users for local/dev. Passwords are plaintext on purpose (fake store)."""

    def __init__(self) -> None:
        self._users: dict[str, tuple[str, User]] = {
            "alice": (
                "alice-pass",
                User(user_id="user-alice", username="alice", practice_id="practice-a"),
            ),
            "bob": (
                "bob-pass",
                User(user_id="user-bob", username="bob", practice_id="practice-b"),
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


def reset_ephemeral_jwt_secret_for_tests() -> None:
    global _ephemeral_secret
    _ephemeral_secret = None


def _jwt_secret() -> str:
    global _ephemeral_secret
    env = os.environ.get(ENV_JWT_SECRET, "").strip()
    if env:
        return env
    if is_real_mode():
        raise RuntimeError(
            f"{ENV_JWT_SECRET} is required when ATTUNE_MODE=real; refusing ephemeral JWT secret"
        )
    if _ephemeral_secret is None:
        _ephemeral_secret = os.urandom(32).hex()
    return _ephemeral_secret


def issue_token(user: User, *, ttl_sec: Optional[int] = None) -> str:
    now = int(time.time())
    ttl = _TOKEN_TTL_SEC if ttl_sec is None else ttl_sec
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "practice_id": user.practice_id,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


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
    if not user_id or not practice_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
        )
    user = user_store.get(str(user_id))
    if user is None:
        return User(user_id=str(user_id), username=str(username), practice_id=str(practice_id))
    return user


def get_current_user(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_security)],
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    return decode_token(creds.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]
