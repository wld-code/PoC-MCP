# Agent Architecture — Concepts, How to Build One, and the Agents in This Repo

This document explains what an **LLM agent** is, the architecture behind one, how
to develop your own, and what you need to know to do it well. It then walks
through the concrete agents shipped in this repository so you can map theory to
code.

If you only want to *run* the agents, see [`04-agents-and-tests.md`](04-agents-and-tests.md).
This file is about *understanding and building* them.

---

## 1. What is an agent?

An **agent** is a loop that lets a language model *act*, not just talk. A plain
LLM call takes text in and gives text out. An agent gives the model a set of
**tools** (functions it can call), then runs a loop:

```
        ┌──────────────────────────────────────────────┐
        │                                                │
   user message ──▶  LLM  ──▶ wants a tool? ──no──▶ final answer
        ▲                         │ yes
        │                         ▼
        └──── tool result ◀── run the tool
```

The model decides *whether* to call a tool, *which* tool, and *with what
arguments*. The agent executes the tool, feeds the result back, and asks the
model again. This repeats until the model produces a final answer (or a safety
cap is hit). That decide→act→observe cycle is the whole idea.

**Why this matters:** the model can now read live data, call APIs, do math with
real numbers, and ground its answers in facts instead of guessing. In this PoC
the tools reach through MCP into a sales database, so the model answers questions
about *actual* data.

---

## 2. Anatomy of an agent

Every agent — regardless of provider — is built from the same parts:

| Part | Responsibility | In this repo |
| --- | --- | --- |
| **Interface** | How a human or system talks to the agent (CLI, HTTP, REPL) | `headless.py`, `web.py`, `agent.py` |
| **Tool source** | Where tools come from and how they're discovered | `mcp_client.py` (MCP) |
| **Provider adapter** | Translates the generic loop to one LLM's API | `providers.py` |
| **The loop** | Decide → call tool → observe → repeat → answer | inside each provider's `send()` |
| **System prompt** | Sets the agent's role, rules, and guardrails | `SYSTEM_PROMPT` in `providers.py` |
| **Conversation state** | Message history for multi-turn context | per-provider `self.messages` |

The art of agent design is keeping these **decoupled**. The interface shouldn't
know which LLM you use; the loop shouldn't know whether tools come from MCP, a
local Python dict, or a remote API. This repo enforces that split with a single
abstract base class (see §6).

---

## 3. The agentic loop in detail

This is the heart of any agent. Pseudocode:

```python
messages = [user_question]
for _ in range(MAX_ROUNDS):                 # 1. safety cap — never loop forever
    response = llm.generate(messages, tools) # 2. ask the model, with tools available
    messages.append(response)

    if response has no tool calls:           # 3. model is done thinking
        return response.text                 #    → final answer

    for call in response.tool_calls:         # 4. model wants to act
        result = run_tool(call.name, call.args)
        messages.append(tool_result(result)) # 5. feed observations back
# loop again with the new observations
```

Five things every robust loop needs:

1. **A round cap** (`MAX_TOOL_ROUNDS = 10` here). Without it, a confused model can
   loop forever and burn money. When the cap is hit, return a clear "I gave up"
   message rather than crashing.
2. **A stop condition.** The model signals it's done by returning text with *no*
   tool calls (`stop_reason != "tool_use"` for Claude). That's your exit.
3. **Faithful tool-result formatting.** Each provider has a specific way to send
   results back (a `tool_result` block for Claude, a `role: "tool"` message for
   OpenAI/Mistral). Get this wrong and the model can't see what happened.
4. **Multiple tool calls per turn.** A model may request several tools at once;
   run them all before looping.
5. **State carried across turns.** Append every message — user, assistant,
   tool results — so the next turn has full context.

---

## 4. What you should know before building one

A short list of things that trip people up:

- **Tools are described by their schema and docstring.** The model picks a tool
  based on its name, description, and parameter schema — nothing else. Write tool
  descriptions *for the model*, like prompt engineering. Vague descriptions →
  wrong tool choices.
- **The model never runs your code.** It only emits a *request* to call a tool
  with arguments. Your agent runs it. So validate arguments and handle errors —
  the model can hallucinate argument values.
- **Ground the model with a system prompt.** "Only state numbers that came back
  from a tool — never invent data" is the single most valuable line in this PoC's
  prompt. It stops the model from fabricating answers.
- **Cost and latency live in the loop.** Every round is a full LLM call. Fewer,
  better tools beat many overlapping ones. Cache or batch where you can.
- **Errors must be legible.** When a tool or the LLM fails, surface *why*
  (out of credits, bad auth, rate limited) — not a stack trace. See
  `explain_llm_error()` in `web.py`.
