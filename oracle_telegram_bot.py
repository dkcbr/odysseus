"""
telegram_bot.py -- runs on Oracle VM.

Requires odysseus_auth.py copied alongside this file (it has zero
Odysseus-project-specific imports, so it's portable standalone).

Real API used:
    get_session(base_url=..., username=..., password=...) -> requests.Session
    (NOT login_session -- that function doesn't exist)

Env vars required:
    ODYSSEUS_USER, ODYSSEUS_PASS, TELEGRAM_BOT_TOKEN
"""

import json
import logging
import os

from telegram.ext import ApplicationBuilder, CommandHandler

from odysseus_auth import get_session  # real function name

ODYSSEUS_BASE = "http://100.93.206.89:7000"
USERNAME = os.environ["ODYSSEUS_USER"]
PASSWORD = os.environ["ODYSSEUS_PASS"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_session = None


def ensure_session():
    global _session
    if _session is None:
        _session = get_session(base_url=ODYSSEUS_BASE, username=USERNAME, password=PASSWORD)
    return _session


async def run(update, context):
    """
    /run <agent> <server> <tool> <json-arguments>
    e.g. /run browser_agent jarvis_browser open '{"url": "https://example.com"}'
    """
    if len(context.args) < 4:
        await update.message.reply_text("Usage: /run <agent> <server> <tool> <json-arguments>")
        return

    agent, server, tool = context.args[:3]
    args_str = " ".join(context.args[3:])

    try:
        arguments = json.loads(args_str)
    except json.JSONDecodeError:
        await update.message.reply_text("arguments must be valid JSON")
        return

    s = ensure_session()
    payload = {"agent": agent, "server": server, "tool": tool, "arguments": arguments}

    try:
        resp = s.post(f"{ODYSSEUS_BASE}/api/agent-tasks", json=payload, timeout=15)
    except Exception as e:
        log.exception("request failed")
        await update.message.reply_text(f"request error: {e}")
        return

    if resp.status_code != 200:
        await update.message.reply_text(f"HTTP {resp.status_code}: {resp.text}")
        return

    data = resp.json()  # real shape: {id, agent, server, tool, arguments, status, result, ...}
    await update.message.reply_text(f"queued task {data.get('id')} for {agent}.{tool}")


async def history(update, context):
    """/history <agent>"""
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /history <agent>")
        return

    agent = context.args[0]
    s = ensure_session()

    resp = s.get(f"{ODYSSEUS_BASE}/api/agent-tasks/history/{agent}", timeout=15)
    if resp.status_code != 200:
        await update.message.reply_text(f"HTTP {resp.status_code}: {resp.text}")
        return

    data = resp.json()  # real shape: {agent: "...", history: [...]}
    entries = data.get("history", [])

    if not entries:
        await update.message.reply_text(f"No history for {agent}")
        return

    lines = []
    for entry in entries[:10]:
        tool = entry.get("tool")
        server = entry.get("server")
        status = entry.get("status")
        lines.append(f"[{status}] {tool} @ {server}")

    await update.message.reply_text("\n".join(lines))


# --- /compare_crypto <symbol>: same real compare_strategies tool as
# /compare_nvda, reusing the exact same confirmed field names (candles_analyzed,
# date_from/date_to, buy_and_hold_return_pct, ranking, strategy_label,
# total_return_pct, total_trades, win_rate_pct, max_drawdown_pct -- NOT
# candles/start_date/end_date/strategies/name/return_pct/trades/win_rate/
# max_drawdown, which are all fabricated). Crypto needs the Yahoo Finance
# "-USD" suffix (confirmed live: "XRP-USD" works) -- different convention
# from coin_analysis's KuCoin pair format ("XRPUSDT").
CRYPTO_YF_SYMBOLS = {"XRP": "XRP-USD", "ADA": "ADA-USD", "HBAR": "HBAR-USD"}


async def compare_crypto(update, context):
    """/compare_crypto <XRP|ADA|HBAR> -- ranked strategy leaderboard on real
    historical data. NOT a test of accumulation-specific logic."""
    if not context.args or context.args[0].upper() not in CRYPTO_YF_SYMBOLS:
        await update.message.reply_text("Usage: /compare_crypto <XRP|ADA|HBAR>")
        return

    label = context.args[0].upper()
    yf_symbol = CRYPTO_YF_SYMBOLS[label]
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=60,
            json={"server": "tradingview", "tool": "compare_strategies",
                  "arguments": {"symbol": yf_symbol, "period": "1y"}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"Compare failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
    except Exception as e:
        await update.message.reply_text(f"Error running comparison: {e}")
        return

    lines = [
        f"{label} strategy comparison ({data.get('period')}, {data.get('candles_analyzed')} candles)",
        f"{data.get('date_from')} to {data.get('date_to')}",
        f"Buy & Hold: {data.get('buy_and_hold_return_pct')}%",
        f"Winner: {data.get('winner')}",
        "",
    ]
    for entry in data.get("ranking", []):
        lines.append(
            f"{entry['rank']}. {entry['strategy_label']}: {entry['total_return_pct']}% "
            f"({entry['total_trades']} trades, {entry['win_rate_pct']}% win rate, "
            f"max DD {entry['max_drawdown_pct']}%)"
        )
    lines.append("")
    lines.append("Real historical data (Yahoo Finance) -- generic strategy comparison, "
                 "not a test of your accumulation-specific logic.")

    await update.message.reply_text("\n".join(lines))


# --- NVDA rung playbook (real, verified against live data via coin_analysis) ---
NVDA_RUNGS = [
    {"name": "T1", "low": 186, "high": 192, "shares": "1-2"},
    {"name": "T2", "low": 172, "high": 182, "shares": "2-4"},
    {"name": "T3", "low": 158, "high": 166, "shares": "4-6"},
    {"name": "T4", "low": 142, "high": 152, "shares": "6-10"},
]
NVDA_SWING_SELL = 225


def check_nvda_zone(s) -> dict:
    """Real playbook logic, reused from nvda_rung_check.py (already verified
    against live data: $206.85 -> BETWEEN_RUNGS). Uses coin_analysis, NOT
    combined_analysis -- that's a different, untested tool."""
    resp = s.post(
        f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
        json={"server": "tradingview", "tool": "coin_analysis",
              "arguments": {"symbol": "NVDA", "exchange": "NASDAQ"}},
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("exit_code") != 0:
        return {"error": result.get("error") or "tool call failed"}

    data = json.loads(result["stdout"])
    price = data["price_data"]["current_price"]

    status = {"price": price, "zone": None, "action": None,
              "rsi": data.get("rsi", {}).get("value"),
              "trend_state": data.get("trend_state"),
              "grade": data.get("grade")}

    if price >= NVDA_SWING_SELL:
        status["zone"] = "SWING_SELL"
        status["action"] = f"Price ${price} >= ${NVDA_SWING_SELL} swing-sell threshold"
    else:
        for rung in NVDA_RUNGS:
            if rung["low"] <= price <= rung["high"]:
                status["zone"] = rung["name"]
                status["action"] = f"Price ${price} is in {rung['name']} buy zone (${rung['low']}-${rung['high']}) -- {rung['shares']} shares"
                break
        else:
            status["zone"] = "BETWEEN_RUNGS"
            status["action"] = f"Price ${price} is between rungs -- no action"

    return status


async def nvda(update, context):
    """/nvda -- manual on-demand NVDA rung check."""
    s = ensure_session()
    try:
        status = check_nvda_zone(s)
    except Exception as e:
        await update.message.reply_text(f"Error checking NVDA: {e}")
        return

    if "error" in status:
        await update.message.reply_text(f"NVDA check failed: {status['error']}")
        return

    msg = (
        f"NVDA: ${status['price']}\n"
        f"Zone: {status['zone']}\n"
        f"{status['action']}\n"
        f"RSI: {status['rsi']} | Trend: {status['trend_state']} | Grade: {status['grade']}"
    )
    await update.message.reply_text(msg)


# --- Crypto playbooks: XLM (real $0.15 rung) and CC (informational only --
# no real price rung exists, target is a 2,000-token count via holdings) ---
XLM_RUNG = 0.15


def _coin_price(s, pair_symbol: str) -> dict:
    resp = s.post(
        f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
        json={"server": "tradingview", "tool": "coin_analysis", "arguments": {"symbol": pair_symbol}},
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("exit_code") != 0:
        return {"error": result.get("error") or "tool call failed"}
    data = json.loads(result["stdout"])
    if "error" in data:
        return {"error": data["error"]}
    return {"price": data["price_data"]["current_price"],
            "rsi": data.get("rsi", {}).get("value"),
            "trend_state": data.get("trend_state")}


def check_xlm_zone(s) -> dict:
    info = _coin_price(s, "XLMUSDT")  # real KuCoin pair format -- bare "XLM" doesn't resolve
    if "error" in info:
        return info
    price = info["price"]
    zone = "BUY_ZONE" if price <= XLM_RUNG else "WAITING"
    action = (f"Price ${price} <= ${XLM_RUNG} rung -- buy zone" if zone == "BUY_ZONE"
              else f"Price ${price} is above the ${XLM_RUNG} rung -- waiting")
    return {"price": price, "zone": zone, "action": action,
            "rsi": info["rsi"], "trend_state": info["trend_state"]}


async def xlm(update, context):
    """/xlm -- manual on-demand XLM rung check ($0.15 threshold)."""
    s = ensure_session()
    try:
        status = check_xlm_zone(s)
    except Exception as e:
        await update.message.reply_text(f"Error checking XLM: {e}")
        return
    if "error" in status:
        await update.message.reply_text(f"XLM check failed: {status['error']}")
        return
    msg = (
        f"XLM: ${status['price']}\n"
        f"Zone: {status['zone']}\n"
        f"{status['action']}\n"
        f"RSI: {status['rsi']}"
    )
    await update.message.reply_text(msg)


async def cc(update, context):
    """/cc -- informational price check only. No real price rung exists for
    CC; the real target is a 2,000-token count (currently 1,000 held),
    tracked via holdings, not live price."""
    s = ensure_session()
    try:
        info = _coin_price(s, "CCUSDT")
    except Exception as e:
        await update.message.reply_text(f"Error checking CC: {e}")
        return
    if "error" in info:
        await update.message.reply_text(f"CC check failed: {info['error']}")
        return
    msg = (
        f"CC: ${info['price']}\n"
        f"RSI: {info['rsi']}\n"
        f"No price rung defined -- target is 2,000 tokens (currently 1,000 held)."
    )
    await update.message.reply_text(msg)


# --- XRP/ADA/HBAR: informational only. These targets are already COMPLETE
# per the user's real portfolio data (XRP 4,002/4,000, ADA 2,000/2,000,
# HBAR 4,000/4,000 across accounts) -- there's nothing left to accumulate,
# so no zone/rung/alert logic applies. Also, coin_analysis returns grade=None
# for crypto pairs (confirmed live), so a grade-based classifier wouldn't
# even be functional if built. Plain price/RSI/trend context only. ---

async def _informational_check(update, pair_symbol: str, label: str):
    s = ensure_session()
    try:
        info = _coin_price(s, pair_symbol)
    except Exception as e:
        await update.message.reply_text(f"Error checking {label}: {e}")
        return
    if "error" in info:
        await update.message.reply_text(f"{label} check failed: {info['error']}")
        return
    msg = (
        f"{label}: ${info['price']}\n"
        f"RSI: {info['rsi']} | Trend: {info['trend_state']}\n"
        f"Informational only -- target already complete."
    )
    await update.message.reply_text(msg)


async def xrp(update, context):
    """/xrp -- informational only (target already complete: 4,002/4,000)."""
    await _informational_check(update, "XRPUSDT", "XRP")


async def ada(update, context):
    """/ada -- informational only (target already complete: 2,000/2,000)."""
    await _informational_check(update, "ADAUSDT", "ADA")


async def hbar(update, context):
    """/hbar -- informational only (target already complete: 4,000/4,000)."""
    await _informational_check(update, "HBARUSDT", "HBAR")


# --- EGX market scan (manual only, no daemon -- situational, not continuous).
# Real tool: egx_market_overview (NOT a fabricated "egx_scan"). Real fields
# confirmed live: symbol, price, changePercent (camelCase, not change_pct),
# volume, rsi, rating, signal. No "sector" field exists on individual
# entries, and there's no separate "anomalies"/"sector_clusters" structure --
# real top-level keys are: exchange, timeframe, total_analyzed, top_gainers,
# top_losers, most_active, market_stats. ---

async def egx(update, context):
    """/egx -- on-demand EGX market snapshot. Manual only, no daemon, no alerts."""
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "tradingview", "tool": "egx_market_overview", "arguments": {}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"EGX scan failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
    except Exception as e:
        await update.message.reply_text(f"Error running EGX scan: {e}")
        return

    lines = [f"EGX scan ({data.get('timeframe')}) -- {data.get('total_analyzed')} names analyzed", ""]

    gainers = data.get("top_gainers", [])
    if gainers:
        lines.append("Top Gainers:")
        for g in gainers[:5]:
            lines.append(f"- {g['symbol']}: {g['changePercent']}% (RSI {g['rsi']}, {g['signal']})")
        lines.append("")

    losers = data.get("top_losers", [])
    if losers:
        lines.append("Top Losers:")
        for l in losers[:5]:
            lines.append(f"- {l['symbol']}: {l['changePercent']}% (RSI {l['rsi']}, {l['signal']})")
        lines.append("")

    if not gainers and not losers:
        lines.append("No significant EGX signals detected.")

    await update.message.reply_text("\n".join(lines))


# --- MSFT/WDAY/SOUN: informational only. Real tool is coin_analysis with
# exchange=NASDAQ (the same, already-verified pattern used for NVDA) --
# NOT egx_stock_screener, which is Egyptian Exchange-only and has no
# per-symbol lookup at all. All three are real, stated Frozen Positions
# in the user's portfolio (never sell at a loss) -- noted honestly for all
# three, not just SOUN, since that's the real, shared status. ---

async def _stock_check(update, symbol: str):
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "tradingview", "tool": "coin_analysis",
                  "arguments": {"symbol": symbol, "exchange": "NASDAQ"}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"{symbol} check failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
        if "error" in data:
            await update.message.reply_text(f"{symbol} check failed: {data['error']}")
            return
    except Exception as e:
        await update.message.reply_text(f"Error checking {symbol}: {e}")
        return

    price = data["price_data"]["current_price"]
    rsi = data.get("rsi", {}).get("value")
    grade = data.get("grade")
    trend = data.get("trend_state")

    msg = (
        f"{symbol}: ${price}\n"
        f"RSI: {rsi} | Trend: {trend} | Grade: {grade}\n"
        f"Frozen position -- never sell at a loss (per your stated rule)."
    )
    await update.message.reply_text(msg)


async def msft(update, context):
    """/msft -- informational only. Frozen position."""
    await _stock_check(update, "MSFT")


async def wday(update, context):
    """/wday -- informational only. Frozen position."""
    await _stock_check(update, "WDAY")


async def soun(update, context):
    """/soun -- informational only. Frozen position."""
    await _stock_check(update, "SOUN")


# --- RKLB/IONQ/RGTI: real re-entry rung checks (single threshold, same
# shape as XLM's $0.15 rung), and DBC: real two-threshold harvest cycle
# (sell $30-32, rebuy $26). Found by auditing the actual stated portfolio
# rules -- these are the only tickers besides NVDA/XLM with a genuine,
# defined price threshold. Everything else (frozen positions, conviction
# holds, thesis-only holds, lottery tickets) deliberately has no zone. ---
REENTRY_SINGLE_THRESHOLD = {
    "RKLB": {"exchange": "NASDAQ", "threshold": 45},
    "IONQ": {"exchange": "NYSE", "threshold": 45},
    "RGTI": {"exchange": "NASDAQ", "threshold": 16.50},
}
DBC_SELL_LOW = 30
DBC_SELL_HIGH = 32
DBC_REBUY = 26


async def _reentry_single_check(update, ticker: str):
    cfg = REENTRY_SINGLE_THRESHOLD[ticker]
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "tradingview", "tool": "coin_analysis",
                  "arguments": {"symbol": ticker, "exchange": cfg["exchange"]}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"{ticker} check failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
        if "error" in data:
            await update.message.reply_text(f"{ticker} check failed: {data['error']}")
            return
    except Exception as e:
        await update.message.reply_text(f"Error checking {ticker}: {e}")
        return

    price = data["price_data"]["current_price"]
    rsi = data.get("rsi", {}).get("value")
    threshold = cfg["threshold"]
    zone = "BUY_ZONE" if price <= threshold else "WAITING"
    action = (f"Price ${price} <= ${threshold} re-entry threshold -- buy zone" if zone == "BUY_ZONE"
              else f"Price ${price} is above the ${threshold} re-entry threshold -- waiting")

    msg = f"{ticker}: ${price}\nZone: {zone}\n{action}\nRSI: {rsi}"
    await update.message.reply_text(msg)


async def rklb(update, context):
    """/rklb -- real re-entry rung check ($45 threshold)."""
    await _reentry_single_check(update, "RKLB")


async def ionq(update, context):
    """/ionq -- real re-entry rung check ($45 threshold)."""
    await _reentry_single_check(update, "IONQ")


async def rgti(update, context):
    """/rgti -- real re-entry rung check ($16.50 threshold)."""
    await _reentry_single_check(update, "RGTI")


async def dbc(update, context):
    """/dbc -- real harvest-cycle check (sell $30-32, rebuy $26)."""
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "tradingview", "tool": "coin_analysis",
                  "arguments": {"symbol": "DBC", "exchange": "AMEX"}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"DBC check failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
        if "error" in data:
            await update.message.reply_text(f"DBC check failed: {data['error']}")
            return
    except Exception as e:
        await update.message.reply_text(f"Error checking DBC: {e}")
        return

    price = data["price_data"]["current_price"]
    rsi = data.get("rsi", {}).get("value")

    if price >= DBC_SELL_LOW:
        zone = "SELL_ZONE"
        action = f"Price ${price} is in the ${DBC_SELL_LOW}-${DBC_SELL_HIGH} sell zone -- harvest"
    elif price <= DBC_REBUY:
        zone = "REBUY_ZONE"
        action = f"Price ${price} <= ${DBC_REBUY} rebuy threshold -- accumulate"
    else:
        zone = "HOLDING"
        action = f"Price ${price} is between rebuy (${DBC_REBUY}) and sell (${DBC_SELL_LOW}-${DBC_SELL_HIGH}) -- holding"

    msg = f"DBC: ${price}\nZone: {zone}\n{action}\nRSI: {rsi}"
    await update.message.reply_text(msg)


# --- /chart <symbol>: real chained workflow -- coin_analysis for the text
# summary + jarvis_browser's real open/screenshot tools for a visual chart,
# combined into one Telegram photo message. Both tools independently
# verified live before this was written. Screenshot travels as the base64
# string /api/mcp/call already returns (the bot runs on Oracle, the browser
# runs inside Odysseus on Pop!_OS -- there's no shared local filesystem, so
# this decodes into an in-memory buffer, not a fake local file path).
import io
import base64


async def chart(update, context):
    """/chart <SYMBOL> -- real price/RSI summary + a real TradingView chart
    screenshot, chained from tools already verified independently."""
    if not context.args:
        await update.message.reply_text("Usage: /chart <SYMBOL>")
        return
    symbol = context.args[0].upper()
    s = ensure_session()

    try:
        analysis_resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "tradingview", "tool": "coin_analysis",
                  "arguments": {"symbol": symbol, "exchange": "NASDAQ"}},
        )
        analysis_resp.raise_for_status()
        analysis_result = analysis_resp.json()
        if analysis_result.get("exit_code") != 0:
            await update.message.reply_text(f"{symbol} check failed: {analysis_result.get('error')}")
            return
        data = json.loads(analysis_result["stdout"])
        if "error" in data:
            await update.message.reply_text(f"{symbol} check failed: {data['error']}")
            return

        price = data["price_data"]["current_price"]
        change = data["price_data"].get("change_percent")
        rsi = data.get("rsi", {}).get("value")
        trend = data.get("trend_state")
        grade = data.get("grade")

        open_resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "jarvis_browser", "tool": "open", "agent": "browser_agent",
                  "arguments": {"url": f"https://www.tradingview.com/symbols/NASDAQ-{symbol}/"}},
        )
        open_resp.raise_for_status()

        shot_resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
            json={"server": "jarvis_browser", "tool": "screenshot", "agent": "browser_agent",
                  "arguments": {}},
        )
        shot_resp.raise_for_status()
        shot_result = shot_resp.json()
        shot_data = json.loads(shot_result["stdout"])
        img_bytes = base64.b64decode(shot_data["screenshot_base64"])

    except Exception as e:
        await update.message.reply_text(f"Error building chart for {symbol}: {e}")
        return

    caption = (
        f"{symbol}: ${price}\n"
        f"Change: {change}% | RSI: {rsi}\n"
        f"Trend: {trend} | Grade: {grade}"
    )
    await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption=caption)


