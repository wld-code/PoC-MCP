"""MCP server exposing the CVC car-gateway API as MCP tools.

CVC is the *read* side of the demo: it tells the agent what a vehicle is and
what it is currently doing. The agent typically calls these tools first — to
find a VIN, check a car is online, or read its charge — before it decides to
act through the ASAP tools.

Thin adapter, same as the ASAP server: every tool forwards to the CVC FastAPI
backend over HTTP. Transport: Streamable HTTP. Binds on :8012, endpoint at /mcp.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8022")

mcp = FastMCP(
    "cvc-gateway",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8012")),
)


def _get(path: str, params: dict | None = None) -> object:
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_vehicles() -> list[dict]:
    """List the connected fleet: VIN, make, model, year, owner, powertrain.

    Use this first to resolve an owner's name or a model into a VIN, which the
    other tools (and the ASAP service-activation tools) need.
    """
    return _get("/vehicles")


@mcp.tool()
def get_vehicle(vin: str) -> dict:
    """Get the full live telemetry snapshot for a vehicle by VIN.

    Includes whether the car is online, engine/door state, energy level and
    range, tire pressures, software version, signal strength and location.
    """
    return _get(f"/vehicles/{vin}")


@mcp.tool()
def vehicle_location(vin: str) -> dict:
    """Get the current GPS location (lat/lon, heading, city) of a vehicle."""
    return _get(f"/vehicles/{vin}/location")


@mcp.tool()
def vehicle_diagnostics(vin: str) -> dict:
    """Get the health score, diagnostic trouble codes and service status."""
    return _get(f"/vehicles/{vin}/diagnostics")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