- **Test without a real model.** A deterministic "mock" provider lets you test the
  entire pipeline for free, offline, in CI. This repo's tests rely on it.
- **Concurrency is your problem, not the model's.** A single tool connection
  shared across requests usually isn't concurrency-safe — serialize with a lock
  or pool connections (see the `asyncio.Lock` in `web.py`).

---

## 5. How to develop an agent — step by step

A practical recipe, mirroring how this repo is built:

1. **Define your tools first.** Decide what the agent can *do*. Here, tools are
   exposed via an MCP server (`mcp_server/server.py`) that wraps a REST API. You
   could equally hand the loop plain Python functions. Each tool needs a name, a
   clear description, and a typed parameter schema.

2. **Pick a transport for tools.** This PoC uses **MCP over Streamable HTTP**, so
   the agent and tools are separate, networked services. `mcp_client.py` connects,
   calls `list_tools()`, and exposes `call_tool(name, args)`.

3. **Write the provider adapter.** Implement the loop for your LLM's API. The
   contract here is one method:

   ```python
   async def send(self, user_text, tools, call_tool) -> str
   ```

   `tools` is the discovered tool list; `call_tool` is an async callback the loop
   invokes to actually run a tool. The provider formats tools into its API's
   shape, runs the loop, and returns the final text.

4. **Add a system prompt and guardrails.** Role, rules, and the "don't invent
   data" instruction.

5. **Wrap it in an interface.** A CLI (`headless.py`), a web server (`web.py`), or
   a REPL (`agent.py`). The interface only collects input, calls
   `provider.send(...)`, and renders the answer + the tool calls made.

6. **Make it testable.** Add a mock provider that maps inputs to tool calls
   deterministically, then write end-to-end tests that boot the real backend and
   drive the full path.

7. **Handle failure and limits.** Round caps, human-readable errors, exit codes
   for scripts.

---

## 6. The provider abstraction (the key to "provider-agnostic")

`agent/providers.py` is what lets this PoC swap Claude, OpenAI, Mistral, or a mock
without touching any agent. They all implement one interface:

```python
class BaseProvider(ABC):
    @abstractmethod
    async def send(self, user_text: str, tools: ToolList,
                   call_tool: CallTool) -> str: ...
```

- `tools` — the MCP tool list, each `{name, description, input_schema}`.
- `call_tool` — an async callback the loop calls to execute a tool. The agent
  supplies it, so the provider never knows tools live behind MCP.
- returns — the model's final text answer.

Selection is one function, driven by an env var, with **lazy SDK imports** (the
Anthropic SDK is only imported if you actually use Claude):

```python
def get_provider(name: str | None = None) -> BaseProvider:
    # name ← LLM_PROVIDER env var, default "claude"
    # "claude" | "openai" | "mistral" | "mock"
```

A shared `SYSTEM_PROMPT` and `MAX_TOOL_ROUNDS = 10` apply across all of them.

### What differs per provider (and why)

Only **two things** differ between providers — everything else is shared:

1. **Tool schema shape.** Claude wants `{name, description, input_schema}`;
   OpenAI and Mistral want `{type: "function", function: {name, description,
   parameters}}`. Each provider has a small `_format_tools()` that maps the MCP
   schema into its API's shape. (Notably, the MCP `input_schema` already matches
   Claude's `input_schema` field — no transform needed.)
2. **How tool results go back.** Claude takes a `tool_result` content block in a
   `user` message; OpenAI/Mistral take a separate message with `role: "tool"`.

Here is Claude's loop, the canonical version (abridged from `providers.py`):

```python
async def send(self, user_text, tools, call_tool) -> str:
    self.messages.append({"role": "user", "content": user_text})
    anthropic_tools = self._format_tools(tools)

    for _ in range(MAX_TOOL_ROUNDS):
        resp = await self.client.messages.create(
            model=self.model, max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT, tools=anthropic_tools,
            messages=self.messages,
        )
        self.messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":          # ← stop condition
            return "".join(b.text for b in resp.content if b.type == "text")

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                output = await call_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        self.messages.append({"role": "user", "content": tool_results})

    return "Stopped: exceeded the maximum number of tool-calling rounds."
```

Default models: Claude → `claude-opus-4-8`, OpenAI → `gpt-4o`, Mistral →
`mistral-large-latest`. Override any with `LLM_MODEL`.

---

## 7. The agents in this repo

All three agents share the same core (`mcp_client.py` + `providers.py`) and run
the identical loop. Only the **interface** differs.

