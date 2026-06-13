"""MCP server exposing the ASAP API as MCP tools.

ASAP is the *orchestration* side of the demo: it owns the **desired** state of
each connected service per vehicle and reconciles it to the **actual** state by
dispatching campaigns to Redbend (the OTA execution layer). Each ``@mcp.tool()``
below is a capability the LLM agent can choose to call. The server is a thin
adapter — every tool forwards to the ASAP FastAPI backend over HTTP.

The docstrings and type hints ARE the contract the model reads: the SDK turns
them into the JSON schema the LLM sees when it decides which tool to call.

Transport: Streamable HTTP. Binds on :8011, MCP endpoint mounted at /mcp.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8021")

mcp = FastMCP(
    "asap-orchestrator",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8011")),
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
def list_services(category: str | None = None) -> list[dict]:
    """List the connected-vehicle services that can be activated.

    Optionally filter by category (e.g. "Comfort", "Security", "Energy").
    Each service has a code (used to activate it), name, price and whether it
    needs embedded hardware or an electric powertrain.
    """
    params = {"category": category} if category else None
    return _get("/services", params)


@mcp.tool()
def service_states(vin: str) -> list[dict]:
    """Desired vs actual state of every service on a vehicle, by VIN.

    For each service returns desired_state and actual_state (ACTIVE/INACTIVE) and
    in_sync. When in_sync is false the service was requested but not actually
    applied (a drift) — last_campaign_id points to the Redbend campaign involved.
    """
    return _get(f"/vehicles/{vin}/service-states")


@mcp.tool()
def activate_service(vin: str, service_code: str) -> dict:
    """Request a service be ACTIVE on a vehicle and orchestrate it.

    Sets the desired state to ACTIVE and dispatches a Redbend campaign to apply
    it; the actual state flips only if the campaign reaches the car. `service_code`
    comes from list_services (e.g. "REMOTE_CLIMATE"). Returns an Operation with
    each reconciliation step, the resulting desired/actual state, the Redbend
    campaign id, and the final status (SUCCEEDED / FAILED).
    """
    return _post("/operations", {"vin": vin, "service_code": service_code, "action": "activate"})


@mcp.tool()
def deactivate_service(vin: str, service_code: str) -> dict:
    """Request a service be INACTIVE on a vehicle (sets desired=INACTIVE and
    reconciles through a Redbend campaign). Returns the Operation."""
    return _post("/operations", {"vin": vin, "service_code": service_code, "action": "deactivate"})


@mcp.tool()
def get_operation(operation_id: str) -> dict:
    """Get the status and step-by-step trace of a reconciliation operation by id."""
    return _get(f"/operations/{operation_id}")


@mcp.tool()
def list_operations(limit: int = 10) -> list[dict]:
    """List the most recent reconciliation operations."""
    return _get("/operations", {"limit": limit})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
