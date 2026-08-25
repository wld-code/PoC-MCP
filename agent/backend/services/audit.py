"""Tiny helper to write an audit_log row — called from every mutating route."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AuditLog


async def record_audit(
    db: AsyncSession,
    user_id: int | None,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict | None = None,
    commit: bool = True,
) -> None:
    db.add(AuditLog(user_id=user_id, action=action, target_type=target_type,
                     target_id=str(target_id), detail=detail or {}))
    if commit:
        await db.commit()
