"""Deep Dive process flows — declarative, ordered tool-call sequences.
Reading/running is open to any authenticated role; editing the catalogue
(CRUD, reset-to-defaults) requires operator+."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models import ProcessFlow
from backend.schemas import FlowIn, FlowOut
from backend.security import require_role
from backend.services.audit import record_audit
from backend.services.util import unique_slug
from backend.seed_data import BUILTIN_FLOWS

router = APIRouter(prefix="/api/flows", tags=["flows"])


def _view(f: ProcessFlow) -> FlowOut:
    return FlowOut(id=f.slug, name=f.name, description=f.description, inputs=f.inputs,
                    steps=f.steps, is_builtin=f.is_builtin)


@router.get("")
async def list_flows(db: AsyncSession = Depends(get_db), _=Depends(require_role("viewer"))) -> dict:
    result = await db.execute(select(ProcessFlow).order_by(ProcessFlow.id))
    return {"flows": [_view(f) for f in result.scalars()]}


@router.post("", status_code=201)
async def create_flow(body: FlowIn, db: AsyncSession = Depends(get_db), user=Depends(require_role("operator"))) -> FlowOut:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    slug = await unique_slug(db, ProcessFlow, body.name)
    flow = ProcessFlow(slug=slug, name=body.name.strip(), description=body.description,
                        inputs=body.inputs, steps=[s.model_dump() for s in body.steps], is_builtin=False)
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    await record_audit(db, user.id, "flow.create", "flow", slug)
    return _view(flow)


@router.put("/{fid}")
async def update_flow(fid: str, body: FlowIn, db: AsyncSession = Depends(get_db),
                       user=Depends(require_role("operator"))) -> FlowOut:
    result = await db.execute(select(ProcessFlow).where(ProcessFlow.slug == fid))
    flow = result.scalar_one_or_none()
    if flow is None:
        raise HTTPException(404, "flow not found")
    flow.name, flow.description = body.name.strip(), body.description
    flow.inputs, flow.steps = body.inputs, [s.model_dump() for s in body.steps]
    await db.commit()
    await db.refresh(flow)
    await record_audit(db, user.id, "flow.update", "flow", fid)
    return _view(flow)


@router.delete("/{fid}")
async def delete_flow(fid: str, db: AsyncSession = Depends(get_db), user=Depends(require_role("operator"))) -> dict:
    result = await db.execute(select(ProcessFlow).where(ProcessFlow.slug == fid))
    flow = result.scalar_one_or_none()
    if flow is None:
        raise HTTPException(404, "flow not found")
    await db.delete(flow)
    await db.commit()
    await record_audit(db, user.id, "flow.delete", "flow", fid)
    return {"deleted": fid}


@router.post("/reset")
async def reset_flows(db: AsyncSession = Depends(get_db), user=Depends(require_role("operator"))) -> dict:
    await db.execute(ProcessFlow.__table__.delete())
    for data in BUILTIN_FLOWS:
        db.add(ProcessFlow(**data, is_builtin=True))
    await db.commit()
    await record_audit(db, user.id, "flow.reset", "flow", "*")
    result = await db.execute(select(ProcessFlow).order_by(ProcessFlow.id))
    return {"flows": [_view(f) for f in result.scalars()]}
