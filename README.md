# MCP PoC — LLM Agent → MCP Server → API → SQLite

A small but complete proof-of-concept showing how a **Model Context Protocol
(MCP)** server lets an LLM agent answer questions over data that lives behind a
real HTTP API.

```
┌──────────┐   MCP (Streamable HTTP)   ┌──────────────┐   HTTP   ┌──────────┐   SQL   ┌────────┐
│  Agent   │ ────────────────────────▶ │  MCP server  │ ───────▶ │  FastAPI │ ──────▶ │ SQLite │
│ (LLM)    │ ◀──────────────────────── │  (FastMCP)   │ ◀─────── │   API    │ ◀────── │  .db   │
└──────────┘     tools / results       └──────────────┘   JSON   └──────────┘  rows   └────────┘
```

- **`api/`** — FastAPI service serving random product & sales data from SQLite.
- **`mcp_server/`** — MCP server (official Python SDK / FastMCP) that exposes the
  API's endpoints as MCP **tools** over Streamable HTTP.
- **`agent/`** — a provider-agnostic LLM agent (Claude by default; OpenAI or
  Mistral via one env var) that connects to the MCP server, discovers its
  tools, and uses them to answer your questions. It ships in **two forms**:
  - **headless** (`headless.py`) — query in, answer out; for automation/CI/Jobs.
  - **UI** (`web.py`) — a browser chat front-end.
  - (plus `agent.py`, an interactive terminal REPL).

Everything runs in Docker, with a Kubernetes tutorial for production.

## Why this shape?

The agent never talks to the API or database directly. It only knows about
**tools** advertised by the MCP server. This is the whole point of MCP: the
model gets a standard, self-describing tool interface, and you can swap the
backend, add auth, or add new tools without touching the model code. The MCP
server is a thin adapter — it forwards each tool call to the FastAPI backend,
which remains the single source of truth.

---

## Quick start (Docker)

Prerequisites: Docker + Docker Compose, and an API key for your chosen LLM.

```bash
cp .env.example .env
# edit .env: set LLM_PROVIDER and the matching API key (e.g. ANTHROPIC_API_KEY)

# Build & start the API, MCP server, and the UI agent
docker compose up -d --build

# UI agent: open the browser chat
open http://localhost:8002

# Headless agent: one-shot question (uses the MCP server + API)
docker compose run --rm agent python headless.py "What are the top 3 products by revenue?"

# Interactive terminal REPL (bonus)
docker compose run --rm agent python agent.py
```

> Tip: run everything with **no API key and no cost** by setting
> `LLM_PROVIDER=mock` in `.env` — the agents still call the MCP tools and return
> real data, just without an LLM phrasing the answer. This is what the tests use.

You can also hit the API directly to see the raw data:

```bash
curl localhost:8000/sales/top?limit=3
curl localhost:8000/sales/summary
open http://localhost:8000/docs        # interactive OpenAPI docs
```

## Quick start (no Docker)

Three terminals, one virtualenv:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r mcp_server/requirements.txt -r agent/requirements.txt

# 1) API  (auto-seeds SQLite on first run)
cd api && DB_PATH=/tmp/poc.db uvicorn main:app --port 8000

# 2) MCP server
cd mcp_server && API_BASE_URL=http://localhost:8000 python server.py

# 3) An agent (pick one) — set ANTHROPIC_API_KEY, or LLM_PROVIDER=mock for no key
cd agent && export MCP_SERVER_URL=http://localhost:8001/mcp
python headless.py "Which products cost more than 400?"   # headless
uvicorn web:app --port 8002                                # UI → http://localhost:8002
```

---

## Choosing the LLM provider

The agent is provider-agnostic. Pick one with `LLM_PROVIDER` and supply the
matching key:

| `LLM_PROVIDER` | Default model           | Key env var         |
| -------------- | ----------------------- | ------------------- |
| `claude` (def) | `claude-opus-4-8`       | `ANTHROPIC_API_KEY` |
| `openai`       | `gpt-4o`                | `OPENAI_API_KEY`    |
| `mistral`      | `mistral-large-latest`  | `MISTRAL_API_KEY`   |

Override the model with `LLM_MODEL`. The MCP tool layer is identical across
providers — only the per-provider function-calling format differs, and that's
handled in `agent/providers.py`.

---

## Documentation

| Doc | What's in it |
| --- | --- |
| [`docs/01-mcp-tutorial.md`](docs/01-mcp-tutorial.md) | How the MCP server works, tool design, transports, and how the agent bridges MCP tools to each LLM |
| [`docs/02-docker.md`](docs/02-docker.md) | Building images, Docker Compose, running in "prod", hardening notes |
| [`docs/03-kubernetes.md`](docs/03-kubernetes.md) | Deploying the whole stack to Kubernetes step by step |
| [`docs/04-agents-and-tests.md`](docs/04-agents-and-tests.md) | The headless & UI agents, the `mock` provider, and the e2e tests |

## Testing

End-to-end tests boot the real API + MCP server and drive every layer
(API → MCP → headless agent → UI agent), using the `mock` provider so they need
**no API key and cost nothing**:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r mcp_server/requirements.txt \
            -r agent/requirements.txt -r tests/requirements.txt
pytest -v
# → 5 passed, 1 skipped   (the skipped one is a real-LLM test; set a key to run it)
```

See [`docs/04-agents-and-tests.md`](docs/04-agents-and-tests.md) for what each
test proves and how to run the live-LLM test.

## Repository layout

```
.
├── api/             FastAPI + SQLite data service
├── mcp_server/      MCP server (FastMCP) wrapping the API
├── agent/           Provider-agnostic agent core + headless.py + web.py + agent.py
├── tests/           End-to-end test suite (pytest)
├── k8s/             Kubernetes manifests
├── docs/            Tutorials
├── docker-compose.yml
└── .env.example
```

## Verified

Run end-to-end on this machine: the data path (Agent → MCP client → MCP server →
FastAPI → SQLite) returns live seeded data, and the **headless** and **UI**
agents both complete the full tool loop. `pytest` reports **5 passed, 1
skipped** (the skip is the optional real-LLM test). The LLM loop follows each
provider's documented tool-use pattern.
