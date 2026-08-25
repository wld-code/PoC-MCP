from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import AuditLog, User
from backend.schemas import AuditOut
from backend.security import require_role

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_role("operator"))])


@router.get("", response_model=list[AuditOut])
async def list_audit(db: AsyncSession = Depends(get_db)) -> list[AuditOut]:
    result = await db.execute(
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
    )
    return [
        AuditOut(id=log.id, user_email=email, action=log.action, target_type=log.target_type,
                  target_id=log.target_id, detail=log.detail, created_at=log.created_at)
        for log, email in result.all()
    ]
