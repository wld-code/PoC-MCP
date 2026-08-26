"""Runs one agent turn (one-shot or chat), reusing the existing MCP + provider
core (`core/mcp_manager.py`, `core/providers.py`) unchanged.

Unifies what `web.py` used to split into `_run_agent` (headless/triggers) and
the inline body of its `chat()` route (session-scoped, multi-turn) into one
place both the `chat` and `agent_runs`/`schedules` routers call — so there is
exactly one tool-calling code path instead of two near-duplicates.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.mcp_manager import DynamicMCPManager
from core.providers import BaseProvider, build_provider

from backend.crypto import decrypt_secret
from backend.models import LlmConfig, RunHistory

# Chat is interactive and session-scoped: a provider instance holds its own
# native-format conversation history (see BaseProvider subclasses), so a
# session must reuse the *same instance* across turns. This is process-local,
# in-memory state by design — a chat conversation living only as long as the
# server process is the same tradeoff web.py made, and persisting mid-turn
# LLM SDK conversation state to a DB is out of scope here.
_chat_sessions: dict[str, dict[str, Any]] = {}   # session_id -> {provider, llm_slug, model}
run_lock = asyncio.Lock()   # serializes tool calls against the shared DynamicMCPManager,
                              # same single-flight tradeoff web.py made (see mcp_manager.py docstring)


def explain_llm_error(provider_name: str, exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "credit balance is too low" in low or "billing" in low or "insufficient_quota" in low:
        title = "Insufficient API credits"
        detail = (f"The account behind LLM **{provider_name}** has no credits. Add credits, "
                  "or pick the **Mock** LLM to run with no real model.")
    elif "authentication" in low or " 401" in low or "invalid x-api-key" in low or "invalid api key" in low:
        title = "Invalid or missing API key"
        detail = f"The API key for LLM **{provider_name}** was rejected. Fix it in the **AI Models** tab."
    elif "rate limit" in low or " 429" in low or "overloaded" in low:
        title = "Rate limited / overloaded"
        detail = "Too many requests right now. Wait a few seconds and retry."
    else:
        title = "The language model request failed"
        detail = ("The MCP tools and the data APIs are fine — the failure is in the LLM call. "
                  "See the technical details below.")
    return (f"⚠️ **{title}**\n\n{detail}\n\n**LLM:** `{provider_name}`\n\n"
            f"**Raw error:**\n\n```\n{msg}\n```")


def provider_from_llm(cfg: LlmConfig, model_override: str | None) -> BaseProvider:
    model = (model_override or "").strip() or cfg.model or None
    api_key = decrypt_secret(cfg.encrypted_api_key) if cfg.encrypted_api_key else None
    return build_provider(cfg.kind, model=model, api_key=api_key, base_url=cfg.base_url or None)


async def run_once(
    mcp: DynamicMCPManager,
    db: AsyncSession,
    cfg: LlmConfig,
    model: str | None,
    question: str,
    source: str,
    user_id: int | None = None,
    schedule_id: int | None = None,
) -> RunHistory:
    """Run a one-shot agent turn (headless-via-API, or a fired schedule) and
    persist it to run_history. Never raises — failures are recorded as an
    error record, matching web.py's `_run_agent` contract."""
    tool_calls: list[dict] = []

    async def call_tool(name: str, args: dict) -> str:
        tool_calls.append({"name": name, "arguments": args})
        return await mcp.call_tool(name, args)

    answer, error = "", False
    async with run_lock:
        tools = await mcp.list_tools()
        try:
            provider = provider_from_llm(cfg, model)
            answer = await provider.send(question, tools, call_tool)
        except Exception as exc:  # noqa: BLE001 — never raise, record instead
            answer, error = explain_llm_error(cfg.name, exc), True

    rec = RunHistory(
        source=source, question=question, answer=answer, tool_calls=tool_calls, error=error,
        llm_label=cfg.name, model=model or cfg.model or "(default)",
        user_id=user_id, schedule_id=schedule_id,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def chat_turn(
    mcp: DynamicMCPManager,
    session_id: str,
    cfg: LlmConfig,
    model: str | None,
    message: str,
) -> tuple[str, list[dict], bool]:
    """Interactive multi-turn chat. Reuses the cached provider instance for
    this session_id as long as the LLM+model selection hasn't changed —
    otherwise starts a fresh conversation (same tradeoff web.py made)."""
    entry = _chat_sessions.get(session_id)
    model_key = (model or "").strip() or cfg.model
    if entry is None or entry["llm_slug"] != cfg.slug or entry["model"] != model_key:
        entry = {"provider": provider_from_llm(cfg, model), "llm_slug": cfg.slug, "model": model_key}
        _chat_sessions[session_id] = entry

    tool_calls: list[dict] = []

    async def call_tool(name: str, args: dict) -> str:
        tool_calls.append({"name": name, "arguments": args})
        return await mcp.call_tool(name, args)

    answer, error = "", False
    async with run_lock:
        tools = await mcp.list_tools()
        try:
            answer = await entry["provider"].send(message, tools, call_tool)
        except Exception as exc:  # noqa: BLE001
            answer, error = explain_llm_error(cfg.name, exc), True
    return answer, tool_calls, error


def invalidate_sessions_for(llm_slug: str) -> None:
    """Drop cached chat sessions using an LLM config that was just edited or
    deleted, so the next turn rebuilds the provider with fresh settings."""
    for sid in [sid for sid, e in _chat_sessions.items() if e["llm_slug"] == llm_slug]:
        del _chat_sessions[sid]
