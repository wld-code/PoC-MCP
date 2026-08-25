from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.schemas import ChatIn, ChatOut
from backend.security import require_role
from backend.services.agent_runner import chat_turn
from backend.services.util import resolve_llm

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, request: Request, db: AsyncSession = Depends(get_db),
                user=Depends(require_role("operator"))) -> ChatOut:
    if not body.message.strip():
        raise HTTPException(400, "message is required")
    cfg = await resolve_llm(db, body.provider)
    if cfg is None:
        raise HTTPException(400, "no LLM is configured — ask an admin to add one in AI Models")
    answer, tool_calls, error = await chat_turn(
        request.app.state.mcp, f"{user.id}:{body.session_id}", cfg, body.model, body.message.strip(),
    )
    return ChatOut(answer=answer, tool_calls=tool_calls, error=error, provider=cfg.slug,
                    model=body.model or cfg.model or "(default)")
