import random
import pytest

from src.geometry import compute_geometry, GeometryInputError


def _bar(ts, o, h, l, c, v=1000.0):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_rejects_empty_input():
    with pytest.raises(GeometryInputError):
        compute_geometry("TEST", [])


def test_rejects_single_bar():
    with pytest.raises(GeometryInputError):
        compute_geometry("TEST", [_bar(0, 1, 1, 1, 1)])


def test_rejects_malformed_bar_missing_field():
    bad = {"timestamp": 0, "open": 1, "high": 1, "low": 1}  # missing close, volume
    with pytest.raises(GeometryInputError):
        compute_geometry("TEST", [bad, _bar(1, 1, 1, 1, 1)])


def test_flat_price_series_has_zero_slope_and_zero_range_and_high_stability():
    bars = [_bar(i, 10, 10, 10, 10) for i in range(20)]
    result = compute_geometry("FLAT", bars, timeframe="1h")
    m = result["metrics"]
    assert m["slope"] == pytest.approx(0.0, abs=1e-9)
    assert m["range_ratio"] == pytest.approx(0.0, abs=1e-9)
    assert m["volatility"] == pytest.approx(0.0, abs=1e-9)
    assert m["stability"] == pytest.approx(1.0, abs=1e-6)
    assert m["retracement"] == pytest.approx(0.0, abs=1e-9)


def test_clean_uptrend_has_positive_slope_and_high_stability():
    bars = [_bar(i, 10 + i, 10 + i + 0.5, 10 + i - 0.5, 10 + i) for i in range(30)]
    result = compute_geometry("UP", bars, timeframe="1h")
    m = result["metrics"]
    assert m["slope"] > 0
    assert m["stability"] > 0.9
    assert m["retracement"] == pytest.approx(0.0, abs=0.05)


def test_clean_downtrend_has_negative_slope_and_high_retracement():
    bars = [_bar(i, 100 - i, 100 - i + 0.5, 100 - i - 0.5, 100 - i) for i in range(30)]
    result = compute_geometry("DOWN", bars, timeframe="1h")
    assert result["metrics"]["slope"] < 0
    assert result["metrics"]["retracement"] == pytest.approx(1.0, abs=0.05)


def test_noisy_choppy_series_has_lower_stability_than_clean_trend():
    random.seed(42)
    clean = [_bar(i, 10 + i, 10 + i + 0.5, 10 + i - 0.5, 10 + i) for i in range(30)]
    choppy = [
        _bar(i, 10, 10 + random.uniform(-2, 2), 10 - random.uniform(-2, 2),
             10 + random.uniform(-3, 3))
        for i in range(30)
    ]
    clean_result = compute_geometry("CLEAN", clean, timeframe="1h")
    choppy_result = compute_geometry("CHOPPY", choppy, timeframe="1h")
    assert clean_result["metrics"]["stability"] > choppy_result["metrics"]["stability"]


def test_window_metadata_reflects_input_bounds_and_count():
    bars = [_bar(100 + i * 900, 1, 1, 1, 1) for i in range(50)]
    result = compute_geometry("META", bars, timeframe="15m")
    assert result["window"]["n_bars"] == 50
    assert result["window"]["start_ts"] == 100
    assert result["window"]["end_ts"] == 100 + 49 * 900
    assert result["symbol"] == "META"
    assert result["timeframe"] == "15m"


def test_input_order_independent_sorted_by_timestamp():
    ordered = [_bar(i, 10 + i, 10 + i, 10 + i, 10 + i) for i in range(10)]
    shuffled = ordered[::-1]
    r1 = compute_geometry("A", ordered, timeframe="1h")
    r2 = compute_geometry("A", shuffled, timeframe="1h")
    assert r1["metrics"] == r2["metrics"]
    assert r1["window"] == r2["window"]
