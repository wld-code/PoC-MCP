"""LLM provider registry. Reading the list is open to any authenticated role
(operators/viewers need it to pick a provider for chat/schedules — it never
exposes the raw key, only `has_key`); creating, editing, deleting, and
managing the default are admin-only since they touch provider credentials."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.crypto import encrypt_secret
from backend.db import get_db
from backend.models import LlmConfig
from backend.schemas import LlmIn, LlmOut, LlmUpdateIn
from backend.security import require_role
from backend.services.agent_runner import invalidate_sessions_for
from backend.services.audit import record_audit
from core.providers import LLM_KINDS

router = APIRouter(prefix="/api/llms", tags=["llms"])


def _view(cfg: LlmConfig) -> LlmOut:
    return LlmOut(id=cfg.slug, name=cfg.name, kind=cfg.kind, model=cfg.model, base_url=cfg.base_url,
                   has_key=bool(cfg.encrypted_api_key), is_default=cfg.is_default, needs_key=cfg.kind != "mock")


@router.get("")
async def list_llms(db: AsyncSession = Depends(get_db), _=Depends(require_role("viewer"))) -> dict:
    result = await db.execute(select(LlmConfig).order_by(LlmConfig.id))
    llms = list(result.scalars())
    default = next((c for c in llms if c.is_default), None)
    return {"llms": [_view(c) for c in llms], "default": default.slug if default else None, "kinds": LLM_KINDS}


@router.post("", status_code=201)
async def create_llm(body: LlmIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))) -> LlmOut:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    if body.kind not in LLM_KINDS:
        raise HTTPException(400, f"kind must be one of {LLM_KINDS}")
    slug = f"llm-{body.name.strip().lower().replace(' ', '-')}"
    n, base = 2, slug
    while (await db.execute(select(LlmConfig).where(LlmConfig.slug == slug))).scalar_one_or_none() is not None:
        slug, n = f"{base}-{n}", n + 1
    cfg = LlmConfig(slug=slug, name=body.name.strip(), kind=body.kind, model=body.model or "",
                     base_url=body.base_url or "",
                     encrypted_api_key=encrypt_secret(body.api_key) if body.api_key else None,
                     created_by_id=user.id)
    db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    await record_audit(db, user.id, "llm.create", "llm", cfg.slug, {"name": cfg.name, "kind": cfg.kind})
    return _view(cfg)


@router.put("/{lid}")
async def update_llm(lid: str, body: LlmUpdateIn, db: AsyncSession = Depends(get_db),
                      user=Depends(require_role("admin"))) -> LlmOut:
    result = await db.execute(select(LlmConfig).where(LlmConfig.slug == lid))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(404, f"unknown LLM '{lid}'")
    if body.kind is not None and body.kind not in LLM_KINDS:
        raise HTTPException(400, f"kind must be one of {LLM_KINDS}")
    for field in ("name", "kind", "model", "base_url"):
        val = getattr(body, field)
        if val is not None:
            setattr(cfg, field, val)
    if body.api_key:   # empty string / None = leave the stored key unchanged
        cfg.encrypted_api_key = encrypt_secret(body.api_key)
    await db.commit()
    await db.refresh(cfg)
    invalidate_sessions_for(cfg.slug)
    await record_audit(db, user.id, "llm.update", "llm", cfg.slug)
    return _view(cfg)


@router.put("/{lid}/default")
async def set_default_llm(lid: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))) -> dict:
    result = await db.execute(select(LlmConfig).where(LlmConfig.slug == lid))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(404, f"unknown LLM '{lid}'")
    all_llms = await db.execute(select(LlmConfig))
    for row in all_llms.scalars():
        row.is_default = row.id == cfg.id
    await db.commit()
    await record_audit(db, user.id, "llm.set_default", "llm", cfg.slug)
    return {"default": cfg.slug}


@router.delete("/{lid}")
async def delete_llm(lid: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))) -> dict:
    result = await db.execute(select(LlmConfig).where(LlmConfig.slug == lid))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        raise HTTPException(404, f"unknown LLM '{lid}'")
    count = (await db.execute(select(LlmConfig))).scalars().all()
    if len(count) <= 1:
        raise HTTPException(400, "cannot delete the last LLM")
    was_default = cfg.is_default
    await db.delete(cfg)
    await db.commit()
    if was_default:
        remaining = (await db.execute(select(LlmConfig).order_by(LlmConfig.id).limit(1))).scalar_one_or_none()
        if remaining is not None:
            remaining.is_default = True
            await db.commit()
    invalidate_sessions_for(lid)
    await record_audit(db, user.id, "llm.delete", "llm", lid)
    return {"deleted": lid}
