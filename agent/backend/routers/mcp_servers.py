"""MCP server registry — connects live via `DynamicMCPManager` (app.state.mcp)
AND persists the URL so it survives a restart. Admin only: an MCP server URL
is effectively an outbound network target the backend will connect to (SSRF
surface), same risk web.py already had — restricting who can add one is the
main new safeguard here."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import McpServerConfig
from backend.schemas import McpIn, McpUpdateIn
from backend.security import require_role
from backend.services.audit import record_audit
from backend.services.util import slugify, unique_slug

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp-servers"],
                    dependencies=[Depends(require_role("admin"))])


@router.get("")
async def list_servers(request: Request) -> dict:
    return {"servers": request.app.state.mcp.servers()}


@router.post("")
async def add_server(body: McpIn, request: Request, db: AsyncSession = Depends(get_db), user=Depends(require_role("admin"))) -> dict:
    if not body.url.strip():
        raise HTTPException(400, "url is required")
    slug = slugify(body.id) if body.id else await unique_slug(db, McpServerConfig, body.url)
    mcp = request.app.state.mcp
    try:
        view = await mcp.add(body.url.strip(), slug)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(McpServerConfig(slug=slug, url=body.url.strip(), created_by_id=user.id))
    await db.commit()
    await record_audit(db, user.id, "mcp_server.create", "mcp_server", slug, {"url": body.url.strip()})
    return view


@router.put("/{sid}")
async def update_server(sid: str, body: McpUpdateIn, request: Request, db: AsyncSession = Depends(get_db),
                         user=Depends(require_role("admin"))) -> dict:
    mcp = request.app.state.mcp
    try:
        view = await mcp.update(sid, body.url.strip())
    except KeyError:
        raise HTTPException(404, "server not found")
    result = await db.execute(select(McpServerConfig).where(McpServerConfig.slug == sid))
    row = result.scalar_one_or_none()
    if row is not None:
        row.url = body.url.strip()
        await db.commit()
    await record_audit(db, user.id, "mcp_server.update", "mcp_server", sid, {"url": body.url.strip()})
    return view


@router.delete("/{sid}")
async def delete_server(sid: str, request: Request, db: AsyncSession = Depends(get_db),
                         user=Depends(require_role("admin"))) -> dict:
    mcp = request.app.state.mcp
    try:
        await mcp.remove(sid)
    except KeyError:
        raise HTTPException(404, "server not found")
    result = await db.execute(select(McpServerConfig).where(McpServerConfig.slug == sid))
    row = result.scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    await record_audit(db, user.id, "mcp_server.delete", "mcp_server", sid)
    return {"deleted": sid}
