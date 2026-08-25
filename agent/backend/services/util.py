from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import LlmConfig


def slugify(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or fallback


async def unique_slug(db: AsyncSession, model, base: str) -> str:
    """Append -2, -3, ... until `slug` is free on `model` (mirrors the
    de-duplication `web.py` did for flow ids)."""
    slug = slugify(base)
    candidate, n = slug, 2
    while (await db.execute(select(model).where(model.slug == candidate))).scalar_one_or_none() is not None:
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


async def resolve_llm(db: AsyncSession, slug_or_none: str | None) -> LlmConfig | None:
    """The LLM a request asked for by slug, or the registry default."""
    if slug_or_none:
        result = await db.execute(select(LlmConfig).where(LlmConfig.slug == slug_or_none))
        cfg = result.scalar_one_or_none()
        if cfg is not None:
            return cfg
    result = await db.execute(select(LlmConfig).where(LlmConfig.is_default.is_(True)).limit(1))
    return result.scalar_one_or_none()
