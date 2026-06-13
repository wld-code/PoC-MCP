"""End-to-end tests for the whole connected-vehicle stack.

Boots the real services as subprocesses:

  - asap-api  (service orchestration)        FastAPI
  - cvc-api   (car gateway / telemetry)       FastAPI
  - asap-mcp  (MCP adapter over asap-api)      MCP / Streamable HTTP
  - cvc-mcp   (MCP adapter over cvc-api)        MCP / Streamable HTTP

then exercises every layer:

  - API HTTP endpoints (both APIs)
  - MCP tool discovery + calls across BOTH servers via MultiMCPClient
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
ASAP_API_DIR = ROOT / "asap_api"
CVC_API_DIR = ROOT / "cvc_api"
REDBEND_API_DIR = ROOT / "redbend_api"
DATALAKE_API_DIR = ROOT / "datalake_api"
ASAP_MCP_DIR = ROOT / "asap_mcp"
CVC_MCP_DIR = ROOT / "cvc_mcp"
REDBEND_MCP_DIR = ROOT / "redbend_mcp"
DATALAKE_MCP_DIR = ROOT / "datalake_mcp"
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
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT)


@pytest.fixture(scope="session")
def services(tmp_path_factory):
    asap_api_port = free_port()
    cvc_api_port = free_port()
    redbend_api_port = free_port()
    datalake_api_port = free_port()
    asap_mcp_port = free_port()
    cvc_mcp_port = free_port()
    redbend_mcp_port = free_port()
    datalake_mcp_port = free_port()
    logs = tmp_path_factory.mktemp("logs")
    procs = []

    # 1) The four fake APIs. Redbend boots first so ASAP can reach it.
    procs.append(_spawn(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(redbend_api_port)],
        REDBEND_API_DIR, {**os.environ}, logs / "redbend_api.log",
    ))
    wait_http(f"http://127.0.0.1:{redbend_api_port}/health")
    procs.append(_spawn(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(asap_api_port)],
        ASAP_API_DIR,
        {**os.environ, "REDBEND_BASE_URL": f"http://127.0.0.1:{redbend_api_port}"},
        logs / "asap_api.log",
    ))
    procs.append(_spawn(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(cvc_api_port)],
        CVC_API_DIR, {**os.environ}, logs / "cvc_api.log",
    ))
    procs.append(_spawn(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(datalake_api_port)],
        DATALAKE_API_DIR, {**os.environ}, logs / "datalake_api.log",
    ))
    wait_http(f"http://127.0.0.1:{asap_api_port}/health")
    wait_http(f"http://127.0.0.1:{cvc_api_port}/health")
    wait_http(f"http://127.0.0.1:{datalake_api_port}/health")

    # 2) The four MCP servers, each pointed at its API.
    procs.append(_spawn(
        [sys.executable, "server.py"], ASAP_MCP_DIR,
        {**os.environ, "API_BASE_URL": f"http://127.0.0.1:{asap_api_port}",
         "MCP_HOST": "127.0.0.1", "MCP_PORT": str(asap_mcp_port)},
        logs / "asap_mcp.log",
    ))
    procs.append(_spawn(
        [sys.executable, "server.py"], CVC_MCP_DIR,
        {**os.environ, "API_BASE_URL": f"http://127.0.0.1:{cvc_api_port}",
         "MCP_HOST": "127.0.0.1", "MCP_PORT": str(cvc_mcp_port)},
        logs / "cvc_mcp.log",
    ))
    procs.append(_spawn(
        [sys.executable, "server.py"], REDBEND_MCP_DIR,
        {**os.environ, "API_BASE_URL": f"http://127.0.0.1:{redbend_api_port}",
         "MCP_HOST": "127.0.0.1", "MCP_PORT": str(redbend_mcp_port)},
        logs / "redbend_mcp.log",
    ))
    procs.append(_spawn(
        [sys.executable, "server.py"], DATALAKE_MCP_DIR,
        {**os.environ, "API_BASE_URL": f"http://127.0.0.1:{datalake_api_port}",
         "MCP_HOST": "127.0.0.1", "MCP_PORT": str(datalake_mcp_port)},
        logs / "datalake_mcp.log",
    ))
    wait_port("127.0.0.1", asap_mcp_port)
    wait_port("127.0.0.1", cvc_mcp_port)
    wait_port("127.0.0.1", redbend_mcp_port)
    wait_port("127.0.0.1", datalake_mcp_port)

    info = {
        "asap_api": f"http://127.0.0.1:{asap_api_port}",
        "cvc_api": f"http://127.0.0.1:{cvc_api_port}",
        "redbend_api": f"http://127.0.0.1:{redbend_api_port}",
        "datalake_api": f"http://127.0.0.1:{datalake_api_port}",
        "mcp_urls": (f"http://127.0.0.1:{asap_mcp_port}/mcp,"
                     f"http://127.0.0.1:{cvc_mcp_port}/mcp,"
                     f"http://127.0.0.1:{redbend_mcp_port}/mcp,"
                     f"http://127.0.0.1:{datalake_mcp_port}/mcp"),
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
def test_apis_healthy(services):
    assert httpx.get(f"{services['asap_api']}/health").json() == {"status": "ok"}
    assert httpx.get(f"{services['cvc_api']}/health").json() == {"status": "ok"}


def test_cvc_fleet_and_telemetry(services):
    fleet = httpx.get(f"{services['cvc_api']}/vehicles").json()
    assert len(fleet) == 5
    vin = fleet[0]["vin"]
    state = httpx.get(f"{services['cvc_api']}/vehicles/{vin}").json()
    assert "online" in state and "energy_level_pct" in state


def test_asap_catalog_and_activation(services):
    services_list = httpx.get(f"{services['asap_api']}/services").json()
    codes = {s["code"] for s in services_list}
    assert "REMOTE_CLIMATE" in codes
    # Activating an electric-only service on the gasoline car must FAIL eligibility.
    op = httpx.post(
        f"{services['asap_api']}/operations",
        json={"vin": "VR7CONNECT00004", "service_code": "CHARGE_SCHED", "action": "activate"},
    ).json()
    assert op["status"] == "FAILED"
    assert op["steps"][0]["name"] == "eligibility_check"


def test_asap_reconcile_via_redbend(services):
    # Activating remote climate on the hero EV runs the reconcile pipeline, which
    # dispatches a Redbend campaign and flips actual state to ACTIVE.
    op = httpx.post(
        f"{services['asap_api']}/operations",
        json={"vin": "VR7CONNECT00001", "service_code": "REMOTE_CLIMATE", "action": "activate"},
    ).json()
    assert op["status"] == "SUCCEEDED"
    assert [s["name"] for s in op["steps"]] == [
        "eligibility_check", "set_desired_state", "dispatch_to_redbend", "reconcile_state",
    ]
    assert op["desired_state"] == "ACTIVE" and op["actual_state"] == "ACTIVE"
    campaign_id = op["redbend_campaign_id"]
    assert campaign_id  # ASAP created a real Redbend campaign

    # The campaign exists in Redbend and is an activation that reached the car.
    camp = httpx.get(f"{services['redbend_api']}/campaigns/{campaign_id}").json()
    assert camp["type"] == "SERVICE_ACTIVATION" and camp["status"] == "ACTIVATED"

    # service-states now reports REMOTE_CLIMATE in sync (desired == actual == ACTIVE).
    states = httpx.get(f"{services['asap_api']}/vehicles/VR7CONNECT00001/service-states").json()
    rc = next(s for s in states if s["service_code"] == "REMOTE_CLIMATE")
    assert rc["desired_state"] == "ACTIVE" and rc["actual_state"] == "ACTIVE" and rc["in_sync"]


def test_asap_desired_actual_drift(services):
    # Camille's Wi-Fi hotspot was requested but its campaign failed → drift.
    states = httpx.get(f"{services['asap_api']}/vehicles/VR7CONNECT00002/service-states").json()
    wifi = next(s for s in states if s["service_code"] == "WIFI_HOTSPOT")
    assert wifi["desired_state"] == "ACTIVE" and wifi["actual_state"] == "INACTIVE"
    assert wifi["in_sync"] is False
    # The drift references the seeded failed Redbend campaign.
    camp = httpx.get(f"{services['redbend_api']}/campaigns/{wifi['last_campaign_id']}").json()
    assert camp["status"] == "FAILED"


def test_redbend_software_and_campaign(services):
    soft = httpx.get(f"{services['redbend_api']}/vehicles/VR7CONNECT00001/software").json()
    assert soft["modules"] and "platform_version" in soft
    # A FOTA campaign completes and bumps the targeted module's version.
    camp = httpx.post(
        f"{services['redbend_api']}/campaigns",
        json={"vin": "VR7CONNECT00001", "type": "FOTA", "target": "FW_TCU_2025_06"},
    ).json()
    assert camp["status"] == "COMPLETED"
    soft2 = httpx.get(f"{services['redbend_api']}/vehicles/VR7CONNECT00001/software").json()
    tcu = next(m for m in soft2["modules"] if m["name"] == "TCU")
    assert tcu["version"] == "5.2.1"


def test_datalake_analytics(services):
    base = services["datalake_api"]
    datasets = httpx.get(f"{base}/datasets").json()
    assert len(datasets) >= 4 and datasets[0]["row_count"] > 1_000_000_000  # billions of rows
    summary = httpx.get(f"{base}/usage/summary").json()
    assert summary["connected_vehicles"] > 1_000_000 and summary["total_sessions_millions"] > 0
    top = httpx.get(f"{base}/applications/top?limit=3").json()
    assert len(top) == 3 and "trend_pct" in top[0]
    trend = httpx.get(f"{base}/usage/trend", params={"application": "Live Navigation", "weeks": 6}).json()
    assert len(trend["points"]) == 6
    anomalies = httpx.get(f"{base}/anomalies").json()
    # the high-severity anomaly ties back to the Wi-Fi activation drift story
    assert any(a["severity"] == "high" and a["application"] == "In-Car Wi-Fi" for a in anomalies)


def test_cvc_online_offline_narrative(services):
    # The hero car is online; the Opel Astra is deliberately offline.
    hero = httpx.get(f"{services['cvc_api']}/vehicles/VR7CONNECT00001").json()
    opel = httpx.get(f"{services['cvc_api']}/vehicles/VR7CONNECT00004").json()
    assert hero["online"] is True
    assert opel["online"] is False
    # Unknown VIN → 404.
    assert httpx.get(f"{services['cvc_api']}/vehicles/NOPE").status_code == 404


# --------------------------------------------------------------------------- #
# MCP layer — all three servers merged through the agent's MultiMCPClient        #
# --------------------------------------------------------------------------- #
def test_multi_mcp_tools_and_call(services):
    sys.path.insert(0, str(AGENT_DIR))
    from mcp_client import MultiMCPClient

    async def go():
        async with MultiMCPClient(services["mcp_urls"]) as mcp:
            names = {t["name"] for t in await mcp.list_tools()}
            # tools from all FOUR servers are visible as one flat toolbox
            assert {"list_vehicles", "get_vehicle", "vehicle_diagnostics"} <= names  # CVC
            assert {"list_services", "activate_service", "service_states"} <= names  # ASAP
            assert {"list_campaigns", "vehicle_software", "create_campaign"} <= names  # Redbend
            assert {"list_datasets", "service_usage", "anomalies"} <= names  # Datalake
            out = await mcp.call_tool("list_vehicles", {})
            assert "VR7CONNECT00001" in out

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# Headless agent (subprocess, mock provider)                                   #
# --------------------------------------------------------------------------- #
def test_headless_agent(services):
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    res = subprocess.run(
        [sys.executable, "headless.py", "--json", "List the connected vehicles"],
        cwd=AGENT_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["tool_calls"][0]["name"] == "list_vehicles"
    assert "VR7CONNECT00001" in data["answer"]


def test_headless_agent_cross_server(services):
    # The mock provider resolves a VIN on the CVC server, then activates a
    # service on the ASAP server — proving a tool call routes across BOTH servers.
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    res = subprocess.run(
        [sys.executable, "headless.py", "--json", "activate a service on the first car"],
        cwd=AGENT_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    called = [tc["name"] for tc in data["tool_calls"]]
    assert "list_vehicles" in called      # CVC server
    assert "activate_service" in called    # ASAP server
    assert "SUCCEEDED" in data["answer"]


# --------------------------------------------------------------------------- #
# UI agent (web.py over HTTP, mock provider)                                   #
# --------------------------------------------------------------------------- #
def test_web_agent(services, tmp_path_factory):
    web_port = free_port()
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    proc = _spawn(
        [sys.executable, "-m", "uvicorn", "web:app", "--port", str(web_port)],
        AGENT_DIR, env, tmp_path_factory.mktemp("web") / "web.log",
    )
    try:
        base = f"http://127.0.0.1:{web_port}"
        wait_http(f"{base}/health")
        assert "<html" in httpx.get(base).text.lower()
        tool_names = httpx.get(f"{base}/api/tools").json()["tools"]
        assert "list_vehicles" in tool_names and "activate_service" in tool_names

        res = httpx.post(
            f"{base}/api/chat",
            json={"session_id": "t1", "message": "show the diagnostics for the first car"},
            timeout=60,
        ).json()
        assert res["tool_calls"][-1]["name"] == "vehicle_diagnostics"
        assert "health_score" in res["answer"]
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()


def test_web_ui_endpoints(services, tmp_path_factory):
    # Exercises the multi-tab UI backend: per-server tool grouping, the provider
    # menu, a headless run, and a scheduled trigger (create -> list -> delete).
    web_port = free_port()
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    proc = _spawn(
        [sys.executable, "-m", "uvicorn", "web:app", "--port", str(web_port)],
        AGENT_DIR, env, tmp_path_factory.mktemp("webui") / "web.log",
    )
    try:
        base = f"http://127.0.0.1:{web_port}"
        wait_http(f"{base}/health")

        # /api/info: four MCP servers, each carrying its own tools; mock LLM present.
        info = httpx.get(f"{base}/api/info").json()
        assert len(info["servers"]) == 4
        all_tools = {t["name"] for s in info["servers"] for t in s["tools"]}
        assert {"list_vehicles", "activate_service", "vehicle_software", "list_datasets"} <= all_tools
        assert info["tool_count"] == len(all_tools)
        assert any(l["id"] == "mock" for l in info["llms"])

        # headless run -> returns a recorded run with tool calls
        rec = httpx.post(
            f"{base}/api/headless/run",
            json={"question": "list the connected vehicles", "provider": "mock"},
            timeout=60,
        ).json()
        assert rec["error"] is False
        assert rec["tool_calls"][0]["name"] == "list_vehicles"
        assert httpx.get(f"{base}/api/headless/runs").json()["runs"]

        # scheduled trigger: create -> appears in list -> delete
        trig = httpx.post(
            f"{base}/api/headless/triggers",
            json={"question": "list vehicles", "interval_seconds": 5, "provider": "mock",
                  "label": "watch"},
        ).json()
        tid = trig["id"]
        listed = httpx.get(f"{base}/api/headless/triggers").json()["triggers"]
        assert any(t["id"] == tid for t in listed)
        assert httpx.request("DELETE", f"{base}/api/headless/triggers/{tid}").json()["stopped"] == tid
        assert all(t["id"] != tid for t in httpx.get(f"{base}/api/headless/triggers").json()["triggers"])
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()


def test_mcp_crud(services, tmp_path_factory):
    # Add a second copy of the CVC server at runtime, then remove it — proving the
    # MCP CRUD wires a live server in and out of the agent's toolbox.
    web_port = free_port()
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    proc = _spawn(
        [sys.executable, "-m", "uvicorn", "web:app", "--port", str(web_port)],
        AGENT_DIR, env, tmp_path_factory.mktemp("mcpcrud") / "web.log",
    )
    try:
        base = f"http://127.0.0.1:{web_port}"
        wait_http(f"{base}/health")
        listed0 = httpx.get(f"{base}/api/mcp/servers").json()["servers"]
        start = len(listed0)
        # Default server ids must be path-safe (no '/') so DELETE/PUT routing works.
        assert all("/" not in s["id"] for s in listed0)

        # a default (auto-id) server can be removed and re-added — exercises the
        # exact slug-id delete path the UI uses.
        first = listed0[0]
        assert httpx.request("DELETE", f"{base}/api/mcp/servers/{first['id']}").json()["removed"] == first["id"]
        assert len(httpx.get(f"{base}/api/mcp/servers").json()["servers"]) == start - 1
        httpx.post(f"{base}/api/mcp/servers", json={"url": first["url"]}, timeout=30)
        assert len(httpx.get(f"{base}/api/mcp/servers").json()["servers"]) == start

        # add one of the existing MCP urls again under a custom id
        extra_url = services["mcp_urls"].split(",")[0]
        added = httpx.post(f"{base}/api/mcp/servers",
                           json={"url": extra_url, "id": "extra"}, timeout=30).json()
        assert added["status"] == "connected" and added["tool_count"] > 0
        assert len(httpx.get(f"{base}/api/mcp/servers").json()["servers"]) == start + 1

        # adding an unreachable server returns an error status (no crash)
        bad = httpx.post(f"{base}/api/mcp/servers",
                         json={"url": "http://127.0.0.1:9/mcp", "id": "bad"}, timeout=30).json()
        assert bad["status"] == "error"
        httpx.request("DELETE", f"{base}/api/mcp/servers/bad")

        # remove the extra one
        assert httpx.request("DELETE", f"{base}/api/mcp/servers/extra").json()["removed"] == "extra"
        assert len(httpx.get(f"{base}/api/mcp/servers").json()["servers"]) == start
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()


def test_llm_crud(services, tmp_path_factory):
    # Create an LLM config, use it, edit it, set default, delete it.
    web_port = free_port()
    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": "mock"}
    proc = _spawn(
        [sys.executable, "-m", "uvicorn", "web:app", "--port", str(web_port)],
        AGENT_DIR, env, tmp_path_factory.mktemp("llmcrud") / "web.log",
    )
    try:
        base = f"http://127.0.0.1:{web_port}"
        wait_http(f"{base}/health")

        created = httpx.post(f"{base}/api/llms",
                             json={"name": "Test Mock", "kind": "mock"}).json()
        lid = created["id"]
        assert any(l["id"] == lid for l in httpx.get(f"{base}/api/llms").json()["llms"])

        # the new LLM works as a provider id in a headless run
        rec = httpx.post(f"{base}/api/headless/run",
                         json={"question": "list vehicles", "provider": lid}, timeout=60).json()
        assert rec["error"] is False and rec["tool_calls"]

        # edit + set default + delete
        edited = httpx.put(f"{base}/api/llms/{lid}", json={"name": "Renamed"}).json()
        assert edited["name"] == "Renamed"
        assert httpx.put(f"{base}/api/llms/{lid}/default").json()["default"] == lid
        assert httpx.request("DELETE", f"{base}/api/llms/{lid}").json()["removed"] == lid
        assert all(l["id"] != lid for l in httpx.get(f"{base}/api/llms").json()["llms"])
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
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("MISTRAL_API_KEY")
    ),
    reason="no LLM provider key in the environment",
)
def test_headless_agent_live(services):
    if os.environ.get("ANTHROPIC_API_KEY"):
        provider = "claude"
    elif os.environ.get("OPENAI_API_KEY"):
        provider = "openai"
    elif os.environ.get("OPENROUTER_API_KEY"):
        provider = "openrouter"
    else:
        provider = "mistral"

    env = {**os.environ, "MCP_SERVER_URLS": services["mcp_urls"], "LLM_PROVIDER": provider}
    res = subprocess.run(
        [sys.executable, "headless.py", "--json",
         "Is Walid's car online, and which services are active on it?"],
        cwd=AGENT_DIR, env=env, capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    called = {tc["name"] for tc in data["tool_calls"]}
    # A real agent should resolve the owner to a VIN, then read across servers.
    assert "list_vehicles" in called
