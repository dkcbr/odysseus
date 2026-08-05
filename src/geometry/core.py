"""Pure, stateless geometry computation for a single symbol's OHLCV window.

No I/O: callers supply ohlcv_bars themselves. This module does not fetch
data, does not touch the knowledge graph, ingestion rules, or any other
Odysseus subsystem -- deliberately, per the agreed design.

Bar dict shape expected per element of ohlcv_bars:
    {"timestamp": int, "open": float, "high": float, "low": float,
     "close": float, "volume": float}
"""

from typing import List, Dict, Any

from .utils import (
    linear_regression_slope,
    normalized_residual_error,
    swing_high_low,
    volatility,
    range_ratio,
)


class GeometryInputError(ValueError):
    """Raised when ohlcv_bars is empty, too short, or malformed."""


REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def _validate_bars(ohlcv_bars: List[Dict[str, Any]]) -> None:
    if not ohlcv_bars:
        raise GeometryInputError("ohlcv_bars is empty")
    if len(ohlcv_bars) < 2:
        raise GeometryInputError(
            f"need at least 2 bars to compute geometry, got {len(ohlcv_bars)}"
        )
    for i, bar in enumerate(ohlcv_bars):
        missing = [f for f in REQUIRED_FIELDS if f not in bar]
        if missing:
            raise GeometryInputError(f"bar[{i}] missing fields: {missing}")


def compute_geometry(
    symbol: str,
    ohlcv_bars: List[Dict[str, Any]],
    timeframe: str = "unknown",
) -> Dict[str, Any]:
    """Compute price-geometry metrics for one symbol over one OHLCV window.

    Returns:
        {
          "symbol": ..., "timeframe": ...,
          "window": {"start_ts", "end_ts", "n_bars"},
          "metrics": {"retracement", "slope", "stability", "volatility",
                      "range_ratio"}
        }

    Raises GeometryInputError on empty/too-short/malformed input rather than
    silently returning nonsense.
    """
    _validate_bars(ohlcv_bars)

    bars = sorted(ohlcv_bars, key=lambda b: b["timestamp"])
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last_close = closes[-1]

    # Retracement: 0 = last close at the window's swing high, 1 = at the
    # swing low. Matches the convention already used in the earlier
    # HBARUSDT trade-level geometry analysis (entry_position metric).
    swing_high, swing_low = swing_high_low(highs, lows)
    swing_range = swing_high - swing_low
    retracement = (swing_high - last_close) / swing_range if swing_range > 0 else 0.0

    # Slope: regression of close vs. bar index, normalized by average price
    # so it's comparable across symbols/price levels.
    raw_slope, residuals, _fitted = linear_regression_slope(closes)
    avg_price = sum(closes) / len(closes)
    slope = raw_slope / avg_price if avg_price > 0 else 0.0

    # Stability: inverse of normalized residual error -- how cleanly the
    # regression line fits, independent of slope direction.
    norm_error = normalized_residual_error(residuals, avg_price)
    stability = 1.0 / (1.0 + norm_error)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "window": {
            "start_ts": bars[0]["timestamp"],
            "end_ts": bars[-1]["timestamp"],
            "n_bars": len(bars),
        },
        "metrics": {
            "retracement": round(retracement, 6),
            "slope": round(slope, 8),
            "stability": round(stability, 6),
            "volatility": round(volatility(closes), 6),
            "range_ratio": round(range_ratio(highs, lows, last_close), 6),
        },
    }
