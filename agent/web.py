"""UI agent — the "AI Operations Control" web app over the MCP + provider core.

The user-facing UI (``INDEX_HTML``) is an executive *AI Operations Control
Center*: a sidebar + header + investigation workspace + an "Evidence" panel that
surfaces the tool calls behind every answer. It deliberately avoids exposing the
underlying MCP plumbing — to the user it is an "AI agent" backed by secure tools
and data sources.

The FastAPI backend still exposes the full API used by tooling and tests:
  - ``GET  /``                  the control-center UI
  - ``GET  /api/info``          active agent (LLM) + connected data sources + tool count
  - ``POST /api/chat``          run an investigation
  - ``GET  /health``            liveness
  - plus management endpoints kept for automation: ``/api/tools``,
    ``/api/mcp/servers`` (server CRUD), ``/api/llms`` (LLM CRUD),
    ``/api/headless/*`` (one-shot runs + scheduled triggers).

MCP connections are managed by a `DynamicMCPManager` so servers can be added and
removed while the app runs. A lock serializes tool use and registry mutations
(the PoC keeps a single session per server).

Run:  uvicorn web:app --host 0.0.0.0 --port 8002   →  http://localhost:8002
"""
from __future__ import annotations

import asyncio
import itertools
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from mcp_manager import DynamicMCPManager
from providers import LLM_KINDS, build_provider
from servers import MCP_SERVER_URLS

_run_ids = itertools.count(1)
_trigger_ids = itertools.count(1)
_llm_ids = itertools.count(1)
MAX_HISTORY = 50


def _seed_llms() -> tuple[dict, list, str]:
    """Build the initial LLM registry from environment keys.

    Only the provider that matches LLM_PROVIDER inherits LLM_MODEL; the others
    fall back to their own sensible default model id.
    """
    llms: dict[str, dict] = {}
    order: list[str] = []
    active = os.environ.get("LLM_PROVIDER", "").lower()
    env_model = os.environ.get("LLM_MODEL") or ""

    def add(lid, name, kind, default_model="", base_url="", api_key=""):
        model = env_model if (lid == active and env_model) else default_model
        llms[lid] = {"id": lid, "name": name, "kind": kind, "model": model,
                     "base_url": base_url, "api_key": api_key}
        order.append(lid)

    add("mock", "Mock (no LLM)", "mock")
    if os.environ.get("OPENROUTER_API_KEY"):
        add("openrouter", "OpenRouter", "openai-compatible",
            "qwen/qwen3-30b-a3b-instruct-2507", "https://openrouter.ai/api/v1",
            os.environ["OPENROUTER_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        add("claude", "Claude (Anthropic)", "anthropic", "claude-opus-4-8",
            "", os.environ["ANTHROPIC_API_KEY"])
    if os.environ.get("OPENAI_API_KEY"):
        add("openai", "OpenAI", "openai", "gpt-4o", "", os.environ["OPENAI_API_KEY"])
    if os.environ.get("MISTRAL_API_KEY"):
        add("mistral", "Mistral", "mistral", "mistral-large-latest", "",
            os.environ["MISTRAL_API_KEY"])

    default = active if active in llms else order[0]
    return llms, order, default


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = DynamicMCPManager()
    for url in [u.strip() for u in MCP_SERVER_URLS.split(",") if u.strip()]:
        try:
            await manager.add(url)
        except Exception as exc:  # noqa: BLE001 — keep booting even if one is down
            print(f"[web] could not connect MCP server {url}: {exc}")
    app.state.mcp = manager

    llms, order, default = _seed_llms()
    app.state.llms = llms
    app.state.llm_order = order
    app.state.default_llm = default

    app.state.sessions: dict[str, dict] = {}     # chat: session_id -> {provider,llm_id,model}
    app.state.lock = asyncio.Lock()
    app.state.history: list[dict] = []
    app.state.triggers: dict[int, dict] = {}
    try:
        yield
    finally:
        for trig in app.state.triggers.values():
            trig["active"] = False
            if trig.get("_task"):
                trig["_task"].cancel()
        await manager.aclose()


app = FastAPI(title="Connected Fleet Agent UI", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Request bodies                                                               #
# --------------------------------------------------------------------------- #
class ChatIn(BaseModel):
    session_id: str
    message: str
    provider: str | None = None    # an LLM registry id
    model: str | None = None


class HeadlessIn(BaseModel):
    question: str
    provider: str | None = None
    model: str | None = None


class TriggerIn(BaseModel):
    question: str
    interval_seconds: int = 60
    label: str | None = None
    provider: str | None = None
    model: str | None = None


class ToolIn(BaseModel):
    name: str
    arguments: dict = {}


class McpIn(BaseModel):
    url: str
    id: str | None = None


class McpUpdateIn(BaseModel):
    url: str


class LlmIn(BaseModel):
    name: str
    kind: str
    model: str | None = ""
    base_url: str | None = ""
    api_key: str | None = ""


class LlmUpdateIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None     # empty string = leave the stored key unchanged


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #
def explain_llm_error(provider: str, exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "credit balance is too low" in low or "billing" in low or "insufficient_quota" in low:
        title = "Insufficient API credits"
        detail = (f"The account behind LLM **{provider}** has no credits. Add credits, "
                  "or pick the **Mock** LLM (top-right) to run with no real model.")
    elif "authentication" in low or " 401" in low or "invalid x-api-key" in low or "invalid api key" in low:
        title = "Invalid or missing API key"
        detail = f"The API key for LLM **{provider}** was rejected. Fix it in the **LLMs** tab."
    elif "rate limit" in low or " 429" in low or "overloaded" in low:
        title = "Rate limited / overloaded"
        detail = "Too many requests right now. Wait a few seconds and retry."
    else:
        title = "The language model request failed"
        detail = ("The MCP tools and the data APIs are fine — the failure is in the LLM call. "
                  "See the technical details below.")
    return (f"⚠️ **{title}**\n\n{detail}\n\n**LLM:** `{provider}`\n\n"
            f"**Raw error:**\n\n```\n{msg}\n```")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _llm_view(cfg: dict, default_id: str) -> dict:
    return {
        "id": cfg["id"], "name": cfg["name"], "kind": cfg["kind"],
        "model": cfg["model"], "base_url": cfg["base_url"],
        "has_key": bool(cfg["api_key"]), "is_default": cfg["id"] == default_id,
        "needs_key": cfg["kind"] != "mock",
    }


def _provider_from_llm(cfg: dict, model_override: str | None):
    model = (model_override or "").strip() or cfg["model"] or None
    return build_provider(cfg["kind"], model=model,
                          api_key=cfg["api_key"] or None, base_url=cfg["base_url"] or None)


def _remember(state, rec: dict) -> None:
    state.history.insert(0, rec)
    del state.history[MAX_HISTORY:]


async def _run_agent(state, question: str, llm_id: str, model: str | None, source: str) -> dict:
    """Run a one-shot agent turn and record it. Never raises."""
    rec = {"id": next(_run_ids), "ts": _now(), "source": source, "provider": llm_id,
           "model": model or "(default)", "question": question, "answer": "",
           "tool_calls": [], "error": False}
    cfg = state.llms.get(llm_id)
    if cfg is None:
        rec["answer"], rec["error"] = f"⚠️ Unknown LLM '{llm_id}'.", True
        _remember(state, rec)
        return rec

    tool_calls: list[dict] = []

    async def call_tool(name: str, args: dict) -> str:
        tool_calls.append({"name": name, "arguments": args})
        return await state.mcp.call_tool(name, args)

    async with state.lock:
        tools = await state.mcp.list_tools()
        try:
            provider = _provider_from_llm(cfg, model)
            rec["answer"] = await provider.send(question, tools, call_tool)
        except Exception as exc:  # noqa: BLE001
            rec["answer"], rec["error"] = explain_llm_error(cfg["name"], exc), True
    rec["tool_calls"] = tool_calls
    _remember(state, rec)
    return rec


def _trigger_view(trig: dict) -> dict:
    return {"id": trig["id"], "label": trig["label"], "interval": trig["interval"],
            "provider": trig["provider"], "model": trig["model"] or "(default)",
            "question": trig["question"], "active": trig["active"],
            "created_at": trig["created_at"], "run_count": trig["run_count"],
            "runs": trig["runs"][:5]}


# --------------------------------------------------------------------------- #
# Info / health / tools                                                        #
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tools")
async def tools(request: Request) -> dict:
    return {"tools": [t["name"] for t in await request.app.state.mcp.list_tools()]}


@app.post("/api/tool")
async def call_tool(body: ToolIn, request: Request) -> dict:
    """Run a single tool directly (used by the Deep Dive guided flows).

    Routes to the owning MCP server via the manager and returns the raw result —
    no LLM involved, so a flow can call tools step by step, deterministically.
    """
    state = request.app.state
    async with state.lock:
        try:
            result = await state.mcp.call_tool(body.name, body.arguments or {})
            ok = not str(result).startswith("(error")
        except Exception as exc:  # noqa: BLE001
            result, ok = str(exc), False
    return {"name": body.name, "arguments": body.arguments or {}, "result": result, "ok": ok}


@app.get("/api/info")
async def info(request: Request) -> dict:
    state = request.app.state
    return {
        "default_llm": state.default_llm,
        "llms": [_llm_view(state.llms[i], state.default_llm) for i in state.llm_order],
        "servers": state.mcp.servers(),
        "tool_count": len(await state.mcp.list_tools()),
        "kinds": LLM_KINDS,
    }


# --------------------------------------------------------------------------- #
# MCP server CRUD                                                              #
# --------------------------------------------------------------------------- #
@app.get("/api/mcp/servers")
async def mcp_list(request: Request) -> dict:
    return {"servers": request.app.state.mcp.servers()}


@app.post("/api/mcp/servers")
async def mcp_add(body: McpIn, request: Request) -> dict:
    state = request.app.state
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    async with state.lock:
        try:
            return await state.mcp.add(body.url.strip(), body.id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))


@app.put("/api/mcp/servers/{sid}")
async def mcp_update(sid: str, body: McpUpdateIn, request: Request) -> dict:
    state = request.app.state
    async with state.lock:
        try:
            return await state.mcp.update(sid, body.url.strip())
        except KeyError:
            raise HTTPException(status_code=404, detail="server not found")


@app.delete("/api/mcp/servers/{sid}")
async def mcp_delete(sid: str, request: Request) -> dict:
    state = request.app.state
    async with state.lock:
        try:
            await state.mcp.remove(sid)
        except KeyError:
            raise HTTPException(status_code=404, detail="server not found")
    return {"removed": sid}


# --------------------------------------------------------------------------- #
# LLM CRUD                                                                     #
# --------------------------------------------------------------------------- #
@app.get("/api/llms")
async def llm_list(request: Request) -> dict:
    state = request.app.state
    return {"llms": [_llm_view(state.llms[i], state.default_llm) for i in state.llm_order],
            "default": state.default_llm, "kinds": LLM_KINDS}


@app.post("/api/llms")
async def llm_add(body: LlmIn, request: Request) -> dict:
    state = request.app.state
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if body.kind not in LLM_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(LLM_KINDS)}")
    lid = f"llm-{next(_llm_ids)}"
    state.llms[lid] = {"id": lid, "name": body.name.strip(), "kind": body.kind,
                       "model": (body.model or "").strip(), "base_url": (body.base_url or "").strip(),
                       "api_key": (body.api_key or "").strip()}
    state.llm_order.append(lid)
    return _llm_view(state.llms[lid], state.default_llm)


@app.put("/api/llms/{lid}")
async def llm_update(lid: str, body: LlmUpdateIn, request: Request) -> dict:
    state = request.app.state
    cfg = state.llms.get(lid)
    if cfg is None:
        raise HTTPException(status_code=404, detail="LLM not found")
    if body.kind is not None and body.kind not in LLM_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(LLM_KINDS)}")
    if body.name is not None:
        cfg["name"] = body.name.strip() or cfg["name"]
    if body.kind is not None:
        cfg["kind"] = body.kind
    if body.model is not None:
        cfg["model"] = body.model.strip()
    if body.base_url is not None:
        cfg["base_url"] = body.base_url.strip()
    if body.api_key:                       # only overwrite the key when a new one is sent
        cfg["api_key"] = body.api_key.strip()
    # Changing a config invalidates cached chat sessions using it.
    for sid, sess in list(state.sessions.items()):
        if sess.get("llm_id") == lid:
            del state.sessions[sid]
    return _llm_view(cfg, state.default_llm)


@app.put("/api/llms/{lid}/default")
async def llm_set_default(lid: str, request: Request) -> dict:
    state = request.app.state
    if lid not in state.llms:
        raise HTTPException(status_code=404, detail="LLM not found")
    state.default_llm = lid
    return {"default": lid}


