"""In-memory store for the ASAP API — desired/actual service state + reconcile.

ASAP owns, per vehicle and per service, a **desired** state and an **actual**
state. Activating a service means: set desired = ACTIVE, then reconcile by asking
**Redbend** (the OTA platform) to push the activation; the actual state only
flips once Redbend reports the campaign reached the car. If the campaign fails,
desired and actual diverge — a drift the agent can detect and explain.

No database: state lives in memory, seeded reproducibly. The VIN list matches CVC
and Redbend so the three services describe the same fleet.
"""
from __future__ import annotations

import itertools
import os
import random
from datetime import datetime, timezone

import httpx

REDBEND_BASE_URL = os.environ.get("REDBEND_BASE_URL", "http://localhost:8023")

FLEET = [
    {"vin": "VR7CONNECT00001", "owner": "Walid Abdaoui",   "powertrain": "electric"},
    {"vin": "VR7CONNECT00002", "owner": "Camille Laurent", "powertrain": "electric"},
    {"vin": "VR7CONNECT00003", "owner": "Mehdi Benali",    "powertrain": "hybrid"},
    {"vin": "VR7CONNECT00004", "owner": "Sofia Rossi",     "powertrain": "gasoline"},
    {"vin": "VR7CONNECT00005", "owner": "Liam Dupont",     "powertrain": "electric"},
]
POWERTRAIN_BY_VIN = {v["vin"]: v["powertrain"] for v in FLEET}

SERVICES = [
    {"code": "REMOTE_CLIMATE", "name": "Remote Climate Control", "category": "Comfort",
     "description": "Pre-heat or cool the cabin from the mobile app.",
     "monthly_price": 4.99, "requires_hardware": False, "electric_only": False},
    {"code": "SVT", "name": "Stolen Vehicle Tracking", "category": "Security",
     "description": "Locate and immobilise the vehicle if it is reported stolen.",
     "monthly_price": 9.99, "requires_hardware": True, "electric_only": False},
    {"code": "ECALL_PLUS", "name": "eCall Plus Emergency", "category": "Safety",
     "description": "Automatic emergency call with live location after a crash.",
     "monthly_price": 0.0, "requires_hardware": True, "electric_only": False},
    {"code": "LIVE_NAV", "name": "Live Navigation & Traffic", "category": "Navigation",
     "description": "Real-time traffic, hazards and connected destinations.",
     "monthly_price": 7.50, "requires_hardware": False, "electric_only": False},
    {"code": "WIFI_HOTSPOT", "name": "In-Car Wi-Fi Hotspot", "category": "Connectivity",
     "description": "Turn the car into a 4G/5G Wi-Fi access point.",
     "monthly_price": 14.99, "requires_hardware": True, "electric_only": False},
    {"code": "OTA_UPDATES", "name": "Over-the-Air Updates", "category": "Software",
     "description": "Download and install vehicle software updates remotely.",
     "monthly_price": 0.0, "requires_hardware": False, "electric_only": False},
    {"code": "REMOTE_UNLOCK", "name": "Remote Door Lock / Unlock", "category": "Comfort",
     "description": "Lock or unlock the doors from the app.",
     "monthly_price": 2.99, "requires_hardware": True, "electric_only": False},
    {"code": "CHARGE_SCHED", "name": "Smart Charging Scheduler", "category": "Energy",
     "description": "Schedule charging for off-peak tariffs and pre-conditioning.",
     "monthly_price": 3.99, "requires_hardware": False, "electric_only": True},
]
SERVICE_BY_CODE = {s["code"]: s for s in SERVICES}

ACTIVE, INACTIVE = "ACTIVE", "INACTIVE"

