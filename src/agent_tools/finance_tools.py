import asyncio
import json
from typing import Any, Dict

import os

FMP_BASE_URL = "https://financialmodelingprep.com/stable"


class TickerLookupTool:
    """Look up real, live company/ticker identity and quote data via Financial
    Modeling Prep. Exists to stop the model from answering ticker/company
    identity or price questions from its own (frequently wrong, especially
    for small/mid-cap names) parametric memory. Fails closed: no API key,
    unknown ticker, or any request error returns a clear error - never a
    guessed answer.
    """

    async def execute(self, content: str, ctx: dict) -> dict:
        raw = content.strip()
        symbol = raw
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    symbol = str(parsed.get("symbol") or parsed.get("ticker") or "").strip()
            except json.JSONDecodeError:
                symbol = ""
        if not symbol:
            symbol = raw.split("\n")[0].strip()
        symbol = symbol.upper()
        if not symbol or any(c in symbol for c in (" ", "\t", "\n")):
            return {
                "error": "lookup_ticker: provide a single ticker symbol, e.g. KTOS",
                "exit_code": 1,
            }

        api_key = os.environ.get("FMP_API_KEY")
        if not api_key:
            return {
                "error": (
                    "lookup_ticker: FMP_API_KEY is not configured. Do not answer "
                    "this question from memory - tell the user real ticker data "
                    "isn't available right now."
                ),
                "exit_code": 1,
            }

        loop = asyncio.get_running_loop()
        try:
            import httpx

            def _fetch():
                resp = httpx.get(
                    f"{FMP_BASE_URL}/profile",
                    params={"symbol": symbol, "apikey": api_key},
                    timeout=10,
                )
                resp.raise_for_status()
                return resp.json()

            data = await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=15)
        except asyncio.TimeoutError:
            return {"error": f"lookup_ticker: timed out looking up {symbol}", "exit_code": 1}
        except Exception as e:
            return {
                "error": f"lookup_ticker: request failed for {symbol}: {type(e).__name__}: {e}",
                "exit_code": 1,
            }

        if not data or not isinstance(data, list) or not data:
            return {
                "error": (
                    f"lookup_ticker: no verified data found for '{symbol}'. This may not be "
                    "a real ticker, or it's delisted/unsupported. Do not guess a company "
                    "name for it - tell the user it could not be verified."
                ),
                "exit_code": 1,
            }

        d = data[0]
        output = (
            f"symbol: {d.get('symbol')}\n"
            f"companyName: {d.get('companyName')}\n"
            f"price: {d.get('price')}\n"
            f"change: {d.get('change')} ({d.get('changePercentage')}%)\n"
            f"marketCap: {d.get('marketCap')}\n"
            f"sector: {d.get('sector')}\n"
            f"industry: {d.get('industry')}\n"
            f"exchange: {d.get('exchangeFullName')}\n"
            f"isActivelyTrading: {d.get('isActivelyTrading')}\n"
            f"description: {(d.get('description') or '')[:400]}"
        )
        return {"output": output, "exit_code": 0}
