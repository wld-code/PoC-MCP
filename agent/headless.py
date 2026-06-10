"""Headless agent — no UI, no interaction. One question in, one answer out.

Designed for automation: cron jobs, CI steps, Kubernetes Jobs, shell pipelines.
It connects to the MCP server, lets the LLM use the MCP tools, prints the
answer, and exits with code 0 (or non-zero on failure).

Usage:
    python headless.py "What are the top 3 products by revenue?"
    QUESTION="Total revenue?" python headless.py
    python headless.py --json "Which products cost more than 400?"

Config (env): LLM_PROVIDER, LLM_MODEL, MCP_SERVER_URL, and the provider key
(ANTHROPIC_API_KEY / OPENAI_API_KEY / MISTRAL_API_KEY). Use LLM_PROVIDER=mock
to run without any LLM/key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from mcp_client import MCPClient
from providers import get_provider

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp")


async def ask(question: str, as_json: bool) -> int:
    provider_name = os.environ.get("LLM_PROVIDER", "claude")
    tool_calls: list[dict] = []

    async with MCPClient(MCP_SERVER_URL) as mcp:
        tools = await mcp.list_tools()
        provider = get_provider(provider_name)

        async def call_tool(name: str, args: dict) -> str:
            tool_calls.append({"name": name, "arguments": args})
            return await mcp.call_tool(name, args)

        answer = await provider.send(question, tools, call_tool)

    if as_json:
        print(
            json.dumps(
                {
                    "provider": provider_name,
                    "question": question,
                    "answer": answer,
                    "tool_calls": tool_calls,
                },
                indent=2,
            )
        )
    else:
        print(answer)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless MCP agent.")
    parser.add_argument("question", nargs="*", help="The question to ask.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON object with the answer and the tool calls made.",
    )
    args = parser.parse_args()

    question = " ".join(args.question) or os.environ.get("QUESTION", "")
    if not question:
        parser.error("provide a question as an argument or via QUESTION env var")

    sys.exit(asyncio.run(ask(question, args.json)))


if __name__ == "__main__":
    main()
