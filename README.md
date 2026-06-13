# MCP PoC — Agentic AI over three APIs · Connected-Vehicle scenario

[![CI](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

A complete, runnable **tutorial** for the **Model Context Protocol (MCP)** and
**agentic AI**. An LLM agent reads live vehicle data and orchestrates service
activations by calling **MCP tools** across **two backends** — never touching
the APIs or databases directly.

> 📚 **Start here:** [`docs/06-agentic-ai-tutorial.md`](docs/06-agentic-ai-tutorial.md)
> — a step-by-step walkthrough of what an agent is, what makes it *agentic*, and
> how it uses the three MCP servers below.

## The scenario

Four fake backends model a car maker's connected-services platform:

| System | What it is | Does | API | MCP server |
| --- | --- | --- | --- | --- |
| **CVC** | *Connected Vehicle Cloud* — the **car gateway** | **Reads** live telemetry: fleet, online status, charge, location, diagnostics | `cvc_api/` `:8022` | `cvc_mcp/` `:8012` |
| **ASAP** | *Activation Service & Aggregation Platform* | **Orchestrates** activation: owns each service's **desired vs actual** state and reconciles by dispatching to Redbend | `asap_api/` `:8021` | `asap_mcp/` `:8011` |
| **Redbend** | OTA update platform | **Executes** on the vehicle: FOTA (firmware), SOTA (software) and **service-activation** campaigns | `redbend_api/` `:8023` | `redbend_mcp/` `:8013` |
| **Data Lake** | analytics warehouse | **Aggregates** massive connected-services data across the whole installed base: datasets, usage, top apps, trends, anomalies | `datalake_api/` `:8024` | `datalake_mcp/` `:8014` |

One agent connects to **all four** MCP servers, sees one merged toolbox, and
reasons across them — e.g. *"is the car online (CVC) before I request activation
(ASAP), executed by an OTA campaign (Redbend)?"*, or *"what's the fleet-wide
anomaly (Data Lake) and which car does it trace back to?"*

```
            ┌──────────────────────────────────────────────────────────────────┐
            │                            AGENT (LLM)                            │
            │                one merged toolbox of 19 MCP tools                 │
            └────────┬────────────────┬───────────────┬────────────────┬───────┘
                 MCP │            MCP │           MCP │            MCP │
            ┌────────▼───┐   ┌────────▼────┐  ┌───────▼─────┐  ┌───────▼──────┐
            │  cvc-mcp   │   │  asap-mcp   │  │ redbend-mcp │  │ datalake-mcp │
            │ 4 read     │   │ 6 orchestr. │  │ 4 OTA tools │  │ 5 analytics  │
            └─────┬──────┘   └─────┬───────┘  └──────┬──────┘  └──────┬───────┘
                  │                │                 │                │
            ┌─────▼──────┐   ┌─────▼──────┐   ┌───────▼──────┐  ┌──────▼───────┐
            │  cvc-api   │   │  asap-api  │──▶│  redbend-api │  │ datalake-api │
            │ car gateway│   │ desired/   │   │ OTA execution│  │ billions of  │
            │  (reads)   │   │ actual     │   │ FOTA/SOTA/svc│  │ rows · TB    │
            └────────────┘   └────────────┘   └──────────────┘  └──────────────┘
                          ASAP reconciles desired→actual by
                          dispatching a campaign to Redbend
```

## Why MCP

The agent never queries an API or a database. It only sees **tools** the MCP
servers advertise (self-describing: name + description + JSON schema). You can
add a tool, swap a backend, or add auth **without changing the agent** — it
rediscovers tools at startup. MCP servers are thin adapters; the APIs stay the
single source of truth.

## Features

- **Four fake APIs** — `asap-api` (orchestration, desired/actual), `cvc-api`
  (car gateway), `redbend-api` (OTA / activation executor) and `datalake-api`
  (massive connected-services analytics), serving seeded random-but-reproducible
  data, no database needed.
- **Four MCP servers** — thin FastMCP adapters, one per API, over Streamable HTTP.
- **Control-plane / data-plane split** — ASAP tracks the **desired** state and
  reconciles it to the **actual** state by dispatching campaigns to Redbend; a
  failed campaign shows up as a **drift** the agent can detect and explain.
- **Multi-MCP agent** — connects to **all three** servers and routes each tool
  call to its owner (`agent/mcp_client.py`); the web app uses a
  `DynamicMCPManager` so servers can be added/removed at runtime.
- **Provider-agnostic** — Claude (default), OpenAI, OpenRouter, or Mistral via one
  env var (or switch live in the UI); a `mock` provider runs the full pipeline
  with **no API key and no cost**.
- **Live CRUD in the UI** — manage **MCP servers** and **LLM configs** from the
  browser (add/edit/remove), no restart.
- **Three agent entry points** — `headless.py` (automation/CI/Jobs), `web.py`
  (4-tab browser app), `agent.py` (interactive REPL).
- **End-to-end tests** that boot all four real services and drive every layer.

---

## Quick start

**Requirements:** Docker + Docker Compose. An LLM key is optional (`mock` is free).

```bash
cp .env.example .env             # then edit .env if you have an LLM key
docker compose up -d --build     # asap-api, cvc-api, asap-mcp, cvc-mcp, agent-web
```

Open the **web chat** → http://localhost:8002 and try:

- `List the connected vehicles`
- `Is Walid's car online, and what services are active on it?`
- `Activate remote climate on Walid's car`
- `Try to enable smart charging on the Opel` *(eligibility failure → FAILED)*

Run the **headless agent** (with a JSON trace of every tool call):

```bash
docker compose run --rm agent python headless.py --json \
  "Is Walid's car online, and which services are active on it?"
```

Hit the **raw APIs** directly:

```bash
curl localhost:8022/vehicles                              # CVC: the fleet
curl localhost:8021/services                               # ASAP: the service catalogue
curl localhost:8021/vehicles/VR7CONNECT00002/service-states # ASAP: desired vs actual (note the Wi-Fi drift)
curl localhost:8023/vehicles/VR7CONNECT00001/software       # Redbend: on-vehicle software + updates
curl localhost:8024/anomalies                               # Data Lake: fleet-wide flagged anomalies
open http://localhost:8021/docs                            # OpenAPI docs (ASAP · CVC :8022 · Redbend :8023 · Data Lake :8024)
```

> **No key? No problem.** Set `LLM_PROVIDER=mock` in `.env` — the agents still
> call the real MCP tools on both servers and return live data; only the
> natural-language phrasing is skipped. This is exactly what the tests use.

---

## Configuration

`.env` (copied from `.env.example`):

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | `claude` · `openai` · `openrouter` · `mistral` · `mock` | `claude` |
| `LLM_MODEL` | Override the model id (optional) | provider default |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` / `MISTRAL_API_KEY` | Key for the chosen provider | — |
| `ASAP_API_PORT` / `CVC_API_PORT` / `REDBEND_API_PORT` / `DATALAKE_API_PORT` | Host ports for the four APIs | `8021` / `8022` / `8023` / `8024` |
| `ASAP_MCP_PORT` / `CVC_MCP_PORT` / `REDBEND_MCP_PORT` / `DATALAKE_MCP_PORT` | Host ports for the four MCP servers | `8011` / `8012` / `8013` / `8014` |
| `WEB_PORT` | Host port for the web chat UI | `8002` |
| `MCP_SERVER_URLS` | Comma-separated MCP endpoints the agent connects to | both local servers |

Provider defaults: `claude` → `claude-opus-4-8`, `openai` → `gpt-4o`,
`openrouter` → `openai/gpt-4o-mini` (any tool-capable OpenRouter model id),
`mistral` → `mistral-large-latest`, `mock` → no LLM. The MCP tool layer is
identical across providers; only the function-calling format differs
(`agent/providers.py`). OpenRouter reuses the OpenAI Chat Completions format, so
one `OPENROUTER_API_KEY` unlocks hundreds of models behind the same agent loop.

---

## The agents

| Agent | File | For |
| --- | --- | --- |
| **Headless** | `agent/headless.py` | Automation, CI, cron, K8s Jobs, pipelines. Query in → answer out → exit. `--json` emits the answer + the tool calls made. |
| **Web UI** | `agent/web.py` | A 4-tab browser app with MCP/LLM CRUD (see below). Served at `/`. |
| **REPL** | `agent/agent.py` | Interactive terminal session. |

All three share the same core: `MultiMCPClient` (both servers) + the
provider-agnostic LLM loop in `providers.py`.

### The web app (4 tabs)

- **💬 Chat** — the interactive agent. A header dropdown picks the active **LLM**
  and an input overrides the **model** live.
- **🧩 MCP Servers** — **CRUD** the connected MCP servers: add one by URL at
  runtime (its tools join the merged toolbox immediately), edit its URL, or
  remove it. Each server is a card showing its status and the tools it
  contributes; unreachable URLs fail fast with a clear error.
- **🧠 LLMs** — **CRUD** the LLM configurations the agent can use: name, **kind**
  (`mock`, `openai-compatible`, `openai`, `anthropic`, `mistral`), API key, base
  URL and model. Create as many as you like (e.g. several OpenRouter models),
  set a default, edit or delete them.
- **⚙️ Headless** — configure and fire the headless agent: **run once**, click a
  **preset task**, or create a **scheduled trigger** that re-runs a task on a
  timer (fires immediately, then every N seconds) — with live run history.

Backend endpoints: `GET /api/info`, `GET /api/tools`, `POST /api/chat`,
`GET|POST|PUT|DELETE /api/mcp/servers[/{id}]` (MCP CRUD),
`GET|POST|PUT|DELETE /api/llms[/{id}]` + `PUT /api/llms/{id}/default` (LLM CRUD),
`POST /api/headless/run`, `GET /api/headless/runs`,
`POST|GET|DELETE /api/headless/triggers`.

> MCP and LLM edits are **in-memory** (re-seeded from `MCP_SERVER_URLS` and the
> env keys at startup), which suits the PoC; persist them to a store for real use.

---

## Testing

End-to-end tests boot the **eight real services** (four APIs + four MCP servers)
and drive every layer — APIs → MCP (merged) → headless agent → web agent — using
the `mock` provider: **no key, no cost, deterministic**.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r asap_api/requirements.txt -r cvc_api/requirements.txt \
            -r redbend_api/requirements.txt -r datalake_api/requirements.txt \
            -r asap_mcp/requirements.txt -r cvc_mcp/requirements.txt \
            -r redbend_mcp/requirements.txt -r datalake_mcp/requirements.txt \
            -r agent/requirements.txt -r tests/requirements.txt
pytest -v
# → 15 passed, 1 skipped  (the skip is the optional live-LLM test; set a key to run it)
```

What each test covers:

| Test | Verifies |
| --- | --- |
| `test_apis_healthy` / `test_cvc_fleet_and_telemetry` | the APIs boot; CVC returns the 5-car fleet + telemetry |
| `test_asap_catalog_and_activation` | ASAP catalogue + an eligibility **FAILED** operation |
| `test_asap_reconcile_via_redbend` | activation runs eligibility→set-desired→**dispatch to Redbend**→reconcile; actual flips to ACTIVE |
| `test_asap_desired_actual_drift` | a requested service whose Redbend campaign failed shows desired≠actual (drift) |
| `test_redbend_software_and_campaign` | Redbend reports on-vehicle software; a FOTA campaign completes and bumps the module version |
| `test_datalake_analytics` | the Data Lake reports billion-row datasets, usage, app trends and the flagged anomaly |
| `test_multi_mcp_tools_and_call` | the agent merges tools from **all four** servers and calls one |
| `test_cvc_online_offline_narrative` | the hero car is online, the Opel is offline |
| `test_headless_agent` / `test_headless_agent_cross_server` | the headless agent runs the loop and routes calls across servers |
| `test_web_agent` / `test_web_ui_endpoints` | the web UI serves, lists tools, runs a chat, and exposes per-server grouping + triggers |
| `test_mcp_crud` | add an MCP server at runtime → its tools appear → remove it; unreachable URL errors cleanly |
| `test_llm_crud` | create an LLM config, use it in a run, edit, set default, delete |
| `test_headless_agent_live` | *(optional)* a real LLM resolves an owner to a VIN and reads across servers |

---

## Run without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r asap_api/requirements.txt -r cvc_api/requirements.txt \
            -r redbend_api/requirements.txt -r datalake_api/requirements.txt \
            -r asap_mcp/requirements.txt -r cvc_mcp/requirements.txt \
            -r redbend_mcp/requirements.txt -r datalake_mcp/requirements.txt -r agent/requirements.txt

# terminal 1 — Redbend API (ASAP needs it)
cd redbend_api && uvicorn main:app --port 8023
# terminal 2 — ASAP API (points at Redbend)
cd asap_api && REDBEND_BASE_URL=http://localhost:8023 uvicorn main:app --port 8021
# terminal 3 — CVC API   |   terminal 4 — Data Lake API
cd cvc_api && uvicorn main:app --port 8022
cd datalake_api && uvicorn main:app --port 8024
# terminals 5-8 — the MCP servers
cd asap_mcp     && API_BASE_URL=http://localhost:8021 python server.py
cd cvc_mcp      && API_BASE_URL=http://localhost:8022 python server.py
cd redbend_mcp  && API_BASE_URL=http://localhost:8023 python server.py
cd datalake_mcp && API_BASE_URL=http://localhost:8024 python server.py
# terminal 9 — an agent (set a key, or LLM_PROVIDER=mock)
cd agent && export MCP_SERVER_URLS=http://localhost:8011/mcp,http://localhost:8012/mcp,http://localhost:8013/mcp,http://localhost:8014/mcp
python headless.py "What's the main fleet-wide anomaly?"       # headless
uvicorn web:app --port 8002                                    # UI → :8002
```

---

## Project structure

```
.
├── asap_api/       FastAPI — orchestration: desired/actual state, reconcile via Redbend (ASAP)
├── cvc_api/        FastAPI — car gateway / telemetry (CVC)
├── redbend_api/    FastAPI — OTA execution: FOTA/SOTA + service-activation campaigns (Redbend)
├── datalake_api/   FastAPI — analytics: datasets, usage, trends, anomalies (Data Lake)
├── asap_mcp/       MCP server wrapping ASAP as 6 orchestration tools
├── cvc_mcp/        MCP server wrapping CVC as 4 read tools
├── redbend_mcp/    MCP server wrapping Redbend as 4 OTA tools
├── datalake_mcp/   MCP server wrapping the Data Lake as 5 analytics tools
├── agent/          Multi-MCP agent core + headless.py · web.py · agent.py
├── tests/          End-to-end test suite (pytest)
├── docs/           Tutorials (start with 06)
├── api/  mcp_server/   Original single-API example (products/sales) — kept for reference
├── k8s/            Kubernetes manifests
├── docker-compose.yml
└── .env.example
```

## Documentation

| Doc | Contents |
| --- | --- |
| [`docs/06-agentic-ai-tutorial.md`](docs/06-agentic-ai-tutorial.md) | **Start here** — what an agent is, the agentic loop, the four-system scenario (CVC/ASAP/Redbend/Data Lake), desired vs actual state, a full run, how to extend |
| [`docs/01-mcp-tutorial.md`](docs/01-mcp-tutorial.md) | MCP concepts, tool design, transports, LLM bridging |
| [`docs/05-agent-architecture.md`](docs/05-agent-architecture.md) | Agents in general: the loop, how to build one |
| [`docs/04-agents-and-tests.md`](docs/04-agents-and-tests.md) | The agents, the `mock` provider, the e2e tests |
| [`docs/02-docker.md`](docs/02-docker.md) · [`docs/03-kubernetes.md`](docs/03-kubernetes.md) | Containerisation & deployment |

> Docs 01–05 were written for the original single-API products/sales example
> (still in `api/` + `mcp_server/`). Every MCP and agent concept they explain
> applies identically here — only the domain and the tool names differ.

---

## Troubleshooting

| Symptom | Cause / Fix |
| --- | --- |
| Web chat shows **"Insufficient API credits"** | The LLM account has no credits. Add credits, or set `LLM_PROVIDER=mock` and `docker compose up -d`. |
| Web chat shows **"Invalid or missing API key"** | Key wrong or not matching `LLM_PROVIDER`; fix `.env`, then `docker compose up -d`. |
| `bind: address already in use` | A host port is taken. Override it in `.env` (e.g. `WEB_PORT`, `ASAP_API_PORT`). |
| Want to watch tool calls live | `docker compose logs -f agent-web` |

## Notes

- Secrets live only in `.env` (git-ignored) / Kubernetes Secrets — never in images.
- The four APIs keep state **in memory** (fine for a PoC); restart resets it. The
  fleet, catalogue, software inventory and analytics are seeded deterministically
  so demos are reproducible.
- Verified end-to-end on this machine: four APIs + four MCP servers boot, the
  agent merges **19 tools** across servers, ASAP reconciles desired→actual via a
  Redbend campaign, the Data Lake answers fleet-wide questions, and `pytest` →
  **15 passed, 1 skipped** (16 with a live key).

## License

[MIT](LICENSE) © Walid Abdaoui
