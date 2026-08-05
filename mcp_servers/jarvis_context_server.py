"""
jarvis_context_server.py

Built-in MCP server exposing a single read-only tool: get_jarvis_context.
Real, honest boundary: this tool can only VIEW state (health rollup, vault
graph, MCP server list) -- it cannot call other MCP tools, write memory,
open panels, or take any action. A view, not a control surface.

Runs as a genuinely separate subprocess (confirmed: built-in servers are
spawned via command=python, args=[script_path], not in-process), so it
calls back into the real, already-verified /api/jarvis/context REST route
over HTTP rather than trying to import the live app's in-process state
directly (which would be None in a fresh process, confirmed earlier
tonight the hard way).
"""

import asyncio
import os
import sys
import urllib.request

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("jarvis_context")

_INTERNAL_TOKEN = os.environ.get("ODYSSEUS_INTERNAL_TOKEN", "")
_CONTEXT_URL = "http://127.0.0.1:7000/api/jarvis/context"


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_jarvis_context",
            description=(
                "Get a real, read-only snapshot of Jarvis/Odysseus's current "
                "state: system health rollup and subsystem statuses, real "
                "vault notes with their tags (from the knowledge graph), and "
                "the list of currently connected MCP servers. This is a view "
                "only -- it does not call other tools, write memory, or take "
                "any action."
            ),
            inputSchema={"type": "object", "properties": {}},
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "get_jarvis_context":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    try:
        req = urllib.request.Request(
            _CONTEXT_URL,
            headers={"X-Odysseus-Internal-Token": _INTERNAL_TOKEN},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        body = resp.read().decode()
        return [TextContent(type="text", text=body)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error fetching Jarvis context: {e}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
