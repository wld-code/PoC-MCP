# Tutorial — How an agent works, end to end

> A hands-on tutorial built around a real-ish scenario: **connected vehicles**.
> You will see exactly what an "AI agent" is, what makes it *agentic*, and how it
> uses **three MCP servers** to read a car's state, decide what should change, and
> make it happen on the vehicle.

This is the centrepiece tutorial of the repo. Read it top to bottom — every
concept is tied to code you can run.

---

## 1. The scenario

We model a tiny slice of a car maker's connected-services platform. There are
**four backend systems**, and each is a separate fake API:

| System | What it is | Role | API | MCP server |
| --- | --- | --- | --- | --- |
| **CVC** | *Connected Vehicle Cloud* — the **car gateway** | **Reads** live data about a vehicle (online status, charge, location, diagnostics) | `cvc_api/` (`:8022`) | `cvc_mcp/` (`:8012`) |
| **ASAP** | *Activation Service & Aggregation Platform* — the **orchestrator** | Owns each service's **desired vs actual** state per vehicle and **reconciles** them | `asap_api/` (`:8021`) | `asap_mcp/` (`:8011`) |
| **Redbend** | the **OTA platform** | **Executes** on the car: firmware (FOTA), software (SOTA) and **service-activation** campaigns | `redbend_api/` (`:8023`) | `redbend_mcp/` (`:8013`) |
| **Data Lake** | the **analytics** warehouse | **Aggregates** massive connected-services data across the whole installed base (datasets, usage, top apps, trends, anomalies) | `datalake_api/` (`:8024`) | `datalake_mcp/` (`:8014`) |

The first three describe the **same fleet of 5 vehicles** (same VINs) from three
angles; the Data Lake zooms out to the **whole installed base** (millions of cars):

- **CVC** knows what each car *is doing right now*.
- **ASAP** knows what services each car *should have* (desired) vs *really has*
  (actual), and orchestrates the change — but it does **not** touch the car itself.
- **Redbend** is what actually *reaches the vehicle*: ASAP asks it to push an
  activation (or a firmware/software update), and Redbend reports back.
- **Data Lake** holds the *big picture*: billions of events, terabytes of signals,
  per-application usage and flagged anomalies — for fleet-wide questions.

This is the classic **control plane / data plane** split (ASAP decides *what*
should be true; Redbend makes it true), plus an **analytics plane** (the Data
Lake) for the aggregate view.

A single agent connects to **all four** MCP servers and reasons across them.
That is the whole point: real agents combine multiple tool sources to get
something done.

```
            ┌──────────────────────────────────────────────────────────────────┐
            │                            AGENT (LLM)                            │
            │              sees ONE merged toolbox of 19 tools                  │
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
                          ASAP reconciles desired→actual by asking Redbend
                          to run a campaign; the actual state flips only when
                          the campaign reaches the car.
```

---

## 2. What *is* an agent?

An **LLM** on its own only produces text. It becomes an **agent** when you give
it two things:

1. **Tools** — functions it is allowed to call (here: the MCP tools).
2. **A loop** — instead of answering in one shot, it can *call a tool, look at
   the result, and decide what to do next*, repeatedly, until it is done.

That loop is what "**agentic**" means. The model is not just answering — it is
**planning and acting** in steps, choosing tools based on what it learns.

```
        ┌──────────────────────────────────────────────┐
        │                                              │
   user │   ┌─────────┐   wants a tool?   ┌─────────┐   │  no tool wanted
   ─────┼──▶│   LLM   │ ───────yes──────▶ │  tool   │   │  ───────────────▶ final answer
        │   └─────────┘                   └────┬────┘   │
        │        ▲                             │        │
        │        └──────── tool result ────────┘        │
        │              (loop again)                     │
        └──────────────────────────────────────────────┘
```

The code for this loop lives in `agent/providers.py`. Here is the heart of it
(Claude version, simplified):

