"""Redbend API — the OTA / service-activation execution layer.

Redbend is the platform that actually pushes software to the vehicle: firmware
(FOTA), software (SOTA), and service-enablement packages. ASAP (the orchestrator)
calls it to make a desired state real; the agent can also inspect campaigns and
the on-vehicle software inventory directly.

Run:  uvicorn main:app --host 0.0.0.0 --port 8023
Docs: http://localhost:8023/docs
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

import store
from models import Campaign, CampaignRequest, VehicleSoftware

app = FastAPI(
    title="Redbend API",
    description=(
        "Over-the-air update & service-activation execution platform "
        "(FOTA / SOTA / service enablement) for connected vehicles."
    ),
    version="1.0.0",
)

VALID_TYPES = {"FOTA", "SOTA", "SERVICE_ACTIVATION", "SERVICE_DEACTIVATION"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/vehicles/{vin}/software", response_model=VehicleSoftware)
def vehicle_software(vin: str) -> VehicleSoftware:
    """Installed firmware/software per module, plus any available OTA updates."""
    soft = store.get_software(vin)
    if soft is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return VehicleSoftware(**soft)


@app.get("/campaigns", response_model=list[Campaign])
def list_campaigns(limit: int = Query(default=10, ge=1, le=100)) -> list[Campaign]:
    """The most recent OTA / activation campaigns."""
    return [Campaign(**c) for c in store.list_campaigns(limit)]


@app.get("/campaigns/{campaign_id}", response_model=Campaign)
def get_campaign(campaign_id: str) -> Campaign:
    """Status and step-by-step trace of one campaign."""
    camp = store.get_campaign(campaign_id)
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return Campaign(**camp)


@app.post("/campaigns", response_model=Campaign)
def create_campaign(req: CampaignRequest) -> Campaign:
    """Launch an OTA / activation campaign and return the finished result."""
    ctype = req.type.upper()
    if ctype not in VALID_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"type must be one of {', '.join(sorted(VALID_TYPES))}")
    if req.vin not in store.list_vehicles():
        raise HTTPException(status_code=404, detail=f"Unknown VIN '{req.vin}'")
    return Campaign(**store.create_campaign(req.vin, ctype, req.target))
