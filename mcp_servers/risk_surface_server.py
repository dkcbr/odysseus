"""
mcp_servers/risk_surface_server.py

MCP server exposing the risk-surface computation pipeline
(src/geometry, src/factors, src/regime, src/risk_events, src/risk_surface)
as an agent-callable tool, avoiding the HTTP-auth gap that blocks
routes/risk_surface.py from being called by generic MCP fetch tools
(confirmed real: wigolo.fetch reaches that route but gets 401, since MCP
tools carry no session cookie).

Follows the exact real pattern of mcp_servers/rag_server.py (the only
verified-working Python MCP server precedent in this codebase): the
official `mcp` SDK, Server + stdio_server() + server.run(), not a
run_stdio_server() method (which doesn't exist in that SDK).
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk_surface import compute_risk_surface
from src.risk_surface_fetchers import fetch_graph_entities, fetch_ohlcv

server = Server("risk_surface")

_TOOL_NAME = "compute_risk_surface"  # plain snake_case, matching real precedent (manage_rag, create_entities, etc -- no dotted names seen anywhere in this codebase)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=_TOOL_NAME,
            description=(
                "Compute a unified risk surface for a symbol: real OHLCV-derived "
                "geometry (retracement/slope/stability/volatility/range), factor "
                "exposure, current+recent regime, and recent risk events. Returns "
                "the same envelope as GET /api/risk/surface/{symbol}."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Exchange trading symbol for OHLCV, e.g. 'HBARUSDT' (Binance.US spot).",
                    },
                    "graph_symbol": {
                        "type": "string",
                        "description": (
                            "Optional: the risk engine's own symbol vocabulary, e.g. "
                            "'HBAR' (distinct from the exchange symbol -- confirmed real, "
                            "not interchangeable). Defaults to symbol unchanged if omitted."
                        ),
                    },
                    "timeframe": {
                        "type": "string",
                        "description": "Binance.US kline interval, default '15m'.",
                    },
                    "window": {
                        "type": "integer",
                        "description": "Number of real (non-filler) bars for geometry, default 120.",
                    },
                },
                "required": ["symbol"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != _TOOL_NAME:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    symbol = arguments.get("symbol")
    if not symbol:
        return [TextContent(type="text", text="Error: 'symbol' is required")]

    graph_symbol = arguments.get("graph_symbol") or symbol
    timeframe = arguments.get("timeframe", "15m")
    window = arguments.get("window", 120)

    try:
        ohlcv_bars = await fetch_ohlcv(symbol, timeframe, window)
    except Exception as e:
        return [TextContent(type="text", text=f"Error: OHLCV fetch failed: {e}")]

    if len(ohlcv_bars) < 2:
        msg = (
            f"Error: insufficient real (non-filler) bars for {symbol!r} at "
            f"{timeframe!r} (got {len(ohlcv_bars)}, need at least 2) -- check "
            f"the symbol is a valid Binance.US trading pair"
        )
        return [TextContent(type="text", text=msg)]

    try:
        factor_entities = await fetch_graph_entities("Factor")
        regime_entities = await fetch_graph_entities("Regime")
        riskevent_entities = await fetch_graph_entities("RiskEvent")
    except Exception as e:
        return [TextContent(type="text", text=f"Error: graph fetch failed: {e}")]

    surface = compute_risk_surface(
        graph_symbol, ohlcv_bars, factor_entities, regime_entities, riskevent_entities,
        timeframe=timeframe,
    )
    surface["symbol"] = symbol
    surface["graph_symbol_used"] = graph_symbol

    return [TextContent(type="text", text=json.dumps(surface))]


# Real connection parameters for the ONE server this tool actually needs,
# pulled directly from the live mcp_servers DB row (id 1751838b) --
# NOT hardcoded guesses.
_KG_SERVER_ID = "1751838b"
_KG_SERVER_NAME = "knowledge-graph-memory"
_KG_COMMAND = "npx"
_KG_ARGS = ["-y", "@modelcontextprotocol/server-memory"]
_KG_ENV = {"MEMORY_FILE_PATH": "/app/data/knowledge-graph.jsonl"}


async def run():
    # CRITICAL, learned the hard way: this subprocess does NOT inherit the
    # real running Odysseus process's live mcp_manager connections -- a
    # fresh `import app` creates a fresh, disconnected McpManager instance,
    # so this server must establish its own.
    #
    # The first version of this file called mcp_manager.connect_all_enabled()
    # here, which connects to ALL registered servers, not just the one this
    # tool needs. That caused a real production incident: every time this
    # server process got spawned (including retries), it re-triggered a full
    # reconnect-everything cascade, spawning duplicate copies of wigolo,
    # filesystem, memory, etc. Five concurrent copies of this server
    # multiplied into dozens of redundant child processes and brought the
    # host machine to a halt (confirmed via `ps aux`: 100-235% CPU per
    # risk_surface_server.py process, memory climbing from ~6GB to 25GB+
    # used). Fixed by connecting to ONLY the specific server this tool
    # actually needs, via connect_server() with its real parameters (above,
    # pulled directly from the live DB row) -- never connect_all_enabled()
    # from inside a server that is itself one of the things being connected.
    import app as _app

    try:
        await asyncio.wait_for(
            _app.mcp_manager.connect_server(
                server_id=_KG_SERVER_ID,
                name=_KG_SERVER_NAME,
                transport="stdio",
                command=_KG_COMMAND,
                args=_KG_ARGS,
                env=_KG_ENV,
            ),
            timeout=20,
        )
    except Exception as e:
        # Non-fatal: fetch_graph_entities will surface its own clear error
        # ("MCP server not connected: 1751838b") if this failed, rather than
        # this process refusing to start at all.
        print(f"risk_surface_server: knowledge-graph-memory connection failed (non-fatal): {e}", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
