"""Provider-agnostic LLM layer.

Each provider keeps its own conversation history in the provider's native
message format and exposes a single coroutine:

    await provider.send(user_text, tools, call_tool) -> assistant_text

`tools` is the neutral MCP tool list ([{name, description, input_schema}]).
`call_tool(name, args)` is an async callback that runs an MCP tool and returns
its text output. Each provider runs its own tool-calling loop internally.

Default provider is Claude. Select with the LLM_PROVIDER env var:
    claude (default) | openai | mistral
SDKs are imported lazily so you only need the one you actually use.
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

ToolList = list[dict[str, Any]]
CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]

SYSTEM_PROMPT = (
    "You are a helpful data analyst assistant. You answer questions about a "
    "product catalog and sales figures by calling the available tools. Only "
    "state numbers that came back from a tool — never invent data. Be concise."
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
    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY
        # `or` (not the get default) so an empty LLM_MODEL="" falls back too.
        self.model = os.environ.get("LLM_MODEL") or "claude-opus-4-8"
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
    def __init__(self) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI()  # reads OPENAI_API_KEY
        self.model = os.environ.get("LLM_MODEL") or "gpt-4o"
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
# Mistral                                                                      #
# --------------------------------------------------------------------------- #
class MistralProvider(BaseProvider):
    def __init__(self) -> None:
        from mistralai import Mistral

        self.client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
        self.model = os.environ.get("LLM_MODEL") or "mistral-large-latest"
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
    """Routes a question to one MCP tool by keyword and returns its output.

    This exercises the *entire* agent→MCP→API→SQLite path without calling any
    real LLM, so end-to-end tests run with zero cost and no credentials.
    """

    async def send(self, user_text: str, tools: ToolList, call_tool: CallTool) -> str:
        text = user_text.lower()
        names = {t["name"] for t in tools}

        if "top" in text and "top_products" in names:
            name, args = "top_products", {"limit": 3}
        elif any(k in text for k in ("summary", "total", "revenue", "sales")):
            name, args = "sales_summary", {}
        elif any(k in text for k in ("search", "price", "cost", "cheap", "expensive")):
            name, args = "search_products", {"min_price": 100}
        elif "product" in text and "list_products" in names:
            name, args = "list_products", {"limit": 5}
        else:
            name, args = "sales_summary", {}

        output = await call_tool(name, args)
        return f"[mock:{name}] {output}"


def get_provider(name: str | None = None) -> BaseProvider:
    name = (name or os.environ.get("LLM_PROVIDER", "claude")).lower()
    if name == "claude":
        return ClaudeProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "mistral":
        return MistralProvider()
    if name == "mock":
        return MockProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER: {name!r} (use claude | openai | mistral | mock)"
    )