# --- /charts: multi-asset version of /chart. Reuses the exact same
# real fields (price_data.current_price/change_percent, rsi.value,
# trend_state, grade) and the same base64/BytesIO bridge -- NOT a fake
# "capture_chart.py" script, which doesn't exist. Crypto uses the correct
# KuCoin PAIR format ("XLMUSDT"), not a bare symbol + separate exchange
# param -- that exact combination was already tried and failed earlier
# today ("No data found for XLM on kucoin").
CHARTS_ASSETS = [
    ("NVDA", "coin_analysis", {"symbol": "NVDA", "exchange": "NASDAQ"}, "NASDAQ-NVDA"),
    ("MSFT", "coin_analysis", {"symbol": "MSFT", "exchange": "NASDAQ"}, "NASDAQ-MSFT"),
    ("WDAY", "coin_analysis", {"symbol": "WDAY", "exchange": "NASDAQ"}, "NASDAQ-WDAY"),
    ("SOUN", "coin_analysis", {"symbol": "SOUN", "exchange": "NASDAQ"}, "NASDAQ-SOUN"),
    ("XLM", "coin_analysis", {"symbol": "XLMUSDT"}, "KUCOIN-XLMUSDT"),
    ("XRP", "coin_analysis", {"symbol": "XRPUSDT"}, "KUCOIN-XRPUSDT"),
    ("ADA", "coin_analysis", {"symbol": "ADAUSDT"}, "KUCOIN-ADAUSDT"),
    ("HBAR", "coin_analysis", {"symbol": "HBARUSDT"}, "KUCOIN-HBARUSDT"),
]


