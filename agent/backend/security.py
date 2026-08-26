"""Password hashing, JWT issuance/verification, and RBAC dependencies.

- Passwords: Argon2id via `argon2-cffi` (actively maintained, no config
  footguns — the library's defaults are already OWASP-recommended).
- Tokens: PyJWT. Short-lived access token (Authorization: Bearer, kept in
  memory on the SPA — never localStorage) + longer-lived refresh token
  (httpOnly+Secure+SameSite cookie, never readable by JS). This pair
  minimizes XSS blast radius (a stolen access token expires in minutes; the
  refresh token can't be read by injected JS at all).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import get_db
from backend.models import ROLES, User

settings = get_settings()
_hasher = PasswordHasher()

REFRESH_COOKIE_NAME = "refresh_token"
ROLE_RANK = {role: i for i, role in enumerate(ROLES[::-1])}  # viewer=0, operator=1, admin=2

# Points bearer-token extraction at the login route for OpenAPI's sake; the
# actual login endpoint uses OAuth2PasswordRequestForm (form-encoded), not JSON.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


# --------------------------------------------------------------------------- #
# Passwords                                                                    #
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except Exception:  # noqa: BLE001 — malformed hash, treat as no match
        return False


# --------------------------------------------------------------------------- #
# JWT                                                                          #
# --------------------------------------------------------------------------- #
def _create_token(user_id: int, role: str, kind: Literal["access", "refresh"], expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "role": role, "type": kind, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user: User) -> str:
    return _create_token(user.id, user.role, "access", timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(user: User) -> str:
    return _create_token(user.id, user.role, "refresh", timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: Literal["access", "refresh"]) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid or expired token: {exc}") from exc
    if payload.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return payload


# --------------------------------------------------------------------------- #
# Dependencies                                                                 #
# --------------------------------------------------------------------------- #
async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                             headers={"WWW-Authenticate": "Bearer"})
    payload = decode_token(token, "access")
    user_id = int(payload["sub"])
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_role(min_role: str):
    """FastAPI dependency: 403s unless the current user's role >= min_role
    in the admin > operator > viewer hierarchy."""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK[user.role] < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                 f"Requires role '{min_role}' or higher (you are '{user.role}')")
        return user

    return _checker


async def get_refresh_user(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    payload = decode_token(refresh_token, "refresh")
    user = await db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()
