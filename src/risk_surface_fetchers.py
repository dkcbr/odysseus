"""
src/risk_surface_fetchers.py

Shared data-fetching layer for risk-surface computation, extracted from
routes/risk_surface.py so both the HTTP route and the future
mcp_servers/risk_surface_server.py MCP tool can use identical fetch logic
without duplication or drift.

No behavior change from the route's original private helpers -- verbatim
extraction, functions renamed to drop the leading underscore since they're
now a public, importable module.
"""

import json as _json

import httpx

_KG_ID = "1751838b"  # knowledge-graph-memory server id, same one used throughout
_BINANCEUS_KLINES = "https://api.binance.us/api/v3/klines"


async def fetch_graph_entities(entity_type: str) -> list:
    """Pull all entities of one type from the live graph, same call
    pattern as /api/graph/nodes in routes/diagnostics_routes.py.

    Note: this imports `app` internally (for app.mcp_manager), which only
    resolves correctly when called from within the running Odysseus
    process (HTTP route handler or an MCP server subprocess that Odysseus
    itself spawned with the right working directory/path) -- not
    standalone outside that context.
    """
    import app as _app

    graph_result = await _app.mcp_manager.call_tool(f"mcp__{_KG_ID}__read_graph", {})
    graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
    entities = graph_data.get("entities", [])
    return [e for e in entities if e.get("entityType") == entity_type]


async def fetch_ohlcv(symbol: str, timeframe: str, n_bars: int) -> list:
    """Fetch the most recent n_bars real candles for symbol from
    Binance.US spot (confirmed reachable from inside the Odysseus
    container; Bybit and Binance.com are geo-blocked from this host).
    Returns bars already shaped for compute_geometry(), and already
    excludes zero-volume filler bars (a confirmed real-data quirk of this
    source)."""
    params = {"symbol": symbol, "interval": timeframe, "limit": min(n_bars * 2, 1000)}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_BINANCEUS_KLINES, params=params)
        resp.raise_for_status()
        raw = resp.json()

    bars = []
    for k in raw:
        volume = float(k[5])
        if volume <= 0:
            continue  # synthetic no-trade filler bar, not real market data
        bars.append({
            "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": volume,
        })
    return bars[-n_bars:]
