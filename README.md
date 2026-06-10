# MCP PoC — LLM Agent · MCP Server · FastAPI · SQLite

[![CI](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml/badge.svg)](https://github.com/wld-code/PoC-MCP/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

A complete, runnable proof-of-concept of the **Model Context Protocol (MCP)**: an
LLM agent answers questions over data that lives behind a real HTTP API, by
calling **MCP tools** instead of touching the backend directly.

`Agent (LLM)` → `MCP server` → `FastAPI` → `SQLite` — each piece is its own
container, fully Dockerized, with a Kubernetes path and end-to-end tests.

```
┌──────────┐   MCP (Streamable HTTP)   ┌──────────────┐   HTTP   ┌──────────┐   SQL   ┌────────┐
│  Agent   │ ────────────────────────▶ │  MCP server  │ ───────▶ │  FastAPI │ ──────▶ │ SQLite │
│  (LLM)   │ ◀──────────────────────── │  (FastMCP)   │ ◀─────── │   API    │ ◀────── │  .db   │
└──────────┘     tools / results       └──────────────┘   JSON   └──────────┘  rows   └────────┘
```

## Why MCP

The agent never queries the API or the database. It only sees **tools** the MCP
server advertises (self-describing: name + description + JSON schema). You can
swap the backend, add authentication, or add tools **without changing the agent**.
The MCP server is a thin adapter; the FastAPI service stays the single source of
truth.

## Features

- **Provider-agnostic agent** — Claude (default), OpenAI, or Mistral via one env
  var; a `mock` provider runs the full pipeline with **no API key and no cost**.
- **Two agents** — `headless` (automation/CI/Jobs) and a modern **web chat UI**.
- **Official MCP Python SDK (FastMCP)** over Streamable HTTP — production-friendly.
- **Dockerized** end-to-end + **Kubernetes** manifests + step-by-step tutorials.
- **End-to-end tests** that boot the real services and exercise every layer.

---

## Quick start

**Requirements:** Docker + Docker Compose. An LLM API key is optional (use
`LLM_PROVIDER=mock` to run for free).

```bash
cp .env.example .env          # then edit .env (see Configuration below)
docker compose up -d --build  # builds & starts api, mcp-server, agent-web
```

Open the **web chat** → http://localhost:8002

Run the **headless agent**:

```bash
docker compose run --rm agent python headless.py "Top 3 products by revenue?"
```

Hit the **raw API** directly:

```bash
curl localhost:8000/sales/summary
open  http://localhost:8000/docs        # interactive OpenAPI docs
```

> **No key? No problem.** Set `LLM_PROVIDER=mock` in `.env` — the agents still
> call the real MCP tools and return live data; only the natural-language
> phrasing is skipped. This is exactly what the test suite uses.

> **Ports already in use?** The defaults are `8000/8001/8002`. Override the host
> ports in `.env` with `API_PORT`, `MCP_PORT`, `WEB_PORT` (containers keep using
> 8000/8001/8002 internally).

---

## Configuration

`.env` (copied from `.env.example`):

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_PROVIDER` | `claude` · `openai` · `mistral` · `mock` | `claude` |
| `LLM_MODEL` | Override the model id (optional) | provider default |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `MISTRAL_API_KEY` | Key for the chosen provider | — |
| `API_PORT` / `MCP_PORT` / `WEB_PORT` | Host port overrides | `8000` / `8001` / `8002` |

Provider defaults:

| `LLM_PROVIDER` | Default model | Key |
| --- | --- | --- |
| `claude` | `claude-opus-4-8` | `ANTHROPIC_API_KEY` |
| `openai` | `gpt-4o` | `OPENAI_API_KEY` |
| `mistral` | `mistral-large-latest` | `MISTRAL_API_KEY` |
| `mock` | — (no LLM) | none |

The MCP tool layer is identical across providers; only the per-provider
function-calling format differs (`agent/providers.py`).

---

## The two agents

| Agent | File | For |
| --- | --- | --- |
| **Headless** | `agent/headless.py` | Automation, CI, cron, Kubernetes Jobs, pipelines. Query in → answer out → exit. `--json` emits the answer + the tool calls made. |
| **Web UI** | `agent/web.py` | A browser chat: Markdown answers, MCP tool-call chips, clear error messages. Served at `/`; chat at `POST /api/chat`. |

(Plus `agent/agent.py`, an interactive terminal REPL.)

```bash
# Headless — one-shot
docker compose run --rm agent python headless.py --json "Products under $50"

# Web UI — already running from `docker compose up`
open http://localhost:8002
```

---

## Testing

End-to-end tests boot the **real** API + MCP server and drive every layer
(API → MCP → headless agent → web agent) using the `mock` provider — **no key,
no cost, deterministic**.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r mcp_server/requirements.txt \
            -r agent/requirements.txt -r tests/requirements.txt
pytest -v
# → 5 passed, 1 skipped   (the skip is the optional live-LLM test; set a key to run it)
```

---

## Run without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r mcp_server/requirements.txt -r agent/requirements.txt

# terminal 1 — API (auto-seeds SQLite)
cd api && DB_PATH=/tmp/poc.db uvicorn main:app --port 8000
# terminal 2 — MCP server
cd mcp_server && API_BASE_URL=http://localhost:8000 python server.py
# terminal 3 — an agent (set a key, or LLM_PROVIDER=mock)
cd agent && export MCP_SERVER_URL=http://localhost:8001/mcp
python headless.py "Which products cost more than 400?"   # headless
uvicorn web:app --port 8002                                # UI → :8002
```

---

## Project structure

```
.
├── api/            FastAPI + SQLite data service
├── mcp_server/     MCP server (FastMCP) wrapping the API as tools
├── agent/          Provider-agnostic core + headless.py · web.py · agent.py
├── tests/          End-to-end test suite (pytest)
├── k8s/            Kubernetes manifests (Deployments, Services, PVC, Job, Secret)
├── docs/           Tutorials
├── docker-compose.yml
└── .env.example
```

## Documentation

| Doc | Contents |
| --- | --- |
| [`docs/01-mcp-tutorial.md`](docs/01-mcp-tutorial.md) | MCP concepts, tool design, transports, LLM bridging |
| [`docs/02-docker.md`](docs/02-docker.md) | Images, Compose, prod hardening |
| [`docs/03-kubernetes.md`](docs/03-kubernetes.md) | Deploy the stack to Kubernetes, step by step |
| [`docs/04-agents-and-tests.md`](docs/04-agents-and-tests.md) | The two agents, the `mock` provider, the e2e tests |
| [`docs/05-agent-architecture.md`](docs/05-agent-architecture.md) | What an agent is, the agentic loop, how to build one, the agents in this repo |

---

## Troubleshooting

| Symptom | Cause / Fix |
| --- | --- |
| Web chat shows **"Insufficient API credits"** | The LLM account has no credits. Add credits, or set `LLM_PROVIDER=mock` and `docker compose up -d`. |
| Web chat shows **"Invalid or missing API key"** | Key wrong or not matching `LLM_PROVIDER`; fix `.env`, then `docker compose up -d`. |
| `bind: address already in use` | Ports `8000`/`8002` taken. Set `API_PORT` / `WEB_PORT` in `.env`. |
| Want to watch tool calls live | `docker compose logs -f agent-web` |

## Common commands

```bash
docker compose ps                  # status
docker compose logs -f agent-web   # follow the UI agent
docker compose up -d --build       # rebuild after a change
docker compose down                # stop (keep data);  add -v to wipe the DB volume
```

## Notes

- Secrets live only in `.env` (git-ignored) / Kubernetes Secrets — never in images.
- SQLite is single-writer (fine for a PoC); swap for Postgres for real load — the
  MCP server and agents don't change.
- Verified end-to-end on this machine: full data path returns live data, both
  agents complete the tool loop, `pytest` → **5 passed, 1 skipped**.

## License

[MIT](LICENSE) © Walid Abdaoui