```python
self.messages.append({"role": "user", "content": user_text})

for _ in range(MAX_TOOL_ROUNDS):           # the agentic loop (capped for safety)
    resp = await self.client.messages.create(
        model=self.model, system=SYSTEM_PROMPT,
        tools=anthropic_tools, messages=self.messages,
    )
    self.messages.append({"role": "assistant", "content": resp.content})

    if resp.stop_reason != "tool_use":      # the model is done → return its text
        return "".join(b.text for b in resp.content if b.type == "text")

    for block in resp.content:              # the model asked to run tool(s)
        if block.type == "tool_use":
            output = await call_tool(block.name, block.input)   # ← runs an MCP tool
            tool_results.append({"type": "tool_result",
                                 "tool_use_id": block.id, "content": output})
    self.messages.append({"role": "user", "content": tool_results})  # feed back, loop
```

Three things make this *agentic*, not a single API call:

- The model **chooses** which tool to call (we never hard-code "call get_vehicle").
- The tool **result is fed back** into the conversation, so the next decision is
  informed by what the previous tool returned.
- It **repeats** until the model decides it has enough to answer.

---

## 3. How the agent discovers tools (it is not told them)

The agent does **not** have the tools baked in. At startup it asks each MCP
server "what can you do?" and the server answers with self-describing tool
schemas — name, human description, and a JSON schema of arguments.

```python
# agent/mcp_client.py
result = await self._session.list_tools()
return [{"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in result.tools]
```

Those descriptions come straight from the **docstrings and type hints** in the
MCP server. This is why the tool docstrings are written so deliberately — *they
are the instructions the model reads*. For example, in `cvc_mcp/server.py`:

```python
@mcp.tool()
def list_vehicles() -> list[dict]:
    """List the connected fleet: VIN, make, model, year, owner, powertrain.

    Use this first to resolve an owner's name or a model into a VIN, which the
    other tools (and the ASAP service-activation tools) need.
    """
    return _get("/vehicles")
```

That second sentence ("use this first to resolve an owner's name into a VIN") is
a *hint to the model*. It is how we steer the agent without hard-coding logic.

---

## 4. Four servers, one toolbox (multi-MCP)

A real agent rarely has a single source of tools. Ours connects to **all four**
MCP servers and merges their tools into one flat list. The LLM does not know (or
care) which server a tool lives on — it just sees a toolbox of 19 tools.

`agent/mcp_client.py` has a `MultiMCPClient` that:

1. Connects to every URL in `MCP_SERVER_URLS` (comma-separated).
2. Calls `list_tools()` on each and concatenates the results.
3. Remembers a private map `tool name → owning server`.
4. On `call_tool(name, args)`, routes the call to the server that owns that tool.

```python
for url in self.urls:
    client = await self._stack.enter_async_context(MCPClient(url))
    for tool in await client.list_tools():
        self._owner[tool["name"]] = client     # remember who owns each tool
        self._tools.append(tool)
...
async def call_tool(self, name, arguments):
    return await self._owner[name].call_tool(name, arguments)   # route it
```

The four MCP toolboxes:

| CVC (read) | ASAP (orchestrate) | Redbend (execute) | Data Lake (analytics) |
| --- | --- | --- | --- |
| `list_vehicles` | `list_services` | `vehicle_software` | `list_datasets` |
| `get_vehicle` | `service_states` | `list_campaigns` | `service_usage` |
| `vehicle_location` | `activate_service` | `get_campaign` | `top_applications` |
| `vehicle_diagnostics` | `deactivate_service` | `create_campaign` | `usage_trend` |
| | `get_operation` | | `anomalies` |
| | `list_operations` | | |

> The web app's **MCP Servers** tab shows exactly this grouping, and lets you
> add/remove servers live (CRUD).

---

## 5. A full agentic run, step by step

Ask the agent:

> **"Activate remote climate on Walid's car."**

Watch what an LLM agent does — it has no idea what "Walid's car" is, so it has to
work it out using tools, reading across the servers:

| Round | The model thinks… | Tool it calls | Server | What comes back |
| --- | --- | --- | --- | --- |
| 1 | "Who is Walid? I need a VIN." | `list_vehicles()` | CVC | Walid → `VR7CONNECT00001` (Peugeot e-3008, electric) |
| 2 | "Is the car reachable before I push a command?" | `get_vehicle("VR7CONNECT00001")` | CVC | `online: true`, 50% charge, in Paris |
| 3 | "Request activation." | `activate_service("VR7CONNECT00001", "REMOTE_CLIMATE")` | ASAP | Operation `op-000001` **SUCCEEDED**; desired/actual=ACTIVE; `redbend_campaign_id: cmp-000001` |
| 4 | "Done — summarise." | *(no tool)* | — | Final natural-language answer |

