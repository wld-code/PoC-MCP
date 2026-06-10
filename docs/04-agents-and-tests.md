# Agents & End-to-End Tests

This PoC ships **two agents** over the same core (`mcp_client.py` +
`providers.py`), plus an optional interactive CLI:

| File | Kind | Use it for |
| --- | --- | --- |
| `agent/headless.py` | **Headless agent** | Automation, CI, cron, Kubernetes Jobs, shell pipelines |
| `agent/web.py` | **UI agent** | A browser chat front-end |
| `agent/agent.py` | Interactive CLI | A terminal REPL (bonus) |

All three discover MCP tools at startup and run the same provider tool loop —
only the *interface* differs.

---

## The headless agent

Query in, answer out, then exit. No prompts, no server.

```bash
cd agent
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_/MISTRAL_; or LLM_PROVIDER=mock
export MCP_SERVER_URL=http://localhost:8001/mcp

python headless.py "What are the top 3 products by revenue?"
python headless.py --json "Total revenue across all sales?"   # structured output
QUESTION="Which products cost over 400?" python headless.py    # via env var
```

`--json` emits the answer **and** the tool calls the model made — handy for
piping into other tools or asserting in CI:

```json
{
  "provider": "mock",
  "question": "Top 3 products by revenue?",
  "answer": "[mock:top_products] {... Pro Notebook ...}",
  "tool_calls": [{"name": "top_products", "arguments": {"limit": 3}}]
}
```

Exit code is `0` on success, non-zero on failure — so it composes in scripts.
In Kubernetes it runs as a **Job** (`k8s/agent-job.yaml`).

## The UI agent

A FastAPI web app with a single-page chat UI. It keeps one MCP connection open
and a per-browser-session conversation in memory.

```bash
cd agent
export ANTHROPIC_API_KEY=sk-ant-...        # or LLM_PROVIDER=mock
export MCP_SERVER_URL=http://localhost:8001/mcp

uvicorn web:app --host 0.0.0.0 --port 8002
# open http://localhost:8002
```

Endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | The chat UI |
| `GET` | `/health` | Liveness/readiness |
| `GET` | `/api/tools` | List discovered MCP tool names |
| `POST` | `/api/chat` | `{session_id, message}` → `{answer, tool_calls}` |

The UI shows which tools were called under each answer (`🔧 called: …`), so you
can see the agent reaching through MCP into the API. In Kubernetes it runs as a
**Deployment + Service** (`k8s/agent-web.yaml`).

## The `mock` provider (no key, no cost)

Set `LLM_PROVIDER=mock` to run either agent with **no LLM and no API key**. The
mock routes a question to one MCP tool by keyword and returns its output. It
exercises the *entire* path — agent → MCP client → MCP server → API → SQLite —
which is exactly what the e2e tests use, so CI runs free and offline.

---

## End-to-end tests

`tests/test_e2e.py` boots the **real** API and MCP server as subprocesses
(fresh temp SQLite), then drives every layer. With the `mock` provider it needs
no credentials.

### Run them

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r api/requirements.txt -r mcp_server/requirements.txt \
            -r agent/requirements.txt -r tests/requirements.txt

pytest -v
```

Expected:

```
tests/test_e2e.py::test_api_health            PASSED
tests/test_e2e.py::test_api_seeded_data       PASSED
tests/test_e2e.py::test_mcp_tools_and_call    PASSED
tests/test_e2e.py::test_headless_agent        PASSED
tests/test_e2e.py::test_web_agent             PASSED
tests/test_e2e.py::test_headless_agent_live   SKIPPED   (no LLM key)
5 passed, 1 skipped
```

### What each test proves

| Test | Layer verified |
| --- | --- |
| `test_api_health` | FastAPI is up |
| `test_api_seeded_data` | SQLite auto-seeded; aggregates compute correctly |
| `test_mcp_tools_and_call` | MCP discovery + a tool call (client → server → API) |
| `test_headless_agent` | Headless agent runs the full loop and reports its tool calls |
| `test_web_agent` | UI agent serves the page and answers over `/api/chat` |
| `test_headless_agent_live` | A real LLM call — **only runs if a provider key is set** |

### Running the live test

To exercise a real model end-to-end, export a key before `pytest`:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY / MISTRAL_API_KEY
pytest -v -k live
```

It asks "total revenue across all sales?" and asserts the model actually called
the `sales_summary` tool — i.e. it went through MCP rather than guessing.

### In CI

The mock-based tests are the CI default (fast, free, deterministic). A typical
GitHub Actions step:

```yaml
- run: pip install -r api/requirements.txt -r mcp_server/requirements.txt \
                   -r agent/requirements.txt -r tests/requirements.txt
- run: pytest -v          # mock provider → no secrets needed
```
