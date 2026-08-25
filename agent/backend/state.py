"""Process-wide singletons that outlive any single request.

Routers reach the `DynamicMCPManager` via `request.app.state.mcp` (regular
FastAPI DI). The APScheduler job function has no `Request` to hand it one —
it runs on a timer, not behind a route — so it needs a plain module-level
handle. Both point at the *same* manager instance, set once in
`backend/main.py`'s lifespan.
"""
from __future__ import annotations

from core.mcp_manager import DynamicMCPManager

mcp_manager: DynamicMCPManager | None = None
