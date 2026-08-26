# MCP PoC — Agentic AI over four APIs · Connected-Vehicle scenario

[![CI](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

A complete, runnable **tutorial** for the **Model Context Protocol (MCP)** and
**agentic AI**. An LLM agent reads live vehicle data and orchestrates service
activations by calling **MCP tools** across **four backends** — never touching
the APIs or databases directly.

> 📚 **Start here:** [`docs/06-agentic-ai-tutorial.md`](docs/06-agentic-ai-tutorial.md)
> — a step-by-step walkthrough of what an agent is, what makes it *agentic*, and
> how it uses the four MCP servers below.

## The scenario

Four fake backends model a car maker's connected-services platform:

| System | What it is | Does | API | MCP server |
| --- | --- | --- | --- | --- |
| **CVC** | *Connected Vehicle Cloud* — the **car gateway** | **Reads** live telemetry: fleet, online status, charge, location, diagnostics | `cvc_api/` `:8022` | `cvc_mcp/` `:8012` |
| **ASAP** | *Activation Service & Aggregation Platform* | **Orchestrates** activation: owns each service's **desired vs actual** state and reconciles by dispatching to Redbend | `asap_api/` `:8021` | `asap_mcp/` `:8011` |
| **Redbend** | OTA update platform | **Executes** on the vehicle: FOTA (firmware), SOTA (software) and **service-activation** campaigns | `redbend_api/` `:8023` | `redbend_mcp/` `:8013` |
| **Data Lake** | analytics warehouse | **Aggregates** massive connected-services data across the whole installed base: datasets, usage, top apps, trends, anomalies | `datalake_api/` `:8024` | `datalake_mcp/` `:8014` |

A **backend API** connects to **all four** MCP servers, sees one merged toolbox, and
reasons across them — e.g. *"is the car online (CVC) before I request activation
(ASAP), executed by an OTA campaign (Redbend)?"*, or *"what's the fleet-wide
anomaly (Data Lake) and which car does it trace back to?"* A separate **React
frontend** and any **headless caller** (CLI, cron, CI) drive that same backend
over an authenticated REST API — see [Application architecture](#application-architecture) below.

```
            ┌──────────────────────────────────────────────────────────────────┐
            │                          BACKEND API (agent)                     │
            │                one merged toolbox of 19 MCP tools                │
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
- **Multi-MCP agent** — connects to **all four** servers and routes each tool
  call to its owner (`agent/core/mcp_client.py`); the backend uses a
  `DynamicMCPManager` so servers can be added/removed at runtime.
- **Provider-agnostic** — Claude (default), OpenAI, OpenRouter, or Mistral via one
  env var (or switch live in the UI); a `mock` provider runs the full pipeline
  with **no API key and no cost**.
- **Decoupled frontend/backend** — a FastAPI JSON API (`agent/backend/`) and a
  separately built React SPA (`frontend/`), talking over REST/CORS — no server-
  rendered HTML.
- **Auth, roles, and an audit trail** — JWT login, three roles (`admin` /
  `operator` / `viewer`), every mutating action recorded — see
  [Application architecture](#application-architecture).
- **Persistent scheduler** — automations are APScheduler jobs backed by
  Postgres; they survive a backend restart, unlike a naive in-memory timer.
- **Live CRUD in the UI** — manage **MCP servers**, **LLM configs**, and **Deep
  Dive process flows** (create/edit/delete/reorder steps, any flow including
  the built-ins) from the browser, no restart.
- **Three ways to run the agent** — `headless.py` (automation/CI/Jobs, no
  server or DB needed), the backend's `POST /api/agents/run` (headless over
  HTTP, authenticated), and the React web app (interactive, see below).
- **End-to-end tests** that boot all nine real services (APIs, MCP servers,
  backend) and drive every layer, including auth/RBAC and the scheduler
  actually firing a persisted job.

---

## Quick start

**Requirements:** Docker + Docker Compose. An LLM key is optional (`mock` is free).

```bash
cp .env.example .env
# required: set JWT_SECRET_KEY and FERNET_KEY in .env — see Configuration below
# (ADMIN_PASSWORD has a placeholder default; fine for a quick spin, change it for anything real)
docker compose up -d --build     # 4 APIs + 4 MCP servers + postgres + backend + frontend
```

Open the **web app** → http://localhost:3000 and sign in with `ADMIN_EMAIL` /
`ADMIN_PASSWORD` from `.env` (defaults: `admin@example.com` / `change-me-now` —
**change this before any real use**). From **Mission Control**, try:

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
| `WEB_PORT` | Host port for the backend API | `8002` |
| `FRONTEND_PORT` | Host port for the React web app — **open this one** | `3000` |
| `MCP_SERVER_URLS` | Comma-separated MCP endpoints the backend connects to | all four local servers |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT` | Backend database | `aiops` / `aiops` / `aiops` / `5432` |
| `JWT_SECRET_KEY` | **Required.** Signs access/refresh tokens — generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` | — |
| `FERNET_KEY` | **Required.** Encrypts stored LLM provider API keys at rest — generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | — |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | The one admin account seeded on first boot | `admin@example.com` / `change-me-now` |
| `CORS_ORIGINS` | Origins allowed to call the backend (comma-separated) | `http://localhost:3000` |
| `COOKIE_SECURE` | `Secure` flag on the refresh-token cookie. Keep `false` for this plain-HTTP stack; set `true` once deployed behind HTTPS | `false` |

Provider defaults: `claude` → `claude-opus-4-8`, `openai` → `gpt-4o`,
`openrouter` → `openai/gpt-4o-mini` (any tool-capable OpenRouter model id),
`mistral` → `mistral-large-latest`, `mock` → no LLM. The MCP tool layer is
identical across providers; only the function-calling format differs
(`agent/core/providers.py`). OpenRouter reuses the OpenAI Chat Completions
format, so one `OPENROUTER_API_KEY` unlocks hundreds of models behind the same
agent loop.

---

## Application architecture

The agent core (`agent/core/` — `mcp_client.py`, `mcp_manager.py`,
`providers.py`, `servers.py`) is shared by three separate ways to run it:

| Entry point | File | For |
| --- | --- | --- |
| **Headless CLI** | `agent/headless.py` | Automation, CI, cron, K8s Jobs, pipelines. Query in → answer out → exit. `--json` emits the answer + the tool calls made. **No database, no server** — this stays exactly as simple as before. |
| **Backend API** | `agent/backend/` | A FastAPI JSON API — auth, chat, headless-over-HTTP, persistent scheduling, CRUD. Everything below lives here. |
| **REPL** | `agent/agent.py` | Interactive terminal session. |

The **frontend** (`frontend/`, a React + Vite SPA) is a separate project that
talks to the backend purely over REST — it never touches MCP or an LLM SDK
directly, and the backend never renders HTML. In production `frontend/`'s
Dockerfile builds it and serves it via nginx, which also reverse-proxies
`/api/*` to the backend; in dev, Vite does the same proxying (`vite.config.ts`).

### Auth, roles, and security

- **JWT auth** — `POST /api/auth/login` (email + password) returns a
  short-lived (15 min) access token; a longer-lived (7 day) refresh token is
  set as an **httpOnly** cookie, invisible to JS. The SPA keeps the access
  token **in memory only** (never `localStorage`) and transparently refreshes
  it via the cookie on a 401 (`frontend/src/api/client.ts`).
- **Three roles**, checked on every route (`agent/backend/security.py`):
  `viewer` (read-only), `operator` (chat, run/schedule the agent, manage
  process flows), `admin` (+ manage users, MCP servers, and LLM configs/keys).
  See the RBAC matrix in each router for the exact split.
- **Passwords** hashed with Argon2id (`argon2-cffi`); **LLM provider API
  keys** encrypted at rest with Fernet (`agent/backend/crypto.py`) — never
  returned by the API, only a `has_key` boolean.
- **Rate limiting** on `/api/auth/login` (5/min) against brute-forcing.
- **CORS** locked to `CORS_ORIGINS` (never `*`); security headers
  (`X-Content-Type-Options`, `X-Frame-Options`, …) on every response.
- **Audit trail** — every mutating action (login, CRUD, a schedule firing) is
  recorded with who did it (`GET /api/audit`).

One admin user is seeded on first boot from `ADMIN_EMAIL`/`ADMIN_PASSWORD`;
create everyone else from the **Users** page (admin-only).

### Persistent scheduling

Automations used to be a per-request `asyncio.sleep` loop — gone on restart.
They're now [APScheduler](https://apscheduler.readthedocs.io/) jobs backed by
Postgres (`agent/backend/services/scheduler.py`): create one with a cron
expression or a plain interval, and it survives a backend restart because the
job itself — not just its description — is persisted. `GET /api/schedules`
reads `next_run_time` live off the APScheduler job (not a DB column) so the
UI can show a countdown; it's `null` while paused. Manage them from the
**Automations** page or `POST/GET/DELETE /api/schedules`,
`POST /api/schedules/{id}/pause|resume`, `GET /api/schedules/{id}/runs`.

### The web app — "AI Control Tower"

A sidebar SPA (no internal MCP terminology surfaced to the user):

- **Mission Control** — the interactive agent. Ask a question; get an *Executive
  Answer* plus an **Evidence** panel listing the tools the agent used.
- **Automations** — create/pause/resume/delete persistent schedules (cron or
  interval); a live countdown to the next run, and each schedule's run history
  (answer + evidence) inline, updating automatically.
- **Deep Dive** — understand each **core process flow** end to end (Bootstrap,
  Pairing, Service Activation, FOTA/SOTA, Fleet Analytics): the ordered steps,
  which system/tool each uses and *why* — then **run the flow for a VIN** and see
  each tool's real result step by step, optionally with an **LLM explanation**
  of what happened next to the results (*Run flow with AI*).
- **Insights** — **business insights** auto-derived from the data lake, plus a
  one-click LLM-generated executive brief.
- **Data Sources** *(admin)* — **CRUD** the connected MCP servers (add/edit/remove
  at runtime; tools join the toolbox instantly).
- **AI Models** — everyone can see the registry (name/kind/model, never the
  key); **admin** can add/edit/delete and set the default.
- **Process Flows** — a visual editor (operator+) for the Deep Dive catalogue:
  create/edit/delete any flow (including the built-ins), reorder steps on a
  color-coded timeline, and pick tools from the live MCP registry with
  inline validation.
- **Audit Trail** *(operator+)* — every action and every agent run, attributed.
- **Users** *(admin)* — create accounts, assign roles, disable/delete.

Full route list: `GET /api/info`, `GET /api/tools`, `POST /api/tool` (run one
tool — used by Deep Dive), `POST /api/chat`, `POST /api/agents/run` +
`GET /api/agents/runs` (headless-over-HTTP), `GET|POST /api/schedules` +
`POST /api/schedules/{id}/pause|resume` + `DELETE /api/schedules/{id}` +
`GET /api/schedules/{id}/runs`,
`GET|POST|PUT|DELETE /api/mcp/servers[/{id}]` (admin),
`GET|POST|PUT|DELETE /api/llms[/{id}]` + `PUT /api/llms/{id}/default` (read: any
role; write: admin), `GET|POST|PUT|DELETE /api/flows[/{id}]` +
`POST /api/flows/reset`, `GET /api/audit` (operator+),
`GET|POST|PUT|DELETE /api/users[/{id}]` (admin), `POST /api/auth/login|refresh|logout`
+ `GET /api/auth/me`.

> Everything above is **persisted in Postgres** — MCP servers, LLM configs
> (keys encrypted), process flows, schedules, run history, users, audit log.
> Nothing is lost on restart.

---

## Testing

End-to-end tests boot the **real services** (four APIs + four MCP servers +
the backend) and drive every layer — APIs → MCP (merged) → headless agent →
backend (auth/RBAC, chat, scheduler) — using the `mock` provider and a
throwaway SQLite DB per backend instance: **no key, no cost, no Postgres,
deterministic**.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r asap_api/requirements.txt -r cvc_api/requirements.txt \
            -r redbend_api/requirements.txt -r datalake_api/requirements.txt \
            -r asap_mcp/requirements.txt -r cvc_mcp/requirements.txt \
            -r redbend_mcp/requirements.txt -r datalake_mcp/requirements.txt \
            -r agent/requirements.txt -r tests/requirements.txt
pytest -v
# → 17 passed, 1 skipped  (the skip is the optional live-LLM test; set a key to run it)
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
| `test_headless_agent` / `test_headless_agent_cross_server` | the headless CLI runs the loop and routes calls across servers, with no server/DB |
| `test_backend_auth_and_rbac` | login success/failure, 401 with no token, an admin-created viewer is **403'd** on operator/admin routes, refresh-cookie flow |
| `test_backend_chat` | the backend serves, lists tools, and answers a chat message with real tool calls |
| `test_backend_info_flows_and_agent_run` | `/api/info`, direct tool calls, Deep Dive flow CRUD, headless-over-HTTP run + audit log entry |
| `test_backend_mcp_crud` | add an MCP server at runtime → its tools appear → remove it; unreachable URL errors cleanly |
| `test_backend_llm_crud` | create an LLM config, use it in a run, edit, set default, delete |
| `test_backend_schedules_persist_and_fire` | create a persistent schedule → **it actually fires** (polled up to 30s) → writes to run history → pause → delete |
| `test_headless_agent_live` | *(optional)* a real LLM resolves an owner to a VIN and reads across servers |

The frontend has its own check — `cd frontend && npm ci && npm run build`
(typechecks + bundles; run automatically in CI).

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

# terminal 9 — headless CLI (no DB needed; set a key, or LLM_PROVIDER=mock)
cd agent && export MCP_SERVER_URLS=http://localhost:8011/mcp,http://localhost:8012/mcp,http://localhost:8013/mcp,http://localhost:8014/mcp
python headless.py "What's the main fleet-wide anomaly?"

# terminal 10 — the backend API (needs Postgres — or point DATABASE_URL at a
# local SQLite file for a quick spin: sqlite+aiosqlite:///./dev.db)
cd agent
export DATABASE_URL=postgresql+asyncpg://aiops:aiops@localhost:5432/aiops
export JWT_SECRET_KEY=dev-only-change-me FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=change-me-now
export CORS_ORIGINS=http://localhost:5173
uvicorn backend.main:app --port 8002        # tables auto-created for SQLite; run `alembic -c backend/alembic.ini upgrade head` for Postgres

# terminal 11 — the frontend (dev server, proxies /api -> :8002)
cd frontend && npm install && npm run dev   # UI → http://localhost:5173
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
├── agent/
│   ├── core/           shared MCP + provider core (mcp_client, mcp_manager, providers, servers)
│   ├── headless.py      one-shot CLI agent — no DB, no server
│   ├── agent.py          interactive REPL
│   └── backend/          FastAPI API: auth/RBAC, chat, scheduler, all CRUD (see below)
│       ├── main.py, config.py, db.py, models.py, schemas.py, security.py, crypto.py
│       ├── routers/       auth, users, chat, agent_runs, schedules, mcp_servers, llms, flows, audit, system
│       ├── services/      agent_runner (shared tool-calling path), scheduler (APScheduler)
│       └── alembic/        DB migrations
├── frontend/       React + Vite + TypeScript SPA — decoupled from the backend, built separately
├── tests/          End-to-end test suite (pytest)
├── docs/           Tutorials (start with 06)
├── api/  mcp_server/   Original single-API example (products/sales) — kept for reference
├── k8s/            Kubernetes manifests (currently model the older api/+mcp_server/ pair —
│                   not yet updated for the 4-service + backend + frontend architecture)
├── docker-compose.yml
└── .env.example
```

## Documentation

| Doc | Contents |
| --- | --- |
| [`docs/06-agentic-ai-tutorial.md`](docs/06-agentic-ai-tutorial.md) | **Start here** — what an agent is, the agentic loop, the four-system scenario (CVC/ASAP/Redbend/Data Lake), desired vs actual state, a full run, how to extend |
| [`docs/07-dashboard-diagrams.md`](docs/07-dashboard-diagrams.md) | The functional schema, the technical architecture schema, and a sequence diagram for each of the 10 dashboard operations (auth, chat, automations, deep dive, insights, data sources, AI models, process flows, audit trail, users) |
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
| Web chat shows **"Insufficient API credits"** | The LLM account has no credits. Add credits, or pick the **Mock** LLM. |
| Web chat shows **"Invalid or missing API key"** | Key wrong; fix it in the **AI Models** tab (admin). |
| Backend refuses to start (`set a random JWT_SECRET_KEY...`) | `JWT_SECRET_KEY`, `FERNET_KEY`, or `ADMIN_PASSWORD` missing from `.env` — see Configuration. |
| Logged in, but every request 401s again after ~15 min | Expected — the access token is short-lived and refreshes silently via the cookie. If it doesn't, check `COOKIE_SECURE` matches how you're serving the app (must be `false` over plain HTTP). |
| `403 Forbidden` on a page/action | Your role doesn't allow it — see the RBAC matrix in [Application architecture](#application-architecture). An admin can change your role in **Users**. |
| `bind: address already in use` | A host port is taken. Override it in `.env` (e.g. `FRONTEND_PORT`, `WEB_PORT`, `ASAP_API_PORT`). |
| Want to watch tool calls live | `docker compose logs -f backend` |

## Notes

- Secrets live only in `.env` (git-ignored) / Kubernetes Secrets — never in images.
  LLM provider API keys are additionally encrypted at rest (Fernet) in Postgres.
- The four fake APIs keep state **in memory** (fine for a PoC); restart resets
  it. The fleet, catalogue, software inventory and analytics are seeded
  deterministically so demos are reproducible. The **backend**'s own state
  (users, MCP/LLM registries, flows, schedules, run history, audit log) is
  persisted in **Postgres** and survives a restart.
- Verified end-to-end on this machine: four APIs + four MCP servers + the
  backend boot, the agent merges **19 tools** across servers, ASAP reconciles
  desired→actual via a Redbend campaign, the Data Lake answers fleet-wide
  questions, a persisted schedule actually fires and is recorded in the audit
  trail, and `pytest` → **17 passed, 1 skipped** (18 with a live key). The
  frontend builds clean (`npm run build`, zero TypeScript errors).

## License

[MIT](LICENSE) © Walid Abdaoui
