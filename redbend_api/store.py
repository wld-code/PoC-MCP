"""In-memory store for the Redbend API — seeded OTA campaigns + software state.

Like the other backends, everything lives in process memory with a fixed RNG
seed so the software inventory is reproducible. Campaigns are created at runtime
(by ASAP, or directly by the agent) and kept so they can be inspected later.

The VIN list matches CVC and ASAP, so the three services describe the same fleet
from three angles: CVC = what the car is doing, ASAP = what services it should
have, Redbend = what software/firmware is on it and which OTA jobs have run.
"""
from __future__ import annotations

import itertools
import random
from datetime import datetime, timedelta, timezone

FLEET = ["VR7CONNECT00001", "VR7CONNECT00002", "VR7CONNECT00003",
         "VR7CONNECT00004", "VR7CONNECT00005"]

MODULES = ["TCU", "IVI", "BCM", "ADAS", "Gateway"]

# Catalogue of OTA packages a campaign can install (FOTA = firmware, SOTA = software).
PACKAGES = {
    "FW_TCU_2025_06": {"type": "FOTA", "target_module": "TCU", "version": "5.2.1",
                       "description": "Telematics unit security & connectivity fixes", "size_mb": 48.5},
    "FW_ADAS_2025_05": {"type": "FOTA", "target_module": "ADAS", "version": "3.4.0",
                        "description": "Driver-assist perception model update", "size_mb": 120.0},
    "SW_IVI_2025_07": {"type": "SOTA", "target_module": "IVI", "version": "8.1.0",
                       "description": "Infotainment UI & navigation refresh", "size_mb": 310.0},
}

_rng = random.Random(7)
_SOFTWARE: dict[str, dict] = {}
for _vin in FLEET:
    _modules = [{"name": m, "version": f"{_rng.randint(2, 6)}.{_rng.randint(0, 9)}.{_rng.randint(0, 9)}"}
                for m in MODULES]
    # Give some vehicles a pending update available.
    _avail = []
    for _pid, _pkg in PACKAGES.items():
        if _rng.random() > 0.5:
            _avail.append({"package_id": _pid, **_pkg})
    _SOFTWARE[_vin] = {
        "vin": _vin,
        "platform_version": f"VP{_rng.randint(2, 4)}.{_rng.randint(0, 9)}",
        "modules": _modules,
        "available_updates": _avail,
    }

_COUNTER = itertools.count(1)
CAMPAIGNS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _step(name: str, status: str, detail: str, when: datetime) -> dict:
    return {"name": name, "status": status, "detail": detail, "at": when.isoformat()}


# Seed one pre-existing FAILED service-activation campaign. ASAP references it as
# the reason a service is "requested but not actually active" (a desired/actual
# drift the agent can explain).
def _seed_failed_campaign() -> None:
    start = _now() - timedelta(days=2)
    CAMPAIGNS["cmp-000000"] = {
        "campaign_id": "cmp-000000",
        "vin": "VR7CONNECT00002",
        "type": "SERVICE_ACTIVATION",
        "target": "WIFI_HOTSPOT",
        "status": "FAILED",
        "created_at": start.isoformat(),
        "steps": [
            _step("provision_package", "SUCCEEDED", "Wi-Fi hotspot package provisioned.", start),
            _step("download_to_vehicle", "FAILED",
                  "Vehicle lost connectivity before the package finished downloading.",
                  start + timedelta(seconds=2)),
        ],
        "summary": "Wi-Fi hotspot activation did not reach the vehicle (download interrupted).",
    }


_seed_failed_campaign()


def list_vehicles() -> list[str]:
    return list(FLEET)


def get_software(vin: str) -> dict | None:
    snap = _SOFTWARE.get(vin)
    if snap is None:
        return None
    return dict(snap, updated_at=_now().isoformat())


def list_campaigns(limit: int = 10) -> list[dict]:
    items = sorted(CAMPAIGNS.values(), key=lambda c: c["created_at"], reverse=True)
    return items[:limit]


def get_campaign(campaign_id: str) -> dict | None:
    return CAMPAIGNS.get(campaign_id)


def create_campaign(vin: str, ctype: str, target: str) -> dict:
    """Run a (simulated) OTA campaign and persist it.

    FOTA/SOTA campaigns install a package and bump the module version;
    service-activation campaigns push the enablement package. All succeed
    deterministically here — the interesting failure case is the seeded one
    above, which keeps the happy path reliable for the tutorial.
    """
    cid = f"cmp-{next(_COUNTER):06d}"
    start = _now()

    def stamp(i: int) -> datetime:
        return start + timedelta(seconds=i)

    ctype = ctype.upper()
    steps: list[dict] = []

    if ctype in ("FOTA", "SOTA"):
        pkg = PACKAGES.get(target)
        kind = "firmware" if ctype == "FOTA" else "software"
        steps.append(_step("download_package", "SUCCEEDED",
                           f"Downloaded {target} to the vehicle.", stamp(1)))
        steps.append(_step("verify_signature", "SUCCEEDED",
                           "Package signature verified.", stamp(2)))
        steps.append(_step("install", "SUCCEEDED",
                           f"Installed {kind} on {pkg['target_module'] if pkg else 'module'}.", stamp(3)))
        steps.append(_step("activate", "SUCCEEDED",
                           "Module restarted and running the new version.", stamp(4)))
        status = "COMPLETED"
        # Reflect the new version + drop it from available updates.
        if pkg and vin in _SOFTWARE:
            for mod in _SOFTWARE[vin]["modules"]:
                if mod["name"] == pkg["target_module"]:
                    mod["version"] = pkg["version"]
            _SOFTWARE[vin]["available_updates"] = [
                u for u in _SOFTWARE[vin]["available_updates"] if u["package_id"] != target
            ]
        summary = f"{ctype} campaign {target} completed on {vin}."
    else:  # SERVICE_ACTIVATION / SERVICE_DEACTIVATION
        verb = "activation" if ctype == "SERVICE_ACTIVATION" else "deactivation"
        steps.append(_step("provision_package", "SUCCEEDED",
                           f"Service {verb} package for {target} provisioned.", stamp(1)))
        steps.append(_step("download_to_vehicle", "SUCCEEDED",
                           "Package downloaded to the vehicle's connectivity unit (TCU).", stamp(2)))
        steps.append(_step("install", "SUCCEEDED",
                           f"Service {target} {verb} package installed.", stamp(3)))
        steps.append(_step("apply", "SUCCEEDED",
                           f"Service {target} {verb} applied in the vehicle.", stamp(4)))
        status = "ACTIVATED"
        summary = f"Service {verb} for {target} reached {vin}."

    campaign = {
        "campaign_id": cid, "vin": vin, "type": ctype, "target": target,
        "status": status, "created_at": start.isoformat(), "steps": steps, "summary": summary,
    }
    CAMPAIGNS[cid] = campaign
    return campaign
