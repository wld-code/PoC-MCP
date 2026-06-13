"""In-memory store for the Datalake API — seeded, massive-looking analytics.

There is no real data lake here; we synthesize reproducible aggregates that
*look* like the output of one (billions of rows, terabytes, millions of active
vehicles), so the demo conveys the scale of connected-services data without a
real warehouse. A fixed RNG seed keeps every figure stable across boots.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

# In-car applications / connected services tracked across the installed base.
APPLICATIONS = [
    ("Live Navigation", "Navigation"),
    ("Remote Climate", "Comfort"),
    ("In-Car Wi-Fi", "Connectivity"),
    ("Media Streaming", "Entertainment"),
    ("Voice Assistant", "Assistant"),
    ("Smart Charging", "Energy"),
    ("EV Trip Planner", "Energy"),
    ("Parking Finder", "Navigation"),
    ("App Marketplace", "Platform"),
    ("Stolen Vehicle Tracking", "Security"),
]

CONNECTED_VEHICLES = 4_280_000          # whole installed base (not the 5 demo cars)
EVENTS_PER_SECOND = 312_000

DATASETS = [
    {"name": "connected_service_events", "description": "Every connected-service interaction across the fleet.",
     "row_count": 8_420_000_000, "size_tb": 12.6, "freshness": "live", "partitioned_by": "day / region"},
    {"name": "in_car_app_sessions", "description": "In-car application session records.",
     "row_count": 2_130_000_000, "size_tb": 4.8, "freshness": "hourly", "partitioned_by": "day / app"},
    {"name": "vehicle_signals", "description": "Raw vehicle signal time-series (CAN / sensors).",
     "row_count": 31_500_000_000, "size_tb": 84.2, "freshness": "live", "partitioned_by": "hour / model"},
    {"name": "ota_campaign_telemetry", "description": "OTA campaign delivery and install telemetry.",
     "row_count": 540_000_000, "size_tb": 1.9, "freshness": "hourly", "partitioned_by": "campaign"},
    {"name": "billing_usage", "description": "Usage & billing events for paid connected services.",
     "row_count": 1_240_000_000, "size_tb": 2.3, "freshness": "daily", "partitioned_by": "day / market"},
]

_rng = random.Random(11)
_USAGE: dict[str, dict] = {}
for _name, _cat in APPLICATIONS:
    _USAGE[_name] = {
        "application": _name,
        "category": _cat,
        "monthly_active_vehicles": _rng.randint(120_000, 2_400_000),
        "sessions_millions": round(_rng.uniform(3, 180), 1),
        "avg_session_min": round(_rng.uniform(2, 34), 1),
        "data_volume_tb": round(_rng.uniform(0.8, 240), 1),
        "trend_pct": round(_rng.uniform(-18, 42), 1),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def list_datasets() -> list[dict]:
    return list(DATASETS)


def top_applications(limit: int = 5) -> list[dict]:
    apps = sorted(_USAGE.values(), key=lambda a: a["sessions_millions"], reverse=True)
    return apps[:limit]


def usage_summary(period: str = "30d") -> dict:
    total_sessions = round(sum(a["sessions_millions"] for a in _USAGE.values()), 1)
    total_volume = round(sum(a["data_volume_tb"] for a in _USAGE.values()), 1)
    mav = max(a["monthly_active_vehicles"] for a in _USAGE.values())
    top_growth = max(_USAGE.values(), key=lambda a: a["trend_pct"])["application"]
    return {
        "period": period,
        "connected_vehicles": CONNECTED_VEHICLES,
        "monthly_active_vehicles": mav,
        "total_sessions_millions": total_sessions,
        "data_volume_tb": total_volume,
        "events_ingested_per_second": EVENTS_PER_SECOND,
        "top_growth_application": top_growth,
        "generated_at": _now().isoformat(),
    }


def usage_trend(application: str, weeks: int = 8) -> dict | None:
    # Match case-insensitively / by substring so the agent can pass loose names.
    key = None
    for name in _USAGE:
        if name.lower() == application.lower() or application.lower() in name.lower():
            key = name
            break
    if key is None:
        return None
    app = _USAGE[key]
    rng = random.Random(hash(key) & 0xFFFF)
    weeks = max(2, min(weeks, 26))
    base = app["sessions_millions"] / 4.0           # weekly ~ monthly/4
    slope = (app["trend_pct"] / 100.0) * base / weeks
    points = []
    now = _now()
    for i in range(weeks):
        wk = now - timedelta(weeks=(weeks - 1 - i))
        val = base + slope * i + rng.uniform(-base * 0.06, base * 0.06)
        points.append({"week": wk.strftime("%Y-W%W"), "sessions_millions": round(max(0.1, val), 2)})
    return {"application": key, "period_weeks": weeks, "points": points}


def anomalies() -> list[dict]:
    now = _now()
    return [
        {"id": "anom-001", "severity": "high", "application": "In-Car Wi-Fi",
         "metric": "activation_success_rate",
         "detail": "Activation success rate dropped 34% in the last 48h — OTA activation "
                   "packages are failing to reach vehicles (download interruptions).",
         "impacted_vehicles": 18_400, "detected_at": (now - timedelta(hours=6)).isoformat()},
        {"id": "anom-002", "severity": "medium", "application": "Media Streaming",
         "metric": "data_volume",
         "detail": "Data volume spiked +220% in the EU region, concentrated on a single "
                   "content provider — likely a caching regression.",
         "impacted_vehicles": 96_200, "detected_at": (now - timedelta(hours=14)).isoformat()},
        {"id": "anom-003", "severity": "low", "application": "Voice Assistant",
         "metric": "p95_latency",
         "detail": "p95 response latency above SLA in ~3% of sessions during peak hours.",
         "impacted_vehicles": 7_900, "detected_at": (now - timedelta(days=1)).isoformat()},
    ]
