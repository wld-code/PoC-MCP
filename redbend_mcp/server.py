"""MCP server exposing the Redbend OTA API as MCP tools.

Redbend is the *execution* layer: it pushes firmware (FOTA), software (SOTA) and
service-activation packages to vehicles. The agent uses these tools to inspect a
car's installed software, see what OTA jobs have run, and launch new ones.
Activations the agent requests through ASAP are ultimately carried out by a
Redbend campaign — these tools let the agent look "under the hood" at that.

Thin adapter over the Redbend FastAPI backend. Transport: Streamable HTTP.
Binds on :8013, MCP endpoint mounted at /mcp.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8023")

mcp = FastMCP(
    "redbend-ota",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8013")),
)


def _get(path: str, params: dict | None = None) -> object:
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def _post(path: str, body: dict) -> object:
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = client.post(path, json=body)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def vehicle_software(vin: str) -> dict:
    """Get the on-vehicle software: platform version, per-module firmware/software
    versions, and any OTA updates available for the vehicle (by VIN)."""
    return _get(f"/vehicles/{vin}/software")


@mcp.tool()
def list_campaigns(limit: int = 10) -> list[dict]:
    """List recent OTA / service-activation campaigns (FOTA, SOTA, activations)."""
    return _get("/campaigns", {"limit": limit})


@mcp.tool()
def get_campaign(campaign_id: str) -> dict:
    """Get the status and step-by-step trace of one campaign by id (e.g. "cmp-000001")."""
    return _get(f"/campaigns/{campaign_id}")


@mcp.tool()
def create_campaign(vin: str, type: str = "SERVICE_ACTIVATION", target: str = "") -> dict:
    """Launch an OTA campaign on a vehicle and return the finished result.

    `type` is one of: FOTA (firmware), SOTA (software), SERVICE_ACTIVATION or
    SERVICE_DEACTIVATION. `target` is the package id (for FOTA/SOTA, from
    vehicle_software) or the service code (for activations). Note: to turn a
    connected service on/off you should normally go through ASAP's
    activate_service, which sets the desired state AND dispatches a Redbend
    campaign for you.
    """
    return _post("/campaigns", {"vin": vin, "type": type, "target": target})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
