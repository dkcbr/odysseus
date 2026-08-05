"""
routes/risk_surface.py

Real backend route exposing the risk-surface computation pipeline
(src/geometry, src/factors, src/regime, src/risk_events, src/risk_surface
-- 5 modules, 53 real tests) as a live, callable HTTP endpoint.

Fetch logic (graph entities, OHLCV) lives in src/risk_surface_fetchers.py,
a shared module also used by mcp_servers/risk_surface_server.py -- kept
here as route-local functions until the extraction, then imported.
"""

import time

from fastapi import APIRouter, Request, HTTPException
import httpx

from core.middleware import require_admin
from src.risk_surface import compute_risk_surface
from src.risk_surface_fetchers import fetch_graph_entities, fetch_ohlcv

router = APIRouter(prefix="/api/risk", tags=["risk-surface"])


@router.get("/surface/{symbol}")
async def get_risk_surface(
    request: Request,
    symbol: str,
    timeframe: str = "15m",
    window: int = 120,
    graph_symbol: str = None,
) -> dict:
    """Real, live risk-surface computation for one symbol.

    Query params:
      timeframe: Binance.US kline interval, default 15m
      window: number of real (non-filler) bars to use for geometry, default 120
      graph_symbol: optional override for the factor/regime/risk-event graph
        lookup. IMPORTANT, confirmed live: the OHLCV symbol (e.g. HBARUSDT,
        an exchange trading pair) and the graph symbol (e.g. HBAR, the risk
        engine's own portfolio-universe vocabulary) are two different real
        namespaces. Calling with only symbol=HBARUSDT correctly returns an
        empty factor_exposure, since the graph has no entity using that
        exact string. Pass graph_symbol explicitly to populate both for the
        same real asset. Defaults to symbol unchanged if not given -- this
        route never silently strips/guesses suffixes.

    Returns the compute_risk_surface() envelope plus symbol bookkeeping:
      symbol: the requested OHLCV symbol (echoed back as given)
      graph_symbol_used: whichever symbol was actually used for the graph lookups
      geometry, factor_exposure, regime, events: as produced by the module
      _meta: duration_ms, ohlcv_bars_used, graph_entities_checked counts
    """
    require_admin(request)

    t0 = time.monotonic()
    try:
        ohlcv_bars = await fetch_ohlcv(symbol, timeframe, window)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"OHLCV fetch failed: {e}")

    if len(ohlcv_bars) < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                f"insufficient real (non-filler) bars for {symbol!r} at {timeframe!r} "
                f"(got {len(ohlcv_bars)}, need at least 2) -- check the symbol is a "
                f"valid Binance.US trading pair"
            ),
        )

    factor_entities = await fetch_graph_entities("Factor")
    regime_entities = await fetch_graph_entities("Regime")
    riskevent_entities = await fetch_graph_entities("RiskEvent")

    lookup_symbol = graph_symbol if graph_symbol else symbol
    surface = compute_risk_surface(
        lookup_symbol, ohlcv_bars, factor_entities, regime_entities, riskevent_entities,
        timeframe=timeframe,
    )
    surface["symbol"] = symbol  # report back the requested OHLCV symbol
    surface["graph_symbol_used"] = lookup_symbol
    surface["_meta"] = {
        "duration_ms": round((time.monotonic() - t0) * 1000, 1),
        "ohlcv_bars_used": len(ohlcv_bars),
        "graph_entities_checked": {
            "Factor": len(factor_entities),
            "Regime": len(regime_entities),
            "RiskEvent": len(riskevent_entities),
        },
    }
    return surface
