"""MCP server exposing the Datalake analytics API as MCP tools.

The data lake is the *analytics* source: massive aggregated data on how connected
services and in-car applications are used across the whole installed base. The
agent uses these tools to answer big-picture questions — usage, top apps, trends,
and anomalies — rather than single-vehicle queries (which CVC handles).

Thin adapter over the Datalake FastAPI backend. Transport: Streamable HTTP.
Binds on :8014, MCP endpoint mounted at /mcp.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8024")

mcp = FastMCP(
    "datalake-insights",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8014")),
)


def _get(path: str, params: dict | None = None) -> object:
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_datasets() -> list[dict]:
    """List the datasets in the connected-services data lake.

    Each has a description, row_count (often billions), on-disk size_tb and
    freshness — use it to convey the scale of available data."""
    return _get("/datasets")


@mcp.tool()
def service_usage(period: str = "30d") -> dict:
    """Fleet-wide connected-services usage summary for a period (e.g. "30d").

    Returns connected vehicles, monthly active vehicles, total sessions, data
    volume, ingest rate and the fastest-growing application."""
    return _get("/usage/summary", {"period": period})


@mcp.tool()
def top_applications(limit: int = 5) -> list[dict]:
    """Top in-car applications / connected services by usage, with growth trend."""
    return _get("/applications/top", {"limit": limit})


@mcp.tool()
def usage_trend(application: str, weeks: int = 8) -> dict:
    """Weekly usage trend (sessions) for one application/service by name."""
    return _get("/usage/trend", {"application": application, "weeks": weeks})


@mcp.tool()
def anomalies() -> list[dict]:
    """Usage anomalies the lake's detectors have flagged across the fleet,
    with severity, the affected application, impacted vehicles and detail."""
    return _get("/anomalies")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