# --- seeded desired/actual state -------------------------------------------- #
# Each service has {desired, actual, last_operation_id, last_campaign_id}.
random.seed(42)
STATES: dict[str, dict[str, dict]] = {}
for _v in FLEET:
    bucket: dict[str, dict] = {}
    eligible = [s["code"] for s in SERVICES
                if not (s["electric_only"] and _v["powertrain"] == "gasoline")]
    active = set(random.sample(eligible, random.randint(2, 4)))
    for s in SERVICES:
        on = s["code"] in active
        bucket[s["code"]] = {
            "desired": ACTIVE if on else INACTIVE,
            "actual": ACTIVE if on else INACTIVE,
            "last_operation_id": None,
            "last_campaign_id": None,
        }
    STATES[_v["vin"]] = bucket

# Hero vehicle: keep REMOTE_CLIMATE fully INACTIVE so the canonical demo runs the
# whole reconcile pipeline (set desired -> dispatch to Redbend -> actual ACTIVE).
STATES["VR7CONNECT00001"]["REMOTE_CLIMATE"].update(desired=INACTIVE, actual=INACTIVE)

# Seeded DRIFT: Camille's Wi-Fi hotspot was requested (desired ACTIVE) but the
# Redbend campaign failed, so it never actually turned on (actual INACTIVE).
# Matches the pre-seeded failed campaign cmp-000000 in the Redbend service.
STATES["VR7CONNECT00002"]["WIFI_HOTSPOT"].update(
    desired=ACTIVE, actual=INACTIVE, last_campaign_id="cmp-000000")

_OP_COUNTER = itertools.count(1)
OPERATIONS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_services(category: str | None = None) -> list[dict]:
    if category:
        cat = category.lower()
        return [s for s in SERVICES if s["category"].lower() == cat]
    return list(SERVICES)


def service_states(vin: str) -> list[dict]:
    """Desired vs actual state for every service on a vehicle."""
    out: list[dict] = []
    bucket = STATES.get(vin, {})
    for s in SERVICES:
        st = bucket.get(s["code"], {"desired": INACTIVE, "actual": INACTIVE,
                                    "last_operation_id": None, "last_campaign_id": None})
        out.append({
            "vin": vin,
            "service_code": s["code"],
            "service_name": s["name"],
            "category": s["category"],
            "desired_state": st["desired"],
            "actual_state": st["actual"],
            "in_sync": st["desired"] == st["actual"],
            "last_operation_id": st["last_operation_id"],
            "last_campaign_id": st["last_campaign_id"],
        })
    return out


def list_operations(limit: int = 10) -> list[dict]:
    ops = sorted(OPERATIONS.values(), key=lambda o: o["created_at"], reverse=True)
    return ops[:limit]


def get_operation(operation_id: str) -> dict | None:
    return OPERATIONS.get(operation_id)


def _step(name: str, status: str, detail: str, when: datetime) -> dict:
    return {"name": name, "status": status, "detail": detail, "at": when.isoformat()}


def _dispatch_to_redbend(vin: str, service_code: str, action: str) -> dict:
    """Ask Redbend to push the activation/deactivation. Returns the campaign."""
    ctype = "SERVICE_ACTIVATION" if action == "activate" else "SERVICE_DEACTIVATION"
    with httpx.Client(base_url=REDBEND_BASE_URL, timeout=10.0) as client:
        resp = client.post("/campaigns", json={"vin": vin, "type": ctype, "target": service_code})
        resp.raise_for_status()
        return resp.json()


