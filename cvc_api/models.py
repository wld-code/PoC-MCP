"""Pydantic response models for the CVC API.

CVC = *Connected Vehicle Cloud*, the car gateway. It is the read-side of the
tutorial: it reports what each vehicle currently is and does — telemetry,
diagnostics and GPS — but it does not change anything. An agent typically reads
from CVC to decide whether an action on ASAP is sensible (e.g. "is the car
online before I push a remote-climate command?").
"""
from __future__ import annotations

from pydantic import BaseModel


class Vehicle(BaseModel):
    vin: str
    make: str
    model: str
    year: int
    owner: str
    powertrain: str   # electric | hybrid | gasoline


class TirePressures(BaseModel):
    front_left: float
    front_right: float
    rear_left: float
    rear_right: float


class Location(BaseModel):
    vin: str
    latitude: float
    longitude: float
    heading_deg: int
    city: str
    updated_at: str


class VehicleState(BaseModel):
    vin: str
    make: str
    model: str
    online: bool                 # is the car reachable over the network right now
    engine_on: bool
    doors_locked: bool
    odometer_km: int
    energy_type: str             # battery | fuel
    energy_level_pct: int        # state of charge or fuel level, 0-100
    range_km: int
    tire_pressures_bar: TirePressures
    software_version: str
    signal_strength_pct: int     # cellular signal quality of the car gateway
    location: Location
    updated_at: str


class Diagnostics(BaseModel):
    vin: str
    health_score: int            # 0-100, higher is healthier
    dtc_codes: list[str]         # diagnostic trouble codes, e.g. "P0420"
    warnings: list[str]
    last_service_km: int
    next_service_due_km: int
    updated_at: str
