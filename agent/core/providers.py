"""Provider-agnostic LLM layer.

Each provider keeps its own conversation history in the provider's native
message format and exposes a single coroutine:

    await provider.send(user_text, tools, call_tool) -> assistant_text

`tools` is the neutral MCP tool list ([{name, description, input_schema}]).
`call_tool(name, args)` is an async callback that runs an MCP tool and returns
its text output. Each provider runs its own tool-calling loop internally.

Default provider is Claude. Select with the LLM_PROVIDER env var:
    claude (default) | openai | openrouter | mistral | mock
SDKs are imported lazily so you only need the one you actually use.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

ToolList = list[dict[str, Any]]
CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]

SYSTEM_PROMPT = (
    "You are a connected-vehicle operations assistant for a car maker. You help "
    "with three systems, each exposed as MCP tools:\n"
    "  • CVC (the car gateway): read-only live data about vehicles — the fleet "
    "list, telemetry (online status, charge, location), and diagnostics.\n"
    "  • ASAP (the orchestrator): owns each service's DESIRED vs ACTUAL state per "
    "vehicle. activate_service/deactivate_service set the desired state and "
    "reconcile it by dispatching a Redbend campaign; service_states shows desired "
    "vs actual and whether they are in sync (a mismatch is a drift).\n"
    "  • Redbend (the OTA execution layer): actually pushes software to the car — "
    "firmware (FOTA), software (SOTA) and service-activation packages. Use it to "
    "read on-vehicle software/available updates, inspect campaigns, or run an OTA.\n"
    "  • Data Lake (analytics): massive aggregated data on how connected services "
    "and in-car applications are used across the WHOLE installed base — datasets, "
    "usage summaries, top applications, trends and flagged anomalies. Use it for "
    "big-picture / fleet-wide questions (not single-vehicle ones).\n"
    "Single-vehicle tools need a VIN; data-lake tools are fleet-wide and need none. If the user names an owner or a model instead, first "
    "call list_vehicles to resolve it to a VIN. Before activating a service it is "
    "good practice to check the vehicle is online via CVC. To turn a service on/off "
    "prefer ASAP's activate_service/deactivate_service (it sets desired state AND "
    "drives Redbend) rather than calling Redbend directly. Only state facts that "
    "came back from a tool — never invent data. Report final status and key steps. "
    "Be concise and clear."
)

MAX_TOOL_ROUNDS = 10  # safety cap on the agentic loop


class BaseProvider(ABC):
    @abstractmethod
    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        ...


# --------------------------------------------------------------------------- #
# Claude (Anthropic) — default                                                #
# --------------------------------------------------------------------------- #
class ClaudeProvider(BaseProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic

        # Pass an explicit key if given, else AsyncAnthropic reads ANTHROPIC_API_KEY.
        self.client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
        # `or` (not the get default) so an empty LLM_MODEL="" falls back too.
        self.model = model or os.environ.get("LLM_MODEL") or "claude-opus-4-8"
        self.messages: list[dict[str, Any]] = []

    def _format_tools(self, tools: ToolList) -> list[dict[str, Any]]:
        # MCP schema already matches the Anthropic tool shape.
        return [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]

    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        self.messages.append({"role": "user", "content": user_text})
        anthropic_tools = self._format_tools(tools)

        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=SYSTEM_PROMPT,
                tools=anthropic_tools,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")

            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    output = await call_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )
            self.messages.append({"role": "user", "content": tool_results})

        return "Stopped: exceeded the maximum number of tool-calling rounds."


# --------------------------------------------------------------------------- #
# OpenAI                                                                       #
# --------------------------------------------------------------------------- #
class OpenAIProvider(BaseProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key   # else AsyncOpenAI reads OPENAI_API_KEY
        if base_url:
            kwargs["base_url"] = base_url  # point at any OpenAI-compatible endpoint
        self.client = AsyncOpenAI(**kwargs)
        self.model = model or os.environ.get("LLM_MODEL") or "gpt-4o"
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def _format_tools(self, tools: ToolList) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        self.messages.append({"role": "user", "content": user_text})
        openai_tools = self._format_tools(tools)

        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=openai_tools,
            )
            msg = resp.choices[0].message
            self.messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                return msg.content or ""

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                output = await call_tool(tc.function.name, args)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": output}
                )

        return "Stopped: exceeded the maximum number of tool-calling rounds."


# --------------------------------------------------------------------------- #
# OpenRouter — one key, hundreds of models, OpenAI-compatible API              #
# --------------------------------------------------------------------------- #
class OpenRouterProvider(OpenAIProvider):
    """OpenRouter (https://openrouter.ai) speaks the OpenAI Chat Completions API,
    so we reuse the entire OpenAI agentic loop and tool format — only the client
    (base URL + key) and the default model id differ.

    `LLM_MODEL` selects any OpenRouter model that supports tool calling, e.g.
    "openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "google/gemini-flash-1.5".
    Key: OPENROUTER_API_KEY.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        super().__init__(
            model=model or os.environ.get("LLM_MODEL") or "openai/gpt-4o-mini",
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
            base_url=base_url or os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        )


# --------------------------------------------------------------------------- #
# Mistral                                                                      #
# --------------------------------------------------------------------------- #
class MistralProvider(BaseProvider):
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        from mistralai import Mistral

        self.client = Mistral(api_key=api_key or os.environ.get("MISTRAL_API_KEY", ""))
        self.model = model or os.environ.get("LLM_MODEL") or "mistral-large-latest"
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def _format_tools(self, tools: ToolList) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        self.messages.append({"role": "user", "content": user_text})
        mistral_tools = self._format_tools(tools)

        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.client.chat.complete_async(
                model=self.model,
                messages=self.messages,
                tools=mistral_tools,
            )
            msg = resp.choices[0].message
            self.messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": msg.tool_calls,
                }
            )

            if not msg.tool_calls:
                return msg.content or ""

            for tc in msg.tool_calls:
                raw = tc.function.arguments
                args = json.loads(raw) if isinstance(raw, str) else raw
                output = await call_tool(tc.function.name, args)
                self.messages.append(
                    {
                        "role": "tool",
                        "name": tc.function.name,
                        "tool_call_id": tc.id,
                        "content": output,
                    }
                )

        return "Stopped: exceeded the maximum number of tool-calling rounds."


