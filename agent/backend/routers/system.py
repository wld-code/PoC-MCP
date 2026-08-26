"""Agent runtime introspection: connected MCP servers, merged toolbox, and a
direct one-tool-call endpoint (used by the Deep Dive flow runner — no LLM
involved, deterministic step-by-step execution)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import LlmConfig
from backend.schemas import LlmOut, ToolIn
from backend.security import require_role
from backend.services.agent_runner import run_lock

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/info")
async def info(request: Request, db: AsyncSession = Depends(get_db), _=Depends(require_role("viewer"))) -> dict:
    mcp = request.app.state.mcp
    result = await db.execute(select(LlmConfig))
    llms = list(result.scalars())
    default = next((c for c in llms if c.is_default), None)
    return {
        "default_llm": default.slug if default else None,
        "llms": [
            LlmOut(id=c.slug, name=c.name, kind=c.kind, model=c.model, base_url=c.base_url,
                   has_key=bool(c.encrypted_api_key), is_default=c.is_default, needs_key=c.kind != "mock")
            for c in llms
        ],
        "servers": mcp.servers(),
        "tool_count": len(await mcp.list_tools()),
    }


@router.get("/tools")
async def tools(request: Request, _=Depends(require_role("viewer"))) -> dict:
    return {"tools": [t["name"] for t in await request.app.state.mcp.list_tools()]}


@router.post("/tool")
async def call_tool(body: ToolIn, request: Request, _=Depends(require_role("operator"))) -> dict:
    mcp = request.app.state.mcp
    async with run_lock:
        try:
            result = await mcp.call_tool(body.name, body.arguments or {})
            ok = not str(result).startswith("(error")
        except Exception as exc:  # noqa: BLE001
            result, ok = str(exc), False
    return {"name": body.name, "arguments": body.arguments or {}, "result": result, "ok": ok}
