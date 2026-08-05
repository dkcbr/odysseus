import pytest

from src.factors import (
    parse_entity_name,
    parse_loadings,
    parse_factor_entity,
    group_by_date,
    find_symbol_across_days,
    FactorParseError,
)


def test_parse_entity_name_extracts_date_and_component():
    result = parse_entity_name("factor:2026-07-26:3")
    assert result == {"date": "2026-07-26", "component_index": 3}


def test_parse_entity_name_rejects_legacy_format():
    # Legacy-era entities (factor:1_crypto, factor:2_secondary) are
    # deliberately out of scope for this parser -- different, undated
    # format from a separate era, per the live schema's documented
    # naming evolution.
    with pytest.raises(FactorParseError):
        parse_entity_name("factor:1_crypto")


def test_parse_entity_name_rejects_garbage():
    with pytest.raises(FactorParseError):
        parse_entity_name("not_a_factor_name")


# Real observation strings, copied verbatim from an actual read_graph() dump
# of the live knowledge graph -- not invented examples.
REAL_TOP_LOADINGS_1 = (
    "top_loadings: TOXR (-0.247), XRP (-0.247), GXRP (-0.247), "
    "XRPI (-0.246), XRPZ (-0.246), ETHE (-0.232), ETH (-0.232), ADA (-0.229)"
)
REAL_TOP_LOADINGS_2 = (
    "top_loadings: MP (-0.297), UUUU (-0.267), WDAY (0.251), "
    "ASTS (-0.25), TMC (-0.244), ICLN (-0.235), RGTI (-0.227), ENPH (-0.223)"
)


def test_parse_loadings_real_string_1():
    result = parse_loadings(REAL_TOP_LOADINGS_1)
    assert len(result) == 8
    assert result[0] == {"symbol": "TOXR", "loading": -0.247}
    assert result[-1] == {"symbol": "ADA", "loading": -0.229}


def test_parse_loadings_handles_variable_decimal_precision():
    # Real data has 1-3 decimal digits (e.g. "-0.25" has 2, "-0.247" has 3)
    # -- confirmed by scanning all 72 real daily factor entities.
    result = parse_loadings(REAL_TOP_LOADINGS_2)
    by_symbol = {r["symbol"]: r["loading"] for r in result}
    assert by_symbol["ASTS"] == -0.25  # 2 decimal digits in source
    assert by_symbol["MP"] == -0.297   # 3 decimal digits in source


def test_parse_loadings_handles_positive_values():
    result = parse_loadings(REAL_TOP_LOADINGS_2)
    by_symbol = {r["symbol"]: r["loading"] for r in result}
    assert by_symbol["WDAY"] == 0.251


def test_parse_loadings_works_with_or_without_prefix():
    with_prefix = parse_loadings(REAL_TOP_LOADINGS_1)
    without_prefix = parse_loadings(REAL_TOP_LOADINGS_1.replace("top_loadings: ", ""))
    assert with_prefix == without_prefix


def test_parse_loadings_empty_string_returns_empty_list():
    assert parse_loadings("top_loadings:") == []
    assert parse_loadings("") == []


def test_parse_loadings_symbols_normalized_uppercase():
    result = parse_loadings("top_loadings: msft (0.324), Wday (0.251)")
    symbols = {r["symbol"] for r in result}
    assert symbols == {"MSFT", "WDAY"}


def test_parse_factor_entity_full_real_shape():
    # Real entity shape, verified directly against the live graph dump.
    entity = {
        "name": "factor:2026-07-26:3",
        "entityType": "Factor",
        "observations": [
            "date: 2026-07-26",
            "variance_explained: 0.0686",
            "top_loadings: DBC (-0.491), MLPI (-0.475), INDA (0.341), "
            "MSFT (0.324), SOUN (0.277), WDAY (0.251), ABTC (0.211), SCHD (-0.142)",
            "canonicalId: factor:2026_07_26:3",
        ],
    }
    result = parse_factor_entity(entity)
    assert result["date"] == "2026-07-26"
    assert result["component_index"] == 3
    assert result["variance_explained"] == pytest.approx(0.0686)
    assert len(result["loadings"]) == 8
    assert result["loadings"][0] == {"symbol": "DBC", "loading": -0.491}


def test_parse_factor_entity_missing_top_loadings_returns_empty_not_error():
    entity = {
        "name": "factor:2026-07-27:1",
        "entityType": "Factor",
        "observations": ["date: 2026-07-27", "variance_explained: 0.5"],
    }
    result = parse_factor_entity(entity)
    assert result["loadings"] == []
    assert result["variance_explained"] == pytest.approx(0.5)


def test_parse_factor_entity_rejects_legacy_name():
    entity = {
        "name": "factor:1_crypto",
        "entityType": "Factor",
        "observations": ["description: Factor 1 loads heavily on XRP..."],
    }
    with pytest.raises(FactorParseError):
        parse_factor_entity(entity)


def test_group_by_date_shapes_output_per_design_spec():
    parsed = [
        {"date": "2026-07-26", "component_index": 1, "variance_explained": 0.4, "loadings": []},
        {"date": "2026-07-26", "component_index": 3, "variance_explained": 0.06, "loadings": []},
        {"date": "2026-07-27", "component_index": 1, "variance_explained": 0.5, "loadings": []},
    ]
    grouped = group_by_date(parsed)
    assert set(grouped.keys()) == {"2026-07-26", "2026-07-27"}
    assert [c["component_index"] for c in grouped["2026-07-26"]["components"]] == [1, 3]


def test_find_symbol_across_days_hbar_multi_index_case():
    # Real-world motivating case: HBAR's component index genuinely shifts
    # day to day (confirmed: 4, 7, 8 across different real days) -- this
    # is exactly why symbol-centric lookup, not fixed-index lookup, is
    # required for compute_risk_surface.
    parsed = [
        {"date": "2026-08-01", "component_index": 4, "variance_explained": None,
         "loadings": [{"symbol": "HBAR", "loading": 0.19}, {"symbol": "MSFT", "loading": 0.3}]},
        {"date": "2026-08-02", "component_index": 7, "variance_explained": None,
         "loadings": [{"symbol": "HBAR", "loading": 0.15}]},
        {"date": "2026-08-03", "component_index": 8, "variance_explained": None,
         "loadings": [{"symbol": "MSFT", "loading": 0.28}]},  # HBAR absent this day
    ]
    hits = find_symbol_across_days(parsed, "hbar")  # lowercase input, should normalize
    assert len(hits) == 2
    assert hits[0] == {"date": "2026-08-01", "component_index": 4, "loading": 0.19}
    assert hits[1] == {"date": "2026-08-02", "component_index": 7, "loading": 0.15}
