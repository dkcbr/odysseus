"""Real, direct tests for src/portfolio_parser.py -- the parser separating
confirmed holdings from pending orders (added 2026-08-17, see the KTOS
"17 shares" investigation in the vault), and get_live_holdings, the
live-brokerage check that's retained but NOT currently called from
agent_loop.py's streaming path (real, structural async issue, tracked
in GitHub issue #12).
"""
import asyncio
import json
import tempfile
from datetime import datetime, timezone, timedelta
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.portfolio_parser import (
    parse_portfolio_context,
    get_confirmed_and_pending,
    get_live_holdings,
    get_freshness_recommendation,
)

# Real, minimal, self-contained fixture matching the actual document's two
# real table formats -- not the full, live document, so these tests don't
# depend on data/portfolio_context.md's real, changing content.
_FIXTURE = """
Some prose above the tables.

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| KTOS | 16 | $56.56 | $53.59 | $857.45 | -5.26% | Long-term conviction |
| MP | 11 | $50.00 | $58.90 | $647.90 | +17.8% | |

Some prose between the tables.

| Ticker | Qty | Limit | Side | Notes |
|--------|-----|-------|------|-------|
| KTOS | 1 | $46.50 | BUY | |
| MP | 3 | $50.00 | BUY | |
| MP | 1 | $54.00 | BUY | |
| MP | 2 | $75.00 | SELL | |
"""


def test_parses_confirmed_holdings():
    result = parse_portfolio_context(_FIXTURE)
    assert result.confirmed_holdings["KTOS"] == 16.0
    assert result.confirmed_holdings["MP"] == 11.0


def test_parses_pending_buy_orders():
    result = parse_portfolio_context(_FIXTURE)
    assert result.pending_qty_for("KTOS", "BUY") == 1.0
    assert result.pending_qty_for("MP", "BUY") == 4.0  # 3 + 1


def test_parses_pending_sell_orders():
    result = parse_portfolio_context(_FIXTURE)
    assert result.pending_qty_for("MP", "SELL") == 2.0
    assert result.pending_qty_for("KTOS", "SELL") == 0.0


def test_unknown_ticker_returns_zero_pending_not_error():
    result = parse_portfolio_context(_FIXTURE)
    assert result.pending_qty_for("ZZZZ", "BUY") == 0.0


# Real, added 2026-09-18: mirrors the exact real bug DK hit live --
# "How many shares of PL do I own?" answered 3 (only the Roth IRA
# holding), silently discarding a real, separate 30-share Taxable
# holding of the same ticker. The real document legitimately has this
# shape whenever a position is held in more than one real account.
_MULTI_ACCOUNT_FIXTURE = """
## Reconciled Holdings -- Public.com Taxable

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| PL | 30 | $21.83 | $16.92 | $507.60 | -22.48% | |

## Reconciled Holdings -- Public.com Roth IRA

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| PL | 3 | $16.60 | $16.92 | $50.76 | +1.95% | |
"""


def test_same_ticker_across_multiple_real_accounts_is_summed_not_overwritten():
    result = parse_portfolio_context(_MULTI_ACCOUNT_FIXTURE)
    assert result.confirmed_holdings["PL"] == 33.0
    assert "ZZZZ" not in result.confirmed_holdings


# Real, added 2026-09-20, following directly from the audit prompted by
# the multi-account bug above: a fixture mirroring the REAL, full,
# actual structure of data/portfolio_context.md (confirmed directly
# against the live document's real section order), not just the
# minimal two-table shape the original fixture used. Exercises the
# specific real risks the audit found:
#   - a Crypto section whose real table header says "Token", not
#     "Ticker" (so the section-boundary sentinel strings never match
#     it) -- confirmed live that its rows still get picked up as
#     confirmed holdings, correctly, but by regex-format coincidence
#     rather than deliberate section detection.
#   - a real account (Fidelity) with a pending-orders table but no
#     confirmed-holdings table at all.
#   - the real, live document's actual order of sections: Taxable
#     Holdings -> ETFs (same real header) -> Crypto ("Token" header) ->
#     Roth Holdings -> Roth Orders -> Taxable Orders -> Fidelity Orders
#     -> a later, unrelated table ("Crypto Unit Targets") that must NOT
#     be misparsed even though the in/out-of-orders toggle never resets
#     after the last real orders section in the actual document.
_FULL_STRUCTURE_FIXTURE = """
## Reconciled Holdings -- Public.com Taxable

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| KTOS | 19 | $55.97 | $53.59 | $1,018.21 | -4.25% | |

## ETFs -- Public.com Taxable

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| VOO | 2 | $550.00 | $560.00 | $1,120.00 | +1.8% | |

## Crypto -- Public.com Taxable

| Token | Qty | Basis | Current Price | Current Value | P&L % | Notes |
|-------|-----|-------|--------------|---------------|-------|-------|
| ADA | 2000 | $0.28 | $0.22 | $442.58 | -20.91% | |

## Reconciled Holdings -- Public.com Roth IRA

| Ticker | Shares | Basis | Current Price | Current Value | P&L % | Notes |
|--------|--------|-------|----------------|----------------|-------|-------|
| KTOS | 3 | $16.60 | $16.92 | $50.76 | +1.95% | |

## Active Limit Orders -- Public.com Roth IRA

| Ticker | Qty | Limit | Side | Notes |
|--------|-----|-------|------|-------|
| KTOS | 1 | $46.50 | BUY | |

## Active Limit Orders -- Public.com Taxable

| Ticker | Qty | Limit | Side | Notes |
|--------|-----|-------|------|-------|
| VOO | 1 | $540.00 | BUY | |

## Active Limit Orders -- Fidelity Roth IRA

| Ticker | Qty | Limit | Side | Notes |
|--------|-----|-------|------|-------|
| SPCX | 1 | $140.00 | BUY | Fidelity-only, unverified |

## Crypto Unit Targets

| Token | Taxable | Roth | Total | Target | Status |
|-------|---------|------|-------|--------|--------|
| ADA | 2,000 | 0 | 2,000 | 2,000 | Complete |
"""


