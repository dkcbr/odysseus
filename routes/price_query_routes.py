# routes/price_query_routes.py
"""Real, standalone, non-agent-loop price-query path -- built 2026-09-18,
following the previous session's own real conclusion after the reverted
agent_loop.py interceptor: the intermittency bug was specific to running
inside stream_agent_loop's own execution path (root cause never found,
see jarvis-todo.md's 2026-09-17 entry), so this deliberately runs entirely
outside it. Deployed standalone; NOT yet wired into chat_routes.py's real,
live, complex chat endpoint (deliberately deferred -- that route has
real, unmapped side effects -- session saving, SSE streaming format,
incognito handling -- worth mapping properly before integrating, not
bolting onto at the end of a long session).

Uses the real, already-proven TickerLookupTool directly (financial
data), not a parallel reimplementation -- same fail-closed behavior,
same FMP-backed real data, same error messages a caller already knows
how to interpret. Detection reuses the real, already-tested regex/
stoplist infrastructure from src/tool_index.py (ToolIndex._BARE_TICKER_RE,
._TICKER_STOPLIST) rather than duplicating it.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.agent_tools.finance_tools import TickerLookupTool
from src.tool_index import get_tool_index


class PriceQueryRequest(BaseModel):
    text: str


def _parse_ticker_lookup_output(output: str) -> dict:
    """Parse TickerLookupTool's real "key: value\\n"-per-line output back
    into structured fields. Simple split, not a fragile regex -- the
    tool's own format (finance_tools.py) is a fixed, deterministic
    f-string, not free-form model output."""
    fields: dict = {}
    for line in output.split("\n"):
        if ": " in line:
            key, _, value = line.partition(": ")
            fields[key] = value
    return fields


def setup_price_query_routes():
    router = APIRouter()

    @router.post("/api/price-query")
    async def price_query(req: PriceQueryRequest, request: Request):
        require_admin(request)

        idx = get_tool_index()
        if idx is None:
            return {
                "error": "DETECTION_UNAVAILABLE",
                "message": "Ticker-detection index is not currently available.",
            }

        text = (req.text or "").strip()
        if not text:
            return {
                "error": "NO_TICKER_DETECTED",
                "message": "No query text provided.",
            }

        # Real, deliberate design choice: this endpoint's entire purpose is
        # ticker lookup, so a confident bare-ticker match alone (the same
        # real, stoplist-filtered regex used by the reverted interceptor)
        # is sufficient -- no separate price-keyword requirement layered on
        # top, unlike the interceptor which needed to justify intruding
        # into otherwise-unrelated chat.
        candidates = [
            t for t in idx._BARE_TICKER_RE.findall(text)
            if t not in idx._TICKER_STOPLIST
        ]
        if not candidates:
            return {
                "error": "NO_TICKER_DETECTED",
                "message": (
                    "No confident ticker symbol found in the query text "
                    "(checked against a real stoplist of common acronyms)."
                ),
            }

        ticker = candidates[0]
        result = await TickerLookupTool().execute(ticker, {})

        if "error" in result:
            return {
                "error": "QUOTE_LOOKUP_FAILED",
                "message": result["error"],
                "ticker": ticker,
            }

        fields = _parse_ticker_lookup_output(result.get("output", ""))
        return {
            "ticker": fields.get("symbol", ticker),
            "name": fields.get("companyName"),
            "price": fields.get("price"),
            "change": fields.get("change"),
            "marketCap": fields.get("marketCap"),
            "sector": fields.get("sector"),
            "industry": fields.get("industry"),
            "exchange": fields.get("exchange"),
            "isActivelyTrading": fields.get("isActivelyTrading"),
        }

    return router
