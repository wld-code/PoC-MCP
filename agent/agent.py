"""Interactive CLI agent that chats with the API *through* the MCP server.

Flow:
  1. Connect to the MCP server (Streamable HTTP).
  2. Discover available tools.
  3. Start a REPL: each user message goes to the selected LLM provider, which
     may call MCP tools (which call the API) before producing an answer.

Run:  python agent.py            # interactive
      python agent.py "..."      # single question, then exit

Config (env): LLM_PROVIDER, LLM_MODEL, MCP_SERVER_URL, plus the provider's
API key (ANTHROPIC_API_KEY / OPENAI_API_KEY / MISTRAL_API_KEY).
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp_client import MCPClient
from providers import get_provider

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp")


async def run(one_shot: str | None) -> None:
    provider_name = os.environ.get("LLM_PROVIDER", "claude")
    print(f"• LLM provider: {provider_name}")
    print(f"• MCP server:   {MCP_SERVER_URL}")

    async with MCPClient(MCP_SERVER_URL) as mcp:
        tools = await mcp.list_tools()
        print(f"• Tools:        {', '.join(t['name'] for t in tools)}\n")

        provider = get_provider(provider_name)

        async def call_tool(name: str, args: dict) -> str:
            print(f"  ↪ calling tool: {name}({args})")
            return await mcp.call_tool(name, args)

        if one_shot is not None:
            answer = await provider.send(one_shot, tools, call_tool)
            print(f"\nassistant> {answer}")
            return

        print("Ask me about products or sales. Type 'exit' to quit.\n")
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user.lower() in {"exit", "quit"}:
                break
            if not user:
                continue
            answer = await provider.send(user, tools, call_tool)
            print(f"assistant> {answer}\n")


def main() -> None:
    one_shot = " ".join(sys.argv[1:]) or None
    asyncio.run(run(one_shot))


if __name__ == "__main__":
    main()