def test_crypto_section_with_token_header_still_counts_as_confirmed_holding():
    # Real, confirmed live: the Crypto table's header says "Token", not
    # "Ticker", so it never matches either section-boundary sentinel --
    # this test pins down that its rows are still correctly counted,
    # not silently dropped, and documents that this currently works by
    # regex-shape coincidence, not deliberate section-aware parsing.
    result = parse_portfolio_context(_FULL_STRUCTURE_FIXTURE)
    assert result.confirmed_holdings["ADA"] == 2000.0


def test_holdings_across_taxable_and_roth_and_etf_sections_all_summed():
    # KTOS appears in both Taxable (19) and Roth (3) real holdings
    # tables -- same real shape as the PL bug, pinned here with a
    # different ticker across the full, real document structure.
    result = parse_portfolio_context(_FULL_STRUCTURE_FIXTURE)
    assert result.confirmed_holdings["KTOS"] == 22.0
    assert result.confirmed_holdings["VOO"] == 2.0


def test_later_unrelated_table_after_last_orders_section_is_not_misparsed():
    # Real, important: the in/out-of-orders toggle never resets after
    # the Fidelity orders section (the last real orders table in the
    # actual document) -- there is no holdings-table sentinel after it
    # to flip back. Confirms this stays safe for the real "Crypto Unit
    # Targets" table specifically (its rows don't match _ORDER_ROW's
    # strict $-prefixed-column + literal BUY/SELL requirement), not
    # just assumed safe.
    result = parse_portfolio_context(_FULL_STRUCTURE_FIXTURE)
    order_tickers = {o.ticker for o in result.pending_orders}
    assert "ADA" not in order_tickers
    # And it must not have been silently miscounted as a confirmed
    # holding a second time via the orders path either.
    ada_orders = [o for o in result.pending_orders if o.ticker == "ADA"]
    assert ada_orders == []


def test_ticker_held_only_in_unverified_fidelity_has_no_confirmed_holding():
    # Real, honest edge case: Fidelity Roth IRA has a real pending-orders
    # table but no confirmed-holdings table at all (unverified, no real
    # API/MCP access exists for that brokerage). A ticker that only
    # appears there must not be reported as a confirmed holding, and
    # pending_qty_for must still correctly find its real pending order.
    result = parse_portfolio_context(_FULL_STRUCTURE_FIXTURE)
    assert "SPCX" not in result.confirmed_holdings
    assert result.pending_qty_for("SPCX", "BUY") == 1.0


def test_range_quantity_uses_low_end():
    fixture_with_range = _FIXTURE + "| NVDA | 1-2 | $190.00 | BUY | T1 rung |\n"
    result = parse_portfolio_context(fixture_with_range)
    assert result.pending_qty_for("NVDA", "BUY") == 1.0


# --- get_live_holdings ---
# Real, isolated tests -- mock the MCP manager rather than hit a real
# network call, matching this repo's existing test conventions
# (test_tool_index_schema_parity.py etc. avoid pulling in heavy
# real dependencies for unit tests).

def _fresh_wrapper():
    """Real, isolated PublicComWrapper instance -- avoids the shared,
    module-level cache/rate-limiter causing cross-test contamination
    (confirmed directly: without this, a success-case test's cached
    result was being returned by later, unrelated failure-case tests)."""
    from src.portfolio_parser import PublicComWrapper
    return PublicComWrapper(cache_ttl_seconds=60.0, min_call_interval_seconds=0.0, max_retries=1)


