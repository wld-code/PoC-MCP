"""CVC API — the car gateway. Reports live information about each vehicle.

Second of the two fake backends. The MCP server (cvc_mcp) wraps it; the agent
reads from it to understand the *state* of a vehicle (online, charge, location,
diagnostics) before deciding whether to act on it through ASAP.

Run:  uvicorn main:app --host 0.0.0.0 --port 8022
Docs: http://localhost:8022/docs
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

import store
from models import Diagnostics, Location, Vehicle, VehicleState

app = FastAPI(
    title="CVC API",
    description=(
        "Connected Vehicle Cloud — the car gateway. Exposes live telemetry, "
        "diagnostics and location for each connected vehicle."
    ),
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/vehicles", response_model=list[Vehicle])
def list_vehicles() -> list[Vehicle]:
    """The connected fleet: VIN, make, model, year, owner, powertrain."""
    return [Vehicle(**v) for v in store.list_vehicles()]


@app.get("/vehicles/{vin}", response_model=VehicleState)
def get_vehicle(vin: str) -> VehicleState:
    """Full live telemetry snapshot for one vehicle."""
    state = store.get_vehicle(vin)
    if state is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return VehicleState(**state)


@app.get("/vehicles/{vin}/location", response_model=Location)
def get_location(vin: str) -> Location:
    """Current GPS position of the vehicle."""
    loc = store.get_location(vin)
    if loc is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return Location(**loc)


@app.get("/vehicles/{vin}/diagnostics", response_model=Diagnostics)
def get_diagnostics(vin: str) -> Diagnostics:
    """Health score, diagnostic trouble codes and service status."""
    diag = store.get_diagnostics(vin)
    if diag is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return Diagnostics(**diag)
