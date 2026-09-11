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
    assert "ZZZZ" not in result.confirmed_holdings


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
