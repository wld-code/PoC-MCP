"""User management — admin only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import ROLES, User
from backend.schemas import UserCreateIn, UserOut, UserUpdateIn
from backend.security import get_current_user, hash_password, require_role
from backend.services.audit import record_audit

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateIn, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_user),
) -> User:
    if body.role not in ROLES:
        raise HTTPException(400, f"role must be one of {ROLES}")
    email = body.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(409, f"a user with email '{email}' already exists")
    user = User(email=email, hashed_password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await record_audit(db, admin.id, "user.create", "user", user.id, {"email": email, "role": body.role})
    return user


@router.put("/{uid}", response_model=UserOut)
async def update_user(
    uid: int, body: UserUpdateIn, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_user),
) -> User:
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404, "user not found")
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        if user.id == admin.id and body.role != "admin":
            raise HTTPException(400, "you cannot demote your own account")
        user.role = body.role
    if body.is_active is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(400, "you cannot deactivate your own account")
        user.is_active = body.is_active
    if body.password:
        user.hashed_password = hash_password(body.password)
    await db.commit()
    await db.refresh(user)
    await record_audit(db, admin.id, "user.update", "user", user.id, body.model_dump(exclude={"password"}))
    return user


@router.delete("/{uid}")
async def delete_user(uid: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_user)) -> dict:
    if uid == admin.id:
        raise HTTPException(400, "you cannot delete your own account")
    user = await db.get(User, uid)
    if user is None:
        raise HTTPException(404, "user not found")
    await db.delete(user)
    await db.commit()
    await record_audit(db, admin.id, "user.delete", "user", uid)
    return {"deleted": uid}