async def charts(update, context):
    """/charts -- multi-asset version of /chart. Sends one combined text
    summary, then each real chart photo in sequence. This is genuinely
    slow (8 sequential real MCP + browser round-trips) -- that's an honest
    tradeoff of the real network path, not a bug."""
    s = ensure_session()
    await update.message.reply_text(f"Building {len(CHARTS_ASSETS)}-asset chart snapshot, this will take a bit...")

    summary_lines = ["Multi-Asset Chart Snapshot", ""]
    photos = []

    for label, tool, args, tv_path in CHARTS_ASSETS:
        try:
            analysis_resp = s.post(
                f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
                json={"server": "tradingview", "tool": tool, "arguments": args},
            )
            analysis_resp.raise_for_status()
            analysis_result = analysis_resp.json()
            if analysis_result.get("exit_code") != 0:
                summary_lines.append(f"{label}: check failed ({analysis_result.get('error')})")
                continue
            data = json.loads(analysis_result["stdout"])
            if "error" in data:
                summary_lines.append(f"{label}: check failed ({data['error']})")
                continue

            price = data["price_data"]["current_price"]
            change = data["price_data"].get("change_percent")
            rsi = data.get("rsi", {}).get("value")
            trend = data.get("trend_state")
            summary_lines.append(f"{label}: ${price} ({change}%) -- RSI {rsi}, {trend}")

            open_resp = s.post(
                f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
                json={"server": "jarvis_browser", "tool": "open", "agent": "browser_agent",
                      "arguments": {"url": f"https://www.tradingview.com/symbols/{tv_path}/"}},
            )
            open_resp.raise_for_status()

            shot_resp = s.post(
                f"{ODYSSEUS_BASE}/api/mcp/call", timeout=30,
                json={"server": "jarvis_browser", "tool": "screenshot", "agent": "browser_agent",
                      "arguments": {}},
            )
            shot_resp.raise_for_status()
            shot_data = json.loads(shot_resp.json()["stdout"])
            img_bytes = base64.b64decode(shot_data["screenshot_base64"])
            photos.append((label, img_bytes))

        except Exception as e:
            summary_lines.append(f"{label}: error ({e})")

    await update.message.reply_text("\n".join(summary_lines))
    for label, img_bytes in photos:
        await update.message.reply_photo(photo=io.BytesIO(img_bytes), caption=label)


