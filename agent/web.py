"""UI agent — a small web chat front-end over the same MCP + provider core.

A FastAPI app that:
  - holds one persistent MCP client connection (opened at startup),
  - keeps a per-browser-session conversation (in memory),
  - serves a single-page chat UI at "/", and
  - answers messages at POST /api/chat, surfacing which MCP tools were called.

Run:  uvicorn web:app --host 0.0.0.0 --port 8002
Then open http://localhost:8002

Config (env): LLM_PROVIDER, LLM_MODEL, MCP_SERVER_URL, provider key.
Use LLM_PROVIDER=mock to run with no LLM/key.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from mcp_client import MCPClient
from providers import BaseProvider, get_provider

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open one MCP connection for the lifetime of the server and cache the
    # tool list. A lock serializes tool use (the PoC keeps a single MCP session).
    client = MCPClient(MCP_SERVER_URL)
    await client.__aenter__()
    app.state.mcp = client
    app.state.tools = await client.list_tools()
    app.state.sessions: dict[str, BaseProvider] = {}
    app.state.lock = asyncio.Lock()
    app.state.provider_name = os.environ.get("LLM_PROVIDER", "claude")
    try:
        yield
    finally:
        await client.__aexit__(None, None, None)


app = FastAPI(title="MCP Agent UI", lifespan=lifespan)


class ChatIn(BaseModel):
    session_id: str
    message: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tools")
async def tools(request: Request) -> dict:
    return {"tools": [t["name"] for t in request.app.state.tools]}


@app.get("/api/info")
async def info(request: Request) -> dict:
    state = request.app.state
    return {
        "provider": state.provider_name,
        "tools": [{"name": t["name"], "description": t["description"]} for t in state.tools],
    }


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request) -> dict:
    state = request.app.state
    # Serialize: the single MCP session isn't built for concurrent calls.
    async with state.lock:
        provider = state.sessions.get(body.session_id)
        if provider is None:
            provider = get_provider()
            state.sessions[body.session_id] = provider

        tool_calls: list[dict] = []

        async def call_tool(name: str, args: dict) -> str:
            tool_calls.append({"name": name, "arguments": args})
            return await state.mcp.call_tool(name, args)

        answer = await provider.send(body.message, state.tools, call_tool)

    return {"answer": answer, "tool_calls": tool_calls}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>MCP Agent</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {
    --accent1:#6d5efc; --accent2:#8b5cf6; --accent3:#ec4899;
    --bg:#eef1f8; --card:#ffffff; --ink:#1d2333; --muted:#7b829a;
    --user1:#6d5efc; --user2:#8b5cf6; --bot:#f4f5fb; --line:#e8eaf2;
  }
  * { box-sizing:border-box; }
  html,body { height:100%; }
  body {
    margin:0; font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
    color:var(--ink);
    background:linear-gradient(135deg,#eef1f8 0%,#e7ecfb 50%,#f3ecfb 100%);
    display:flex; align-items:center; justify-content:center; padding:24px;
  }
  .app {
    width:100%; max-width:880px; height:calc(100vh - 48px); max-height:920px;
    background:var(--card); border-radius:24px; overflow:hidden;
    display:flex; flex-direction:column;
    box-shadow:0 24px 70px rgba(54,46,120,.18), 0 2px 8px rgba(54,46,120,.06);
  }

  header {
    padding:18px 22px; color:#fff; display:flex; align-items:center; gap:14px;
    background:linear-gradient(120deg,var(--accent1),var(--accent2) 55%,var(--accent3));
  }
  .logo {
    width:44px; height:44px; border-radius:13px; display:grid; place-items:center;
    background:rgba(255,255,255,.18); backdrop-filter:blur(6px); font-size:22px;
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.25);
  }
  .htitle { font-weight:700; font-size:17px; letter-spacing:.2px; }
  .hsub { font-size:12.5px; opacity:.85; }
  .badge {
    margin-left:auto; display:flex; align-items:center; gap:8px; font-size:12.5px;
    background:rgba(255,255,255,.16); padding:7px 12px; border-radius:999px;
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.22);
  }
  .dot { width:8px; height:8px; border-radius:50%; background:#36e07f;
    box-shadow:0 0 0 0 rgba(54,224,127,.6); animation:pulse 2s infinite; }
  @keyframes pulse { 70%{box-shadow:0 0 0 7px rgba(54,224,127,0);} 100%{box-shadow:0 0 0 0 rgba(54,224,127,0);} }

  #chat { flex:1; overflow-y:auto; padding:24px; display:flex; flex-direction:column; gap:16px; scroll-behavior:smooth; }
  #chat::-webkit-scrollbar { width:9px; }
  #chat::-webkit-scrollbar-thumb { background:#d8dcea; border-radius:9px; }

  .row { display:flex; gap:11px; align-items:flex-end; max-width:88%; animation:rise .28s ease both; }
  .row.me { align-self:flex-end; flex-direction:row-reverse; }
  @keyframes rise { from{opacity:0; transform:translateY(8px);} to{opacity:1; transform:none;} }
  .av { width:34px; height:34px; border-radius:50%; flex:0 0 34px; display:grid; place-items:center; font-size:17px; }
  .av.bot { background:linear-gradient(135deg,#ede9ff,#f6e9fb); box-shadow:inset 0 0 0 1px #e6e1fb; }
  .av.me  { background:linear-gradient(135deg,var(--user1),var(--user2)); color:#fff; }

  .bubble { padding:12px 15px; border-radius:16px; font-size:14.5px; }
  .me .bubble { background:linear-gradient(135deg,var(--user1),var(--user2)); color:#fff; border-bottom-right-radius:5px; }
  .bot .bubble { background:var(--bot); color:var(--ink); border:1px solid var(--line); border-bottom-left-radius:5px; }

  /* markdown inside assistant bubbles */
  .bubble p { margin:0 0 8px; } .bubble p:last-child { margin-bottom:0; }
  .bubble h1,.bubble h2,.bubble h3 { font-size:15px; margin:6px 0; }
  .bubble ul,.bubble ol { margin:6px 0; padding-left:20px; }
  .bubble code { background:#ebe9fb; padding:1px 6px; border-radius:6px; font-size:12.5px; }
  .bubble pre { background:#1d2333; color:#e7e9ee; padding:12px; border-radius:10px; overflow:auto; }
  .bubble pre code { background:none; padding:0; }
  .bubble table { border-collapse:collapse; margin:6px 0; width:100%; font-size:13px; }
  .bubble th,.bubble td { border:1px solid var(--line); padding:6px 10px; text-align:left; }
  .bubble th { background:#efeefb; }

  .tools { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 0 45px; }
  .chip { font-size:11.5px; color:#5b4fd6; background:#efeefb; border:1px solid #e2def9;
    padding:4px 10px; border-radius:999px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }

  .loading { display:inline-flex; gap:5px; align-items:center; padding:3px 0; }
  .loading span { width:8px; height:8px; border-radius:50%; background:#b9bed3;
    animation:bounce 1.2s infinite ease-in-out both; }
  .loading span:nth-child(1){animation-delay:-.24s;} .loading span:nth-child(2){animation-delay:-.12s;}
  @keyframes bounce { 0%,80%,100%{transform:scale(.5);opacity:.4;} 40%{transform:scale(1);opacity:1;} }

  /* welcome / empty state */
  .welcome { margin:auto; text-align:center; color:var(--muted); max-width:460px; }
  .welcome .big { font-size:40px; }
  .welcome h2 { color:var(--ink); margin:10px 0 6px; font-size:20px; }
  .sugg { display:flex; flex-wrap:wrap; gap:9px; justify-content:center; margin-top:18px; }
  .sugg button { border:1px solid var(--line); background:#fff; color:var(--ink); cursor:pointer;
    padding:9px 14px; border-radius:12px; font-size:13px; transition:.15s; }
  .sugg button:hover { border-color:var(--accent2); color:var(--accent1); transform:translateY(-1px);
    box-shadow:0 6px 16px rgba(109,94,252,.14); }

  footer { padding:14px 18px; border-top:1px solid var(--line); background:#fbfbfe; }
  form { display:flex; gap:10px; align-items:center; }
  #input { flex:1; padding:13px 17px; border-radius:14px; border:1px solid var(--line);
    background:#fff; color:var(--ink); font-size:14.5px; outline:none; transition:.15s; }
  #input:focus { border-color:var(--accent2); box-shadow:0 0 0 4px rgba(139,92,246,.12); }
  #send { width:46px; height:46px; flex:0 0 46px; border:0; border-radius:14px; cursor:pointer;
    display:grid; place-items:center; color:#fff;
    background:linear-gradient(135deg,var(--accent1),var(--accent2)); transition:.15s; }
  #send:hover { transform:translateY(-1px); box-shadow:0 8px 20px rgba(109,94,252,.35); }
  #send:disabled { opacity:.45; cursor:default; transform:none; box-shadow:none; }
  #send svg { width:20px; height:20px; }

  @media (max-width:560px){ body{padding:0;} .app{height:100vh; max-height:none; border-radius:0;} }
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">🤖</div>
    <div>
      <div class="htitle">MCP Agent</div>
      <div class="hsub">chats with the API through the MCP server</div>
    </div>
    <div class="badge"><span class="dot"></span><span id="prov">connected</span></div>
  </header>

  <div id="chat"></div>

  <footer>
    <form id="form">
      <input id="input" autocomplete="off" placeholder="Ask about products or sales…" autofocus />
      <button id="send" type="submit" aria-label="Send">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
             stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
      </button>
    </form>
  </footer>
</div>

<script>
  marked.setOptions({ breaks:true });
  const sessionId = Math.random().toString(36).slice(2);
  const chat = document.getElementById("chat");
  const form = document.getElementById("form");
  const input = document.getElementById("input");
  const send = document.getElementById("send");
  const prov = document.getElementById("prov");

  const SUGGESTIONS = [
    "Top 3 products by revenue",
    "What is the total sales revenue?",
    "Show products under $50",
    "What are the most expensive products?",
  ];

  function showWelcome() {
    const w = document.createElement("div");
    w.className = "welcome";
    w.innerHTML =
      '<div class="big">✨</div><h2>Ask me anything about the catalog</h2>' +
      '<div>I pull live data through MCP tools — products, prices, stock and sales.</div>' +
      '<div class="sugg">' + SUGGESTIONS.map(s => "<button>" + s + "</button>").join("") + '</div>';
    chat.appendChild(w);
    w.querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => { input.value = b.textContent; sendMessage(); }));
  }
  function clearWelcome() { const w = chat.querySelector(".welcome"); if (w) w.remove(); }

  function bubble(role, htmlOrText, isHtml) {
    const row = document.createElement("div");
    row.className = "row " + role;
    const av = document.createElement("div");
    av.className = "av " + role;
    av.textContent = role === "me" ? "🧑" : "🤖";
    const b = document.createElement("div");
    b.className = "bubble";
    if (isHtml) b.innerHTML = htmlOrText; else b.textContent = htmlOrText;
    row.appendChild(av); row.appendChild(b);
    chat.appendChild(row);
    chat.scrollTop = chat.scrollHeight;
    return { row, bubble: b };
  }
  function addTools(calls) {
    if (!calls || !calls.length) return;
    const d = document.createElement("div");
    d.className = "tools";
    d.innerHTML = calls.map(c =>
      '<span class="chip">🔧 ' + c.name + "(" + JSON.stringify(c.arguments) + ")</span>").join("");
    chat.appendChild(d);
    chat.scrollTop = chat.scrollHeight;
  }

  async function sendMessage() {
    const text = input.value.trim();
    if (!text) return;
    clearWelcome();
    bubble("me", text, false);
    input.value = "";
    send.disabled = true;

    const { row, bubble: b } = bubble("bot", "", true);
    b.innerHTML = '<span class="loading"><span></span><span></span><span></span></span>';
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });
      const data = await res.json();
      b.innerHTML = marked.parse(data.answer || "*(no answer)*");
      addTools(data.tool_calls);
    } catch (err) {
      b.innerHTML = "⚠️ Error: " + err;
    } finally {
      send.disabled = false;
      input.focus();
      chat.scrollTop = chat.scrollHeight;
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });

  fetch("/api/info").then(r => r.json()).then(d => {
    if (d.provider) prov.textContent = d.provider.charAt(0).toUpperCase() + d.provider.slice(1);
  }).catch(() => {});

  showWelcome();
</script>
</body>
</html>"""
