"""APScheduler wiring — persistent jobstore, replacing the old asyncio-loop
triggers (a per-trigger `asyncio.create_task` + `sleep(interval)`, lost on
every restart).

Gotcha worth flagging: APScheduler 3.x's `SQLAlchemyJobStore` is *sync*
SQLAlchemy internally (it predates SQLAlchemy's async support) — it cannot
take our async `DATABASE_URL` (`+asyncpg`/`+aiosqlite`) directly. We derive a
plain sync URL for the jobstore only; the app's own engine (`backend/db.py`)
stays fully async.
"""
from __future__ import annotations

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from backend import state
from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import LlmConfig, Schedule
from backend.services import agent_runner
from backend.services.audit import record_audit

scheduler = AsyncIOScheduler()


def _sync_db_url(async_url: str) -> str:
    """asyncpg/aiosqlite URL -> the sync-driver equivalent APScheduler needs."""
    if async_url.startswith("postgresql+asyncpg://"):
        return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if async_url.startswith("sqlite+aiosqlite://"):
        return async_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return async_url  # already sync, or a driver we don't need to special-case


def configure_scheduler() -> None:
    settings = get_settings()
    scheduler.add_jobstore(SQLAlchemyJobStore(url=_sync_db_url(settings.database_url)), "default")


def _job_trigger(schedule: Schedule):
    if schedule.cron_expression:
        return CronTrigger.from_crontab(schedule.cron_expression)
    return IntervalTrigger(seconds=schedule.interval_seconds or 60)


async def run_scheduled_agent(schedule_id: int) -> None:
    """The APScheduler job body. Reloads the schedule + LLM config fresh from
    the DB every fire (never trusts stale closure state), runs the same
    `agent_runner.run_once` path chat/API use, and records the outcome."""
    if state.mcp_manager is None:
        return  # app not fully started (shouldn't happen once jobstore is loaded post-lifespan)
    async with SessionLocal() as db:
        schedule = await db.get(Schedule, schedule_id)
        if schedule is None or not schedule.active:
            return
        cfg = await db.get(LlmConfig, schedule.llm_id) if schedule.llm_id else None
        if cfg is None:
            result = await db.execute(select(LlmConfig).where(LlmConfig.is_default.is_(True)).limit(1))
            cfg = result.scalar_one_or_none()
            if cfg is None:
                return
        rec = await agent_runner.run_once(
            state.mcp_manager, db, cfg, schedule.model, schedule.question,
            source="schedule", schedule_id=schedule.id,
        )
        await record_audit(db, schedule.created_by_id, "schedule.fire", "schedule", schedule.id,
                            {"run_id": rec.id, "error": rec.error})


def schedule_job(schedule: Schedule) -> None:
    scheduler.add_job(
        run_scheduled_agent, trigger=_job_trigger(schedule), id=str(schedule.id),
        args=[schedule.id], replace_existing=True, misfire_grace_time=60,
    )


def unschedule_job(schedule_id: int) -> None:
    if scheduler.get_job(str(schedule_id)) is not None:
        scheduler.remove_job(str(schedule_id))


def pause_job(schedule_id: int) -> None:
    if scheduler.get_job(str(schedule_id)) is not None:
        scheduler.pause_job(str(schedule_id))


def resume_job(schedule_id: int) -> None:
    if scheduler.get_job(str(schedule_id)) is not None:
        scheduler.resume_job(str(schedule_id))
