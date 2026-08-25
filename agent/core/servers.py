"""Resolve which MCP servers the agent should connect to.

The agent talks to four MCP servers in this tutorial:
  - the ASAP orchestrator  (service activation, desired/actual)  http://…:8011/mcp
  - the CVC car gateway    (vehicle telemetry)                    http://…:8012/mcp
  - the Redbend OTA platform (FOTA/SOTA + activation execution)   http://…:8013/mcp
  - the Datalake analytics (massive connected-services usage)     http://…:8014/mcp

`MCP_SERVER_URLS` is a comma-separated list. For backward compatibility we also
accept the old single-server `MCP_SERVER_URL`. If neither is set we default to
all four local servers, so `python headless.py "…"` just works after `compose up`.
"""
from __future__ import annotations

import os

_DEFAULT = ("http://localhost:8011/mcp,"
            "http://localhost:8012/mcp,"
            "http://localhost:8013/mcp,"
            "http://localhost:8014/mcp")

MCP_SERVER_URLS = (
    os.environ.get("MCP_SERVER_URLS")
    or os.environ.get("MCP_SERVER_URL")
    or _DEFAULT
)