# --------------------------------------------------------------------------- #
# Mock — no LLM, no API key. Deterministic, for tests / CI / offline demos.    #
# --------------------------------------------------------------------------- #
class MockProvider(BaseProvider):
    """Routes a question to MCP tools by keyword and returns their output.

    This exercises the *entire* agent → MCP → API path across BOTH servers
    (CVC + ASAP) without calling any real LLM, so end-to-end tests run with zero
    cost and no credentials. It is deliberately dumb keyword routing — not a real
    agent — but it proves the plumbing end to end. Tools that need a VIN trigger
    a quick `list_vehicles` lookup first, mimicking what a real LLM would do.
    """

    async def _first_vin(self, call_tool: CallTool) -> str | None:
        # MCP returns one JSON content block per list item, so the joined text
        # is several objects rather than one array. Just grab the first VIN.
        raw = await call_tool("list_vehicles", {})
        m = re.search(r'"vin"\s*:\s*"([^"]+)"', raw)
        return m.group(1) if m else None

    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        text = user_text.lower()
        names = {t["name"] for t in tools}

        # No-VIN routes first — including the fleet-wide data-lake analytics.
        if any(k in text for k in ("anomaly", "anomalies")) and "anomalies" in names:
            output = await call_tool("anomalies", {})
            return f"[mock:anomalies] {output}"
        if any(k in text for k in ("dataset", "data lake", "datalake")) and "list_datasets" in names:
            output = await call_tool("list_datasets", {})
            return f"[mock:list_datasets] {output}"
        if any(k in text for k in ("top app", "applications", "most used")) and "top_applications" in names:
            output = await call_tool("top_applications", {"limit": 5})
            return f"[mock:top_applications] {output}"
        if "trend" in text and "usage_trend" in names:
            output = await call_tool("usage_trend", {"application": "Live Navigation", "weeks": 8})
            return f"[mock:usage_trend] {output}"
        if any(k in text for k in ("usage", "active vehicles", "sessions", "adoption")) and "service_usage" in names:
            output = await call_tool("service_usage", {"period": "30d"})
            return f"[mock:service_usage] {output}"
        if "campaign" in text and "list_campaigns" in names:
            output = await call_tool("list_campaigns", {"limit": 5})
            return f"[mock:list_campaigns] {output}"
        if "operation" in text and "list_operations" in names:
            output = await call_tool("list_operations", {"limit": 5})
            return f"[mock:list_operations] {output}"
        if any(k in text for k in ("catalog", "catalogue", "service")) and not any(
                k in text for k in ("activate", "state", "desired", "actual", "sync")):
            output = await call_tool("list_services", {})
            return f"[mock:list_services] {output}"

        # Anything else likely needs a vehicle. Resolve a VIN first.
        vin = await self._first_vin(call_tool)

        if vin and "activate" in text and "activate_service" in names:
            output = await call_tool("activate_service", {"vin": vin, "service_code": "REMOTE_CLIMATE"})
            return f"[mock:activate_service] {output}"
        if vin and any(k in text for k in ("software", "firmware", "update", "fota", "sota", "version")):
            output = await call_tool("vehicle_software", {"vin": vin})
            return f"[mock:vehicle_software] {output}"
        if vin and any(k in text for k in ("desired", "actual", "sync", "subscription",
                                            "active service", "what services", "service state")):
            output = await call_tool("service_states", {"vin": vin})
            return f"[mock:service_states] {output}"
        if vin and any(k in text for k in ("diagnostic", "fault", "dtc", "health")):
            output = await call_tool("vehicle_diagnostics", {"vin": vin})
            return f"[mock:vehicle_diagnostics] {output}"
        if vin and any(k in text for k in ("location", "where", "gps", "position")):
            output = await call_tool("vehicle_location", {"vin": vin})
            return f"[mock:vehicle_location] {output}"
        if vin and any(k in text for k in ("telemetry", "online", "charge", "battery", "state", "status")):
            output = await call_tool("get_vehicle", {"vin": vin})
            return f"[mock:get_vehicle] {output}"

        # Default: list the fleet.
        output = await call_tool("list_vehicles", {})
        return f"[mock:list_vehicles] {output}"


