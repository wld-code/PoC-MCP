"""ASAP API — orchestrates connected-vehicle service activation.

ASAP is the control plane: it owns the **desired** state of every service per
vehicle and reconciles it to the **actual** state by dispatching campaigns to
**Redbend** (the OTA execution layer). The MCP server (asap_mcp) wraps it; the
agent never calls it directly.

Run:  uvicorn main:app --host 0.0.0.0 --port 8021
Docs: http://localhost:8021/docs
Needs: REDBEND_BASE_URL pointing at the Redbend API.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

import store
from models import Operation, OperationRequest, Service, ServiceState

app = FastAPI(
    title="ASAP API",
    description=(
        "Activation Service & Aggregation Platform — orchestrates service "
        "activation by tracking desired vs actual state and reconciling through "
        "Redbend (OTA execution)."
    ),
    version="2.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/services", response_model=list[Service])
def list_services(
    category: str | None = Query(default=None, description="Filter by category"),
) -> list[Service]:
    """The catalogue of services that can be activated on a vehicle."""
    return [Service(**s) for s in store.list_services(category)]


@app.get("/vehicles/{vin}/service-states", response_model=list[ServiceState])
def service_states(vin: str) -> list[ServiceState]:
    """Desired vs actual state of every service on a vehicle (and whether in sync)."""
    return [ServiceState(**s) for s in store.service_states(vin)]


@app.get("/operations", response_model=list[Operation])
def list_operations(limit: int = Query(default=10, ge=1, le=100)) -> list[Operation]:
    """The most recent reconciliation operations."""
    return [Operation(**o) for o in store.list_operations(limit)]


@app.get("/operations/{operation_id}", response_model=Operation)
def get_operation(operation_id: str) -> Operation:
    """Status and step-by-step trace of one orchestration operation."""
    op = store.get_operation(operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    return Operation(**op)


@app.post("/operations", response_model=Operation)
def create_operation(req: OperationRequest) -> Operation:
    """Reconcile a service to a new desired state (activate or deactivate).

    Sets the desired state, dispatches a Redbend campaign to apply it, and
    updates the actual state from the campaign result.
    """
    if req.action not in {"activate", "deactivate"}:
        raise HTTPException(status_code=400, detail="action must be 'activate' or 'deactivate'")
    if req.service_code not in store.SERVICE_BY_CODE:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown service_code '{req.service_code}'. "
                   f"Known codes: {', '.join(store.SERVICE_BY_CODE)}",
        )
    return Operation(**store.create_operation(req.vin, req.service_code, req.action))