# --- /intel: full market intelligence packet. Reuses every already-verified
# tool and field name (coin_analysis x8, compare_strategies x2,
# egx_market_overview x1). Text-only -- /charts already exists separately
# for visuals; bundling charts here too would mean 11 sequential real
# round-trips instead of 3, a real, unnecessary slowdown.
INTEL_ASSETS = [
    ("NVDA", {"symbol": "NVDA", "exchange": "NASDAQ"}),
    ("MSFT", {"symbol": "MSFT", "exchange": "NASDAQ"}),
    ("WDAY", {"symbol": "WDAY", "exchange": "NASDAQ"}),
    ("SOUN", {"symbol": "SOUN", "exchange": "NASDAQ"}),
    ("XLM", {"symbol": "XLMUSDT"}),
    ("XRP", {"symbol": "XRPUSDT"}),
    ("ADA", {"symbol": "ADAUSDT"}),
    ("HBAR", {"symbol": "HBARUSDT"}),
]


def _call_mcp(s, server, tool, arguments, agent=None):
    body = {"server": server, "tool": tool, "arguments": arguments}
    if agent:
        body["agent"] = agent
    resp = s.post(f"{ODYSSEUS_BASE}/api/mcp/call", timeout=60, json=body)
    resp.raise_for_status()
    result = resp.json()
    if result.get("exit_code") != 0:
        raise RuntimeError(result.get("error") or "tool call failed")
    return json.loads(result["stdout"])


