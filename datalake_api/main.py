"""Datalake API — analytics over massive connected-services data.

The fourth fake backend. It exposes aggregate analytics about how connected
services and in-car applications are used across the whole installed base. The
MCP server (datalake_mcp) wraps it; the agent queries it for the big picture
(usage, top apps, trends, anomalies) rather than single-vehicle data.

Run:  uvicorn main:app --host 0.0.0.0 --port 8024
Docs: http://localhost:8024/docs
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

import store
from models import Anomaly, ApplicationUsage, Dataset, ServiceUsageSummary, UsageTrend

app = FastAPI(
    title="Datalake API",
    description=(
        "Analytics data lake for connected-vehicle services — aggregated usage of "
        "services and in-car applications across the installed base."
    ),
    version="1.0.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/datasets", response_model=list[Dataset])
def list_datasets() -> list[Dataset]:
    """Catalogue of datasets in the lake, with row counts and sizes."""
    return [Dataset(**d) for d in store.list_datasets()]


@app.get("/usage/summary", response_model=ServiceUsageSummary)
def usage_summary(period: str = Query(default="30d")) -> ServiceUsageSummary:
    """Fleet-wide connected-services usage summary for a period."""
    return ServiceUsageSummary(**store.usage_summary(period))


@app.get("/applications/top", response_model=list[ApplicationUsage])
def top_applications(limit: int = Query(default=5, ge=1, le=20)) -> list[ApplicationUsage]:
    """Top in-car applications / services by usage, with growth trend."""
    return [ApplicationUsage(**a) for a in store.top_applications(limit)]


@app.get("/usage/trend", response_model=UsageTrend)
def usage_trend(
    application: str = Query(..., description="Application/service name"),
    weeks: int = Query(default=8, ge=2, le=26),
) -> UsageTrend:
    """Weekly usage trend (sessions) for one application/service."""
    trend = store.usage_trend(application, weeks)
    if trend is None:
        raise HTTPException(status_code=404, detail=f"Unknown application '{application}'")
    return UsageTrend(**trend)


@app.get("/anomalies", response_model=list[Anomaly])
def anomalies() -> list[Anomaly]:
    """Usage anomalies the lake's detectors have flagged across the fleet."""
    return [Anomaly(**a) for a in store.anomalies()]