| Agent | File | Interface | Built for |
| --- | --- | --- | --- |
| **Headless** | `agent/headless.py` | CLI, one-shot | Automation, CI, cron, K8s Jobs, shell pipelines |
| **Web UI** | `agent/web.py` | FastAPI + browser chat | Interactive use, demos |
| **Interactive CLI** | `agent/agent.py` | Terminal REPL | Manual exploration |

### Headless agent (`headless.py`)

Question in → answer out → exit. No server, no prompts.

```bash
python headless.py "What are the top 3 products by revenue?"
python headless.py --json "Total revenue across all sales?"
```

It opens an MCP connection, discovers tools, builds a provider, and passes a
`call_tool` callback that records every tool call. With `--json` it emits the
answer **and** the tool calls — useful for asserting in CI that the model went
through MCP rather than guessing. Exit code is `0`/non-zero so it composes in
scripts; in Kubernetes it runs as a **Job**.

### Web UI agent (`web.py`)

A FastAPI app serving a single-page chat UI. Key architectural choices:

- **One MCP connection** opened at startup via a `lifespan` context manager and
  reused for all requests.
- **Per-session provider state** — each browser session gets its own provider
  instance in `app.state.sessions[session_id]`, so each conversation keeps its own
  history.
- **An `asyncio.Lock`** serializes tool calls, because the single shared MCP
  session is not concurrency-safe.
- **Human-readable errors** via `explain_llm_error()` — out-of-credits, bad key,
  and rate-limit failures become clear messages, not stack traces.

The chat endpoint is the whole pattern in miniature:

```python
@app.post("/api/chat")
async def chat(body, request):
    async with state.lock:
        provider = state.sessions.setdefault(body.session_id, get_provider())
        tool_calls = []
        async def call_tool(name, args):
            tool_calls.append({"name": name, "arguments": args})
            return await state.mcp.call_tool(name, args)
        try:
            answer = await provider.send(body.message, state.tools, call_tool)
            error = False
        except Exception as exc:
            answer, error = explain_llm_error(state.provider_name, exc), True
    return {"answer": answer, "tool_calls": tool_calls, "error": error}
```

The UI renders answers as Markdown and shows which tools each answer called, so
you can watch the agent reach through MCP into the API. In Kubernetes it runs as a
**Deployment + Service**.

### Interactive CLI (`agent.py`)

A terminal REPL for manual poking. Same core, conversation kept in memory across
turns until you exit.

### The `mock` provider — the fourth "agent brain"

`LLM_PROVIDER=mock` swaps the LLM for a keyword router that maps a question to one
MCP tool and returns its output:

```python
if "top" in text:                    name, args = "top_products", {"limit": 3}
elif "summary"/"total"/"revenue"...: name, args = "sales_summary", {}
elif "search"/"price"/"cost"...:     name, args = "search_products", {"min_price": 100}
elif "product" in text:              name, args = "list_products", {"limit": 5}
else:                                name, args = "sales_summary", {}
```

It needs no API key and no network to an LLM, yet exercises the *entire* path —
agent → MCP client → MCP server → API → SQLite. That's why CI runs free, offline,
and deterministically.

---

## 8. End-to-end request flow

Putting it together, here's what happens when you ask the headless agent
*"What are the top 3 products by revenue?"*:

```
you ─▶ headless.py
        │  MCPClient.list_tools()  ──────────────▶ MCP server returns 5 tool schemas
        │  ClaudeProvider.send(question, tools, call_tool)
        │     LLM decides: call top_products(limit=3)
        │     call_tool("top_products", {"limit": 3})
        │        └▶ MCPClient.call_tool (Streamable HTTP)
        │             └▶ MCP server: top_products() → GET /sales/top?limit=3
        │                  └▶ FastAPI → SQLite: SELECT ... ORDER BY revenue LIMIT 3
        │             ◀─── JSON rows back up the chain
        │     LLM receives the rows, writes the final answer
        ▼
   prints answer + tool_calls, exits 0
```

The model never touches the database. It only *decides* and *reads results*; the
agent and MCP layer do the work. That separation — model decides, tools execute,
results ground the next decision — is the essence of agent architecture.

---

## 9. Further reading in this repo

- [`01-mcp-tutorial.md`](01-mcp-tutorial.md) — what MCP is and how the server exposes tools
- [`02-docker.md`](02-docker.md) — running the whole stack in containers
- [`03-kubernetes.md`](03-kubernetes.md) — deploying agents as Jobs and Deployments
- [`04-agents-and-tests.md`](04-agents-and-tests.md) — running the agents and the test suite
- `agent/providers.py` — the provider abstraction and all four "brains"
- `agent/mcp_client.py` — the Streamable HTTP MCP client
