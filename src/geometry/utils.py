"""Pure computation helpers for geometry metrics. No I/O, no graph access --
just numpy (already a project dependency)."""

import numpy as np


def linear_regression_slope(values):
    """Fit values ~ index via least squares. Returns (slope, residuals, fitted)."""
    n = len(values)
    x = np.arange(n)
    y = np.array(values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residuals = y - fitted
    return float(slope), residuals, fitted


def normalized_residual_error(residuals, price_scale):
    """RMS residual error normalized by price scale -- a dimensionless
    fraction, comparable across symbols/price levels."""
    if price_scale <= 0:
        return float("inf")
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    return rms / price_scale


def swing_high_low(highs, lows):
    """Swing high/low over the window: the window's max high and min low.
    Minimal, well-defined version -- a fractal-based detector could replace
    this later without changing compute_geometry's interface."""
    return max(highs), min(lows)


def log_returns(closes):
    closes = np.array(closes, dtype=float)
    if len(closes) < 2:
        return np.array([])
    return np.diff(np.log(closes))


def volatility(closes):
    """Standard deviation of log returns over the window."""
    r = log_returns(closes)
    if len(r) == 0:
        return 0.0
    return float(np.std(r))


def range_ratio(highs, lows, last_close):
    if last_close <= 0:
        return float("inf")
    return (max(highs) - min(lows)) / last_close
