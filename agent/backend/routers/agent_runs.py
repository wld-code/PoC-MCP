"""Headless-style one-shot runs — the API equivalent of `python headless.py`,
for callers that want a single request/response instead of a CLI process
(dashboards, external automation, etc.)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import RunHistory
from backend.schemas import RunIn, RunOut
from backend.security import require_role
from backend.services.agent_runner import run_once
from backend.services.audit import record_audit
from backend.services.util import resolve_llm

router = APIRouter(prefix="/api/agents", tags=["agent-runs"])


@router.post("/run", response_model=RunOut)
async def run_agent(body: RunIn, request: Request, db: AsyncSession = Depends(get_db),
                     user=Depends(require_role("operator"))) -> RunHistory:
    if not body.question.strip():
        raise HTTPException(400, "question is required")
    cfg = await resolve_llm(db, body.provider)
    if cfg is None:
        raise HTTPException(400, "no LLM is configured — ask an admin to add one in AI Models")
    rec = await run_once(request.app.state.mcp, db, cfg, body.model, body.question.strip(),
                          source="api", user_id=user.id)
    await record_audit(db, user.id, "agent.run", "run_history", rec.id, {"error": rec.error})
    return rec


@router.get("/runs", response_model=list[RunOut])
async def list_runs(db: AsyncSession = Depends(get_db), _=Depends(require_role("viewer"))) -> list[RunHistory]:
    result = await db.execute(select(RunHistory).order_by(RunHistory.created_at.desc()).limit(50))
    return list(result.scalars())
