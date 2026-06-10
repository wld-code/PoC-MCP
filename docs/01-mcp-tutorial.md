# MCP Tutorial — building the server and the agent

This walks through *what MCP is*, how the MCP server in this repo is built, and
how the agent uses it. By the end you'll understand every moving part.

## 1. What is MCP?

The **Model Context Protocol** is an open standard for connecting LLMs to
external capabilities. Instead of hand-wiring each model to each data source,
you expose your capabilities once as an **MCP server**, and any MCP-aware client
(an agent, an IDE, a desktop app) can discover and call them.

An MCP server can expose three kinds of things:

- **Tools** — functions the model can call (this PoC uses these).
- **Resources** — readable data the model can pull in (like files).
- **Prompts** — reusable prompt templates.

A tool has a **name**, a **description**, and a JSON-Schema **input schema**.
The client fetches these, hands them to the LLM, and when the LLM decides to
call one, the client routes the call back to the server.

## 2. Transports: stdio vs Streamable HTTP

MCP servers can speak over different transports:

- **stdio** — the client launches the server as a subprocess and talks over
  stdin/stdout. Great for local desktop tools; awkward to deploy as a shared
  network service.
- **Streamable HTTP** — the server is a normal HTTP service with an MCP
  endpoint (here, `/mcp`). This is what we use: each component is its own
  container/pod, and the agent connects over the network. This is the
  production-friendly choice and the reason the Docker/K8s story is clean.

## 3. The MCP server (`mcp_server/server.py`)

We use the **official MCP Python SDK** and its `FastMCP` helper. Each tool is
just a decorated Python function — FastMCP turns the signature + docstring into
the tool's input schema and description automatically.

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-data", host="0.0.0.0", port=8001)

@mcp.tool()
def top_products(limit: int = 5) -> list[dict]:
    """Get the top `limit` products by revenue, with units sold and revenue."""
    return _get("/sales/top", {"limit": limit})

if __name__ == "__main__":
    mcp.run(transport="streamable-http")   # serves MCP at http://0.0.0.0:8001/mcp
```

Key design points:

- **The server is a thin adapter.** Every tool forwards to the FastAPI backend
  over HTTP (`_get(...)`). The API stays the source of truth; the MCP server
  adds no business logic. Swapping SQLite for Postgres later changes nothing
  here.
- **Docstrings matter.** The model reads the tool description to decide when to
  call it. Write them for the model: say *when* to use the tool, not just what
  it does.
- **Type hints become the schema.** `limit: int = 5` tells the model the
  argument is an optional integer. `category: str | None = None` becomes an
  optional string.

> Version note: this repo pins `mcp==1.27.2`. FastMCP `1.12.0` had a bug that
> crashed on `X | None` parameter annotations — pin a recent version.

The five tools exposed:

| Tool | Backend endpoint | Purpose |
| --- | --- | --- |
| `list_products` | `GET /products` | List/filter products |
| `get_product` | `GET /products/{id}` | One product by id |
| `search_products` | `GET /products/search` | Name + price-range search |
| `sales_summary` | `GET /sales/summary` | Aggregate totals |
| `top_products` | `GET /sales/top` | Top sellers by revenue |

## 4. The agent as an MCP client (`agent/mcp_client.py`)

The agent uses the SDK's Streamable HTTP client. It opens one session for the
whole chat so tool calls are cheap:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

read, write, _ = await stack.enter_async_context(streamablehttp_client(url))
session = await stack.enter_async_context(ClientSession(read, write))
await session.initialize()

tools  = await session.list_tools()                 # discover
result = await session.call_tool("top_products", {"limit": 3})   # invoke
```

`list_tools()` gives us the schemas; `call_tool()` runs one and returns content
blocks, which we flatten to text.

## 5. Bridging MCP tools to the LLM (`agent/providers.py`)

This is where "provider-agnostic" lives. MCP gives us a neutral tool list:

```python
{"name": ..., "description": ..., "input_schema": {...JSON Schema...}}
```

Each provider converts that to its own function-calling format and runs a tool
loop:

- **Claude** — the MCP schema *is* the Anthropic tool shape (`name`,
  `description`, `input_schema`). The loop: send messages → if
  `stop_reason == "tool_use"`, run the tool, append a `tool_result`, repeat →
  else return the text. We use `claude-opus-4-8` with adaptive thinking.
- **OpenAI** — wrap each tool as `{"type": "function", "function": {...,
  "parameters": input_schema}}`. Loop on `message.tool_calls`, append
  `role: "tool"` results.
- **Mistral** — same function-calling shape as OpenAI, via the Mistral SDK.

The agent loop itself (`agent/agent.py`) is tiny: discover tools, then for each
user message call `provider.send(text, tools, call_tool)` where `call_tool`
routes through the MCP client. A `MAX_TOOL_ROUNDS` cap prevents runaway loops.

## 6. The flow of one question

> *"What are the top 3 products by revenue?"*

1. Agent sends the question + tool schemas to the LLM.
2. LLM replies: "call `top_products(limit=3)`".
3. Agent (MCP client) → MCP server → `GET /sales/top?limit=3` → SQLite.
4. JSON rows flow back up; agent feeds them to the LLM as a tool result.
5. LLM writes the final natural-language answer from the real numbers.

## 7. Extending it

- **Add a tool:** write one more `@mcp.tool()` function in `server.py`. The
  agent picks it up automatically on next connect — no agent code changes.
- **Add auth:** put an API gateway / token check in front of `/mcp`, or add a
  bearer-token check in the MCP server. Keep secrets out of tool inputs.
- **Add resources/prompts:** FastMCP supports `@mcp.resource()` and
  `@mcp.prompt()` the same way.

Next: [`02-docker.md`](02-docker.md).
