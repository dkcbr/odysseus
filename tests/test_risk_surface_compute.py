import pytest

from src.risk_surface import compute_risk_surface


def _bar(ts, c, v=1000.0):
    return {"timestamp": ts, "open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": v}


# Minimal synthetic entities exercising each module's real, verified shape
# (not the full graph dump -- that's covered by the per-module validation
# scripts; this test verifies the *assembly* logic, i.e. that
# compute_risk_surface correctly wires the four modules together).

FACTOR_ENTITY_WITH_SYMBOL = {
    "name": "factor:2026-08-01:2",
    "entityType": "Factor",
    "observations": ["date: 2026-08-01", "top_loadings: TESTSYM (0.42), OTHER (-0.1)"],
}
FACTOR_ENTITY_LEGACY = {
    "name": "factor:1_crypto",  # expected to be skipped, not fatal
    "entityType": "Factor",
    "observations": ["top_loadings: XRP (-0.3)"],
}

REGIME_ENTITY = {
    "name": "regime:2026-08-01",
    "entityType": "Regime",
    "observations": ["date: 2026-08-01", "vol_level: NORMAL", "current_vol_annualized: 0.35"],
}

RISKEVENT_ENTITY = {
    "name": "riskevent:2026-08-01:factordominance",
    "entityType": "RiskEvent",
    "observations": ["date: 2026-08-01", "category: FactorDominance", "severity: warning",
                      "detail: test event"],
}
RISKEVENT_MALFORMED = {"name": "not_a_riskevent", "observations": []}  # expected to be skipped


def test_assembles_all_four_subsystems():
    bars = [_bar(i * 900, 10 + i * 0.01) for i in range(20)]
    result = compute_risk_surface(
        "TESTSYM", bars,
        [FACTOR_ENTITY_WITH_SYMBOL], [REGIME_ENTITY], [RISKEVENT_ENTITY],
        timeframe="15m",
    )
    assert result["symbol"] == "TESTSYM"
    assert result["geometry"]["symbol"] == "TESTSYM"
    assert result["geometry"]["timeframe"] == "15m"
    assert len(result["factor_exposure"]) == 1
    assert result["factor_exposure"][0]["loading"] == pytest.approx(0.42)
    assert result["regime"]["current"]["vol_label"] == "NORMAL"
    assert len(result["events"]) == 1
    assert result["events"][0]["category"] == "FactorDominance"


def test_malformed_entities_skipped_not_fatal():
    bars = [_bar(i * 900, 10) for i in range(20)]
    # legacy factor entity + malformed risk event both present alongside
    # good ones -- the whole call should still succeed
    result = compute_risk_surface(
        "TESTSYM", bars,
        [FACTOR_ENTITY_WITH_SYMBOL, FACTOR_ENTITY_LEGACY],
        [REGIME_ENTITY],
        [RISKEVENT_ENTITY, RISKEVENT_MALFORMED],
    )
    assert len(result["factor_exposure"]) == 1  # only the daily-format one contributes
    assert len(result["events"]) == 1  # only the well-formed one survives


def test_symbol_not_in_any_factor_returns_empty_exposure_not_error():
    bars = [_bar(i * 900, 10) for i in range(20)]
    result = compute_risk_surface(
        "NOTFOUND", bars, [FACTOR_ENTITY_WITH_SYMBOL], [REGIME_ENTITY], [RISKEVENT_ENTITY],
    )
    assert result["factor_exposure"] == []


def test_empty_regime_entities_gives_none_current_not_error():
    bars = [_bar(i * 900, 10) for i in range(20)]
    result = compute_risk_surface("TESTSYM", bars, [], [], [])
    assert result["regime"]["current"] is None
    assert result["regime"]["history"] == []
    assert result["events"] == []
    assert result["factor_exposure"] == []
    # geometry still computes fine with no graph data at all -- it's independent
    assert result["geometry"]["symbol"] == "TESTSYM"


def test_regime_history_respects_n_param():
    bars = [_bar(i * 900, 10) for i in range(20)]
    regimes = [
        {**REGIME_ENTITY, "name": f"regime:2026-08-0{i}",
         "observations": [f"date: 2026-08-0{i}", "vol_level: NORMAL"]}
        for i in range(1, 6)
    ]
    result = compute_risk_surface(
        "TESTSYM", bars, [], regimes, [], regime_history_n=2,
    )
    assert len(result["regime"]["history"]) == 2


def test_recent_events_n_param_limits_and_sorts_newest_first():
    bars = [_bar(i * 900, 10) for i in range(20)]
    events = [
        {**RISKEVENT_ENTITY, "name": f"riskevent:2026-08-0{i}:factordominance",
         "observations": [f"date: 2026-08-0{i}", "category: FactorDominance", "severity: warning"]}
        for i in range(1, 6)
    ]
    result = compute_risk_surface(
        "TESTSYM", bars, [], [], events, recent_events_n=2,
    )
    assert len(result["events"]) == 2
    assert result["events"][0]["date"] == "2026-08-05"  # newest first
