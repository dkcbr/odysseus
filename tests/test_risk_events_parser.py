import pytest

from src.risk_events import (
    parse_risk_event,
    filter_by_category,
    sorted_by_date,
    RiskEventParseError,
)

# Real entities, copied verbatim from an actual read_graph() dump.

LEGACY_ENTITY = {
    "name": "riskevent:2026-07-22:factor1_dominance",
    "entityType": "RiskEvent",
    "observations": [
        "timestamp: 2026-07-22T00:00:00Z",
        "category: FactorDominance",
        "severity: warning (interpretive judgment based on the size of the "
        "deviation below -- no formal severity thresholds were defined or "
        "used last night)",
        "description: factor:1_crypto contributed 94.5% of portfolio variance "
        "against a 50% budget target, a deviation of +44.5%.",
        'json: {"budgeted":0.50,"actual":0.945,"deviation":0.445}',
        "canonicalId: riskevent:2026_07_22:factor1_dominance",
    ],
}

MODERN_FACTORDOMINANCE = {
    "name": "riskevent:2026-08-02:factordominance",
    "entityType": "RiskEvent",
    "observations": [
        "date: 2026-08-02", "category: FactorDominance", "severity: warning",
        "detail: Factor 1 explains 40.7% of variance, over 2x the 12.5% fair "
        "share for 8 factors. Top loadings: TOXR, GXRP, XRPZ, XRPI, XRP.",
        "canonicalId: riskevent:2026_08_02:factordominance",
        "live_verification: Public.com brokerage account checked directly on "
        "2026-08-02 shows actual crypto/XRP-cluster concentration at 45.4% "
        "of portfolio equity...",
    ],
}

MODERN_CORRELATIONBREAKDOWN = {
    "name": "riskevent:2026-08-02:correlationbreakdown",
    "entityType": "RiskEvent",
    "observations": [
        "date: 2026-08-02", "category: CorrelationBreakdown", "severity: warning",
        "detail: 3 pair(s) moved from low baseline correlation to high recent "
        "correlation (last 20 days): ENPH/SCHD: -0.02->-0.72; ICLN/SCHD: "
        "-0.11->-0.75; MP/SCHD: -0.22->-0.71.",
        "canonicalId: riskevent:2026_08_02:correlationbreakdown",
        "dust_vs_real_assessment: Of the 3 flagged pairs, ENPH/SCHD is judged "
        "likely noise given ENPH is a near-dust-sized position...",
    ],
}

CORRELATIONREGIME_ENTITY = {
    "name": "riskevent:2026-08-03:correlationregime:mp_schd",
    "entityType": "RiskEvent",
    "observations": [
        "date: 2026-08-03", "category: CorrelationRegime", "pair: MP/SCHD",
        "rho_recent: -0.708 (window: 20d, real, matches independently-verified "
        "prior computation)",
        "rho_baseline: 0.088 (window: 90d ending immediately before recent "
        "window -- explicit documented choice, NOT the original "
        "correlationbreakdown node's unconfirmed baseline methodology)",
        "delta: -0.7959", "classification: breakdown",
        "source: real daily OHLCV via yfinance, log returns, computed "
        "2026-08-03T01:30:30.869622+00:00",
    ],
}


def test_rejects_malformed_name():
    with pytest.raises(RiskEventParseError):
        parse_risk_event({"name": "not_a_riskevent", "observations": []})


def test_legacy_entity_date_from_name_not_timestamp_field():
    result = parse_risk_event(LEGACY_ENTITY)
    # date comes from the name, not the "timestamp:" observation --
    # confirms this even though the observation format differs from modern
    assert result["date"] == "2026-07-22"
    assert result["category"] == "FactorDominance"


def test_legacy_entity_uses_description_not_detail():
    result = parse_risk_event(LEGACY_ENTITY)
    assert result["detail"]["text"].startswith("factor:1_crypto contributed 94.5%")


def test_legacy_severity_kept_raw_not_cleaned():
    result = parse_risk_event(LEGACY_ENTITY)
    # real value has trailing commentary -- not stripped, matching the
    # same discipline used for legacy vol_state in the regime module
    assert result["severity"].startswith("warning")
    assert "interpretive judgment" in result["severity"]


def test_legacy_extra_captures_json_field():
    result = parse_risk_event(LEGACY_ENTITY)
    assert "json" in result["detail"]["extra"]
    assert "0.945" in result["detail"]["extra"]["json"]


def test_modern_factordominance_basic_shape():
    result = parse_risk_event(MODERN_FACTORDOMINANCE)
    assert result["date"] == "2026-08-02"
    assert result["category"] == "FactorDominance"
    assert result["severity"] == "warning"  # clean, no commentary in modern entities
    assert result["detail"]["text"].startswith("Factor 1 explains 40.7%")


def test_modern_factordominance_captures_adhoc_extra_field():
    result = parse_risk_event(MODERN_FACTORDOMINANCE)
    assert "live_verification" in result["detail"]["extra"]


def test_modern_correlationbreakdown_captures_different_adhoc_field():
    result = parse_risk_event(MODERN_CORRELATIONBREAKDOWN)
    assert result["category"] == "CorrelationBreakdown"
    assert "dust_vs_real_assessment" in result["detail"]["extra"]


def test_correlationregime_has_no_severity_and_no_text_detail():
    # The real, defining difference of this third shape.
    result = parse_risk_event(CORRELATIONREGIME_ENTITY)
    assert result["severity"] is None
    assert "text" not in result["detail"]


def test_correlationregime_numeric_fields_parsed_with_commentary_stripped():
    result = parse_risk_event(CORRELATIONREGIME_ENTITY)
    d = result["detail"]
    assert d["pair"] == "MP/SCHD"
    assert d["rho_recent"] == pytest.approx(-0.708)
    assert d["rho_baseline"] == pytest.approx(0.088)
    assert d["delta"] == pytest.approx(-0.7959)
    assert d["classification"] == "breakdown"
    # raw text preserved alongside the parsed float -- no information lost
    assert "window: 20d" in d["rho_recent_raw"]
    assert "NOT the original" in d["rho_baseline_raw"]


def test_raw_observations_always_preserved_unmodified():
    for entity in (LEGACY_ENTITY, MODERN_FACTORDOMINANCE, CORRELATIONREGIME_ENTITY):
        result = parse_risk_event(entity)
        assert result["raw_observations"] == entity["observations"]


def test_filter_by_category():
    parsed = [
        parse_risk_event(MODERN_FACTORDOMINANCE),
        parse_risk_event(MODERN_CORRELATIONBREAKDOWN),
        parse_risk_event(CORRELATIONREGIME_ENTITY),
    ]
    fd = filter_by_category(parsed, "FactorDominance")
    assert len(fd) == 1
    assert fd[0]["name"] == MODERN_FACTORDOMINANCE["name"]


def test_sorted_by_date_newest_first_default():
    parsed = [parse_risk_event(LEGACY_ENTITY), parse_risk_event(CORRELATIONREGIME_ENTITY)]
    result = sorted_by_date(parsed)
    assert result[0]["date"] == "2026-08-03"
    assert result[1]["date"] == "2026-07-22"


def test_sorted_by_date_oldest_first():
    parsed = [parse_risk_event(CORRELATIONREGIME_ENTITY), parse_risk_event(LEGACY_ENTITY)]
    result = sorted_by_date(parsed, newest_first=False)
    assert result[0]["date"] == "2026-07-22"
