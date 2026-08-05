import pytest

from src.regime import (
    parse_regime_entity,
    current_regime,
    regime_history,
    parse_riskregime_entity,
    RegimeParseError,
)

# Real entities, copied verbatim from an actual read_graph() dump.
LEGACY_ENTITY = {
    "name": "regime:2026-07-22:crypto_highvol",
    "entityType": "Regime",
    "observations": [
        "name: Crypto-Dominant High-Vol Regime",
        "start_date: 2026-07-22",
        "vol_state: high (annualized portfolio vol ~31-51% depending on method, "
        "both elevated for a diversified portfolio)",
        "description: Regime characterized by elevated volatility and Factor 1 "
        "dominance driven by XRP, TOXR, GXRP, XRPI, XRPZ, ETHE, GBTC, ETH, and ADA.",
        "dominant_factor: factor:1_crypto",
        "cluster: crypto",
        'json: {"notes":"Quarter-horizon PCA shows crypto cluster driving variance."}',
        "@2026-07-22T18:23:00Z initialized: true",
        "canonicalId: regime:2026_07_22:crypto_highvol",
    ],
}

MODERN_ENTITY_NORMAL = {
    "name": "regime:2026-07-26",
    "entityType": "Regime",
    "observations": [
        "date: 2026-07-26", "vol_level: NORMAL",
        "current_vol_annualized: 0.3714", "percentile_in_own_history: 62",
        "method: relative to this portfolio's own historical rolling-vol range "
        "(53 observations, 10-day window)",
        "canonicalId: regime:2026_07_26",
    ],
}

MODERN_ENTITY_LOW = {
    "name": "regime:2026-07-29",
    "entityType": "Regime",
    "observations": [
        "date: 2026-07-29", "vol_level: LOW",
        "current_vol_annualized: 0.3250", "percentile_in_own_history: 29",
        "method: relative to this portfolio's own historical rolling-vol range "
        "(55 observations, 10-day window)",
        "canonicalId: regime:2026_07_29",
    ],
}

RISKREGIME_SNAPSHOT = {
    "name": "riskregime:2026-08-03",
    "entityType": "RiskRegime",
    "observations": [
        "date: 2026-08-03", "primary_label: mixed_stress",
        'tags: ["crypto_dominant", "correlation_breakdown"]',
        "vol_level: NORMAL (percentile 60)",
        "crypto_factor_share: 0.40700000000000003",
        'correlation_breakdowns: ["ENPH/SCHD", "ICLN/SCHD", "MP/SCHD"]',
        "PLACEHOLDER_THRESHOLDS: crypto_share>0.35, correlation_magnitude>0.6, "
        "vol_percentile>=80 -- explicit, unvalidated first-pass values, not back-tested",
    ],
}

RISKREGIME_REFERENCE = {
    "name": "regime:correlation_breakdown",  # note: entityType RiskRegime, name doesn't start with riskregime:
    "entityType": "RiskRegime",
    "observations": [
        "created: 2026-08-03",
        "status: canonical -- elevated to a first-class regime, not a tag, "
        "per real back-test evidence",
        "evidence: consistently associated with the DEEPEST average and worst "
        "drawdowns across BOTH v1 and v2 back-tests",
    ],
}


def test_parse_modern_entity_uses_vol_level():
    result = parse_regime_entity(MODERN_ENTITY_NORMAL)
    assert result["date"] == "2026-07-26"
    assert result["era"] == "modern"
    assert result["vol_label"] == "NORMAL"
    assert result["vol_annualized"] == pytest.approx(0.3714)
    assert result["percentile"] == 62


def test_parse_modern_entity_low_vol():
    result = parse_regime_entity(MODERN_ENTITY_LOW)
    assert result["vol_label"] == "LOW"
    assert result["percentile"] == 29


def test_parse_legacy_entity_uses_vol_state_not_vol_level():
    result = parse_regime_entity(LEGACY_ENTITY)
    assert result["date"] == "2026-07-22"
    assert result["era"] == "legacy"
    # legacy value is free text -- starts with "high" but has trailing commentary,
    # confirmed real, not cleaned up here (that would be inventing structure
    # the source data doesn't have)
    assert result["vol_label"].startswith("high")
    # legacy entities have no current_vol_annualized/percentile fields at all
    assert result["vol_annualized"] is None
    assert result["percentile"] is None


def test_does_not_invent_unified_vocabulary():
    # Explicit regression test for the exact mistake already made and
    # corrected once this session: the parser must NOT map real values
    # onto an invented low_vol/mid_vol/high_vol scale.
    modern = parse_regime_entity(MODERN_ENTITY_NORMAL)
    legacy = parse_regime_entity(LEGACY_ENTITY)
    assert modern["vol_label"] not in ("low_vol", "mid_vol", "high_vol")
    assert legacy["vol_label"] not in ("low_vol", "mid_vol", "high_vol")
    assert modern["vol_label"] == "NORMAL"  # real value, real vocabulary
    assert legacy["vol_label"].startswith("high")  # different real vocabulary, same field concept


def test_rejects_undated_riskregime_reference_name():
    with pytest.raises(RegimeParseError):
        parse_regime_entity(RISKREGIME_REFERENCE)


def test_current_regime_picks_latest_modern_only():
    parsed = [
        parse_regime_entity(LEGACY_ENTITY),
        parse_regime_entity(MODERN_ENTITY_LOW),   # 2026-07-29
        parse_regime_entity(MODERN_ENTITY_NORMAL),  # 2026-07-26
    ]
    result = current_regime(parsed)
    assert result["date"] == "2026-07-29"  # latest by date among modern-era only
    assert result["era"] == "modern"


def test_current_regime_none_when_only_legacy():
    parsed = [parse_regime_entity(LEGACY_ENTITY)]
    assert current_regime(parsed) is None


def test_regime_history_sorted_oldest_to_newest_and_limited():
    parsed = [
        parse_regime_entity(MODERN_ENTITY_LOW),    # 07-29
        parse_regime_entity(MODERN_ENTITY_NORMAL),  # 07-26
    ]
    hist = regime_history(parsed)
    assert [r["date"] for r in hist] == ["2026-07-26", "2026-07-29"]
    limited = regime_history(parsed, n=1)
    assert [r["date"] for r in limited] == ["2026-07-29"]


def test_parse_riskregime_daily_snapshot_best_effort():
    result = parse_riskregime_entity(RISKREGIME_SNAPSHOT)
    assert result["date"] == "2026-08-03"
    assert result["primary_label"] == "mixed_stress"
    assert result["is_dated_snapshot"] is True
    # vol_level_raw kept raw on purpose -- includes trailing "(percentile 60)"
    assert result["vol_level_raw"] == "NORMAL (percentile 60)"


def test_parse_riskregime_reference_entity_mostly_none():
    result = parse_riskregime_entity(RISKREGIME_REFERENCE)
    assert result["date"] is None
    assert result["primary_label"] is None
    assert result["is_dated_snapshot"] is False
    assert result["name"] == "regime:correlation_breakdown"