The agent **planned a multi-tool sequence across the backends** from a one-line
request, choosing each step based on the previous result. No part of that
sequence was hard-coded. (A more curious agent might also call
`get_campaign("cmp-000001")` on **Redbend** to see the OTA steps that applied it.)

### What ASAP actually does — desired vs actual, reconciled by Redbend

`activate_service` is **not** a flag flip, and ASAP never touches the car itself.
It owns two states per service — the **desired** state (what was requested) and
the **actual** state (what is really applied) — and reconciles them by asking
**Redbend** to run an OTA campaign:

```
eligibility_check → set_desired_state(ACTIVE) → dispatch_to_redbend → reconcile_state
                                                       │
                                                       ▼  (Redbend, the executor)
                              provision_package → download_to_vehicle → install → apply
```

The actual state flips to ACTIVE only once the Redbend campaign reports it
reached the car. The operation records the `redbend_campaign_id`, so the agent
can follow the thread from "I requested this" all the way to "here is the OTA job
that did it".

### Desired ≠ actual: detecting **drift**

Because desired and actual are tracked separately, they can diverge. The fleet is
seeded with one such case: **Camille's Wi-Fi hotspot** was requested (desired =
ACTIVE) but its Redbend campaign (`cmp-000000`) **failed** mid-download, so it
never actually turned on (actual = INACTIVE). Ask:

> **"On Camille's car, is any service requested but not actually active? Why?"**

A good agent calls `service_states` (ASAP) → sees `in_sync: false` for
`WIFI_HOTSPOT` → follows `last_campaign_id` to `get_campaign("cmp-000000")`
(Redbend) → and explains the drift: *the activation was requested but the OTA
campaign failed because the vehicle lost connectivity*. That cross-system
explanation is exactly what the control-plane/data-plane split makes possible.

### Redbend also does FOTA/SOTA

Redbend isn't only for service activation — it is the OTA platform. Ask
*"what firmware updates are available for Walid's car, and install the TCU one"*
and the agent uses `vehicle_software` (to see available packages) then
`create_campaign(type="FOTA", …)` to install it; the module's version bumps.

### Zooming out — the analytics plane (Data Lake)

CVC/ASAP/Redbend are about *one car*. The **Data Lake** is about *all of them* —
billions of events, terabytes of signals, per-application usage. It lets the
agent answer executive, fleet-wide questions. Ask:

> **"What is the main anomaly across our connected services, and which vehicle issue does it trace back to?"**

The agent calls `anomalies` (Data Lake) → sees a **high-severity** anomaly:
*In-Car Wi-Fi activation success rate dropped 34% (≈18,400 vehicles) — OTA
activation packages failing to reach vehicles*. That is the **same root cause**
as the per-vehicle Wi-Fi drift in §5 — the macro signal in the lake and the micro
drift in ASAP/Redbend are two views of one problem, and the agent can connect
them. Other prompts: *"how much connected-services data do we have?"*
(`list_datasets` → billions of rows / TB), *"which apps are growing fastest?"*
(`top_applications`, `usage_trend`), *"summarise fleet usage"* (`service_usage`).

### When the agent should *not* act — the offline car

One vehicle in the fleet (the Opel Astra, `VR7CONNECT00004`) is deliberately
**offline**. Ask:

> **"Turn on Wi-Fi hotspot for Sofia's car."**

A good agent calls `get_vehicle` first, sees `online: false`, and **tells you it
cannot push the command to a car that is not connected** — instead of blindly
calling `activate_service`. This is the value of giving the model *read* tools
alongside *action* tools: it can check before it acts. (The system prompt in
`agent/providers.py` nudges it to do exactly this.)

### A built-in failure case — eligibility

Ask to activate **Smart Charging Scheduler** (`CHARGE_SCHED`, electric-only) on
the gasoline Opel. ASAP's `eligibility_check` step fails deterministically and
the operation comes back **FAILED** with a clear reason. The agent reports the
failure faithfully instead of pretending it worked — because the system prompt
says *only state facts that came back from a tool*.

---

