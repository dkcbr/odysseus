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
import asyncio
import re
import time
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


class PublicComWrapper:
    """Real, centralized wrapper around the public_com MCP connector
    (server id 74167655). Added 2026-08-17 to consolidate retries,
    caching, and rate limiting for any live-holdings verification flow,
    rather than leaving each caller to hand-roll its own error handling.

    Returns a structured, consistent shape: {qty, ts, source, confidence}
    - qty: real, current share quantity (float), or None if unavailable.
    - ts: real timestamp of the underlying price data, or None.
    - source: "live" (fresh API call this request) or "cache" (served
      from a recent, real cached result within TTL).
    - confidence: "high" (fresh live data), "medium" (cached, within
      TTL), or "low" (all retries failed, no usable data).

    Real, honest note on async safety: this wrapper's underlying
    mcp.call_tool() usage was suspected, then found NOT confirmed, to
    cause a real async cancel-scope error when called from
    agent_loop.py's streaming generator (see GitHub issue #12 and its
    correction comment -- the original diagnosis didn't hold up under
    a minimal repro, and the real cause of that specific symptom
    remains unresolved). This wrapper does not change that risk
    either way -- callers integrating it into the live streaming path
    should test directly in that real context before trusting it
    there, not assume this wrapper resolves the open question.
    """

    def __init__(self, cache_ttl_seconds: float = 60.0, min_call_interval_seconds: float = 2.0, max_retries: int = 2):
        self._cache: dict = {}  # ticker -> (result_dict, real_fetch_monotonic_time)
        self._cache_ttl = cache_ttl_seconds
        self._min_interval = min_call_interval_seconds
        self._max_retries = max_retries
        self._last_call_monotonic: float = 0.0
        self._lock = asyncio.Lock()

    async def get_holdings(self, ticker: str) -> dict:
        """Real, single entry point. Always returns the structured
        {qty, ts, source, confidence} shape -- never raises, never
        returns None (callers can rely on the dict always being
        present; check confidence == "low" for "no usable data")."""
        now = time.monotonic()

        cached = self._cache.get(ticker)
        if cached is not None:
            result, fetched_at = cached
            if now - fetched_at < self._cache_ttl:
                return {**result, "source": "cache", "confidence": "medium"}

        async with self._lock:
            # Real, simple rate limit: enforce a minimum real interval
            # between actual outbound API calls, regardless of ticker.
            elapsed = time.monotonic() - self._last_call_monotonic
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)

            last_error = None
            for attempt in range(self._max_retries + 1):
                try:
                    self._last_call_monotonic = time.monotonic()
                    raw = await self._fetch_raw(ticker)
                    if raw is not None:
                        result = {"qty": raw["quantity"], "ts": raw.get("last_price_ts"), "source": "live", "confidence": "high"}
                        self._cache[ticker] = (result, time.monotonic())
                        return result
                    # Real, honest: ticker genuinely not held is not a
                    # retriable failure -- a real, successful call that
                    # found nothing. Don't retry, don't treat as an error.
                    return {"qty": None, "ts": None, "source": "live", "confidence": "high"}
                except Exception as e:
                    last_error = e
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (attempt + 1))  # real, simple backoff

            return {"qty": None, "ts": None, "source": "live", "confidence": "low", "error": str(last_error) if last_error else "unknown"}

    async def _fetch_raw(self, ticker: str) -> Optional[dict]:
        """Real, direct MCP call -- the same real logic as the original
        get_live_holdings(), now centralized here."""
        from src.tool_utils import get_mcp_manager
        mcp = get_mcp_manager()
        if not mcp:
            raise RuntimeError("MCP manager not available")
        result = await mcp.call_tool("mcp__74167655__get_portfolio", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"Unexpected result type from get_portfolio: {type(result)}")
        stdout = result.get("stdout") or result.get("output") or ""
        if not stdout:
            raise RuntimeError("Empty response from get_portfolio")
        import json as _json
        data = _json.loads(stdout) if isinstance(stdout, str) else stdout
        for p in data.get("positions", []):
            if p.get("instrument", {}).get("symbol") == ticker:
                return {
                    "quantity": float(p.get("quantity", 0)),
                    "last_price_ts": p.get("lastPrice", {}).get("timestamp"),
                }
        return None  # real, honest: successfully queried, ticker just isn't held


# Real, module-level shared instance -- so the cache and rate limiter
# are genuinely shared across callers within the same process, not
# reset per-call.
_public_com_wrapper = PublicComWrapper()


async def get_live_holdings(ticker: str) -> Optional[dict]:
    """Real, backward-compatible wrapper around PublicComWrapper, kept
    for existing callers/tests. New callers should prefer
    _public_com_wrapper.get_holdings() directly for the full
    {qty, ts, source, confidence} shape."""
    result = await _public_com_wrapper.get_holdings(ticker)
    if result["qty"] is None:
        return None
    return {"quantity": result["qty"], "last_price_ts": result["ts"]}