def create_operation(vin: str, service_code: str, action: str) -> dict:
    """Reconcile one service to its desired state via a Redbend campaign.

    Pipeline: eligibility_check -> set_desired_state -> dispatch_to_redbend
    (Redbend runs the OTA campaign) -> reconcile_state (actual := result).
    """
    op_id = f"op-{next(_OP_COUNTER):06d}"
    start = _now()
    service = SERVICE_BY_CODE[service_code]
    target = ACTIVE if action == "activate" else INACTIVE
    steps: list[dict] = []
    campaign_id: str | None = None

    def stamp(i: int) -> datetime:
        from datetime import timedelta
        return start + timedelta(seconds=i)

    # 1) Eligibility.
    powertrain = POWERTRAIN_BY_VIN.get(vin)
    bucket = STATES.get(vin)
    if bucket is None:
        steps.append(_step("eligibility_check", "FAILED",
                           f"VIN {vin} is not a known connected vehicle.", stamp(1)))
        return _finish(op_id, vin, service, action, target, INACTIVE, "FAILED", None, steps, start,
                       f"Unknown vehicle {vin}.")
    if service["electric_only"] and powertrain == "gasoline":
        steps.append(_step("eligibility_check", "FAILED",
                           f"{service['name']} requires an electric powertrain; {vin} is {powertrain}.",
                           stamp(1)))
        return _finish(op_id, vin, service, action, target, bucket[service_code]["actual"],
                       "FAILED", None, steps, start,
                       f"Could not {action} {service['name']} on {vin}: not eligible.")
    steps.append(_step("eligibility_check", "SUCCEEDED",
                       f"{service['name']} is eligible for {vin}.", stamp(1)))

    state = bucket[service_code]
    # 2) Set desired state.
    state["desired"] = target
    steps.append(_step("set_desired_state", "SUCCEEDED",
                       f"Desired state of {service['code']} set to {target}.", stamp(2)))

    # Already reconciled? Nothing to push.
    if state["actual"] == target:
        steps.append(_step("reconcile_state", "SKIPPED",
                           f"Actual state already {target} — no campaign needed.", stamp(3)))
        state["last_operation_id"] = op_id
        return _finish(op_id, vin, service, action, target, state["actual"], "SUCCEEDED",
                       state["last_campaign_id"], steps, start,
                       f"{service['name']} already {target} on {vin}; nothing to do.")

    # 3) Dispatch to Redbend (the OTA execution layer).
    try:
        campaign = _dispatch_to_redbend(vin, service_code, action)
        campaign_id = campaign["campaign_id"]
        steps.append(_step("dispatch_to_redbend", "SUCCEEDED",
                           f"Redbend campaign {campaign_id} ({campaign['type']}) "
                           f"finished with status {campaign['status']}.", stamp(3)))
    except Exception as exc:  # noqa: BLE001 — Redbend unreachable / errored
        steps.append(_step("dispatch_to_redbend", "FAILED",
                           f"Could not dispatch to Redbend: {exc}", stamp(3)))
        state["last_operation_id"] = op_id
        return _finish(op_id, vin, service, action, target, state["actual"], "FAILED",
                       None, steps, start,
                       f"{service['name']} desired={target} on {vin}, but the Redbend "
                       f"campaign could not be dispatched (drift).")

    # 4) Reconcile actual state from the campaign outcome.
    if campaign["status"] in ("ACTIVATED", "COMPLETED"):
        state["actual"] = target
        steps.append(_step("reconcile_state", "SUCCEEDED",
                           f"Vehicle confirmed; actual state is now {target}.", stamp(4)))
        status, summary = "SUCCEEDED", (
            f"{service['name']} {action}d on {vin} via Redbend campaign {campaign_id}.")
    else:
        steps.append(_step("reconcile_state", "FAILED",
                           f"Redbend campaign {campaign_id} did not reach the vehicle; "
                           f"actual stays {state['actual']} (drift).", stamp(4)))
        status, summary = "FAILED", (
            f"{service['name']} desired={target} on {vin}, but campaign {campaign_id} failed (drift).")

    state["last_operation_id"] = op_id
    state["last_campaign_id"] = campaign_id
    return _finish(op_id, vin, service, action, target, state["actual"], status,
                   campaign_id, steps, start, summary)


def _finish(op_id, vin, service, action, desired, actual, status, campaign_id,
            steps, start, summary) -> dict:
    operation = {
        "operation_id": op_id, "vin": vin, "service_code": service["code"],
        "service_name": service["name"], "action": action, "desired_state": desired,
        "actual_state": actual, "status": status, "redbend_campaign_id": campaign_id,
        "created_at": start.isoformat(), "steps": steps, "summary": summary,
    }
    OPERATIONS[op_id] = operation
    return operation
