"""End-to-end tests for the whole stack.

Boots the real API and MCP server as subprocesses (fresh temp SQLite DB),
then exercises every layer:

  - API HTTP endpoints
  - MCP tool discovery + tool calls (Agent's MCP client → MCP server → API)
  - Headless agent (subprocess) using the deterministic `mock` provider
  - UI agent (web.py) over HTTP using the `mock` provider
  - Optional: a live LLM run, only if a provider key is present

The `mock` provider means the agent tests need no API key and cost nothing.

Run:  pytest -v        (from the repo root, inside a venv with the deps)
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import httpx
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
MCP_DIR = ROOT / "mcp_server"
AGENT_DIR = ROOT / "agent"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_http(url: str, timeout: float = 40) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.3)
    raise RuntimeError(f"timeout waiting for {url}: {last}")


def wait_port(host: str, port: int, timeout: float = 40) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError(f"timeout waiting for {host}:{port}")


def _spawn(cmd: list[str], cwd: pathlib.Path, env: dict, log: pathlib.Path):
    fh = open(log, "w")
    return subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT
    )


@pytest.fixture(scope="session")
def services(tmp_path_factory):
    api_port = free_port()
    mcp_port = free_port()
    db = tmp_path_factory.mktemp("db") / "e2e.db"
    logs = tmp_path_factory.mktemp("logs")
    procs = []

    # 1) API
    procs.append(
        _spawn(
            [sys.executable, "-m", "uvicorn", "main:app", "--port", str(api_port)],
            API_DIR,
            {**os.environ, "DB_PATH": str(db), "AUTO_SEED": "1"},
            logs / "api.log",
        )
    )
    wait_http(f"http://127.0.0.1:{api_port}/health")

    # 2) MCP server (points at the API)
    procs.append(
        _spawn(
            [sys.executable, "server.py"],
            MCP_DIR,
            {
                **os.environ,
                "API_BASE_URL": f"http://127.0.0.1:{api_port}",
                "MCP_HOST": "127.0.0.1",
                "MCP_PORT": str(mcp_port),
            },
            logs / "mcp.log",
        )
    )
    wait_port("127.0.0.1", mcp_port)

    info = {
        "api": f"http://127.0.0.1:{api_port}",
        "mcp_url": f"http://127.0.0.1:{mcp_port}/mcp",
        "logs": logs,
    }
    try:
        yield info
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(5)
            except Exception:  # noqa: BLE001
                p.kill()


# --------------------------------------------------------------------------- #
# API layer                                                                    #
# --------------------------------------------------------------------------- #
def test_api_health(services):
    assert httpx.get(f"{services['api']}/health").json() == {"status": "ok"}


def test_api_seeded_data(services):
    summary = httpx.get(f"{services['api']}/sales/summary").json()
    assert summary["total_sales"] > 0
    assert summary["total_revenue"] > 0
    top = httpx.get(f"{services['api']}/sales/top?limit=3").json()
    assert len(top) == 3 and "revenue" in top[0]


# --------------------------------------------------------------------------- #
# MCP layer (uses the agent's own MCP client)                                  #
# --------------------------------------------------------------------------- #
def test_mcp_tools_and_call(services):
    sys.path.insert(0, str(AGENT_DIR))
    from mcp_client import MCPClient

    async def go():
        async with MCPClient(services["mcp_url"]) as mcp:
            names = {t["name"] for t in await mcp.list_tools()}
            assert {
                "list_products",
                "get_product",
                "search_products",
                "sales_summary",
                "top_products",
            } <= names
            out = await mcp.call_tool("sales_summary", {})
            assert "total_revenue" in out

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# Headless agent (subprocess, mock provider)                                   #
# --------------------------------------------------------------------------- #
def test_headless_agent(services):
    env = {**os.environ, "MCP_SERVER_URL": services["mcp_url"], "LLM_PROVIDER": "mock"}
    res = subprocess.run(
        [sys.executable, "headless.py", "--json", "Give me the overall sales summary"],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["tool_calls"][0]["name"] == "sales_summary"
    assert "total_revenue" in data["answer"]


# --------------------------------------------------------------------------- #
# UI agent (web.py over HTTP, mock provider)                                   #
# --------------------------------------------------------------------------- #
def test_web_agent(services, tmp_path_factory):
    web_port = free_port()
    env = {**os.environ, "MCP_SERVER_URL": services["mcp_url"], "LLM_PROVIDER": "mock"}
    proc = _spawn(
        [sys.executable, "-m", "uvicorn", "web:app", "--port", str(web_port)],
        AGENT_DIR,
        env,
        tmp_path_factory.mktemp("web") / "web.log",
    )
    try:
        base = f"http://127.0.0.1:{web_port}"
        wait_http(f"{base}/health")
        assert "<html" in httpx.get(base).text.lower()
        assert "sales_summary" in httpx.get(f"{base}/api/tools").json()["tools"]

        res = httpx.post(
            f"{base}/api/chat",
            json={"session_id": "t1", "message": "show me the top products"},
            timeout=60,
        ).json()
        assert res["tool_calls"][0]["name"] == "top_products"
        assert "revenue" in res["answer"]
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()


# --------------------------------------------------------------------------- #
# Optional live LLM run — only if a provider key is configured                 #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
    ),
    reason="no LLM provider key in the environment",
)
def test_headless_agent_live(services):
    if os.environ.get("ANTHROPIC_API_KEY"):
        provider = "claude"
    elif os.environ.get("OPENAI_API_KEY"):
        provider = "openai"
    else:
        provider = "mistral"

    env = {**os.environ, "MCP_SERVER_URL": services["mcp_url"], "LLM_PROVIDER": provider}
    res = subprocess.run(
        [sys.executable, "headless.py", "--json", "What is the total revenue across all sales?"],
        cwd=AGENT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert any(tc["name"] == "sales_summary" for tc in data["tool_calls"])
