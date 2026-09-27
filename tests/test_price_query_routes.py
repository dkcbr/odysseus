"""Regression tests for the standalone, non-agent-loop price-query
endpoint (routes/price_query_routes.py), built 2026-09-18 following the
prior session's real conclusion that the agent-loop interceptor approach
had unexplained non-deterministic behavior. This endpoint deliberately
runs entirely outside stream_agent_loop.

Tests the real, pure helper (_parse_ticker_lookup_output) directly --
the route function itself depends on FastAPI's Request/require_admin and
a real network call to Financial Modeling Prep, so it's exercised via
live, manual testing (see jarvis-todo.md) rather than mocked unit tests
here, matching this session's established preference for real
verification over synthetic mocks for network-dependent code.
"""

from routes.price_query_routes import _parse_ticker_lookup_output


def test_parses_real_ticker_lookup_output_format():
    # Exact real format TickerLookupTool.execute() produces
    # (finance_tools.py's own f-string), captured live.
    output = (
        "symbol: KTOS\n"
        "companyName: Kratos Defense & Security Solutions, Inc.\n"
        "price: 47.46\n"
        "change: -0.2 (-0.41964%)\n"
        "marketCap: 8899699200\n"
        "sector: Industrials\n"
        "industry: Aerospace & Defense\n"
        "exchange: NASDAQ Global Select\n"
        "isActivelyTrading: True\n"
        "description: Kratos Defense & Security Solutions, Inc. primarily..."
    )

    fields = _parse_ticker_lookup_output(output)

    assert fields["symbol"] == "KTOS"
    assert fields["companyName"] == "Kratos Defense & Security Solutions, Inc."
    assert fields["price"] == "47.46"
    assert fields["change"] == "-0.2 (-0.41964%)"
    assert fields["sector"] == "Industrials"
    assert fields["isActivelyTrading"] == "True"


def test_parses_empty_output_to_empty_dict():
    assert _parse_ticker_lookup_output("") == {}


def test_ignores_lines_without_a_colon_separator():
    # Real, defensive case: a malformed or partial line shouldn't crash
    # parsing or silently produce a garbage key.
    output = "symbol: KTOS\nthis line has no colon\nprice: 47.46"

    fields = _parse_ticker_lookup_output(output)

    assert fields == {"symbol": "KTOS", "price": "47.46"}
    assert len(fields) == 2


def test_value_containing_colon_is_preserved_whole():
    # partition() splits on the FIRST ": " only -- a value that itself
    # contains a colon (e.g. a ratio or a time) must not get truncated.
    output = "change: -0.2 (-0.41964%)\nnote: ratio 3:1 confirmed"

    fields = _parse_ticker_lookup_output(output)

    assert fields["note"] == "ratio 3:1 confirmed"
