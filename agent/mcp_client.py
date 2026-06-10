"""Thin async wrapper around an MCP server over Streamable HTTP.

Uses the official MCP Python SDK client. Exposes two things the agent needs:
  - `list_tools()` -> the tool schemas to hand to the LLM
  - `call_tool(name, args)` -> run a tool and get text back

Kept open for the lifetime of a chat session via an async context manager so
we don't pay connection setup on every tool call.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MCPClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        # streamable_http_client yields (read, write, _) streams.
        read, write, _ = await self._stack.enter_async_context(
            streamable_http_client(self.url)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        assert self._session is not None
        result = await self._session.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments)
        # Concatenate any text content blocks into a single string result.
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts) if parts else "(no output)"