async def intel(update, context):
    """/intel -- full market intelligence packet: 8-asset snapshot, NVDA +
    XRP strategy comparison, EGX overview. Text-only."""
    s = ensure_session()
    await update.message.reply_text("Building market intelligence packet, this will take a bit...")

    lines = ["Jarvis Market Intelligence Packet", "", "[Multi-Asset Snapshot]"]

    for label, args in INTEL_ASSETS:
        try:
            data = _call_mcp(s, "tradingview", "coin_analysis", args)
            if "error" in data:
                lines.append(f"{label}: check failed ({data['error']})")
                continue
            price = data["price_data"]["current_price"]
            change = data["price_data"].get("change_percent")
            rsi = data.get("rsi", {}).get("value")
            trend = data.get("trend_state")
            lines.append(f"{label}: ${price} ({change}%) -- RSI {rsi}, {trend}")
        except Exception as e:
            lines.append(f"{label}: error ({e})")

    for label, symbol in [("NVDA", "NVDA"), ("XRP", "XRP-USD")]:
        lines.append("")
        lines.append(f"[Strategy Check - {label}]")
        try:
            data = _call_mcp(s, "tradingview", "compare_strategies", {"symbol": symbol, "period": "1y"})
            lines.append(f"Buy & Hold: {data.get('buy_and_hold_return_pct')}%")
            lines.append(f"Winner: {data.get('winner')}")
            top = next((e for e in data.get("ranking", []) if e.get("rank") == 1), None)
            if top:
                lines.append(f"Best return: {top['total_return_pct']}% ({top['total_trades']} trades)")
        except Exception as e:
            lines.append(f"error ({e})")

    lines.append("")
    lines.append("[EGX Overview]")
    try:
        data = _call_mcp(s, "tradingview", "egx_market_overview", {})
        stats = data.get("market_stats", {})
        lines.append(f"Breadth: {stats.get('advancing')} advancing / {stats.get('declining')} declining "
                     f"/ {stats.get('unchanged')} unchanged (avg {stats.get('avg_change')}%)")
        gainers = data.get("top_gainers", [])[:3]
        losers = data.get("top_losers", [])[:3]
        if gainers:
            lines.append("Top Gainers: " + ", ".join(f"{g['symbol']} +{g['changePercent']}%" for g in gainers))
        if losers:
            lines.append("Top Losers: " + ", ".join(f"{l['symbol']} {l['changePercent']}%" for l in losers))
    except Exception as e:
        lines.append(f"error ({e})")

    await update.message.reply_text("\n".join(lines))


