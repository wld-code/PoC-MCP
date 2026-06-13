"""Pydantic response models for the Redbend API.

Redbend is the **execution** layer of the demo: the over-the-air (OTA) platform
that actually pushes software to a vehicle. It runs **FOTA** (firmware OTA, e.g.
an ECU update), **SOTA** (software OTA, e.g. an app/middleware update) and
**service-activation** campaigns (pushing the enablement package that turns a
connected service on or off in the car).

ASAP (the orchestrator) decides *what* the desired state is and asks Redbend to
make it real; Redbend reports back whether the campaign reached the vehicle.
"""
from __future__ import annotations

from pydantic import BaseModel


class Module(BaseModel):
    name: str            # e.g. "TCU", "IVI", "ADAS"
    version: str


class AvailableUpdate(BaseModel):
    package_id: str
    type: str            # FOTA | SOTA
    target_module: str
    version: str
    description: str
    size_mb: float


class VehicleSoftware(BaseModel):
    vin: str
    platform_version: str
    modules: list[Module]
    available_updates: list[AvailableUpdate]
    updated_at: str


class CampaignStep(BaseModel):
    name: str
    status: str          # SUCCEEDED | FAILED | SKIPPED
    detail: str
    at: str


class Campaign(BaseModel):
    campaign_id: str
    vin: str
    type: str            # FOTA | SOTA | SERVICE_ACTIVATION | SERVICE_DEACTIVATION
    target: str          # package id or service code
    status: str          # COMPLETED | ACTIVATED | FAILED | IN_PROGRESS
    created_at: str
    steps: list[CampaignStep]
    summary: str


class CampaignRequest(BaseModel):
    vin: str
    type: str = "SERVICE_ACTIVATION"
    target: str          # package id (FOTA/SOTA) or service code (activation)
