"""Pydantic response models for the Datalake API.

The data lake is the **analytics** plane of the demo: it does not look at one
car (that's CVC) — it holds the *massive* aggregated data about how connected
services and in-car applications are used across the whole installed base
(billions of events, terabytes of signals). The agent queries it to answer
"big picture" questions: usage, top applications, trends and anomalies.
"""
from __future__ import annotations

from pydantic import BaseModel


class Dataset(BaseModel):
    name: str
    description: str
    row_count: int        # number of rows (massive)
    size_tb: float        # on-disk size in terabytes
    freshness: str        # live | hourly | daily
    partitioned_by: str


class ApplicationUsage(BaseModel):
    application: str
    category: str
    monthly_active_vehicles: int
    sessions_millions: float
    avg_session_min: float
    data_volume_tb: float
    trend_pct: float      # vs the previous period


class UsagePoint(BaseModel):
    week: str
    sessions_millions: float


class UsageTrend(BaseModel):
    application: str
    period_weeks: int
    points: list[UsagePoint]


class ServiceUsageSummary(BaseModel):
    period: str
    connected_vehicles: int
    monthly_active_vehicles: int
    total_sessions_millions: float
    data_volume_tb: float
    events_ingested_per_second: int
    top_growth_application: str
    generated_at: str


class Anomaly(BaseModel):
    id: str
    severity: str         # high | medium | low
    application: str
    metric: str
    detail: str
    impacted_vehicles: int
    detected_at: str