# --- /compare_nvda: real strategy leaderboard, NOT a rung backtest.
# compare_strategies' real schema is {symbol, period, initial_capital,
# interval} -- no "exchange", no "timeframe", no custom rule injection.
# This tests 6 GENERIC strategies (rsi/bollinger/macd/ema_cross/supertrend/
# donchian), not the user's specific $186/$225 rung levels -- no real tool
# supports custom threshold backtesting at all. ---

async def compare_nvda(update, context):
    """/compare_nvda -- ranked leaderboard of 6 generic strategies on real
    historical NVDA data. NOT a test of the actual rung levels."""
    s = ensure_session()
    try:
        resp = s.post(
            f"{ODYSSEUS_BASE}/api/mcp/call", timeout=60,
            json={"server": "tradingview", "tool": "compare_strategies",
                  "arguments": {"symbol": "NVDA", "period": "1y"}},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("exit_code") != 0:
            await update.message.reply_text(f"Compare failed: {result.get('error')}")
            return
        data = json.loads(result["stdout"])
    except Exception as e:
        await update.message.reply_text(f"Error running comparison: {e}")
        return

    lines = [
        f"NVDA strategy comparison ({data.get('period')}, {data.get('candles_analyzed')} candles)",
        f"{data.get('date_from')} to {data.get('date_to')}",
        f"Buy & Hold: {data.get('buy_and_hold_return_pct')}%",
        f"Winner: {data.get('winner')}",
        "",
    ]
    for entry in data.get("ranking", []):
        lines.append(
            f"{entry['rank']}. {entry['strategy_label']}: {entry['total_return_pct']}% "
            f"({entry['total_trades']} trades, {entry['win_rate_pct']}% win rate, "
            f"max DD {entry['max_drawdown_pct']}%)"
        )
    lines.append("")
    lines.append("Real historical data (Yahoo Finance) -- not a test of your actual rung levels, "
                 "just generic strategy comparison.")

    await update.message.reply_text("\n".join(lines))


import asyncio

NOTIFY_CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])  # your own chat id, not the bot's
NOTIFY_AGENTS = ["browser_agent", "filesystem_agent"]
NOTIFY_POLL_SECONDS = 5
_last_seen = {}  # agent -> last task id notified


