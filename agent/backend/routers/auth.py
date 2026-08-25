"""Login / refresh / me / logout.

Access token: returned in the JSON body, kept in memory by the SPA (never
localStorage — reduces what an XSS bug can steal).
Refresh token: httpOnly + Secure + SameSite=Lax cookie, invisible to JS.
Rotated on every refresh (a new refresh token is issued and the old one is
implicitly superseded — see note on revocation below).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import get_db
from backend.models import User
from backend.schemas import TokenOut, UserOut
from backend.security import (
    REFRESH_COOKIE_NAME,
    create_access_token,
    create_refresh_token,
    get_current_user,
    get_refresh_user,
    get_user_by_email,
    verify_password,
)
from backend.services.audit import record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)
settings = get_settings()


def _set_refresh_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME, create_refresh_token(user),
        httponly=True, secure=settings.cookie_secure, samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 3600, path="/api/auth",
    )


@router.post("/login", response_model=TokenOut)
@limiter.limit("5/minute")   # blunt brute-force against the password endpoint
async def login(
    request: Request,
    response: Response,
    # `from __future__ import annotations` turns the shorthand `Depends()`
    # (which relies on the parameter's own annotation) into an unresolved
    # ForwardRef at import time — pass the callable explicitly instead.
    form: OAuth2PasswordRequestForm = Depends(OAuth2PasswordRequestForm),
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    user = await get_user_by_email(db, form.username.strip().lower())
    if user is None or not user.is_active or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    _set_refresh_cookie(response, user)
    await record_audit(db, user.id, "login", "user", user.id)
    return TokenOut(access_token=create_access_token(user))


@router.post("/refresh", response_model=TokenOut)
async def refresh(response: Response, user: User = Depends(get_refresh_user)) -> TokenOut:
    _set_refresh_cookie(response, user)   # rotate
    return TokenOut(access_token=create_access_token(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")
    return {"ok": True}