## 6. Run it yourself

```bash
cp .env.example .env
docker compose up -d --build      # 4 APIs + 4 MCP servers + agent-web
open http://localhost:8002        # the web chat agent
```

Try these in the chat:

- `List the connected vehicles`
- `Activate remote climate on Walid's car` *(CVC online check → ASAP → Redbend campaign)*
- `On Camille's car, is any service requested but not actually active? Why?` *(desired/actual drift → Redbend campaign)*
- `What is the main anomaly across connected services and how many vehicles are impacted?` *(Data Lake)*
- `How much connected-services data do we have and which apps are most used?` *(Data Lake)*
- `Turn on Wi-Fi hotspot for Sofia's car` *(offline → the agent should refuse/warn)*
- `Try to enable smart charging on the Opel` *(eligibility failure → FAILED)*

**Headless** (automation / one-shot), with the JSON trace of every tool call:

```bash
docker compose run --rm agent python headless.py --json \
  "On Camille's car, is any service requested but not actually active? Explain using the Redbend campaign."
```

**Choosing the LLM.** Set `LLM_PROVIDER` in `.env`:
`claude` · `openai` · `openrouter` · `mistral` · `mock`. They all share the same
agent loop and MCP tools — only the function-calling format differs
(`agent/providers.py`). **OpenRouter** is handy because one `OPENROUTER_API_KEY`
gives access to hundreds of models (set `LLM_MODEL` to e.g.
`anthropic/claude-3.5-sonnet` or `openai/gpt-4o-mini`); it reuses the OpenAI
format, so the provider is a 10-line subclass.

**No API key?** Set `LLM_PROVIDER=mock`. The `mock` provider is not a real LLM —
it is dumb keyword routing — but it drives the *exact same* agent → MCP → API
path across all three servers, so you can see the plumbing work for free. It is
what the test suite uses by default.

---

## 7. Extend it — add your own tool

Adding a capability is a two-line change to one MCP server; **the agent does not
change at all** (it rediscovers tools at startup). To add "lock the doors":

1. Add an endpoint to `cvc_api/main.py` (or a new action to `asap_api` / `redbend_api`).
2. Add a tool to the matching MCP server:

   ```python
   @mcp.tool()
   def lock_doors(vin: str) -> dict:
       """Lock the doors of a vehicle by VIN."""
       return _post(f"/vehicles/{vin}/lock", {})
   ```

3. Restart that server. The agent will list the new tool and start using it when
   relevant — no agent code, no prompt change required.

That is the core promise of MCP: **capabilities are decoupled from the agent.**
You can even add a whole new MCP **server** at runtime from the web app's
**MCP Servers** tab — its tools join the toolbox immediately.

---

## 8. Where each idea lives in the code

| Concept | File |
| --- | --- |
| The agentic loop (per provider) | `agent/providers.py` → `*.send()` |
| System prompt (what the agent is, how to behave) | `agent/providers.py` → `SYSTEM_PROMPT` |
| Tool discovery + call over MCP | `agent/mcp_client.py` → `MCPClient` |
| Connecting to **all four** servers + routing | `agent/mcp_client.py` → `MultiMCPClient` |
| Runtime add/remove of MCP servers (CRUD) | `agent/mcp_manager.py` → `DynamicMCPManager` |
| Which servers to connect to | `agent/servers.py` |
| CVC (read) · ASAP (orchestrate) · Redbend (execute) · Data Lake (analytics) tools | `cvc_mcp/` · `asap_mcp/` · `redbend_mcp/` · `datalake_mcp/` `server.py` |
| **Desired/actual state + reconcile via Redbend** | `asap_api/store.py` → `create_operation()` |
| OTA campaigns + software inventory | `redbend_api/store.py` → `create_campaign()` |
| Fleet-wide analytics (datasets, usage, anomalies) | `datalake_api/store.py` |
| Vehicle telemetry generation | `cvc_api/store.py` |
| The three agent entry points | `agent/headless.py`, `agent/web.py`, `agent/agent.py` |

For the underlying MCP mechanics (transports, schemas), see
[`01-mcp-tutorial.md`](01-mcp-tutorial.md). For a general explanation of agents
independent of this scenario, see [`05-agent-architecture.md`](05-agent-architecture.md).
