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

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class MCPClient:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout       # bounds the HTTP connect/request (fail fast on dead hosts)
        self.server_name: str = url  # filled from the server's initialize() reply
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        # Use a pre-built httpx client so we can bound the timeout (fail fast on
        # slow/dead servers). The exit stack closes the session before the client.
        http_client = await self._stack.enter_async_context(
            httpx.AsyncClient(timeout=self.timeout)
        )
        # streamable_http_client yields (read, write, _) streams.
        read, write, _ = await self._stack.enter_async_context(
            streamable_http_client(self.url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(
            ClientSession(read, write)
        )
        init = await self._session.initialize()
        # The server announces its own name in the initialize handshake.
        try:
            self.server_name = init.serverInfo.name
        except AttributeError:
            pass
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


class MultiMCPClient:
    """Fan-out wrapper that speaks to *several* MCP servers at once.

    A real agent rarely has a single tool source. Here it connects to two MCP
    servers — the ASAP orchestrator and the CVC car gateway — and presents them
    to the LLM as ONE flat list of tools. The LLM does not know (or care) that
    `get_vehicle` lives on one server and `activate_service` on another: it just
    sees a toolbox and picks tools by name.

    This class keeps a private map of tool-name -> owning client, so when the
    LLM asks to run a tool we route the call to the server that actually
    provides it. If two servers ever exported the same tool name, the first one
    connected wins (and we warn), which keeps routing unambiguous.

    Accepts a comma-separated string or a list of MCP endpoint URLs.
    """

    def __init__(self, urls: str | list[str]) -> None:
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.split(",") if u.strip()]
        if not urls:
            raise ValueError("MultiMCPClient needs at least one MCP server URL")
        self.urls = urls
        self._stack = AsyncExitStack()
        self._clients: list[MCPClient] = []
        self._owner: dict[str, MCPClient] = {}   # tool name -> client that owns it
        self._tools: list[dict[str, Any]] = []
        # Per-server view: [{name, url, tools: [{name, description, input_schema}]}]
        self._servers: list[dict[str, Any]] = []

    async def __aenter__(self) -> "MultiMCPClient":
        for url in self.urls:
            client = await self._stack.enter_async_context(MCPClient(url))
            self._clients.append(client)
            server_tools: list[dict[str, Any]] = []
            for tool in await client.list_tools():
                name = tool["name"]
                if name in self._owner:
                    # Name clash across servers: keep the first, skip the rest.
                    print(f"[MultiMCPClient] duplicate tool '{name}' from {url} ignored")
                    continue
                self._owner[name] = client
                self._tools.append(tool)
                server_tools.append(tool)
            self._servers.append({
                "name": client.server_name,
                "url": url,
                "tools": server_tools,
            })
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def servers(self) -> list[dict[str, Any]]:
        """The connected MCP servers, each with the tools it contributes."""
        return self._servers

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        client = self._owner.get(name)
        if client is None:
            return f"(error: no MCP server exposes a tool named '{name}')"
        return await client.call_tool(name, arguments)
