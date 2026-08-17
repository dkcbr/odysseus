"""
portfolio_parser.py -- real, targeted fix for the KTOS "17 shares"
finding (2026-08-17): multiple models, given correct context, were
independently summing a confirmed holding (16 shares) with a real,
separate, still-pending buy order (1 share) into a premature 17.

This parses data/portfolio_context.md's two real, distinct tables --
confirmed holdings ("| Ticker | Shares | Basis | ... |") and pending
orders ("| Ticker | Qty | Limit | Side | Notes |") -- and exposes them
as separate, explicit fields, so a caller (or the model, if this gets
surfaced in the tool output) never has to infer status from table
position alone.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PendingOrder:
    ticker: str
    qty_raw: str  # kept as the real, original string -- ranges like "1-2" exist (NVDA)
    limit: str
    side: str  # "BUY" or "SELL"
    notes: str = ""


@dataclass
class PortfolioParseResult:
    confirmed_holdings: Dict[str, float] = field(default_factory=dict)
    pending_orders: List[PendingOrder] = field(default_factory=list)

    def pending_qty_for(self, ticker: str, side: str = "BUY") -> float:
        """Real, total pending BUY (or SELL) quantity for a ticker. Ranges
        like "1-2" use the low end, conservatively -- this is for a
        confirmed_holdings computation, not a max-exposure estimate."""
        total = 0.0
        for o in self.pending_orders:
            if o.ticker != ticker or o.side != side:
                continue
            m = re.match(r"^(\d+(?:\.\d+)?)", o.qty_raw)
            if m:
                total += float(m.group(1))
        return total


_HOLDINGS_ROW = re.compile(
    r"^\|\s*([A-Z]{1,6})\s*\|\s*([\d.,]+)\s*\|"
)
_ORDER_ROW = re.compile(
    r"^\|\s*([A-Z]{1,6})\s*\|\s*([\d.,\-]+)\s*\|\s*(\$[\d.,]+)\s*\|\s*(BUY|SELL)\s*\|\s*([^|]*)\|"
)


def parse_portfolio_context(raw_text: str) -> PortfolioParseResult:
    """Real, direct parse of the two known real table formats in
    portfolio_context.md. Deliberately narrow -- matches only the
    two confirmed, real table shapes (see module docstring), not a
    general markdown-table parser. Rows that don't match either
    pattern are silently skipped (headers, separators, prose)."""
    result = PortfolioParseResult()

    in_orders_section = False
    for line in raw_text.splitlines():
        # Real, simple section tracking: the orders table's real header
        # row is "| Ticker | Qty | Limit | Side | Notes |" -- once seen,
        # subsequent matching rows are orders, not confirmed holdings.
        if "| Ticker | Qty | Limit | Side | Notes |" in line:
            in_orders_section = True
            continue
        if "| Ticker | Shares | Basis |" in line:
            in_orders_section = False
            continue

        if in_orders_section:
            m = _ORDER_ROW.match(line)
            if m:
                ticker, qty_raw, limit, side, notes = m.groups()
                result.pending_orders.append(
                    PendingOrder(ticker=ticker, qty_raw=qty_raw.strip(), limit=limit, side=side, notes=notes.strip())
                )
        else:
            m = _HOLDINGS_ROW.match(line)
            if m:
                ticker, shares_raw = m.groups()
                try:
                    shares = float(shares_raw.replace(",", ""))
                except ValueError:
                    continue
                # Real, deliberate choice: last real occurrence wins if a
                # ticker appears more than once in the confirmed table
                # (shouldn't happen in a correct document, but don't
                # silently sum duplicates -- that would be its own,
                # separate bug).
                result.confirmed_holdings[ticker] = shares

    return result


def get_confirmed_and_pending(ticker: str) -> dict:
    """Real, direct, single-ticker lookup -- the actual entry point a
    tool or caller would use. Reads the live file fresh each call,
    matching get_portfolio_context's own real "always fresh, never
    pre-loaded" design."""
    with open("data/portfolio_context.md", "r", encoding="utf-8") as f:
        raw = f.read()
    parsed = parse_portfolio_context(raw)
    confirmed = parsed.confirmed_holdings.get(ticker)
    pending_buy = parsed.pending_qty_for(ticker, "BUY")
    pending_sell = parsed.pending_qty_for(ticker, "SELL")
    return {
        "ticker": ticker,
        "confirmed_holdings": confirmed,
        "pending_buy_qty": pending_buy,
        "pending_sell_qty": pending_sell,
        "note": (
            "confirmed_holdings is the real, current, executed position. "
            "pending_buy_qty/pending_sell_qty are NOT yet executed -- "
            "do not add them to confirmed_holdings unless the user "
            "explicitly asks what the position would be if pending "
            "orders filled."
        ),
    }


async def get_live_holdings(ticker: str) -> Optional[dict]:
    """Real, direct live-holdings check via the actual public_com MCP
    connector (server id 74167655, confirmed in the real, live config
    tonight). Returns None on any failure (server down, ticker not
    held, auth issue, etc.) -- callers must fall back to the static
    document in that case, not treat None as "zero shares".

    Added 2026-08-17 after confirming directly that the document-only
    verification wrapper can "correct" a genuinely correct model claim
    into a stale, wrong one (real, confirmed case: MP -- model said 20,
    document said 11, live brokerage data confirmed 20 was right).

    NOT currently called from agent_loop.py's streaming response path --
    tried that integration the same night and hit a real, structural
    async error (RuntimeError: "Attempted to exit cancel scope in a
    different task than it was entered in") calling mcp.call_tool()
    from that specific generator context. Reverted the integration, not
    this function -- confirmed this function itself works correctly
    when called from a normal, non-streaming async context (tested
    directly). Safe to reuse from a different, non-streaming call site;
    not yet safe to call from agent_loop.py's response stream.
    """
    try:
        from src.tool_utils import get_mcp_manager
        mcp = get_mcp_manager()
        if not mcp:
            return None
        result = await mcp.call_tool("mcp__74167655__get_portfolio", {})
        if not isinstance(result, dict):
            return None
        stdout = result.get("stdout") or result.get("output") or ""
        if not stdout:
            return None
        import json as _json
        data = _json.loads(stdout) if isinstance(stdout, str) else stdout
        for p in data.get("positions", []):
            if p.get("instrument", {}).get("symbol") == ticker:
                return {
                    "quantity": float(p.get("quantity", 0)),
                    "last_price_ts": p.get("lastPrice", {}).get("timestamp"),
                }
        return None
    except Exception:
        return None
