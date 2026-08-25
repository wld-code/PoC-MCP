"""Async SQLAlchemy engine/session — Postgres in compose/prod, SQLite in tests.

Both drivers are async (asyncpg / aiosqlite) so the same session dependency
works everywhere; only the DATABASE_URL differs.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_async_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_models() -> None:
    """Create tables directly (used for the SQLite test DB and local `python -m`
    runs). In docker-compose/prod, Alembic (`alembic upgrade head`) is the real
    migration path — this is a convenience fallback, not a replacement for it.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