@app.delete("/api/llms/{lid}")
async def llm_delete(lid: str, request: Request) -> dict:
    state = request.app.state
    if lid not in state.llms:
        raise HTTPException(status_code=404, detail="LLM not found")
    if len(state.llm_order) == 1:
        raise HTTPException(status_code=400, detail="cannot delete the last LLM")
    del state.llms[lid]
    state.llm_order.remove(lid)
    if state.default_llm == lid:
        state.default_llm = state.llm_order[0]
    for s, sess in list(state.sessions.items()):
        if sess.get("llm_id") == lid:
            del state.sessions[s]
    return {"removed": lid, "default": state.default_llm}


# --------------------------------------------------------------------------- #
# Chat (interactive agent)                                                      #
# --------------------------------------------------------------------------- #
@app.post("/api/chat")
async def chat(body: ChatIn, request: Request) -> dict:
    state = request.app.state
    llm_id = body.provider or state.default_llm
    model = (body.model or "").strip()

    cfg = state.llms.get(llm_id)
    if cfg is None:
        return {"answer": f"⚠️ Unknown LLM '{llm_id}'.", "tool_calls": [], "error": True}

    async with state.lock:
        sess = state.sessions.get(body.session_id)
        if sess is None or sess["llm_id"] != llm_id or sess["model"] != model:
            try:
                provider = _provider_from_llm(cfg, model)
            except Exception as exc:  # noqa: BLE001
                return {"answer": explain_llm_error(cfg["name"], exc), "tool_calls": [], "error": True}
            sess = {"provider": provider, "llm_id": llm_id, "model": model}
            state.sessions[body.session_id] = sess

        provider = sess["provider"]
        tools = await state.mcp.list_tools()
        tool_calls: list[dict] = []

        async def call_tool(n: str, a: dict) -> str:
            tool_calls.append({"name": n, "arguments": a})
            return await state.mcp.call_tool(n, a)

        error = False
        try:
            answer = await provider.send(body.message, tools, call_tool)
        except Exception as exc:  # noqa: BLE001
            answer, error = explain_llm_error(cfg["name"], exc), True

    return {"answer": answer, "tool_calls": tool_calls, "error": error,
            "provider": llm_id, "model": model or "(default)"}


# --------------------------------------------------------------------------- #
# Headless agent — run once, history, and scheduled triggers                    #
# --------------------------------------------------------------------------- #
@app.post("/api/headless/run")
async def headless_run(body: HeadlessIn, request: Request) -> dict:
    state = request.app.state
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    llm_id = body.provider or state.default_llm
    return await _run_agent(state, body.question.strip(), llm_id, body.model, source="manual")


@app.get("/api/headless/runs")
async def headless_runs(request: Request) -> dict:
    return {"runs": request.app.state.history[:25]}


@app.post("/api/headless/triggers")
async def create_trigger(body: TriggerIn, request: Request) -> dict:
    state = request.app.state
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    if body.interval_seconds < 5:
        raise HTTPException(status_code=400, detail="interval must be at least 5 seconds")
    llm_id = body.provider or state.default_llm
    if llm_id not in state.llms:
        raise HTTPException(status_code=400, detail=f"unknown LLM '{llm_id}'")

    tid = next(_trigger_ids)
    trig = {"id": tid, "label": body.label or f"Trigger {tid}", "interval": body.interval_seconds,
            "provider": llm_id, "model": body.model or "", "question": body.question.strip(),
            "active": True, "created_at": _now(), "run_count": 0, "runs": [], "_task": None}
    state.triggers[tid] = trig

    async def loop() -> None:
        while trig["active"]:
            rec = await _run_agent(state, trig["question"], trig["provider"],
                                   trig["model"], source=f"trigger #{tid}")
            trig["run_count"] += 1
            trig["runs"].insert(0, {"id": rec["id"], "ts": rec["ts"], "error": rec["error"],
                                    "tools": [c["name"] for c in rec["tool_calls"]],
                                    "answer": rec["answer"][:240]})
            del trig["runs"][20:]
            try:
                await asyncio.sleep(trig["interval"])
            except asyncio.CancelledError:
                break

    trig["_task"] = asyncio.create_task(loop())
    return _trigger_view(trig)


@app.get("/api/headless/triggers")
async def list_triggers(request: Request) -> dict:
    return {"triggers": [_trigger_view(t) for t in request.app.state.triggers.values()]}


