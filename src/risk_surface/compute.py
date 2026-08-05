"""Orchestration layer: assembles the four real input modules (geometry,
factors, regime, risk_events) into one combined risk-surface view for a
symbol. Pure glue -- no new computation, no reinterpretation, no invented
fields beyond what the individual modules already produce.

Everything is passed in explicitly: no hidden globals, no implicit graph
access. Callers are responsible for fetching OHLCV bars and graph entities
themselves (e.g. via memory_agent tasks + read_graph()).
"""

from typing import List, Dict, Any, Callable

from src.geometry import compute_geometry
from src.factors import parse_factor_entity, find_symbol_across_days, FactorParseError
from src.regime import parse_regime_entity, current_regime, regime_history, RegimeParseError
from src.risk_events import parse_risk_event, sorted_by_date, RiskEventParseError


def compute_risk_surface(
    symbol: str,
    ohlcv_bars: List[Dict[str, Any]],
    factor_entities: List[Dict[str, Any]],
    regime_entities: List[Dict[str, Any]],
    riskevent_entities: List[Dict[str, Any]],
    timeframe: str = "unknown",
    regime_history_n: int = 5,
    recent_events_n: int = 10,
) -> Dict[str, Any]:
    """Assemble the four real subsystems into one risk-surface object.

    Args:
        symbol: e.g. "HBARUSDT"
        ohlcv_bars: already-fetched OHLCV bars for `symbol` (this function
            does no I/O itself -- caller fetches, same principle as
            src.geometry.compute_geometry)
        factor_entities: raw Factor entities from read_graph() -- daily
            entities only matter for symbol lookup; legacy entities are
            silently skipped (find_symbol_across_days ignores anything
            that isn't successfully parsed as daily-format)
        regime_entities: raw Regime entities from read_graph()
        riskevent_entities: raw RiskEvent entities from read_graph()
        timeframe: passed through to compute_geometry, e.g. "15m"
        regime_history_n: how many recent regime days to include
        recent_events_n: how many recent risk events to include

    Returns:
        {
          "symbol": ...,
          "geometry": {...},               # exact compute_geometry() output
          "factor_exposure": [...],        # find_symbol_across_days() output
          "regime": {"current": {...} or None, "history": [...]},
          "events": [...],                 # most recent N, newest first
        }

    Malformed individual entities (wrong name format, etc.) are skipped
    rather than raising -- a single bad entity in a large real batch
    shouldn't take down the whole risk surface. This mirrors how the
    validation scripts for each module already handled real data (some
    entities, like legacy-era ones, are expected to not match the parser
    for a given lookup and that's normal, not an error state).
    """
    geometry = compute_geometry(symbol, ohlcv_bars, timeframe=timeframe)

    parsed_factors = []
    for e in factor_entities:
        try:
            parsed_factors.append(parse_factor_entity(e))
        except FactorParseError:
            continue  # expected: legacy-era entities don't match the daily format
    factor_exposure = find_symbol_across_days(parsed_factors, symbol)

    parsed_regimes = []
    for e in regime_entities:
        try:
            parsed_regimes.append(parse_regime_entity(e))
        except RegimeParseError:
            continue  # expected: entities matching neither modern nor legacy name format
    regime_block = {
        "current": current_regime(parsed_regimes),
        "history": regime_history(parsed_regimes, n=regime_history_n),
    }

    parsed_events = []
    for e in riskevent_entities:
        try:
            parsed_events.append(parse_risk_event(e))
        except RiskEventParseError:
            continue  # expected: malformed name, out of scope for this parser
    events = sorted_by_date(parsed_events)[:recent_events_n]

    return {
        "symbol": symbol,
        "geometry": geometry,
        "factor_exposure": factor_exposure,
        "regime": regime_block,
        "events": events,
    }
