"""Real, direct tests for src/portfolio_parser.py -- the parser separating
confirmed holdings from pending orders (added 2026-08-17, see the KTOS
"17 shares" investigation in the vault), and get_live_holdings, the
live-brokerage check that's retained but NOT currently called from
agent_loop.py's streaming path (real, structural async issue, tracked
in GitHub issue #12).
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.portfolio_parser import (
    parse_portfolio_context,
    get_confirmed_and_pending,
    get_live_holdings,
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

def test_get_live_holdings_returns_quantity_on_success():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {
        "stdout": '{"positions": [{"instrument": {"symbol": "KTOS"}, "quantity": "17", "lastPrice": {"timestamp": "2026-08-17T00:00:00Z"}}]}'
    }
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(get_live_holdings("KTOS"))
    assert result is not None
    assert result["quantity"] == 17.0
    assert result["last_price_ts"] == "2026-08-17T00:00:00Z"


def test_get_live_holdings_returns_none_when_ticker_not_held():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.return_value = {"stdout": '{"positions": []}'}
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(get_live_holdings("ZZZZ"))
    assert result is None


def test_get_live_holdings_returns_none_when_manager_unavailable():
    with patch("src.tool_utils.get_mcp_manager", return_value=None):
        result = asyncio.run(get_live_holdings("KTOS"))
    assert result is None


def test_get_live_holdings_returns_none_on_exception():
    mock_mcp = AsyncMock()
    mock_mcp.call_tool.side_effect = RuntimeError("simulated failure")
    with patch("src.tool_utils.get_mcp_manager", return_value=mock_mcp):
        result = asyncio.run(get_live_holdings("KTOS"))
    assert result is None  # real, deliberate: fails safe, never raises to the caller