@app.delete("/api/headless/triggers/{tid}")
async def delete_trigger(tid: int, request: Request) -> dict:
    state = request.app.state
    trig = state.triggers.pop(tid, None)
    if trig is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    trig["active"] = False
    if trig.get("_task"):
        trig["_task"].cancel()
    return {"stopped": tid}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>AI Operations Control</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root{
    --bg:#f6f7fb; --surface:#ffffff; --surface-2:#fbfbfd;
    --text:#111827; --muted:#6b7280; --faint:#9ca3af;
    --border:#e5e7eb; --border-2:#eef0f4;
    --accent:#2563eb; --accent-dark:#1d4ed8; --accent-soft:#eff4ff;
    --success:#16a34a; --warning:#f59e0b; --danger:#dc2626;
    --shadow:0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.06);
    --shadow-sm:0 1px 2px rgba(16,24,40,.05);
    --r:18px; --r-lg:22px;
  }
  *{box-sizing:border-box;}
  html,body{height:100%;}
  body{margin:0; background:var(--bg); color:var(--text);
    font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;}
  .app{display:grid; grid-template-columns:256px 1fr; height:100vh; min-height:0; overflow:hidden;}
  button{font:inherit;}

  /* sidebar */
  .sidebar{background:var(--surface); border-right:1px solid var(--border); display:flex; flex-direction:column; padding:20px 16px;}
  .brand{display:flex; gap:11px; align-items:center; padding:4px 6px 16px;}
  .brand-mark{width:36px; height:36px; border-radius:11px; display:grid; place-items:center; color:#fff;
    background:linear-gradient(140deg,var(--accent),var(--accent-dark)); box-shadow:var(--shadow-sm);}
  .brand-mark svg{width:19px; height:19px;}
  .brand-name{font-weight:650; font-size:14.5px; letter-spacing:-.01em;}
  .brand-sub{font-size:11.5px; color:var(--muted); margin-top:1px;}
  .nav-label{font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--faint); padding:14px 10px 6px;}
  .nav{display:flex; flex-direction:column; gap:2px;}
  .nav-item{display:flex; align-items:center; gap:11px; padding:9px 10px; border-radius:11px; color:var(--muted);
    font-size:13.5px; font-weight:500; cursor:pointer; user-select:none; transition:.12s; border:0; background:none; text-align:left; width:100%;}
  .nav-item svg{width:17px; height:17px; flex:0 0 17px; stroke:currentColor;}
  .nav-item:hover{background:var(--surface-2); color:var(--text);}
  .nav-item.active{background:var(--accent-soft); color:var(--accent-dark);}
  .sys{margin-top:auto; border:1px solid var(--border); border-radius:14px; padding:12px 13px; background:var(--surface-2);}
  .sys-row{display:flex; align-items:center; gap:9px; font-size:13px; font-weight:550;}
  .sys-sub{font-size:11.5px; color:var(--muted); margin-top:5px; padding-left:17px;}
  .dot{width:8px; height:8px; border-radius:50%; flex:0 0 8px; background:var(--faint);}
  .dot.ok{background:var(--success); box-shadow:0 0 0 3px rgba(22,163,74,.14);}
  .dot.bad{background:var(--danger); box-shadow:0 0 0 3px rgba(220,38,38,.12);}

  /* workspace */
  .workspace{display:flex; flex-direction:column; min-width:0; min-height:0; height:100vh;}
  .topbar{display:flex; align-items:center; gap:18px; padding:18px 26px; border-bottom:1px solid var(--border); background:var(--surface);}
  .topbar h1{margin:0; font-size:19px; font-weight:650; letter-spacing:-.02em;}
  .topbar .sub{margin:2px 0 0; font-size:13px; color:var(--muted);}
  .topbar-right{margin-left:auto; display:flex; align-items:center; gap:12px;}
  .agent-badge{display:flex; align-items:center; gap:9px; padding:7px 12px; border:1px solid var(--border); border-radius:999px; background:var(--surface-2); font-size:12.5px; color:var(--muted);}
  .agent-badge select{border:0; background:transparent; font:inherit; color:var(--text); font-weight:600; outline:none; cursor:pointer; max-width:170px;}
  .btn{border:1px solid var(--border); background:var(--surface); color:var(--text); border-radius:11px; padding:9px 14px; font-size:13px; font-weight:600; cursor:pointer; transition:.12s; display:inline-flex; align-items:center; gap:8px;}
  .btn:hover{border-color:#d6dae2;}
  .btn svg{width:15px; height:15px;}
  .btn-sm{padding:6px 11px; font-size:12px; border-radius:9px;}
  .btn-primary{border:0; background:linear-gradient(140deg,var(--accent),var(--accent-dark)); color:#fff; box-shadow:0 1px 2px rgba(37,99,235,.25), 0 6px 16px rgba(37,99,235,.18);}
  .btn-primary:hover{filter:brightness(1.04);}
  .btn-primary:disabled{opacity:.55; cursor:default; filter:none;}
  .btn-danger{color:var(--danger); border-color:#f0cdcd; background:#fff;}
  .btn-danger:hover{background:#fdf2f2;}

  .content{display:grid; grid-template-columns:1fr 350px; grid-template-rows:minmax(0,1fr); flex:1 1 auto; min-height:0; overflow:hidden;}
  .content.full{grid-template-columns:1fr;}
  .main{overflow-y:auto; overflow-x:hidden; padding:26px; min-height:0; min-width:0;}
  .evidence{overflow-y:auto; overflow-x:hidden; border-left:1px solid var(--border); background:var(--surface); padding:22px 20px; min-height:0; min-width:0;}
  .content.full .evidence{display:none;}

  .view{display:none;}
  .view.active{display:block;}

  /* cards / panels */
  .panel{background:var(--surface); border:1px solid var(--border); border-radius:var(--r-lg); box-shadow:var(--shadow);}
  .card{background:var(--surface); border:1px solid var(--border); border-radius:var(--r); box-shadow:var(--shadow-sm);}
  .sec-title{font-size:14px; font-weight:650; letter-spacing:-.01em; margin:0 0 3px;}
  .sec-hint{font-size:12.5px; color:var(--muted); margin:0 0 14px;}
  .badge{font-size:11px; font-weight:600; padding:3px 9px; border-radius:999px; border:1px solid var(--border); background:var(--surface-2); color:var(--muted);}
  .badge.ok{color:var(--success); border-color:#cdeedd; background:#f1faf5;}
  .badge.bad{color:var(--danger); border-color:#f3cfcf; background:#fdf2f2;}
  .badge.acc{color:var(--accent-dark); border-color:#dbe6ff; background:var(--accent-soft);}
  .badge.warn{color:#92600a; border-color:#f3e2b8; background:#fdf6e7;}

  /* forms */
  .field{display:flex; flex-direction:column; gap:5px; margin-bottom:11px;}
  .field label{font-size:11.5px; color:var(--muted); font-weight:600;}
  .inp, textarea, select.inp{width:100%; border:1px solid var(--border); border-radius:11px; padding:10px 12px; font:inherit; font-size:13.5px; outline:none; background:var(--surface-2); transition:.12s; resize:vertical;}
  .inp:focus, textarea:focus, select.inp:focus{border-color:var(--accent); background:#fff; box-shadow:0 0 0 4px rgba(37,99,235,.10);}
  .rowflex{display:flex; gap:11px; align-items:flex-end; flex-wrap:wrap;}
  .rowflex .field{flex:1; min-width:130px; margin-bottom:0;}
  .formmsg{font-size:12.5px; color:var(--muted); margin-top:8px;}

  /* hero + kpis */
  .hero{background:var(--surface); border:1px solid var(--border); border-radius:var(--r-lg); padding:26px; box-shadow:var(--shadow); position:relative; overflow:hidden;}
  .hero::after{content:""; position:absolute; right:-60px; top:-60px; width:220px; height:220px; border-radius:50%; background:radial-gradient(circle at center, rgba(37,99,235,.10), transparent 70%);}
  .hero h2{margin:0; font-size:25px; font-weight:680; letter-spacing:-.025em; max-width:560px;}
  .hero p{margin:9px 0 0; font-size:14px; color:var(--muted); max-width:580px;}
  .kpis{display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:22px;}
  .kpi{border:1px solid var(--border); border-radius:16px; padding:15px 16px; background:var(--surface-2);}
  .kpi-label{font-size:12px; color:var(--muted); font-weight:550; display:flex; align-items:center; gap:7px;}
  .kpi-value{font-size:26px; font-weight:680; letter-spacing:-.02em; margin-top:7px;}
  .kpi-value small{font-size:13px; color:var(--muted); font-weight:600;}

  /* prompt */
  .prompt{margin-top:18px; padding:18px;}
  .prompt-title{font-size:13px; font-weight:650; letter-spacing:-.01em;}
  .prompt-hint{font-size:12px; color:var(--muted); margin-top:1px;}
  .prompt-row{display:flex; gap:10px; margin-top:13px;}
  #input{flex:1; border:1px solid var(--border); border-radius:13px; padding:13px 15px; font:inherit; font-size:14px; outline:none; background:var(--surface-2); transition:.12s;}
  #input:focus{border-color:var(--accent); background:#fff; box-shadow:0 0 0 4px rgba(37,99,235,.10);}
  .suggestions{display:flex; flex-wrap:wrap; gap:8px; margin-top:13px;}
  .chip{border:1px solid var(--border); background:var(--surface-2); color:var(--text); cursor:pointer; padding:7px 13px; border-radius:999px; font-size:12.5px; font-weight:500; transition:.12s;}
  .chip:hover{border-color:var(--accent); color:var(--accent-dark); background:#fff;}

  /* investigation stream */
  .stream{margin-top:20px; display:flex; flex-direction:column; gap:16px;}
  .inv{display:flex; flex-direction:column; gap:12px;}
  .card-tag{font-size:11px; font-weight:650; text-transform:uppercase; letter-spacing:.06em; color:var(--faint);}
  .req{padding:15px 17px; display:flex; gap:12px; align-items:flex-start;}
  .req .q{font-size:15px; font-weight:560; letter-spacing:-.01em;}
  .req .ic{width:30px;height:30px;border-radius:9px;flex:0 0 30px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent-dark);}
  .req .ic svg{width:16px;height:16px;}
  .answer{overflow:hidden;}
  .answer-head{display:flex; align-items:center; gap:10px; padding:13px 18px; border-bottom:1px solid var(--border-2);}
  .answer-head .ttl{font-size:13.5px; font-weight:650;}
  .answer-head .meta{margin-left:auto; display:flex; gap:8px; align-items:center;}
  .answer-body{padding:6px 20px 18px;}
  .answer-body.err{background:#fdf5f5;}
  .accent-l{border-left:3px solid var(--accent);}
  .analyzing{display:flex; flex-direction:column; gap:11px; padding:18px 20px;}
  .analyzing .lead{display:flex; align-items:center; gap:10px; font-size:13.5px; font-weight:560;}
  .spin{width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .bar{height:6px; border-radius:999px; background:var(--border-2); overflow:hidden;}
  .bar i{display:block; height:100%; width:38%; border-radius:999px; background:linear-gradient(90deg,var(--accent),var(--accent-dark)); animation:slide 1.3s ease-in-out infinite;}
  @keyframes slide{0%{margin-left:-40%;}100%{margin-left:100%;}}
  .steps{display:flex; gap:16px; font-size:11.5px; color:var(--faint); flex-wrap:wrap;}
  .steps span{display:flex; align-items:center; gap:6px;}

  /* markdown */
  .md p{margin:0 0 10px;} .md p:last-child{margin-bottom:0;}
  .md h1,.md h2,.md h3{font-size:15px; font-weight:650; margin:16px 0 8px; letter-spacing:-.01em;}
  .md ul,.md ol{margin:8px 0; padding-left:20px;} .md li{margin:3px 0;}
  .md code{background:#eef1f6; padding:1.5px 6px; border-radius:6px; font-size:12.5px;}
  .md pre{background:#0f172a; color:#e2e8f0; padding:13px; border-radius:12px; overflow:auto;}
  .md pre code{background:none; padding:0;}
  .md table{border-collapse:collapse; width:100%; margin:10px 0; font-size:13px;}
  .md th,.md td{border:1px solid var(--border); padding:7px 11px; text-align:left;}
  .md th{background:var(--surface-2); font-weight:600;}
  .md blockquote{margin:10px 0; padding:4px 14px; border-left:3px solid var(--border); color:var(--muted);}
  .md strong{font-weight:650;}

  /* evidence panel */
  .evi-head{font-size:16px; font-weight:680; letter-spacing:-.02em;}
  .evi-tagline{font-size:12px; color:var(--muted); margin:3px 0 18px;}
  .evi-sec{margin-bottom:20px;}
  .evi-sec-title{font-size:11px; font-weight:650; text-transform:uppercase; letter-spacing:.06em; color:var(--faint); margin-bottom:10px; display:flex; align-items:center; gap:7px;}
  .evi-count{margin-left:auto; font-weight:600; color:var(--muted); letter-spacing:0; text-transform:none;}
  .evi-empty{font-size:12.5px; color:var(--faint); padding:14px; border:1px dashed var(--border); border-radius:13px; text-align:center;}
  .tool{border:1px solid var(--border); border-radius:14px; padding:12px 13px; margin-bottom:9px; background:var(--surface-2);}
  .tool-top{display:flex; align-items:center; gap:8px;}
  .tool-name{font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; font-weight:600;}
  .tool-status{margin-left:auto; font-size:11px; font-weight:600; color:var(--success); display:flex; align-items:center; gap:6px;}
  .tool-args{font-family:ui-monospace,Menlo,monospace; font-size:11px; color:var(--muted); margin-top:8px; background:#fff; border:1px solid var(--border-2); border-radius:9px; padding:7px 9px; word-break:break-all;}
  .tool-time{font-size:11px; color:var(--faint); margin-top:7px;}
  .src{display:flex; align-items:center; gap:9px; padding:9px 11px; border:1px solid var(--border); border-radius:12px; margin-bottom:8px; font-size:13px; background:var(--surface-2);}
  .src b{font-weight:560;}
  .src .n{margin-left:auto; font-size:11px; color:var(--faint);}
  .conf{border:1px solid var(--border); border-radius:14px; padding:13px;}
  .conf-row{display:flex; align-items:center; justify-content:space-between; font-size:12.5px;}
  .conf-level{font-weight:650;}
  .conf-bar{height:7px; border-radius:999px; background:var(--border-2); overflow:hidden; margin-top:9px;}
  .conf-bar i{display:block; height:100%; border-radius:999px; transition:width .5s ease; background:var(--success);}
  .conf-note{font-size:11.5px; color:var(--muted); margin-top:8px;}
  .audit{border:1px solid var(--border); border-radius:14px; padding:12px 13px; font-size:12px; color:var(--muted); background:var(--surface-2);}
  .audit .mono{font-family:ui-monospace,Menlo,monospace; color:var(--text);}
  .why{background:var(--accent-soft); border:1px solid #dce6ff; border-radius:14px; padding:14px;}
  .why-title{font-size:12.5px; font-weight:650; color:var(--accent-dark); display:flex; align-items:center; gap:8px;}
  .why p{margin:7px 0 0; font-size:12px; color:#3b5bbf;}

  /* management lists */
  .listitem{border:1px solid var(--border); border-radius:16px; padding:15px 16px; margin-bottom:12px; background:var(--surface);}
  .listitem .top{display:flex; align-items:center; gap:10px; flex-wrap:wrap;}
  .listitem .name{font-weight:650; font-size:14px;}
  .listitem .url{font-family:ui-monospace,Menlo,monospace; font-size:11.5px; color:var(--faint);}
  .listitem .acts{margin-left:auto; display:flex; gap:7px;}
  .listitem .meta{font-size:12.5px; color:var(--muted); margin-top:7px;}
  .toolchips{display:flex; flex-wrap:wrap; gap:6px; margin-top:11px;}
  .tchip{font-family:ui-monospace,Menlo,monospace; font-size:11px; color:var(--muted); background:var(--surface-2); border:1px solid var(--border-2); border-radius:7px; padding:3px 8px;}
  .runrow{border:1px solid var(--border); border-radius:13px; padding:11px 13px; margin-bottom:9px; background:var(--surface);}
  .runrow .h{display:flex; gap:9px; align-items:center; font-size:11.5px; color:var(--muted); flex-wrap:wrap;}
  .runrow .src-badge{background:var(--accent-soft); color:var(--accent-dark); border-radius:999px; padding:1px 9px; font-weight:600;}
  .runrow .a{font-size:13px; margin-top:7px;}
  .stack{display:flex; flex-direction:column;}
  .gap16>*{margin-bottom:16px;}

  /* predefined agents */
  .agentgrid{display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; margin-top:14px;}
  .agentcard{border:1px solid var(--border); border-radius:16px; padding:14px; cursor:pointer; background:var(--surface); transition:.12s; text-align:left;}
  .agentcard:hover{border-color:var(--accent); box-shadow:0 6px 18px rgba(37,99,235,.10); transform:translateY(-1px);}
  .agentcard.sel{border-color:var(--accent); background:var(--accent-soft);}
  .agentcard .h{display:flex; align-items:center; gap:10px;}
  .agenticon{width:34px; height:34px; flex:0 0 34px; border-radius:10px; display:grid; place-items:center; color:var(--accent-dark); background:var(--accent-soft);}
  .agenticon svg{width:18px; height:18px;}
  .agentcard .nm{font-weight:650; font-size:13.5px;}
  .agentcard .cat{font-size:10.5px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; color:var(--faint);}
  .agentcard .ds{font-size:12px; color:var(--muted); margin-top:9px;}

  /* deep dive flows */
  .flowtabs{display:flex; flex-wrap:wrap; gap:8px;}
  .flowtab{border:1px solid var(--border); background:var(--surface-2); color:var(--text); cursor:pointer; padding:8px 14px; border-radius:11px; font-size:12.5px; font-weight:600; display:flex; align-items:center; gap:8px;}
  .flowtab:hover{border-color:var(--accent); color:var(--accent-dark);}
  .flowtab.sel{border-color:var(--accent); background:var(--accent-soft); color:var(--accent-dark);}
  .flowtab svg{width:15px; height:15px;}
  .sysbadge{font-size:10.5px; font-weight:700; padding:2px 8px; border-radius:999px; letter-spacing:.02em;}
  .sys-cvc{background:#e8f3ff; color:#1d4ed8;} .sys-asap{background:#eafaf2; color:#15803d;}
  .sys-redbend{background:#fff3e6; color:#b45309;} .sys-datalake{background:#f1ecfd; color:#6d28d9;}
  .step{border:1px solid var(--border); border-radius:16px; padding:0; margin-bottom:12px; background:var(--surface); overflow:hidden;}
  .step .sh{display:flex; align-items:center; gap:10px; padding:13px 15px; border-bottom:1px solid var(--border-2);}
  .step .num{width:24px; height:24px; flex:0 0 24px; border-radius:50%; display:grid; place-items:center; font-size:12px; font-weight:700; background:var(--accent-soft); color:var(--accent-dark);}
  .step .tool{font-family:ui-monospace,Menlo,monospace; font-size:12.5px; font-weight:600;}
  .step .st{margin-left:auto; font-size:11px; font-weight:600; display:flex; align-items:center; gap:6px; color:var(--faint);}
  .step .body{padding:12px 15px;}
  .step .what{font-size:13px; font-weight:560;}
  .step .why{font-size:12px; color:var(--muted); margin-top:3px;}
  .step .args{font-family:ui-monospace,Menlo,monospace; font-size:11px; color:var(--muted); margin-top:9px;}
  .step .res{margin-top:10px; background:#0f172a; color:#e2e8f0; border-radius:10px; padding:11px 12px; font-family:ui-monospace,Menlo,monospace; font-size:11px; max-height:220px; overflow:auto; white-space:pre-wrap; word-break:break-word;}
  .step.pending{opacity:.85;} .step.skip .num{background:#eef0f4; color:var(--faint);}
  .flowconn{height:14px; width:2px; background:var(--border); margin:-6px 0 -6px 27px;}

  /* insights */
  .insgrid{display:grid; grid-template-columns:1fr 1fr; gap:16px;}
  .insight{display:flex; gap:11px; padding:11px 0; border-bottom:1px dashed var(--border-2);}
  .insight:last-child{border-bottom:0;}
  .insight .ig{width:30px; height:30px; flex:0 0 30px; border-radius:9px; display:grid; place-items:center;}
  .insight .it{font-size:13px; font-weight:560;} .insight .id{font-size:12px; color:var(--muted); margin-top:2px;}
  .ig.up{background:#eafaf2; color:var(--success);} .ig.down{background:#fdecec; color:var(--danger);}
  .ig.warn{background:#fdf6e7; color:#b45309;} .ig.info{background:var(--accent-soft); color:var(--accent-dark);}
  .approw{display:flex; align-items:center; gap:10px; padding:9px 0; border-bottom:1px dashed var(--border-2);}
  .approw:last-child{border-bottom:0;}
  .approw .an{font-weight:560; font-size:13px;} .approw .ac{font-size:11px; color:var(--faint);}
  .approw .av{margin-left:auto; text-align:right;}
  .approw .as{font-weight:650; font-size:13px;} .approw .at{font-size:11.5px; font-weight:600;}
  .at.up{color:var(--success);} .at.down{color:var(--danger);}
  .anom{border:1px solid var(--border); border-radius:14px; padding:13px; margin-bottom:10px;}
  .anom .top{display:flex; align-items:center; gap:9px;}
  .anom .sev{font-size:10.5px; font-weight:700; text-transform:uppercase; letter-spacing:.04em; padding:2px 9px; border-radius:999px;}
  .sev.high{background:#fdecec; color:var(--danger);} .sev.medium{background:#fdf6e7; color:#b45309;} .sev.low{background:#eef2f7; color:var(--muted);}
  .anom .ttl{font-weight:600; font-size:13px;} .anom .det{font-size:12.5px; color:var(--muted); margin-top:7px;}
  .anom .imp{margin-left:auto; font-size:11.5px; color:var(--faint);}
  @media (max-width:900px){ .insgrid{grid-template-columns:1fr;} }

  @media (max-width:1100px){
    .app{grid-template-columns:1fr;}
    .sidebar{display:none;}
    .content{grid-template-columns:1fr;}
    .evidence{border-left:0; border-top:1px solid var(--border);}
  }
</style>
</head>
<body>
<div class="app">

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-mark"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l8 4.5v9L12 20l-8-4.5v-9L12 2z"/><path d="M12 11v9"/><path d="M4 6.5l8 4.5 8-4.5"/></svg></div>
      <div><div class="brand-name">AI Operations Control</div><div class="brand-sub">Operational intelligence powered by secure tools</div></div>
    </div>
    <div class="nav-label">Workspace</div>
    <nav class="nav" id="nav">
      <button class="nav-item active" data-view="mission">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>
        Mission Control
      </button>
      <button class="nav-item" data-view="automations">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 00-.1-1l2-1.5-2-3.5-2.3 1a7 7 0 00-1.7-1l-.4-2.5h-4l-.4 2.5a7 7 0 00-1.7 1l-2.3-1-2 3.5 2 1.5a7 7 0 000 2l-2 1.5 2 3.5 2.3-1a7 7 0 001.7 1l.4 2.5h4l.4-2.5a7 7 0 001.7-1l2.3 1 2-3.5-2-1.5c.1-.3.1-.7.1-1z"/></svg>
        Automations
      </button>
      <button class="nav-item" data-view="deepdive">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><path d="M3 12h4l3 8 4-16 3 8h4"/></svg>
        Deep Dive
      </button>
      <button class="nav-item" data-view="insights">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 00-3.7 10.7c.4.4.7.9.7 1.5V16h6v-.8c0-.6.3-1.1.7-1.5A6 6 0 0012 3z"/></svg>
        Insights
      </button>
      <div class="nav-label">Configure</div>
      <button class="nav-item" data-view="sources">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></svg>
        Data Sources
      </button>
      <button class="nav-item" data-view="models">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><path d="M12 3v2M5 7l1.5 1.5M3 14h2M19 7l-1.5 1.5M21 14h-2M9 18h6M10 21h4"/><path d="M12 8a4 4 0 00-2.5 7.2c.3.3.5.7.5 1.1V17h4v-.7c0-.4.2-.8.5-1.1A4 4 0 0012 8z"/></svg>
        AI Models
      </button>
      <button class="nav-item" data-view="audit">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="1.8"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>
        Audit Trail
      </button>
    </nav>
    <div class="sys">
      <div class="sys-row"><span class="dot ok" id="sysDot"></span> Agent online</div>
      <div class="sys-sub" id="sysProvider">Connecting…</div>
    </div>
  </aside>

  <!-- WORKSPACE -->
  <div class="workspace">
    <header class="topbar">
      <div>
        <h1 id="viewTitle">Mission Control</h1>
        <p class="sub" id="viewSub">Ask the AI agent to investigate operational data and explain what matters.</p>
      </div>
      <div class="topbar-right">
        <div class="agent-badge"><span class="dot ok"></span><span>AI Agent</span><select id="provider" title="Active AI model"></select></div>
        <button class="btn-primary" id="liveDemo"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5v14l11-7L8 5z"/></svg>Live Demo</button>
      </div>
    </header>

    <div class="content" id="content">
      <main class="main">

        <!-- ===== MISSION CONTROL ===== -->
        <section class="view active" id="view-mission">
          <div class="hero">
            <h2>Understand complex operations in seconds.</h2>
            <p>Connect your APIs, tools and operational data to an AI agent that investigates, explains and recommends the next best action.</p>
            <div class="kpis">
              <div class="kpi"><div class="kpi-label"><span class="dot ok"></span> Data sources connected</div><div class="kpi-value" id="kpiSources">—</div></div>
              <div class="kpi"><div class="kpi-label">AI tools available</div><div class="kpi-value" id="kpiTools">—</div></div>
              <div class="kpi"><div class="kpi-label">Average investigation time</div><div class="kpi-value" id="kpiTime"><small>&lt;</small> 10s</div></div>
            </div>
          </div>
          <div class="panel prompt">
            <div class="prompt-title">Start an executive investigation</div>
            <div class="prompt-hint">Grounded answers with auditable evidence.</div>
            <form class="prompt-row" id="promptForm">
              <input id="input" autocomplete="off" placeholder="Ask the agent to investigate operations…" />
              <button class="btn-primary" type="submit" id="sendBtn">Investigate<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>
            </form>
            <div class="suggestions" id="suggestions"></div>
          </div>
          <section class="stream" id="stream"></section>
        </section>

        <!-- ===== AUTOMATIONS (predefined agents) ===== -->
        <section class="view" id="view-automations">
          <div class="stack gap16">
            <div class="panel" style="padding:18px">
              <div class="sec-title">AI agents</div>
              <div class="sec-hint">Predefined investigators and operators. Pick one, set its inputs, then run it now or schedule it like a recurring job.</div>
              <div class="agentgrid" id="agentGrid"></div>
            </div>

            <div class="panel" id="agentConfig" style="padding:18px; display:none">
              <div class="top" style="display:flex; align-items:center; gap:11px">
                <div class="agenticon" id="agentCfgIcon"></div>
                <div><div class="sec-title" id="agentCfgTitle" style="margin:0"></div><div class="sec-hint" id="agentCfgDesc" style="margin:0"></div></div>
              </div>
              <div class="rowflex" id="agentParams" style="margin-top:14px"></div>
              <div class="rowflex" style="margin-top:14px; align-items:flex-end">
                <button class="btn-primary" id="agentRun">Run now</button>
                <div class="field" style="max-width:170px"><label>Schedule</label>
                  <select class="inp" id="schedEvery">
                    <option value="0">Don't schedule</option>
                    <option value="300">Every 5 minutes</option>
                    <option value="900">Every 15 minutes</option>
                    <option value="3600">Hourly</option>
                    <option value="86400">Daily</option>
                    <option value="custom">Custom…</option>
                  </select>
                </div>
                <div class="field" id="schedCustomWrap" style="max-width:130px; display:none"><label>Every (seconds)</label><input class="inp" id="schedCustom" type="number" min="5" value="120" /></div>
                <button class="btn" id="agentSchedule">Schedule</button>
              </div>
              <div class="formmsg" id="agentMsg"></div>
              <div id="agentResult"></div>
            </div>

            <div class="panel" style="padding:18px">
              <div class="sec-title">Scheduled agents</div>
              <div class="sec-hint">Recurring agent runs — each fires immediately, then on its interval.</div>
              <div id="trigList" style="margin-top:14px"></div>
            </div>
          </div>
        </section>

        <!-- ===== DEEP DIVE (process flows) ===== -->
        <section class="view" id="view-deepdive">
          <div class="panel" style="padding:18px; margin-bottom:16px">
            <div class="sec-title">Core process flows</div>
            <div class="sec-hint">Understand how each connected-vehicle process works end to end — which systems and tools are involved, in what order, and why. Pick a process, enter a vehicle, and run the flow to see real results step by step.</div>
            <div class="flowtabs" id="flowTabs" style="margin-top:14px"></div>
            <div class="rowflex" id="flowInputs" style="margin-top:14px"></div>
            <div class="rowflex" style="margin-top:12px; align-items:center">
              <button class="btn-primary" id="flowRun">Run flow</button>
              <button class="btn" id="flowReset">Reset</button>
              <span class="sec-hint" id="flowSummary" style="margin:0"></span>
            </div>
          </div>
          <div id="flowSteps"></div>
        </section>

        <!-- ===== INSIGHTS (business analytics) ===== -->
        <section class="view" id="view-insights">
          <div class="rowflex" style="margin-bottom:16px; align-items:center">
            <div style="flex:1"><div class="sec-title">Business insights</div>
              <div class="sec-hint" style="margin:0">Patterns and signals derived from the connected-services data lake — adoption, growth, and risks worth a decision.</div></div>
            <button class="btn" id="insightsRefresh">Refresh</button>
            <button class="btn-primary" id="insightsGenerate">Generate executive insight</button>
          </div>
          <div class="kpis" id="insKpis" style="margin-bottom:16px"></div>
          <div class="insgrid">
            <div class="panel" style="padding:18px"><div class="sec-title">Patterns &amp; signals</div><div class="sec-hint">Auto-detected from the data.</div><div id="insPatterns" style="margin-top:8px"></div></div>
            <div class="panel" style="padding:18px"><div class="sec-title">Top applications</div><div class="sec-hint">By usage, with growth trend.</div><div id="insApps" style="margin-top:10px"></div></div>
          </div>
          <div class="panel" style="padding:18px; margin-top:16px"><div class="sec-title">Risks &amp; anomalies</div><div class="sec-hint">Flagged across the fleet — each can trace back to a per-vehicle issue.</div><div id="insAnoms" style="margin-top:10px"></div></div>
          <div id="insReport" style="margin-top:16px"></div>
        </section>

        <!-- ===== DATA SOURCES (mcp) ===== -->
        <section class="view" id="view-sources">
          <div class="panel" style="padding:18px; margin-bottom:16px">
            <div class="sec-title">Connect a data source</div>
            <div class="sec-hint">Point the agent at a tool server endpoint. Its tools join the agent's toolbox instantly.</div>
            <div class="rowflex">
              <div class="field" style="flex:2"><label>Server URL</label><input class="inp" id="srcUrl" placeholder="http://host:port/mcp" /></div>
              <div class="field"><label>Name (optional)</label><input class="inp" id="srcId" placeholder="defaults to the URL" /></div>
              <button class="btn-primary" id="srcAdd">Connect</button>
            </div>
            <div class="formmsg" id="srcMsg"></div>
          </div>
          <div id="srcList"></div>
        </section>

        <!-- ===== AI MODELS (llm) ===== -->
        <section class="view" id="view-models">
          <div class="panel" style="padding:18px; margin-bottom:16px">
            <div class="sec-title" id="modelFormTitle">Add an AI model</div>
            <div class="sec-hint">Configure which model powers the agent. Keys stay on the server.</div>
            <div class="rowflex">
              <div class="field"><label>Name</label><input class="inp" id="mName" placeholder="My fast model" /></div>
              <div class="field" style="max-width:200px"><label>Type</label><select class="inp" id="mKind"></select></div>
            </div>
            <div class="rowflex">
              <div class="field"><label>Model id</label><input class="inp" id="mModel" placeholder="e.g. openai/gpt-4o-mini" /></div>
              <div class="field"><label>Endpoint URL (for OpenAI-compatible)</label><input class="inp" id="mBase" placeholder="https://openrouter.ai/api/v1" /></div>
            </div>
            <div class="rowflex">
              <div class="field" style="flex:2"><label>API key</label><input class="inp" id="mKey" type="password" placeholder="leave blank to keep / not needed for Mock" /></div>
              <button class="btn-primary" id="mSave">Add model</button>
              <button class="btn" id="mCancel" style="display:none">Cancel</button>
            </div>
            <div class="formmsg" id="mMsg"></div>
          </div>
          <div id="modelList"></div>
        </section>

        <!-- ===== AUDIT TRAIL ===== -->
        <section class="view" id="view-audit">
          <div class="panel" style="padding:18px">
            <div class="sec-title">Audit trail</div>
            <div class="sec-hint">Every investigation and automated run, with the tools each one used.</div>
            <div id="auditList" style="margin-top:14px"></div>
          </div>
        </section>

      </main>

      <!-- EVIDENCE (mission only) -->
      <aside class="evidence">
        <div class="evi-head">Evidence</div>
        <div class="evi-tagline">Grounded answers with auditable evidence.</div>
        <div class="evi-sec">
          <div class="evi-sec-title">Evidence collected <span class="evi-count" id="eviCount"></span></div>
          <div id="evidenceList"><div class="evi-empty">No investigation yet. Ask a question to collect evidence.</div></div>
        </div>
        <div class="evi-sec">
          <div class="evi-sec-title">Data sources</div>
          <div id="sourcesMini"></div>
        </div>
        <div class="evi-sec">
          <div class="evi-sec-title">Confidence</div>
          <div class="conf">
            <div class="conf-row"><span>Answer confidence</span><span class="conf-level" id="confLevel">Awaiting input</span></div>
            <div class="conf-bar"><i id="confBar" style="width:0%"></i></div>
            <div class="conf-note" id="confNote">Confidence reflects how much retrieved data backs the answer.</div>
          </div>
        </div>
        <div class="evi-sec">
          <div class="evi-sec-title">Audit trail</div>
          <div class="audit" id="auditNote">No actions recorded yet.</div>
        </div>
        <div class="why">
          <div class="why-title"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/></svg> Why this matters</div>
          <p>Every answer is grounded in retrieved data and auditable tool calls — so decisions are explainable, not guessed.</p>
        </div>
      </aside>
    </div>
  </div>
</div>

<script>
  marked.setOptions({ breaks:true });
  const sessionId = Math.random().toString(36).slice(2);
  const $ = s => document.querySelector(s);
  const el = (t, c, h) => { const e=document.createElement(t); if(c)e.className=c; if(h!=null)e.innerHTML=h; return e; };
  const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

  async function api(method, url, body){
    const opt = { method, headers:{} };
    if (body !== undefined){ opt.headers["Content-Type"]="application/json"; opt.body=JSON.stringify(body); }
    const r = await fetch(url, opt);
    let data = null; try { data = await r.json(); } catch(e){}
    if (!r.ok) throw new Error((data && data.detail) || ("HTTP "+r.status));
    return data;
  }

  const SUGGESTIONS = [
    "Summarize today's operational status",
    "Find the main anomaly in the current dataset",
    "Explain what changed and why it matters",
    "Recommend the next best action",
    "Show the evidence behind your answer",
  ];
  // Predefined agents (playbooks). Each builds a headless prompt from its inputs.
  const IC = {
    plug:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 01-10 0V8zM12 16v6"/></svg>',
    link:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 007 0l2-2a5 5 0 00-7-7l-1 1M14 11a5 5 0 00-7 0l-2 2a5 5 0 007 7l1-1"/></svg>',
    toggle:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="10" rx="5"/><circle cx="16" cy="12" r="3"/></svg>',
    chip:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 16H3l9-16z"/><path d="M12 10v4M12 17h.01"/></svg>',
    chart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5M4 19h16M8 16v-4M12 16V8M16 16v-7"/></svg>',
    search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
  };
  const VEHICLE_PARAM = { key:"vehicle", label:"Vehicle (VIN or owner)", placeholder:"e.g. Walid or VR7CONNECT00001", required:true };
  const PLAYBOOKS = [
    { id:"bootstrap", name:"Bootstrap Investigator", category:"Diagnostics", icon:IC.plug,
      desc:"Checks a vehicle's connectivity bootstrap: known, online and reachable, software version, and which services are provisioned.",
      params:[VEHICLE_PARAM],
      prompt:a => `Investigate the connectivity bootstrap of ${a.vehicle}'s vehicle: confirm it is a known connected vehicle, whether it is currently online via the gateway, its platform/software version, and which connected services are provisioned. Summarize onboarding readiness and flag anything blocking it.` },
    { id:"pairing", name:"Pairing Investigator", category:"Diagnostics", icon:IC.link,
      desc:"Investigates service pairing/activation: which services were requested but did not actually apply, and why.",
      params:[VEHICLE_PARAM],
      prompt:a => `For ${a.vehicle}'s vehicle, review the desired vs actual state of every connected service. Identify any service requested (desired ACTIVE) but not actually active — a pairing/activation failure — and explain the root cause using the related OTA campaign.` },
    { id:"activation", name:"Service Activation Analyzer", category:"Operations", icon:IC.toggle,
      desc:"Analyzes service activation for a vehicle or the whole fleet: what's active, drifting, and recent operations.",
      params:[{ key:"vehicle", label:"Vehicle (VIN or owner — optional)", placeholder:"blank = fleet-wide", required:false }],
      prompt:a => a.vehicle
        ? `Analyze service activations for ${a.vehicle}'s vehicle: list active services, any drift (desired != actual), and the most recent activation operations with their status.`
        : `Analyze recent service-activation operations across the fleet: which succeeded, which failed, and any services stuck in drift (requested but not actually active).` },
    { id:"fota", name:"FOTA Operator", category:"OTA", icon:IC.chip,
      desc:"Checks available firmware/software updates for a vehicle and can launch an OTA campaign for a target package.",
      params:[VEHICLE_PARAM,
        { key:"action", label:"Action", type:"select", options:["Check available updates","Install a specific package"], default:"Check available updates" },
        { key:"target", label:"Target package (for install)", placeholder:"e.g. FW_TCU_2025_06", required:false }],
      prompt:a => a.action === "Install a specific package"
        ? `For ${a.vehicle}'s vehicle, check the available OTA updates, then install the package ${a.target||"(specify a package)"} via an OTA campaign and report the campaign result and the new module version.`
        : `For ${a.vehicle}'s vehicle, list installed software/firmware per module and any available OTA (FOTA/SOTA) updates, and recommend which to install.` },
    { id:"anomaly", name:"Fleet Anomaly Scout", category:"Analytics", icon:IC.alert,
      desc:"Scans the connected-services analytics for the top anomalies across the fleet and what they trace back to.",
      params:[],
      prompt:a => `Scan the connected-services analytics for the most significant anomalies across the fleet. For the top anomaly report severity, impacted vehicles, the affected application, and what per-vehicle issue it likely traces back to.` },
    { id:"usage", name:"Usage Analyst", category:"Analytics", icon:IC.chart,
      desc:"Summarizes fleet-wide usage: data scale, most-used applications and growth trends.",
      params:[{ key:"period", label:"Period", placeholder:"e.g. 30d", default:"30d", required:false }],
      prompt:a => `Give an executive summary of connected-services usage for ${a.period||"the last 30 days"}: the scale of available data, monthly active vehicles, the top applications by usage and which are growing or declining fastest.` },
    { id:"custom", name:"Custom Investigation", category:"Custom", icon:IC.search,
      desc:"Ask the agent anything; runs headless with full auditable evidence.",
      params:[{ key:"question", label:"Question", type:"textarea", placeholder:"Describe what to investigate…", required:true }],
      prompt:a => a.question },
  ];
  const SOURCE_LABELS = { "cvc-gateway":"Vehicle Gateway", "asap-orchestrator":"Service Orchestration", "redbend-ota":"OTA Platform", "datalake-insights":"Connected-Services Data Lake", "demo-data":"Operational Data" };
  const prettySource = n => SOURCE_LABELS[n] || String(n).replace(/[-_]+/g," ").replace(/\b\w/g, c => c.toUpperCase());

  const VIEWS = {
    mission:     ["Mission Control",  "Ask the AI agent to investigate operational data and explain what matters.", true],
    automations: ["Automations",      "Predefined AI agents you can run on demand or schedule.",                    false],
    deepdive:    ["Deep Dive",        "Understand each core process flow end to end — and run the tools for a VIN.", false],
    insights:    ["Insights",         "Business insights and patterns from the connected-services data lake.",       false],
    sources:     ["Data Sources",     "Connect the tools and APIs your AI agent can use.",                          false],
    models:      ["AI Models",        "Configure the AI models available to your agent.",                           false],
    audit:       ["Audit Trail",      "A log of every investigation and automated run.",                            false],
  };

  const input = $("#input"), stream = $("#stream"), providerSel = $("#provider");
  let busy = false, info = null, editingModel = null, currentView = "mission";

  // ---------- router ----------
  function showView(key){
    currentView = key;
    document.querySelectorAll(".nav-item").forEach(n => n.classList.toggle("active", n.dataset.view===key));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id==="view-"+key));
    const [title, sub, evid] = VIEWS[key];
    $("#viewTitle").textContent = title; $("#viewSub").textContent = sub;
    $("#content").classList.toggle("full", !evid);
    $("#liveDemo").style.display = key==="mission" ? "" : "none";
    if (key==="sources") loadSources();
    if (key==="models") loadModels();
    if (key==="automations") loadAutomations();
    if (key==="deepdive") loadDeepDive();
    if (key==="insights") loadInsights();
    if (key==="audit") loadAudit();
  }
  document.querySelectorAll(".nav-item").forEach(n => n.addEventListener("click", () => showView(n.dataset.view)));

  // ---------- info ----------
  async function loadInfo(){
    try{
      info = await api("GET","/api/info");
      const llms = info.llms || [];
      const prev = providerSel.value;
      providerSel.innerHTML = "";
      llms.forEach(l => { const o = el("option", null, esc(l.name||l.id)); o.value = l.id; providerSel.appendChild(o); });
      providerSel.value = (prev && llms.some(l=>l.id===prev)) ? prev : (info.default_llm || (llms[0]&&llms[0].id) || "");
      syncProviderLabel();
      const sources = info.servers || [];
      $("#kpiSources").textContent = sources.length;
      $("#kpiTools").textContent = (typeof info.tool_count==="number") ? info.tool_count : sources.reduce((a,s)=>a+(s.tool_count||0),0);
      const anyDown = sources.some(s => s.status!=="connected");
      $("#sysDot").className = "dot " + (sources.length && !anyDown ? "ok" : (sources.length? "ok":"bad"));
      $("#sourcesMini").innerHTML = sources.length ? sources.map(s =>
        '<div class="src"><span class="dot '+(s.status==="connected"?"ok":"bad")+'"></span><b>'+esc(prettySource(s.name))+'</b><span class="n">'+esc(s.status||"")+'</span></div>').join("")
        : '<div class="evi-empty">No data sources.</div>';
      // model kinds for the form
      const mk = $("#mKind");
      if (mk && !mk.children.length && info.kinds) info.kinds.forEach(k => { const o = el("option", null, k); o.value = k; mk.appendChild(o); });
    }catch(e){ $("#sysProvider").textContent = "Agent ready"; }
  }
  function syncProviderLabel(){
    const o = providerSel.options[providerSel.selectedIndex];
    $("#sysProvider").textContent = o ? o.textContent : "Ready";
  }
  providerSel.addEventListener("change", syncProviderLabel);

  // ---------- mission: investigation ----------
  SUGGESTIONS.forEach(s => { const b=el("button","chip",esc(s)); b.type="button"; b.onclick=()=>{ input.value=s; submit(); }; $("#suggestions").appendChild(b); });

  function addRequestCard(text){
    const inv = el("div","inv");
    const req = el("div","card req",
      '<div class="ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg></div>'+
      '<div><div class="card-tag">Request</div><div class="q"></div></div>');
    req.querySelector(".q").textContent = text;
    const pend = el("div","card answer accent-l",
      '<div class="analyzing"><div class="lead"><span class="spin"></span> Analyzing data sources…</div>'+
      '<div class="bar"><i></i></div>'+
      '<div class="steps"><span><span class="dot ok"></span>Connecting to data sources</span><span><span class="dot"></span>Running tools</span><span><span class="dot"></span>Composing answer</span></div></div>');
    inv.appendChild(req); inv.appendChild(pend); stream.appendChild(inv);
    inv.scrollIntoView({behavior:"smooth", block:"start"});
    return pend;
  }
  function renderAnswer(card, data, ms){
    const isErr = !!data.error, n = (data.tool_calls||[]).length;
    card.classList.toggle("accent-l", !isErr);
    card.innerHTML =
      '<div class="answer-head"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="'+(isErr?'#dc2626':'#2563eb')+'" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v2M5 7l1.5 1.5M3 14h2M19 7l-1.5 1.5M21 14h-2M9 18h6M10 21h4"/><path d="M12 8a4 4 0 00-2.5 7.2c.3.3.5.7.5 1.1V17h4v-.7c0-.4.2-.8.5-1.1A4 4 0 0012 8z"/></svg>'+
      '<span class="ttl">'+(isErr?'Investigation failed':'Executive Answer')+'</span>'+
      '<span class="meta">'+(ms?'<span class="badge">'+(ms/1000).toFixed(1)+'s</span>':'')+'<span class="badge">'+n+' evidence</span><span class="badge '+(isErr?'bad':'ok')+'">'+(isErr?'needs attention':'completed')+'</span></span></div>'+
      '<div class="answer-body md '+(isErr?'err':'')+'"></div>';
    card.querySelector(".answer-body").innerHTML = marked.parse(data.answer || "*(no answer)*");
  }
  function updateEvidence(calls, isErr){
    const list = $("#evidenceList");
    $("#eviCount").textContent = calls.length ? calls.length+" item"+(calls.length>1?"s":"") : "";
    list.innerHTML = calls.length ? calls.map(c => {
      const t = new Date().toLocaleTimeString();
      return '<div class="tool"><div class="tool-top"><span class="tool-name">'+esc(c.name)+'</span><span class="tool-status"><span class="dot ok"></span>completed</span></div>'+
             '<div class="tool-args">'+esc(JSON.stringify(c.arguments||{}))+'</div><div class="tool-time">'+t+'</div></div>';
    }).join("") : '<div class="evi-empty">No tools were needed for this answer.</div>';
    let level, pct, note, color;
    if (isErr){ level="Needs attention"; pct=20; color="var(--danger)"; note="The agent could not ground an answer. Review the error and retry."; }
    else if (calls.length>=1){ level="High"; pct=Math.min(96,62+calls.length*9); color="var(--success)"; note="Grounded in "+calls.length+" auditable tool call"+(calls.length>1?"s":"")+"."; }
    else { level="Moderate"; pct=52; color="var(--warning)"; note="Answer produced without retrieving additional data."; }
    $("#confLevel").textContent = level; const b=$("#confBar"); b.style.width=pct+"%"; b.style.background=color; $("#confNote").textContent=note;
    $("#auditNote").innerHTML = 'Session <span class="mono">'+sessionId.slice(0,8)+'</span> · '+calls.length+' tool call'+(calls.length===1?'':'s')+' logged · <span class="mono">'+new Date().toLocaleString()+'</span>';
  }
  async function submit(){
    if (busy) return;
    const text = input.value.trim(); if (!text) return;
    busy = true; $("#sendBtn").disabled = true;
    const card = addRequestCard(text); input.value = "";
    const t0 = performance.now();
    try{
      const data = await api("POST","/api/chat",{ session_id:sessionId, message:text, provider:providerSel.value, model:"" });
      const ms = performance.now()-t0;
      renderAnswer(card, data, ms); updateEvidence(data.tool_calls||[], !!data.error);
      $("#kpiTime").textContent = (ms/1000).toFixed(1)+"s";
    }catch(e){ renderAnswer(card, {answer:"**Connection error.** "+e.message, error:true}, 0); updateEvidence([], true); }
    finally{ busy=false; $("#sendBtn").disabled=false; input.focus(); }
  }
  $("#promptForm").addEventListener("submit", e => { e.preventDefault(); submit(); });
  $("#liveDemo").addEventListener("click", () => { showView("mission"); input.value = SUGGESTIONS[0]; submit(); });

  // ---------- data sources (mcp) ----------
  async function loadSources(){
    const box = $("#srcList"); box.innerHTML = '<div class="evi-empty">Loading…</div>';
    try{
      const d = await api("GET","/api/mcp/servers");
      const servers = d.servers||[];
      box.innerHTML = servers.length ? servers.map(s => {
        const ok = s.status==="connected";
        return '<div class="listitem"><div class="top"><span class="name">'+esc(prettySource(s.name))+'</span>'+
          '<span class="badge '+(ok?"ok":"bad")+'">'+(ok? s.tool_count+" tools":esc(s.status))+'</span>'+
          '<span class="url">'+esc(s.url)+'</span>'+
          '<span class="acts"><button class="btn btn-sm" onclick="editSource(\''+esc(s.id)+'\',\''+esc(s.url)+'\')">Edit</button>'+
          '<button class="btn btn-sm btn-danger" onclick="delSource(\''+esc(s.id)+'\')">Remove</button></span></div>'+
          (ok && s.tools && s.tools.length ? '<div class="toolchips">'+s.tools.map(t=>'<span class="tchip">'+esc(t.name)+'</span>').join("")+'</div>'
                : (s.error?'<div class="meta" style="color:var(--danger)">'+esc(s.error)+'</div>':''))+'</div>';
      }).join("") : '<div class="evi-empty">No data sources connected yet.</div>';
    }catch(e){ box.innerHTML = '<div class="evi-empty">Could not load: '+esc(e.message)+'</div>'; }
  }
  $("#srcAdd").addEventListener("click", async () => {
    const url = $("#srcUrl").value.trim(); if (!url) return;
    $("#srcMsg").textContent = "Connecting…";
    try{
      const d = await api("POST","/api/mcp/servers",{ url, id:$("#srcId").value.trim()||null });
      $("#srcMsg").textContent = d.status==="connected" ? ("Connected: "+prettySource(d.name)+" ("+d.tool_count+" tools)") : ("Could not connect: "+(d.error||d.status));
      $("#srcUrl").value=""; $("#srcId").value="";
      await loadSources(); await loadInfo();
    }catch(e){ $("#srcMsg").textContent = "Error: "+e.message; }
  });
  window.delSource = async (id) => { try{ await api("DELETE","/api/mcp/servers/"+encodeURIComponent(id)); }catch(e){} await loadSources(); await loadInfo(); };
  window.editSource = async (id, url) => { const nu = prompt("New URL for this data source", url); if (!nu || nu===url) return;
    try{ await api("PUT","/api/mcp/servers/"+encodeURIComponent(id), { url:nu }); }catch(e){ alert("Update failed: "+e.message); } await loadSources(); await loadInfo(); };

  // ---------- ai models (llm) ----------
  async function loadModels(){
    const box = $("#modelList"); box.innerHTML = '<div class="evi-empty">Loading…</div>';
    try{
      const d = await api("GET","/api/llms"); const llms = d.llms||[];
      box.innerHTML = llms.map(l =>
        '<div class="listitem"><div class="top"><span class="name">'+esc(l.name)+'</span>'+
        '<span class="badge acc">'+esc(l.kind)+'</span>'+(l.is_default?'<span class="badge warn">default</span>':'')+
        (l.needs_key ? (l.has_key?'<span class="badge ok">key set</span>':'<span class="badge bad">no key</span>') : '')+
        '<span class="acts">'+(l.is_default?'':'<button class="btn btn-sm" onclick="setDefaultModel(\''+esc(l.id)+'\')">Make default</button>')+
        '<button class="btn btn-sm" onclick="editModel(\''+esc(l.id)+'\')">Edit</button>'+
        '<button class="btn btn-sm btn-danger" onclick="delModel(\''+esc(l.id)+'\')">Delete</button></span></div>'+
        '<div class="meta">model: <b>'+esc(l.model||"(provider default)")+'</b>'+(l.base_url?' · endpoint: '+esc(l.base_url):'')+'</div></div>'
      ).join("");
    }catch(e){ box.innerHTML = '<div class="evi-empty">Could not load: '+esc(e.message)+'</div>'; }
  }
  function resetModelForm(){ editingModel=null; $("#modelFormTitle").textContent="Add an AI model"; $("#mSave").textContent="Add model"; $("#mCancel").style.display="none";
    $("#mName").value=""; $("#mModel").value=""; $("#mBase").value=""; $("#mKey").value=""; $("#mMsg").textContent=""; }
  window.editModel = (id) => { const l=(info.llms||[]).find(x=>x.id===id); if(!l)return; editingModel=id;
    $("#modelFormTitle").textContent="Edit "+l.name; $("#mSave").textContent="Save changes"; $("#mCancel").style.display="";
    $("#mName").value=l.name; $("#mKind").value=l.kind; $("#mModel").value=l.model; $("#mBase").value=l.base_url; $("#mKey").value="";
    $("#mMsg").textContent="Leave API key blank to keep the current one."; showView("models"); window.scrollTo(0,0); };
  $("#mCancel").addEventListener("click", resetModelForm);
  $("#mSave").addEventListener("click", async () => {
    const payload = { name:$("#mName").value.trim(), kind:$("#mKind").value, model:$("#mModel").value.trim(), base_url:$("#mBase").value.trim(), api_key:$("#mKey").value };
    if (!payload.name){ $("#mMsg").textContent="Name is required."; return; }
    try{
      if (editingModel) await api("PUT","/api/llms/"+editingModel, payload);
      else await api("POST","/api/llms", payload);
      resetModelForm(); await loadModels(); await loadInfo();
    }catch(e){ $("#mMsg").textContent = "Error: "+e.message; }
  });
  window.delModel = async (id) => { try{ await api("DELETE","/api/llms/"+id); }catch(e){ alert(e.message); return; } await loadModels(); await loadInfo(); };
  window.setDefaultModel = async (id) => { try{ await api("PUT","/api/llms/"+id+"/default"); }catch(e){} await loadModels(); await loadInfo(); };

  // ---------- automations: predefined agents ----------
  let currentAgent = null, agentsBuilt = false;
  function runCardHtml(rec){
    const tools=(rec.tool_calls||[]).map(c=>'<span class="tchip">'+esc(c.name)+'</span>').join(" ");
    return '<div class="card" style="margin-top:13px; padding:14px"><div class="answer-head" style="padding:0 0 10px"><span class="ttl">Result</span>'+
      '<span class="meta"><span class="badge">'+esc(rec.provider||"")+'</span><span class="badge">'+(rec.tool_calls?rec.tool_calls.length:0)+' evidence</span>'+
      '<span class="badge '+(rec.error?"bad":"ok")+'">'+(rec.error?"error":"completed")+'</span></span></div>'+
      (tools?'<div class="toolchips" style="margin:0 0 10px">'+tools+'</div>':'')+
      '<div class="md">'+marked.parse(rec.answer||"*(no answer)*")+'</div></div>';
  }
  function buildAgentGrid(){
    if (agentsBuilt) return; agentsBuilt = true;
    $("#agentGrid").innerHTML = PLAYBOOKS.map(p =>
      '<button class="agentcard" data-id="'+p.id+'"><div class="h"><div class="agenticon">'+p.icon+'</div>'+
      '<div><div class="nm">'+esc(p.name)+'</div><div class="cat">'+esc(p.category)+'</div></div></div>'+
      '<div class="ds">'+esc(p.desc)+'</div></button>').join("");
    document.querySelectorAll("#agentGrid .agentcard").forEach(c =>
      c.addEventListener("click", () => selectAgent(c.dataset.id)));
  }
  function selectAgent(id){
    const a = PLAYBOOKS.find(p => p.id === id); if (!a) return;
    currentAgent = a;
    document.querySelectorAll("#agentGrid .agentcard").forEach(c => c.classList.toggle("sel", c.dataset.id===id));
    $("#agentCfgIcon").innerHTML = a.icon;
    $("#agentCfgTitle").textContent = a.name;
    $("#agentCfgDesc").textContent = a.desc;
    $("#agentParams").innerHTML = a.params.length ? a.params.map(p => {
      const w = p.type==="textarea" ? "flex:1; min-width:240px" : "";
      let ctrl;
      if (p.type==="select") ctrl = '<select class="inp" data-k="'+p.key+'">'+p.options.map(o=>'<option'+(o===p.default?' selected':'')+'>'+esc(o)+'</option>').join("")+'</select>';
      else if (p.type==="textarea") ctrl = '<textarea class="inp" rows="2" data-k="'+p.key+'" placeholder="'+esc(p.placeholder||"")+'"></textarea>';
      else ctrl = '<input class="inp" data-k="'+p.key+'" placeholder="'+esc(p.placeholder||"")+'" value="'+esc(p.default||"")+'" />';
      return '<div class="field" style="'+w+'"><label>'+esc(p.label)+(p.required?' *':'')+'</label>'+ctrl+'</div>';
    }).join("") : '<div class="sec-hint" style="margin:0">No inputs needed — this agent runs on the whole fleet.</div>';
    $("#agentConfig").style.display = "";
    $("#agentMsg").textContent = ""; $("#agentResult").innerHTML = "";
    $("#agentConfig").scrollIntoView({behavior:"smooth", block:"nearest"});
  }
  function collectArgs(){
    const args = {};
    document.querySelectorAll("#agentParams [data-k]").forEach(i => { args[i.dataset.k] = i.value.trim(); });
    return args;
  }
  function validateAgent(){
    if (!currentAgent) return false;
    for (const p of currentAgent.params){
      if (p.required){
        const i = document.querySelector('#agentParams [data-k="'+p.key+'"]');
        if (!i || !i.value.trim()){ if(i) i.focus(); $("#agentMsg").textContent = p.label+" is required."; return false; }
      }
    }
    return true;
  }
  $("#schedEvery").addEventListener("change", () => {
    $("#schedCustomWrap").style.display = $("#schedEvery").value==="custom" ? "" : "none";
  });
  $("#agentRun").addEventListener("click", async () => {
    if (!validateAgent()) return;
    const q = currentAgent.prompt(collectArgs());
    const btn=$("#agentRun"); btn.disabled=true; btn.textContent="Running…"; $("#agentMsg").textContent="";
    $("#agentResult").innerHTML = '<div class="card" style="margin-top:13px; padding:16px"><div class="analyzing" style="padding:0"><div class="lead"><span class="spin"></span> '+esc(currentAgent.name)+' is investigating…</div><div class="bar"><i></i></div></div></div>';
    try{ const rec = await api("POST","/api/headless/run",{ question:q, provider:providerSel.value, model:"" }); $("#agentResult").innerHTML = runCardHtml(rec); refreshTriggers(); }
    catch(e){ $("#agentResult").innerHTML = '<div class="formmsg" style="color:var(--danger)">Error: '+esc(e.message)+'</div>'; }
    btn.disabled=false; btn.textContent="Run now";
  });
  $("#agentSchedule").addEventListener("click", async () => {
    if (!validateAgent()) return;
    let every = $("#schedEvery").value;
    if (every === "0"){ $("#schedCustomWrap").style.display=""; $("#schedEvery").value="300"; $("#agentMsg").textContent="Pick a schedule interval above."; return; }
    every = every === "custom" ? parseInt($("#schedCustom").value||"120",10) : parseInt(every,10);
    const args = collectArgs();
    const first = currentAgent.params[0] ? args[currentAgent.params[0].key] : "";
    const label = currentAgent.name + (first ? " — "+first : "");
    try{ await api("POST","/api/headless/triggers",{ question:currentAgent.prompt(args), interval_seconds:every, label, provider:providerSel.value, model:"" });
      $("#agentMsg").textContent = "Scheduled: "+label+" (every "+every+"s)."; refreshTriggers(); }
    catch(e){ $("#agentMsg").textContent = "Could not schedule: "+e.message; }
  });
  async function refreshTriggers(){
    const box=$("#trigList");
    try{ const d=await api("GET","/api/headless/triggers"); const ts=d.triggers||[];
      box.innerHTML = ts.length ? ts.map(t =>
        '<div class="listitem"><div class="top"><span class="name">'+esc(t.label)+'</span>'+
        '<span class="badge acc">every '+t.interval+'s · '+t.run_count+' runs</span>'+
        '<span class="acts"><button class="btn btn-sm btn-danger" onclick="stopTrigger('+t.id+')">Stop</button></span></div>'+
        '<div class="meta">"'+esc(t.question)+'" · '+esc(t.provider)+'</div>'+
        '<div class="meta" style="margin-top:6px">'+(t.runs&&t.runs.length ? t.runs.map(r=>'· '+esc(r.ts)+(r.error?' (error)':' ok')+' — '+(r.tools&&r.tools.length?esc(r.tools.join(", ")):"no tools")).join("<br>") : "waiting for first run…")+'</div></div>'
      ).join("") : '<div class="evi-empty">No automations yet.</div>';
    }catch(e){ box.innerHTML='<div class="evi-empty">'+esc(e.message)+'</div>'; }
  }
  window.stopTrigger = async (id) => { try{ await api("DELETE","/api/headless/triggers/"+id); }catch(e){} refreshTriggers(); };
  function loadAutomations(){ buildAgentGrid(); refreshTriggers(); }

  // ---------- deep dive: core process flows ----------
  // Each step calls a real tool via /api/tool. argsFn(ctx) builds the arguments
  // from inputs + values resolved by earlier steps; after(ctx,res) extracts
  // values (vin, campaign id, …) for later steps. Return null from argsFn to skip.
  const SYS = { CVC:"sys-cvc", ASAP:"sys-asap", Redbend:"sys-redbend", "Data Lake":"sys-datalake" };
  function firstVinFor(res, query){
    // res is concatenated JSON objects from list_vehicles. Match owner/model/vin loosely.
    const objs = res.split(/\}\s*\{/).map((s,i,a)=> (i? "{":"")+s+(i<a.length-1?"}":""));
    const q = (query||"").toLowerCase();
    for (const o of objs){
      const vin = (o.match(/"vin"\s*:\s*"([^"]+)"/)||[])[1];
      if (!vin) continue;
      if (!q) return vin;
      if (o.toLowerCase().includes(q)) return vin;
    }
    const m = res.match(/"vin"\s*:\s*"([^"]+)"/); return m? m[1] : null;
  }
  const grabCampaign = res => (res.match(/"(?:redbend_campaign_id|last_campaign_id|campaign_id)"\s*:\s*"([^"]+)"/)||[])[1] || null;

  const FLOWS = [
    { id:"bootstrap", name:"Bootstrap", icon:IC.plug,
      desc:"How a connected vehicle comes online and gets its base services provisioned. We confirm it is a known vehicle, check it is reachable, see which services are provisioned, and check its embedded software stack.",
      inputs:[{key:"vehicle", label:"Vehicle (VIN or owner)", placeholder:"Walid or VR7CONNECT00001", required:true}],
      steps:[
        { sys:"CVC", tool:"list_vehicles", what:"Resolve the vehicle and confirm it exists",
          why:"Every flow starts from a VIN. The gateway is the registry of connected vehicles.",
          argsFn:()=>({}), after:(ctx,res,inp)=>{ ctx.vin = firstVinFor(res, inp.vehicle); } },
        { sys:"CVC", tool:"get_vehicle", what:"Check the car is online and reachable",
          why:"Bootstrap needs live connectivity — energy, signal, software version, online status.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
        { sys:"ASAP", tool:"service_states", what:"See which services are provisioned",
          why:"Bootstrap provisions the base connected services; desired vs actual shows what is really applied.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
        { sys:"Redbend", tool:"vehicle_software", what:"Inspect the embedded software stack",
          why:"The car must run a current firmware/software stack; we also see available OTA updates.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
      ] },
    { id:"pairing", name:"Pairing", icon:IC.link,
      desc:"How an activation request is paired to the vehicle — and how to diagnose a pairing that did not take. We find any service requested but not actually active (a drift), then open the OTA campaign that should have applied it.",
      inputs:[{key:"vehicle", label:"Vehicle (VIN or owner)", placeholder:"Camille (has a Wi-Fi drift)", required:true}],
      steps:[
        { sys:"CVC", tool:"list_vehicles", what:"Resolve the vehicle",
          why:"We need the VIN to inspect its service pairing.",
          argsFn:()=>({}), after:(ctx,res,inp)=>{ ctx.vin = firstVinFor(res, inp.vehicle); } },
        { sys:"ASAP", tool:"service_states", what:"Find desired vs actual — spot the drift",
          why:"A pairing failure shows as desired ACTIVE but actual INACTIVE (in_sync=false).",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null, after:(ctx,res)=>{ ctx.campaign = grabCampaign(res); } },
        { sys:"Redbend", tool:"get_campaign", what:"Open the OTA campaign that should have paired it",
          why:"The campaign trace shows exactly where pairing failed (e.g. download interrupted).",
          argsFn:ctx=> ctx.campaign? {campaign_id:ctx.campaign} : null,
          skipNote:"No drift found — every requested service is actually active, so there is nothing to diagnose." },
      ] },
    { id:"activation", name:"Service Activation", icon:IC.toggle,
      desc:"How a service is activated end to end: ASAP sets the desired state and dispatches an OTA campaign to Redbend; the actual state flips only once the campaign reaches the car.",
      inputs:[
        {key:"vehicle", label:"Vehicle (VIN or owner)", placeholder:"Walid", required:true},
        {key:"service", label:"Service code", placeholder:"REMOTE_CLIMATE", default:"REMOTE_CLIMATE", required:true}],
      steps:[
        { sys:"CVC", tool:"list_vehicles", what:"Resolve the vehicle",
          why:"Activation acts on a VIN.", argsFn:()=>({}),
          after:(ctx,res,inp)=>{ ctx.vin = firstVinFor(res, inp.vehicle); } },
        { sys:"CVC", tool:"get_vehicle", what:"Confirm the car is online before acting",
          why:"You should not push a command to a car that is not connected.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
        { sys:"ASAP", tool:"activate_service", what:"Set desired ACTIVE and dispatch to Redbend",
          why:"ASAP is the control plane: it records the desired state and orchestrates the OTA campaign.",
          argsFn:(ctx,inp)=> ctx.vin? {vin:ctx.vin, service_code:(inp.service||"REMOTE_CLIMATE")} : null,
          after:(ctx,res)=>{ ctx.campaign = grabCampaign(res); } },
        { sys:"Redbend", tool:"get_campaign", what:"See the OTA campaign that applied it",
          why:"Redbend is the data plane — it actually delivered the activation to the vehicle.",
          argsFn:ctx=> ctx.campaign? {campaign_id:ctx.campaign} : null },
        { sys:"ASAP", tool:"service_states", what:"Confirm actual now matches desired",
          why:"After a successful campaign the service is in sync (desired = actual = ACTIVE).",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
      ] },
    { id:"fota", name:"FOTA / SOTA", icon:IC.chip,
      desc:"How an over-the-air firmware/software update is delivered: read the installed stack and available packages, launch the campaign, then verify the install bumped the module version.",
      inputs:[
        {key:"vehicle", label:"Vehicle (VIN or owner)", placeholder:"Walid", required:true},
        {key:"package", label:"Package", placeholder:"FW_TCU_2025_06", default:"FW_TCU_2025_06", required:true}],
      steps:[
        { sys:"CVC", tool:"list_vehicles", what:"Resolve the vehicle", why:"Updates target a VIN.",
          argsFn:()=>({}), after:(ctx,res,inp)=>{ ctx.vin = firstVinFor(res, inp.vehicle); } },
        { sys:"Redbend", tool:"vehicle_software", what:"Read installed versions + available updates",
          why:"You install against the current stack and only what is actually available.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
        { sys:"Redbend", tool:"create_campaign", what:"Launch the FOTA campaign",
          why:"Redbend downloads, verifies, installs and activates the package on the car.",
          argsFn:(ctx,inp)=> ctx.vin? {vin:ctx.vin, type:"FOTA", target:(inp.package||"FW_TCU_2025_06")} : null,
          after:(ctx,res)=>{ ctx.campaign = grabCampaign(res); } },
        { sys:"Redbend", tool:"vehicle_software", what:"Verify the module version was bumped",
          why:"A successful FOTA shows up as a new version on the targeted module.",
          argsFn:ctx=> ctx.vin? {vin:ctx.vin} : null },
      ] },
    { id:"analytics", name:"Fleet Analytics", icon:IC.chart,
      desc:"How the big picture is built from the data lake: fleet-wide usage, the most-used applications, and the anomalies that tie back to per-vehicle issues. This flow is fleet-wide and needs no VIN.",
      inputs:[],
      steps:[
        { sys:"Data Lake", tool:"service_usage", what:"Fleet-wide usage summary",
          why:"Scale of data, monthly active vehicles, total sessions and data volume.", argsFn:()=>({period:"30d"}) },
        { sys:"Data Lake", tool:"top_applications", what:"Most-used applications + growth",
          why:"Which connected services drive usage and which are growing or declining.", argsFn:()=>({limit:5}) },
        { sys:"Data Lake", tool:"anomalies", what:"Flagged anomalies across the fleet",
          why:"The macro signals that often trace back to a per-vehicle drift (e.g. Wi-Fi activations).", argsFn:()=>({}) },
      ] },
  ];

  let currentFlow = null, flowBuilt = false;
  function buildFlowTabs(){
    if (flowBuilt) return; flowBuilt = true;
    $("#flowTabs").innerHTML = FLOWS.map(f =>
      '<button class="flowtab" data-id="'+f.id+'">'+f.icon+esc(f.name)+'</button>').join("");
    document.querySelectorAll("#flowTabs .flowtab").forEach(t => t.addEventListener("click", () => selectFlow(t.dataset.id)));
  }
  function stepCardHtml(s, idx, state, args, res){
    const cls = state==="skip" ? "step skip" : (state==="pending" ? "step pending" : "step");
    const stTxt = state==="done" ? '<span class="dot ok"></span>completed' : state==="skip" ? 'skipped' : state==="run" ? '<span class="spin" style="width:13px;height:13px"></span>running' : 'not run';
    return '<div class="flowconn"></div><div class="'+cls+'"><div class="sh"><span class="num">'+(idx+1)+'</span>'+
      '<span class="sysbadge '+(SYS[s.sys]||"")+'">'+esc(s.sys)+'</span><span class="tool">'+esc(s.tool)+'</span>'+
      '<span class="st">'+stTxt+'</span></div><div class="body"><div class="what">'+esc(s.what)+'</div><div class="why">'+esc(s.why)+'</div>'+
      (args!=null?'<div class="args">arguments: '+esc(JSON.stringify(args))+'</div>':'')+
      (state==="skip"?'<div class="why" style="margin-top:8px;color:var(--warning)">'+esc(s.skipNote||"Skipped — a prerequisite from a previous step was missing.")+'</div>':'')+
      (res!=null?'<div class="res">'+esc(String(res).slice(0,1400))+'</div>':'')+'</div></div>';
  }
  function renderFlow(states){
    const f = currentFlow;
    $("#flowSteps").innerHTML = '<div class="panel" style="padding:18px"><div class="sec-title">'+esc(f.name)+' flow</div>'+
      '<div class="sec-hint">'+esc(f.desc)+'</div><div style="margin-top:6px">'+
      f.steps.map((s,i)=> stepCardHtml(s, i, (states&&states[i]&&states[i].state)||"idle", states&&states[i]?states[i].args:null, states&&states[i]?states[i].res:null)).join("")+
      '</div></div>';
  }
  function selectFlow(id){
    currentFlow = FLOWS.find(f=>f.id===id); if(!currentFlow) return;
    document.querySelectorAll("#flowTabs .flowtab").forEach(t=>t.classList.toggle("sel", t.dataset.id===id));
    $("#flowInputs").innerHTML = currentFlow.inputs.length ? currentFlow.inputs.map(p =>
      '<div class="field"><label>'+esc(p.label)+(p.required?' *':'')+'</label><input class="inp" data-k="'+p.key+'" placeholder="'+esc(p.placeholder||"")+'" value="'+esc(p.default||"")+'" /></div>').join("")
      : '<div class="sec-hint" style="margin:0">This flow is fleet-wide — no vehicle needed.</div>';
    $("#flowSummary").textContent = "";
    renderFlow(null);
  }
  function flowInputs(){ const o={}; document.querySelectorAll("#flowInputs [data-k]").forEach(i=>o[i.dataset.k]=i.value.trim()); return o; }
  $("#flowReset").addEventListener("click", () => { if(currentFlow){ selectFlow(currentFlow.id); } });
  $("#flowRun").addEventListener("click", async () => {
    if (!currentFlow) return;
    const inp = flowInputs();
    for (const p of currentFlow.inputs){ if (p.required && !inp[p.key]){ $("#flowSummary").textContent = p.label+" is required."; return; } }
    const btn=$("#flowRun"); btn.disabled=true; btn.textContent="Running…"; $("#flowSummary").textContent="";
    const ctx = {}; const states = currentFlow.steps.map(()=>({state:"idle", args:null, res:null}));
    let ran=0, skipped=0;
    for (let i=0;i<currentFlow.steps.length;i++){
      const s = currentFlow.steps[i];
      let args; try { args = s.argsFn(ctx, inp); } catch(e){ args = null; }
      if (args===null){ states[i]={state:"skip", args:null, res:null}; skipped++; renderFlow(states); continue; }
      states[i]={state:"run", args, res:null}; renderFlow(states);
      try{
        const d = await api("POST","/api/tool",{ name:s.tool, arguments:args });
        states[i]={state:"done", args, res:d.result};
        if (s.after){ try{ s.after(ctx, String(d.result||""), inp); }catch(e){} }
        ran++;
      }catch(e){ states[i]={state:"done", args, res:"Error: "+e.message}; }
      renderFlow(states);
    }
    $("#flowSummary").textContent = "Flow complete — "+ran+" tool"+(ran===1?"":"s")+" run"+(skipped?(", "+skipped+" skipped"):"")+(ctx.vin?(" · VIN "+ctx.vin):"")+".";
    btn.disabled=false; btn.textContent="Run flow";
  });
  function loadDeepDive(){ buildFlowTabs(); if(!currentFlow) selectFlow(FLOWS[0].id); }

  // ---------- insights: business analytics from the data lake ----------
  function parseObjects(text){
    const out=[]; let depth=0, start=-1, inStr=false, escp=false;
    for (let i=0;i<text.length;i++){ const c=text[i];
      if (inStr){ if(escp)escp=false; else if(c==='\\')escp=true; else if(c==='"')inStr=false; continue; }
      if (c==='"'){inStr=true; continue;}
      if (c==='{'){ if(depth===0)start=i; depth++; }
      else if (c==='}'){ depth--; if(depth===0&&start>=0){ try{ out.push(JSON.parse(text.slice(start,i+1))); }catch(e){} start=-1; } }
    }
    return out;
  }
  const fmtN = n => n>=1e9?(n/1e9).toFixed(1)+"B" : n>=1e6?(n/1e6).toFixed(1)+"M" : n>=1e3?(n/1e3).toFixed(1)+"K" : String(n);
  const SVG_UP='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 17l6-6 4 4 6-7"/><path d="M14 8h6v6"/></svg>';
  const SVG_DN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7l6 6 4-4 6 7"/><path d="M14 16h6v-6"/></svg>';
  const SVG_WARN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l9 16H3l9-16z"/><path d="M12 10v4M12 17h.01"/></svg>';
  const SVG_INFO='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>';

  async function loadInsights(){
    if (!info || !(info.servers||[]).some(s => (s.tools||[]).some(t => t.name==="service_usage"))){
      $("#insPatterns").innerHTML = '<div class="evi-empty">The analytics data lake is not connected. Add it under Data Sources.</div>';
      $("#insKpis").innerHTML=""; $("#insApps").innerHTML=""; $("#insAnoms").innerHTML=""; return;
    }
    $("#insKpis").innerHTML = '<div class="kpi"><div class="kpi-label">Loading…</div></div>';
    try{
      const [u, a, an] = await Promise.all([
        api("POST","/api/tool",{name:"service_usage",arguments:{period:"30d"}}),
        api("POST","/api/tool",{name:"top_applications",arguments:{limit:6}}),
        api("POST","/api/tool",{name:"anomalies",arguments:{}}),
      ]);
      const usage = parseObjects(u.result)[0] || {};
      const apps = parseObjects(a.result);
      const anoms = parseObjects(an.result);
      renderInsights(usage, apps, anoms);
    }catch(e){ $("#insKpis").innerHTML=""; $("#insPatterns").innerHTML='<div class="evi-empty">Could not load analytics: '+esc(e.message)+'</div>'; }
  }
  function renderInsights(usage, apps, anoms){
    // KPIs
    $("#insKpis").innerHTML =
      kpi("Connected vehicles", fmtN(usage.connected_vehicles||0)) +
      kpi("Monthly active", fmtN(usage.monthly_active_vehicles||0)) +
      kpi("Sessions ("+(usage.period||"30d")+")", (usage.total_sessions_millions||0)+"M") +
      kpi("Data volume", (usage.data_volume_tb||0)+" TB");
    // Top apps with trend arrows
    const sorted = apps.slice().sort((x,y)=>(y.sessions_millions||0)-(x.sessions_millions||0));
    $("#insApps").innerHTML = sorted.length ? sorted.map(ap => {
      const up = (ap.trend_pct||0) >= 0;
      return '<div class="approw"><div><div class="an">'+esc(ap.application)+'</div><div class="ac">'+esc(ap.category||"")+'</div></div>'+
        '<div class="av"><div class="as">'+(ap.sessions_millions||0)+'M</div><div class="at '+(up?"up":"down")+'">'+(up?"▲ +":"▼ ")+(ap.trend_pct||0)+'%</div></div></div>';
    }).join("") : '<div class="evi-empty">No application data.</div>';
    // Anomalies
    const sev = {high:0,medium:1,low:2};
    const sa = anoms.slice().sort((x,y)=>(sev[x.severity]??9)-(sev[y.severity]??9));
    $("#insAnoms").innerHTML = sa.length ? sa.map(an =>
      '<div class="anom"><div class="top"><span class="sev '+esc(an.severity)+'">'+esc(an.severity)+'</span>'+
      '<span class="ttl">'+esc(an.application)+' — '+esc(an.metric)+'</span><span class="imp">'+fmtN(an.impacted_vehicles||0)+' vehicles</span></div>'+
      '<div class="det">'+esc(an.detail)+'</div></div>').join("") : '<div class="evi-empty">No anomalies flagged.</div>';
    // Auto patterns / business signals
    const ins = [];
    if (sorted.length){
      const top = sorted[0];
      ins.push(["info", SVG_INFO, "Most-used service: "+top.application, top.sessions_millions+"M sessions/month across "+fmtN(top.monthly_active_vehicles||0)+" active vehicles."]);
      const grow = apps.slice().sort((x,y)=>(y.trend_pct||0)-(x.trend_pct||0))[0];
      if (grow && grow.trend_pct>0) ins.push(["up", SVG_UP, "Fastest-growing: "+grow.application+" (+"+grow.trend_pct+"%)", "Rising demand — a candidate to prioritise, upsell or scale capacity for."]);
      const dec = apps.slice().sort((x,y)=>(x.trend_pct||0)-(y.trend_pct||0))[0];
      if (dec && dec.trend_pct<0) ins.push(["down", SVG_DN, "Declining: "+dec.application+" ("+dec.trend_pct+"%)", "Usage is falling — investigate churn, UX or a regression before it erodes further."]);
    }
    const high = sa.find(x=>x.severity==="high");
    if (high) ins.push(["warn", SVG_WARN, "Top risk: "+high.application+" ("+fmtN(high.impacted_vehicles||0)+" vehicles)", high.detail]);
    if (usage.data_volume_tb) ins.push(["info", SVG_INFO, "Scale: "+(usage.data_volume_tb)+" TB / "+(usage.total_sessions_millions||0)+"M sessions", "Ingesting ~"+fmtN(usage.events_ingested_per_second||0)+" events/sec across the fleet."]);
    $("#insPatterns").innerHTML = ins.length ? ins.map(([k,svg,t,d]) =>
      '<div class="insight"><div class="ig '+k+'">'+svg+'</div><div><div class="it">'+esc(t)+'</div><div class="id">'+esc(d)+'</div></div></div>').join("")
      : '<div class="evi-empty">No signals detected.</div>';
  }
  function kpi(label, val){ return '<div class="kpi"><div class="kpi-label">'+esc(label)+'</div><div class="kpi-value">'+esc(val)+'</div></div>'; }
  $("#insightsRefresh").addEventListener("click", loadInsights);
  $("#insightsGenerate").addEventListener("click", async () => {
    const btn=$("#insightsGenerate"); btn.disabled=true; btn.textContent="Analyzing…";
    $("#insReport").innerHTML = '<div class="panel" style="padding:18px"><div class="analyzing" style="padding:0"><div class="lead"><span class="spin"></span> The AI agent is analyzing patterns and drafting business insights…</div><div class="bar"><i></i></div></div></div>';
    const prompt = "Using the connected-services analytics, produce a concise executive insights brief: (1) the 2-3 most important patterns in service usage and growth, (2) the top business risk or anomaly and who/what it impacts, and (3) 3 concrete recommended actions with the reasoning. Ground every point in the data you retrieve.";
    try{
      const rec = await api("POST","/api/headless/run",{ question:prompt, provider:providerSel.value, model:"" });
      $("#insReport").innerHTML = '<div class="panel" style="padding:18px"><div class="answer-head" style="padding:0 0 10px"><span class="ttl">Executive insight</span>'+
        '<span class="meta"><span class="badge">'+esc(rec.provider||"")+'</span><span class="badge">'+(rec.tool_calls?rec.tool_calls.length:0)+' evidence</span><span class="badge '+(rec.error?"bad":"ok")+'">'+(rec.error?"error":"completed")+'</span></span></div>'+
        '<div class="md">'+marked.parse(rec.answer||"*(no answer)*")+'</div></div>';
    }catch(e){ $("#insReport").innerHTML = '<div class="panel" style="padding:18px"><div class="formmsg" style="color:var(--danger)">Error: '+esc(e.message)+'</div></div>'; }
    btn.disabled=false; btn.textContent="Generate executive insight";
  });

  // ---------- audit ----------
  async function loadAudit(){
    const box=$("#auditList");
    try{ const d=await api("GET","/api/headless/runs"); const runs=d.runs||[];
      box.innerHTML = runs.length ? runs.map(r =>
        '<div class="runrow"><div class="h"><span class="src-badge">'+esc(r.source)+'</span><span>'+esc(r.ts)+'</span><span>'+esc(r.provider)+'</span><span>'+(r.error?"error":"ok")+'</span><span>'+(r.tool_calls?r.tool_calls.length:0)+' tools</span></div>'+
        '<div class="a md">'+marked.parse((r.answer||"").slice(0,400))+'</div></div>'
      ).join("") : '<div class="evi-empty">No runs recorded yet. Use Mission Control or Automations.</div>';
    }catch(e){ box.innerHTML='<div class="evi-empty">'+esc(e.message)+'</div>'; }
  }

  // ---------- live refresh for active background views ----------
  setInterval(() => { if (currentView==="automations") refreshTriggers(); if (currentView==="audit") loadAudit(); }, 6000);

  loadInfo();
  input.focus();
</script>
</body>
</html>"""
