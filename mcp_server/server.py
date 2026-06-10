"""MCP server exposing the Demo Data API as MCP tools.

Built on the official Model Context Protocol Python SDK (`mcp`), using its
FastMCP helper. Each ``@mcp.tool()`` becomes a tool the LLM agent can call.
The server itself is a thin adapter: it forwards every call to the FastAPI
backend over HTTP, so the API stays the single source of truth.

Transport: Streamable HTTP — the modern, production-friendly MCP transport.
The server listens on :8001 and the MCP endpoint is mounted at /mcp.
"""
from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# host/port control where the Streamable HTTP server binds (overridable for
# local runs and tests via MCP_HOST / MCP_PORT).
mcp = FastMCP(
    "demo-data",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8001")),
)


def _get(path: str, params: dict | None = None) -> object:
    """Call the backend API and return parsed JSON (raises on HTTP error)."""
    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
def list_products(category: str | None = None, limit: int = 50) -> list[dict]:
    """List products, optionally filtered by category (e.g. "Electronics").

    Returns up to `limit` products with id, name, category, price and stock.
    """
    return _get("/products", {"category": category, "limit": limit})


@mcp.tool()
def get_product(product_id: int) -> dict:
    """Get a single product by its numeric id."""
    return _get(f"/products/{product_id}")


@mcp.tool()
def search_products(
    q: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = 50,
) -> list[dict]:
    """Search products by name substring and/or price range.

    All filters are optional and combine with AND. Results are ordered by price.
    """
    params = {"q": q, "min_price": min_price, "max_price": max_price, "limit": limit}
    # Drop None so we don't send empty query params.
    params = {k: v for k, v in params.items() if v is not None}
    return _get("/products/search", params)


@mcp.tool()
def sales_summary() -> dict:
    """Get aggregate sales totals: number of sales, total units, total revenue."""
    return _get("/sales/summary")


@mcp.tool()
def top_products(limit: int = 5) -> list[dict]:
    """Get the top `limit` products by revenue, with units sold and revenue."""
    return _get("/sales/top", {"limit": limit})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
