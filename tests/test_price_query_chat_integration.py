"""Regression tests for the deterministic price-query chat-route
short-circuit (detect_price_query/answer_price_query in
src/tool_execution.py), integrated into chat_routes.py's real
/api/chat_stream endpoint on 2026-09-18, following the exact same real
pattern as the existing detect_holdings_query/answer_holdings_query
precedent.

Deliberately more conservative than the reverted agent-loop interceptor
(jarvis-todo.md's 2026-09-17 entry): requires BOTH a real price-intent
keyword AND a valid, non-stoplisted bare ticker, since this check runs
against every single chat message, not just ones already headed into
the agent loop.
"""

import asyncio
from unittest.mock import AsyncMock, patch

from src.tool_execution import detect_price_query, answer_price_query


def test_detects_real_price_query_with_trading_at_phrasing():
    # The exact real phrasing that failed every time through the agent
    # loop all session (0/2 vanilla model, 60% specialized LoRA,
    # non-deterministic interceptor) -- confirmed live working now.
    assert detect_price_query("What is KTOS trading at right now?") == "KTOS"


def test_detects_real_price_query_with_price_of_phrasing():
    assert detect_price_query("What is the price of AAPL?") == "AAPL"


def test_does_not_fire_on_bare_ticker_without_price_keyword():
    # Real, deliberate false-positive guard: a ticker mentioned in an
    # unrelated sentence must not short-circuit into a price quote.
    assert detect_price_query("I am reading about AAPL supply chain history") is None


def test_does_not_fire_on_price_keyword_without_a_valid_ticker():
    assert detect_price_query("Is this worth doing?") is None


def test_does_not_fire_on_stoplisted_tokens_only():
    assert detect_price_query("My CEO asked about the API") is None


def test_does_not_fire_on_the_users_own_name_dk():
    # Real, live-caught bug, 2026-09-24: "DK" is both the user's own
    # real name/initials (appearing constantly in ordinary messages)
    # and a real, valid stock ticker (Delek US Holdings). Confirmed
    # directly, live: this exact collision silently hijacked a real,
    # separate, load-bearing automated pipeline's entire Claude review
    # call for 6 straight days (2026-09-19 through 09-24) -- the
    # pipeline's own system prompt begins "You are reviewing DK's real,
    # live brokerage account...", which also contains real price-intent
    # keywords ("current price"), so both detection conditions were
    # met on every single run. Not a synthetic edge case -- the exact
    # real message text that broke in production, trimmed here.
    real_broken_prompt = (
        "You are reviewing DK's real, live brokerage account -- the "
        "same kind of rung/queue review done manually. Real, hard "
        "rules apply, including checking the real, current price "
        "before any action."
    )
    assert detect_price_query(real_broken_prompt) is None


def test_answer_price_query_formats_real_successful_lookup():
    async def _run():
        fake_result = {
            "output": (
                "symbol: KTOS\n"
                "companyName: Kratos Defense & Security Solutions, Inc.\n"
                "price: 47.46\n"
                "change: -0.2 (-0.41964%)\n"
            ),
            "exit_code": 0,
        }
        with patch(
            "src.agent_tools.finance_tools.TickerLookupTool.execute",
            new=AsyncMock(return_value=fake_result),
        ):
            answer = await answer_price_query("KTOS")

        assert "Kratos Defense & Security Solutions, Inc." in answer
        assert "KTOS" in answer
        assert "$47.46" in answer
        assert "-0.2 (-0.41964%)" in answer

    asyncio.run(_run())


# Real, added 2026-09-26: "How much is TSLA stock right now?" was found
# live to fall through this detector entirely -- the original keyword
# regex only matched the compound phrase "stock price", not bare
# "stock"/"shares"/"crypto"/"coin" adjacent to a ticker.
def test_detects_price_query_via_ticker_adjacent_to_stock_word():
    assert detect_price_query("How much is TSLA stock right now?") == "TSLA"
    assert detect_price_query("Check my TSLA shares performance") == "TSLA"
    assert detect_price_query("What are BTC crypto prices like") == "BTC"
    assert detect_price_query("stock AAPL update") == "AAPL"


def test_adjacency_fallback_is_case_sensitive_on_the_ticker():
    # Real bug caught and fixed while building this: a first attempt
    # applied re.IGNORECASE to the whole compiled pattern, which made
    # the ticker capture group itself case-insensitive -- "The stock
    # market crashed" matched "The" as if it were a valid ticker.
    assert detect_price_query(
        "I don't care about the stock market, but NASA is cool"
    ) is None
    assert detect_price_query("The stock market crashed today because of the Fed") is None
    assert detect_price_query("My CEO asked about the stock options plan") is None
    assert detect_price_query("my shares of responsibility here are equal") is None


# Real, added 2026-09-27: a live trial against the real, running system
# (using the currently-selected default model, not the separately-hidden
# ticker-lookup-lora) confirmed a bare "NVDA?" -- nothing else in the
# message -- still fell through to model synthesis and returned a wrong,
# inconsistent price versus the same ticker answered correctly moments
# earlier through the deterministic path. This is the exact bare-ticker-
# shorthand shape that originally caused ticker-lookup-lora's fabrication;
# hiding that one model never actually closed the underlying gap.
def test_detects_bare_ticker_shorthand_with_nothing_else_in_message():
    assert detect_price_query("NVDA?") == "NVDA"
    assert detect_price_query("NVDA") == "NVDA"
    assert detect_price_query("AAPL!") == "AAPL"
    assert detect_price_query("KTOS.") == "KTOS"


def test_bare_ticker_shorthand_is_case_sensitive_to_avoid_false_positives():
    # Real, deliberate guard: an ordinary short reply typed normally
    # ("sure", "nice", "thanks", "hello") must NOT misfire into a
    # spurious price lookup just because it happens to be 2-5 letters
    # and isn't a stoplisted word. Bare-ticker shorthand is only
    # recognized when typed in ALL CAPS, matching the existing
    # convention _BARE_TICKER_RE already uses elsewhere in this file.
    assert detect_price_query("Sure") is None
    assert detect_price_query("nice") is None
    assert detect_price_query("Hello") is None
    assert detect_price_query("thanks") is None


def test_bare_ticker_shorthand_does_not_fire_on_a_ticker_inside_a_sentence():
    # The narrow, deliberate scope: the ENTIRE message must be just the
    # ticker. A ticker mentioned as part of a larger sentence should
    # still fall through to the model/agent loop as before, matching
    # the adjacency check's own established false-positive guard.
    assert detect_price_query("I'm reading about AAPL's supply chain") is None
    assert detect_price_query("NVDA is a company I follow") is None


def test_bare_ticker_shorthand_still_respects_the_stoplist():
    # "DK" alone must still fall through rather than misfiring as Delek
    # US Holdings -- same collision this stoplist already exists for.
    assert detect_price_query("DK") is None
    assert detect_price_query("DK?") is None
    assert detect_price_query("CEO") is None


def test_answer_price_query_surfaces_the_tools_own_real_error():
    async def _run():
        fake_result = {
            "error": "lookup_ticker: no verified data found for 'ZZZZZ'.",
            "exit_code": 1,
        }
        with patch(
            "src.agent_tools.finance_tools.TickerLookupTool.execute",
            new=AsyncMock(return_value=fake_result),
        ):
            answer = await answer_price_query("ZZZZZ")

        # Real, deliberate: the tool's own fail-closed message is
        # surfaced directly, not replaced with a different one.
        assert answer == fake_result["error"]

    asyncio.run(_run())
