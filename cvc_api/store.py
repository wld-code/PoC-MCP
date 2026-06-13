"""In-memory store for the CVC car-gateway API — seeded random telemetry.

Like ASAP, there is no database: telemetry is generated once at import time
with a fixed RNG seed, so every vehicle reports the same reproducible snapshot
across boots (good for a tutorial). The VIN list is identical to ASAP's so the
two services describe the same fleet from two angles: ASAP knows what services a
car is entitled to, CVC knows what the car is actually doing right now.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

# Same VINs as the ASAP service — extended here with the descriptive metadata a
# car gateway would expose (make / model / year).
FLEET = [
    {"vin": "VR7CONNECT00001", "make": "Peugeot", "model": "e-3008", "year": 2025,
     "owner": "Walid Abdaoui",   "powertrain": "electric"},
    {"vin": "VR7CONNECT00002", "make": "Citroën", "model": "ë-C4",   "year": 2024,
     "owner": "Camille Laurent", "powertrain": "electric"},
    {"vin": "VR7CONNECT00003", "make": "DS",      "model": "DS 7",   "year": 2023,
     "owner": "Mehdi Benali",    "powertrain": "hybrid"},
    {"vin": "VR7CONNECT00004", "make": "Opel",    "model": "Astra",  "year": 2024,
     "owner": "Sofia Rossi",     "powertrain": "gasoline"},
    {"vin": "VR7CONNECT00005", "make": "Jeep",    "model": "Avenger","year": 2025,
     "owner": "Liam Dupont",     "powertrain": "electric"},
]

CITIES = [
    ("Paris", 48.8566, 2.3522),
    ("Lyon", 45.7640, 4.8357),
    ("Marseille", 43.2965, 5.3698),
    ("Lille", 50.6292, 3.0573),
    ("Bordeaux", 44.8378, -0.5792),
]
DTC_LIBRARY = [
    ("P0420", "Catalyst system efficiency below threshold"),
    ("P0300", "Random/multiple cylinder misfire detected"),
    ("B1318", "Battery voltage low"),
    ("C1234", "Wheel speed sensor fault"),
    ("U0100", "Lost communication with ECM/PCM"),
]


def _state_for(vehicle: dict, rng: random.Random, online: bool) -> dict:
    """Generate a reproducible telemetry + diagnostics snapshot for one car."""
    city, base_lat, base_lon = rng.choice(CITIES)
    energy_type = "battery" if vehicle["powertrain"] in ("electric", "hybrid") else "fuel"
    energy_level = rng.randint(15, 100)
    # EV range scales with charge; combustion range is roomier.
    range_km = int(energy_level * (3.8 if energy_type == "battery" else 6.5))

    n_dtc = rng.choices([0, 0, 0, 1, 2], k=1)[0]
    dtcs = rng.sample(DTC_LIBRARY, n_dtc)
    health = 100 - n_dtc * rng.randint(8, 20)

    odometer = rng.randint(5_000, 120_000)
    last_service = max(0, odometer - rng.randint(2_000, 15_000))

    state = {
        "vin": vehicle["vin"],
        "make": vehicle["make"],
        "model": vehicle["model"],
        "online": online,
        "engine_on": online and rng.random() > 0.6,
        "doors_locked": rng.random() > 0.3,
        "odometer_km": odometer,
        "energy_type": energy_type,
        "energy_level_pct": energy_level,
        "range_km": range_km,
        "tire_pressures_bar": {
            "front_left": round(rng.uniform(2.1, 2.6), 1),
            "front_right": round(rng.uniform(2.1, 2.6), 1),
            "rear_left": round(rng.uniform(2.1, 2.6), 1),
            "rear_right": round(rng.uniform(2.1, 2.6), 1),
        },
        "software_version": f"{rng.randint(2, 4)}.{rng.randint(0, 9)}.{rng.randint(0, 20)}",
        "signal_strength_pct": rng.randint(35, 100) if online else 0,
    }
    location = {
        "vin": vehicle["vin"],
        "latitude": round(base_lat + rng.uniform(-0.05, 0.05), 4),
        "longitude": round(base_lon + rng.uniform(-0.05, 0.05), 4),
        "heading_deg": rng.randint(0, 359),
        "city": city,
    }
    diagnostics = {
        "vin": vehicle["vin"],
        "health_score": max(0, min(100, health)),
        "dtc_codes": [c for c, _ in dtcs],
        "warnings": [d for _, d in dtcs],
        "last_service_km": last_service,
        "next_service_due_km": last_service + 30_000,
    }
    return {"state": state, "location": location, "diagnostics": diagnostics}


# Build every snapshot once, deterministically. Online status is fixed (not
# random) so the tutorial has a stable narrative: every car is reachable except
# the Opel Astra (index 3), which we keep OFFLINE on purpose. That gives the
# agent a real reason to read CVC before acting through ASAP — you cannot push a
# command to a car that is not connected.
_OFFLINE_INDEX = 3
_rng = random.Random(42)
_SNAPSHOTS: dict[str, dict] = {
    v["vin"]: _state_for(v, _rng, online=(i != _OFFLINE_INDEX))
    for i, v in enumerate(FLEET)
}
_VEHICLE_BY_VIN = {v["vin"]: v for v in FLEET}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_vehicles() -> list[dict]:
    return list(FLEET)


def get_vehicle(vin: str) -> dict | None:
    snap = _SNAPSHOTS.get(vin)
    if snap is None:
        return None
    state = dict(snap["state"])
    state["location"] = dict(snap["location"], updated_at=_now_iso())
    state["updated_at"] = _now_iso()
    return state


def get_location(vin: str) -> dict | None:
    snap = _SNAPSHOTS.get(vin)
    if snap is None:
        return None
    return dict(snap["location"], updated_at=_now_iso())


def get_diagnostics(vin: str) -> dict | None:
    snap = _SNAPSHOTS.get(vin)
    if snap is None:
        return None
    return dict(snap["diagnostics"], updated_at=_now_iso())
