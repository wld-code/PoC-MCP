"""Pydantic response models for the ASAP API.

ASAP = *Activation Service & Aggregation Platform*. Its job is to **orchestrate**
connected-vehicle service activation. It does NOT push anything to the car
itself — it owns the **desired state** of every service per vehicle, compares it
to the **actual state**, and reconciles the two by dispatching a campaign to
**Redbend** (the OTA execution layer). So ASAP is the control plane:

    desired state  ──reconcile──▶  Redbend campaign  ──▶  actual state

The key object is the ``ServiceState``: for each service on a vehicle it reports
the requested (desired) state, the current (actual) state, and whether they are
in sync. The ``Operation`` records one reconciliation, including the id of the
Redbend campaign it triggered.
"""
from __future__ import annotations

from pydantic import BaseModel


class Service(BaseModel):
    code: str
    name: str
    category: str
    description: str
    monthly_price: float
    requires_hardware: bool
    electric_only: bool


class ServiceState(BaseModel):
    vin: str
    service_code: str
    service_name: str
    category: str
    desired_state: str          # ACTIVE | INACTIVE  (what was requested)
    actual_state: str           # ACTIVE | INACTIVE  (what is really applied)
    in_sync: bool               # desired == actual
    last_operation_id: str | None = None
    last_campaign_id: str | None = None   # the Redbend campaign that last reconciled it


class OperationStep(BaseModel):
    name: str
    status: str                 # SUCCEEDED | FAILED | SKIPPED
    detail: str
    at: str


class Operation(BaseModel):
    operation_id: str
    vin: str
    service_code: str
    service_name: str
    action: str                 # activate | deactivate
    desired_state: str          # the state this operation requested
    actual_state: str           # the state after the operation
    status: str                 # SUCCEEDED | FAILED
    redbend_campaign_id: str | None
    created_at: str
    steps: list[OperationStep]
    summary: str


class OperationRequest(BaseModel):
    vin: str
    service_code: str
    action: str = "activate"    # activate | deactivate
