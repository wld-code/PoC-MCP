"""Persistent, APScheduler-backed automations — replaces the old
`POST /api/headless/triggers` asyncio-loop implementation. Survives restarts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import RunHistory, Schedule
from backend.schemas import RunOut, ScheduleIn, ScheduleOut
from backend.security import require_role
from backend.services import scheduler as sched_svc
from backend.services.audit import record_audit
from backend.services.util import resolve_llm

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: AsyncSession = Depends(get_db), _=Depends(require_role("viewer"))) -> list[Schedule]:
    result = await db.execute(select(Schedule).order_by(Schedule.created_at.desc()))
    return list(result.scalars())


@router.post("", response_model=ScheduleOut, status_code=201)
async def create_schedule(body: ScheduleIn, db: AsyncSession = Depends(get_db),
                           user=Depends(require_role("operator"))) -> Schedule:
    if not body.question.strip():
        raise HTTPException(400, "question is required")
    if bool(body.cron_expression) == bool(body.interval_seconds):
        raise HTTPException(400, "set exactly one of cron_expression or interval_seconds")
    if body.interval_seconds is not None and body.interval_seconds < 5:
        raise HTTPException(400, "interval_seconds must be at least 5")
    cfg = await resolve_llm(db, body.provider)
    if cfg is None:
        raise HTTPException(400, "no LLM is configured — ask an admin to add one in AI Models")

    schedule = Schedule(
        label=body.label or body.question[:60], question=body.question.strip(), llm_id=cfg.id,
        model=body.model or "", cron_expression=body.cron_expression,
        interval_seconds=body.interval_seconds, active=True, created_by_id=user.id,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    sched_svc.schedule_job(schedule)
    await record_audit(db, user.id, "schedule.create", "schedule", schedule.id,
                        {"question": schedule.question, "cron": schedule.cron_expression,
                         "interval": schedule.interval_seconds})
    return schedule


@router.post("/{sid}/pause", response_model=ScheduleOut)
async def pause_schedule(sid: int, db: AsyncSession = Depends(get_db),
                          user=Depends(require_role("operator"))) -> Schedule:
    schedule = await db.get(Schedule, sid)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    schedule.active = False
    await db.commit()
    await db.refresh(schedule)
    sched_svc.pause_job(sid)
    await record_audit(db, user.id, "schedule.pause", "schedule", sid)
    return schedule


@router.post("/{sid}/resume", response_model=ScheduleOut)
async def resume_schedule(sid: int, db: AsyncSession = Depends(get_db),
                           user=Depends(require_role("operator"))) -> Schedule:
    schedule = await db.get(Schedule, sid)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    schedule.active = True
    await db.commit()
    await db.refresh(schedule)
    sched_svc.resume_job(sid)
    await record_audit(db, user.id, "schedule.resume", "schedule", sid)
    return schedule


@router.delete("/{sid}")
async def delete_schedule(sid: int, db: AsyncSession = Depends(get_db),
                           user=Depends(require_role("operator"))) -> dict:
    schedule = await db.get(Schedule, sid)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    sched_svc.unschedule_job(sid)
    await db.delete(schedule)
    await db.commit()
    await record_audit(db, user.id, "schedule.delete", "schedule", sid)
    return {"deleted": sid}


@router.get("/{sid}/runs", response_model=list[RunOut])
async def schedule_runs(sid: int, db: AsyncSession = Depends(get_db),
                         _=Depends(require_role("viewer"))) -> list[RunHistory]:
    result = await db.execute(
        select(RunHistory).where(RunHistory.schedule_id == sid).order_by(RunHistory.created_at.desc()).limit(20)
    )
    return list(result.scalars())
