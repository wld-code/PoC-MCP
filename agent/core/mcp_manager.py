"""Dynamic MCP connection manager — add / remove MCP servers at runtime.

`MultiMCPClient` (in mcp_client.py) opens a fixed set of servers for the whole
process lifetime. This manager makes that set *editable while running*, which is
what the UI's MCP CRUD needs: connect a new server, see its tools appear in the
merged toolbox, disconnect it, and the tools vanish.

The tricky part is lifecycle. The MCP SDK's streamable-HTTP session uses anyio
task groups internally, and anyio requires that a context be *exited in the same
task that entered it*. So each connection gets its own dedicated "owner" task:
it opens the session, publishes the tools, parks until asked to stop, then closes
the session — all in one task. Tool calls are issued from request-handler tasks,
which is fine (the SDK's `call_tool` is safe to await from another task).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from core.mcp_client import MCPClient


def _slug(url: str) -> str:
    """A stable, URL-safe id derived from a server URL (no slashes).

    Server ids end up in REST paths (DELETE /api/mcp/servers/{id}); a raw URL id
    would contain '/' and break routing, so default ids are slugified.
    """
    s = re.sub(r"[^a-zA-Z0-9]+", "-", url).strip("-").lower()
    return s or "source"


async def _probe(url: str, timeout: float = 3.0) -> None:
    """Fast TCP reachability check. Raises if the host:port can't be reached.

    Doing this *before* opening the MCP session means an unreachable URL fails
    cleanly here — we never start (and then have to cancel) an anyio session,
    which is what makes runtime add/remove robust.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    fut = asyncio.open_connection(host, port)
    reader, writer = await asyncio.wait_for(fut, timeout=timeout)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


class ManagedServer:
    """One MCP connection with a self-contained open/serve/close lifecycle."""

    def __init__(self, server_id: str, url: str, connect_timeout: float = 8.0) -> None:
        self.id = server_id          # stable handle used by the CRUD (delete/update)
        self.url = url
        # Bound the HTTP timeout so an unreachable URL errors in-task (clean),
        # rather than hanging until we have to cancel (which is noisy with anyio).
        self.client = MCPClient(url, timeout=connect_timeout)
        self.tools: list[dict[str, Any]] = []
        self.status = "connecting"   # connecting | connected | error
        self.error: str | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        try:
            await self.client.__aenter__()
            self.tools = await self.client.list_tools()
            self.status = "connected"
        except Exception as exc:  # noqa: BLE001 — surface to the UI, don't crash
            self.status, self.error = "error", str(exc)
            self._ready.set()
            return
        self._ready.set()
        # Park here, keeping the session alive, until asked to stop.
        await self._stop.wait()
        try:
            await self.client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass

    async def start(self, timeout: float = 20.0) -> None:
        # 1) Fast reachability check — fail here (cleanly) for dead hosts.
        try:
            await _probe(self.url)
        except Exception as exc:  # noqa: BLE001
            self.status = "error"
            self.error = f"cannot reach {self.url}: {exc.__class__.__name__}"
            return
        # 2) Host is reachable — open the MCP session in its own owner task.
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.status = "error"
            self.error = f"MCP handshake timed out after {timeout:.0f}s"
            self._stop.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

    @property
    def server_name(self) -> str:
        if self.status == "connected":
            return self.client.server_name
        return self.id

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.server_name,
            "url": self.url,
            "status": self.status,
            "error": self.error,
            "tool_count": len(self.tools),
            "tools": [
                {"name": t["name"], "description": t["description"]} for t in self.tools
            ],
        }


class DynamicMCPManager:
    """A live, editable set of MCP connections presented as one toolbox."""

    def __init__(self) -> None:
        self._servers: dict[str, ManagedServer] = {}
        self._order: list[str] = []

    # --- CRUD ------------------------------------------------------------- #
    async def add(self, url: str, server_id: str | None = None) -> dict:
        sid = server_id or _slug(url)
        if sid in self._servers:
            raise ValueError(f"a server with id '{sid}' already exists")
        srv = ManagedServer(sid, url)
        await srv.start()
        self._servers[sid] = srv
        self._order.append(sid)
        return srv.view()

    async def remove(self, server_id: str) -> None:
        srv = self._servers.pop(server_id, None)
        if srv is None:
            raise KeyError(server_id)
        self._order.remove(server_id)
        await srv.stop()

    async def update(self, server_id: str, url: str) -> dict:
        if server_id not in self._servers:
            raise KeyError(server_id)
        # Replace in place (keep the same id and position in the list).
        idx = self._order.index(server_id)
        await self.remove(server_id)
        srv = ManagedServer(server_id, url)
        await srv.start()
        self._servers[server_id] = srv
        self._order.insert(idx, server_id)
        return srv.view()

    # --- read ------------------------------------------------------------- #
    def servers(self) -> list[dict]:
        return [self._servers[sid].view() for sid in self._order]

    async def list_tools(self) -> list[dict[str, Any]]:
        """Merged toolbox across all connected servers (first server wins on clash)."""
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for sid in self._order:
            srv = self._servers[sid]
            if srv.status != "connected":
                continue
            for tool in srv.tools:
                if tool["name"] in seen:
                    continue
                seen.add(tool["name"])
                out.append(tool)
        return out

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        for sid in self._order:
            srv = self._servers[sid]
            if srv.status == "connected" and any(t["name"] == name for t in srv.tools):
                return await srv.client.call_tool(name, arguments)
        return f"(error: no connected MCP server exposes a tool named '{name}')"

    async def aclose(self) -> None:
        for sid in list(self._order):
            await self.remove(sid)