def get_provider(name: str | None = None, model: str | None = None) -> BaseProvider:
    name = (name or os.environ.get("LLM_PROVIDER", "claude")).lower()
    if name == "claude":
        return ClaudeProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    if name == "openrouter":
        return OpenRouterProvider(model)
    if name == "mistral":
        return MistralProvider(model)
    if name == "mock":
        return MockProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER: {name!r} "
        "(use claude | openai | openrouter | mistral | mock)"
    )


# The "kinds" of LLM the UI can configure. A kind is the wire protocol /
# adapter; several providers share one (OpenAI and OpenRouter are both
# "openai-compatible"). Used by the LLM CRUD to build providers from a config.
LLM_KINDS = ["mock", "openai-compatible", "openai", "anthropic", "mistral"]


def build_provider(
    kind: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> BaseProvider:
    """Build a provider from an explicit config (used by the UI's LLM registry).

    `kind` selects the adapter; `api_key`/`base_url`/`model` are optional and fall
    back to environment defaults when omitted. "openai-compatible" is the generic
    case for OpenAI, OpenRouter, Together, local servers, etc.
    """
    kind = (kind or "").lower()
    if kind == "mock":
        return MockProvider()
    if kind == "anthropic":
        return ClaudeProvider(model, api_key=api_key)
    if kind in ("openai", "openai-compatible"):
        return OpenAIProvider(model, api_key=api_key, base_url=base_url)
    if kind == "mistral":
        return MistralProvider(model, api_key=api_key)
    raise ValueError(f"Unknown LLM kind: {kind!r} (use one of {', '.join(LLM_KINDS)})")


# Which provider each key env var unlocks. `mock` needs nothing. Used by the UI
# to show only the providers that are actually usable right now.
PROVIDER_KEYS = {
    "mock": None,
    "openrouter": "OPENROUTER_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

# A sensible default model id per provider (shown as a hint / placeholder in UI).
PROVIDER_DEFAULT_MODEL = {
    "mock": "(no LLM)",
    "openrouter": "qwen/qwen3-30b-a3b-instruct-2507",
    "claude": "claude-opus-4-8",
    "openai": "gpt-4o",
    "mistral": "mistral-large-latest",
}


def available_providers() -> list[dict[str, Any]]:
    """List providers with whether their key is configured in the environment."""
    out: list[dict[str, Any]] = []
    for name, key_env in PROVIDER_KEYS.items():
        out.append({
            "name": name,
            "key_env": key_env,
            "available": key_env is None or bool(os.environ.get(key_env)),
            "default_model": PROVIDER_DEFAULT_MODEL.get(name, ""),
        })
    return out