def test_get_live_holdings_returns_quantity_on_success():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {
        "stdout": '{"positions": [{"instrument": {"symbol": "KTOS"}, "quantity": "17", "lastPrice": {"timestamp": "2026-08-17T00:00:00Z"}}]}'
    }
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(wrapper.get_holdings("KTOS"))
    assert result["qty"] == 17.0
    assert result["ts"] == "2026-08-17T00:00:00Z"
    assert result["source"] == "live"
    assert result["confidence"] == "high"


def test_get_live_holdings_returns_none_when_ticker_not_held():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {"stdout": '{"positions": []}'}
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(wrapper.get_holdings("ZZZZ"))
    assert result["qty"] is None
    assert result["confidence"] == "high"  # real, honest: successful call, just not held


def test_get_live_holdings_returns_none_when_manager_unavailable():
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=None):
        result = asyncio.run(wrapper.get_holdings("KTOS"))
    assert result["qty"] is None
    assert result["confidence"] == "low"


def test_get_live_holdings_returns_none_on_exception():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.side_effect = RuntimeError("simulated failure")
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(wrapper.get_holdings("KTOS"))
    assert result["qty"] is None  # real, deliberate: fails safe, never raises to the caller
    assert result["confidence"] == "low"


def test_wrapper_caches_successful_result():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {
        "stdout": '{"positions": [{"instrument": {"symbol": "KTOS"}, "quantity": "16", "lastPrice": {"timestamp": "2026-08-17T00:00:00Z"}}]}'
    }
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        first = asyncio.run(wrapper.get_holdings("KTOS"))
        second = asyncio.run(wrapper.get_holdings("KTOS"))
    assert first["source"] == "live"
    assert second["source"] == "cache"
    assert second["confidence"] == "medium"
    assert mock_mcp.call_tool.call_count == 1  # real, direct proof the second call used cache, not a fresh API hit


def test_wrapper_retries_before_giving_up():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.side_effect = RuntimeError("transient failure")
    wrapper = _fresh_wrapper()
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(wrapper.get_holdings("KTOS"))
    assert result["confidence"] == "low"
    assert mock_mcp.call_tool.call_count == 2  # real, direct proof: max_retries=1 means 2 total real attempts


# --- get_freshness_recommendation ---

def test_wrapper_persists_freshness_on_successful_check():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {
        "stdout": '{"positions": [{"instrument": {"symbol": "KTOS"}, "quantity": "17", "lastPrice": {"timestamp": "2026-08-17T00:00:00Z"}}]}'
    }
    from src.portfolio_parser import PublicComWrapper
    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = f"{tmpdir}/freshness.json"
        wrapper = PublicComWrapper(freshness_path=fresh_path, min_call_interval_seconds=0.0)
        with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
            asyncio.run(wrapper.get_holdings("KTOS"))
        with open(fresh_path) as f:
            data = json.load(f)
    assert data["KTOS"]["qty_at_check"] == 17.0
    assert data["KTOS"]["source"] == "live"
    assert "last_checked" in data["KTOS"]


def test_freshness_recommendation_trust_document_when_matching():
    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = f"{tmpdir}/freshness.json"
        now = datetime.now(timezone.utc).isoformat()
        with open(fresh_path, "w") as f:
            json.dump({"KTOS": {"last_checked": now, "source": "live", "qty_at_check": 16.0}}, f)
        result = get_freshness_recommendation("KTOS", 16.0, freshness_path=fresh_path)
    assert result["recommendation"] == "trust_document"
    assert result["has_recent_check"] is True


def test_freshness_recommendation_append_note_when_mismatched():
    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = f"{tmpdir}/freshness.json"
        now = datetime.now(timezone.utc).isoformat()
        with open(fresh_path, "w") as f:
            json.dump({"KTOS": {"last_checked": now, "source": "live", "qty_at_check": 17.0}}, f)
        result = get_freshness_recommendation("KTOS", 16.0, freshness_path=fresh_path)
    assert result["recommendation"] == "append_note"


def test_freshness_recommendation_needs_live_check_when_no_record():
    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = f"{tmpdir}/freshness.json"
        result = get_freshness_recommendation("ZZZZ", 5.0, freshness_path=fresh_path)
    assert result["recommendation"] == "needs_live_check"
    assert result["has_recent_check"] is False


def test_freshness_recommendation_needs_live_check_when_record_stale():
    with tempfile.TemporaryDirectory() as tmpdir:
        fresh_path = f"{tmpdir}/freshness.json"
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with open(fresh_path, "w") as f:
            json.dump({"KTOS": {"last_checked": old, "source": "live", "qty_at_check": 16.0}}, f)
        result = get_freshness_recommendation("KTOS", 16.0, freshness_path=fresh_path, max_age_hours=24.0)
    assert result["recommendation"] == "needs_live_check"
    assert result["has_recent_check"] is False


def test_freshness_recommendation_handles_missing_file_gracefully():
    result = get_freshness_recommendation("KTOS", 16.0, freshness_path="/nonexistent/path/freshness.json")
    assert result["recommendation"] == "needs_live_check"