async def _notify_loop(app):
    """Background poller -- checks each agent's history for a new terminal
    task (success/failed) and pushes a Telegram message. Runs inside the
    Application's own event loop via post_init, NOT via asyncio.create_task()
    called before that loop exists (which silently fails to schedule)."""
    s = ensure_session()
    while True:
        try:
            for agent in NOTIFY_AGENTS:
                resp = s.get(f"{ODYSSEUS_BASE}/api/agent-tasks/history/{agent}", timeout=15)
                if resp.status_code != 200:
                    continue
                entries = resp.json().get("history", [])
                if not entries:
                    continue

                latest = entries[0]  # real endpoint sorts newest-first
                task_id = latest.get("id")
                status = latest.get("status")

                if status not in ("success", "failed"):
                    continue  # still pending/running -- nothing to report yet
                if _last_seen.get(agent) == task_id:
                    continue  # already notified about this one

                _last_seen[agent] = task_id
                tool = latest.get("tool")
                server = latest.get("server")
                result = latest.get("result")
                icon = "\u2705" if status == "success" else "\u274c"
                msg = f"{icon} {agent}.{tool} @ {server} [{status}]\n{json.dumps(result)}"
                await app.bot.send_message(chat_id=NOTIFY_CHAT_ID, text=msg)

        except Exception:
            log.exception("notify loop error")

        await asyncio.sleep(NOTIFY_POLL_SECONDS)


# --- MCP server connection state (real fields only) ---
# Real /api/mcp/servers response has NO "connected" boolean and NO
# "last_seen" timestamp (both fabricated in an earlier draft of this
# feature). The real, verified fields are: id, name, transport, command,
# args, env, url, is_enabled, status (a STRING like "connected"/
# "disconnected"), tool_count, error, auth_url. Restart detection isn't
# buildable here -- there's no timestamp field to compare -- so this only
# does outage/recovery detection via status transitions.
_server_status = {}  # server name -> last known status string


async def _server_notify_loop(app):
    s = ensure_session()
    while True:
        try:
            resp = s.get(f"{ODYSSEUS_BASE}/api/mcp/servers", timeout=10)
            if resp.status_code != 200:
                await asyncio.sleep(NOTIFY_POLL_SECONDS)
                continue
            servers = resp.json()

            for entry in servers:
                name = entry.get("name")
                status = entry.get("status")  # real field: string, not boolean
                error = entry.get("error")
                prev = _server_status.get(name)

                if status != "connected" and prev == "connected":
                    msg = f"\u26a0\ufe0f MCP server DOWN: {name} (status: {status})"
                    if error:
                        msg += f"\nError: {error}"
                    await app.bot.send_message(chat_id=NOTIFY_CHAT_ID, text=msg)
                elif status == "connected" and prev is not None and prev != "connected":
                    await app.bot.send_message(
                        chat_id=NOTIFY_CHAT_ID,
                        text=f"\U0001f7e2 MCP server RECOVERED: {name}",
                    )

                _server_status[name] = status

        except Exception:
            log.exception("server notify loop error")

        await asyncio.sleep(NOTIFY_POLL_SECONDS)


async def _post_init(app):
    # Called once the Application's real event loop is running -- the
    # correct place to schedule a background task with this library.
    asyncio.create_task(_notify_loop(app))
    asyncio.create_task(_server_notify_loop(app))


# --- /poller_health: real health check for the TradingView poller's
# SQLite DB, running HERE on Oracle where the actual file lives -- NOT as
# an Odysseus MCP tool, since Odysseus runs in a container on a completely
# different machine (Pop!_OS) with no filesystem access to anything on
# Oracle at all. Real table/column names: market_snapshots (plural), ts
# (not timestamp).
import sqlite3
from datetime import datetime, timezone


async def poller_health(update, context):
    """/poller_health -- checks the real local tradingview_data.db freshness."""
    db_path = os.environ.get("TRADINGVIEW_DB_PATH", "/home/ubuntu/tradingview_data.db")

    if not os.path.exists(db_path):
        await update.message.reply_text(f"DEAD -- db_missing ({db_path})")
        return

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT ts FROM market_snapshots ORDER BY ts DESC LIMIT 1")
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"DEAD -- db_error: {e}")
        return

    if not row:
        await update.message.reply_text("DEAD -- no_rows")
        return

    last_ts = datetime.fromisoformat(row[0])
    now = datetime.now(timezone.utc)
    delta = (now - last_ts).total_seconds()

    if delta < 120:
        status = "HEALTHY"
    elif delta < 300:
        status = "STALE"
    else:
        status = "DEAD"

    await update.message.reply_text(
        f"{status} -- last update {row[0]} ({delta:.0f}s ago)"
    )


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("run", run))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("nvda", nvda))
    app.add_handler(CommandHandler("xlm", xlm))
    app.add_handler(CommandHandler("cc", cc))
    app.add_handler(CommandHandler("xrp", xrp))
    app.add_handler(CommandHandler("ada", ada))
    app.add_handler(CommandHandler("hbar", hbar))
    app.add_handler(CommandHandler("egx", egx))
    app.add_handler(CommandHandler("msft", msft))
    app.add_handler(CommandHandler("wday", wday))
    app.add_handler(CommandHandler("soun", soun))
    app.add_handler(CommandHandler("compare_nvda", compare_nvda))
    app.add_handler(CommandHandler("compare_crypto", compare_crypto))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("charts", charts))
    app.add_handler(CommandHandler("intel", intel))
    app.add_handler(CommandHandler("poller_health", poller_health))
    app.add_handler(CommandHandler("rklb", rklb))
    app.add_handler(CommandHandler("ionq", ionq))
    app.add_handler(CommandHandler("rgti", rgti))
    app.add_handler(CommandHandler("dbc", dbc))
    app.run_polling()


if __name__ == "__main__":
    main()
