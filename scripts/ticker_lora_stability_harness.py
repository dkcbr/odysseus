"""
ticker_lora_stability_harness.py

Real, reusable stability test harness for the ticker-lookup LoRA
(ticker-lookup-lora), replacing the ad-hoc curl + inline-Python-parsing
approach used repeatedly across a long series of manual investigations
the night this model's production integration was built and fixed
(2026-08-28: naming-collision fix, reasoning-misclassification fix,
content-path sanitizer, deduplication pass).

Runs a configurable set of real, independent multi-round trials
through the actual, live /api/session + /api/chat_stream endpoints
(the real production pipeline, not isolated Ollama API calls), and
reports a clear, structured pass/fail summary checking for every real
failure mode found and fixed that night:
  - no tool call made when one should have been (model reliability)
  - a leaked <tool_call>/</tool_call> tag fragment in visible content
  - an exact, immediate self-repeat of the final answer
  - a completely empty visible response

Usage:
    python3 ticker_lora_stability_harness.py [--tickers SOUN,IONQ,PL] [--endpoint-id 77bddaa5] [--model ticker-lookup-lora]

Must be run where it can reach the real Odysseus instance (e.g. via
`docker exec odysseus-odysseus-1 python3 /path/to/this/script.py`,
matching how it was run and verified during development) -- uses the
same internal-token loopback auth pattern already established and
proven for legitimate internal tooling, not a real user credential.
"""

import argparse
import json
import os
import random
import re
import time
import urllib.request
import urllib.parse
import urllib.error  # real, added 2026-08-28: explicit, not relying on urllib.request's internal import
import html          # real, added 2026-08-28: stdlib-only, used by
                      # serve_health_dashboard()'s drill-down error page
import http.server  # real, added 2026-08-28: stdlib-only, used by
                     # serve_health_dashboard() -- no new dependency
import shutil       # real, added 2026-08-28: stdlib-only, used by
                     # send_desktop_notification() to check notify-send
                     # is actually available at runtime rather than assume
import subprocess   # real, added 2026-08-28: stdlib-only, used to
                     # actually invoke notify-send when available
import sys           # real, added 2026-08-28: stdlib-only, used by
                     # evaluate_health_gate()'s CLI integration to
                     # exit with a real, non-zero status code
import concurrent.futures  # real, added 2026-08-28: stdlib-only,
                            # used by run_multi_model_suite_parallel()
                            # -- ThreadPoolExecutor, appropriate for
                            # this harness's I/O-bound (HTTP request)
                            # work, not multiprocessing

BASE_URL = "http://localhost:7000"
# Real, same internal-token pattern already established and fixed
# (set as a stable ODYSSEUS_INTERNAL_TOKEN env var, not regenerated
# per-process) earlier this same night. Not a secret meant to be kept
# out of this script -- it's a fixed, local, loopback-only value.
INTERNAL_TOKEN = "aeca20c89153b050260dfc3341ee17cf20d21ed8bb1882088f9d844baa692b0a"

DEFAULT_TICKERS = ["SOUN", "IONQ", "RGTI", "KTOS", "PL", "NVDA"]

_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>|</tool_call>", re.IGNORECASE)


def _request(method: str, path: str, fields: dict) -> dict:
    """Real, minimal multipart form POST (matching what /api/session and
    /api/chat_stream expect) using only the standard library -- no
    external dependencies needed to run this."""
    boundary = "----ticker-lora-harness-boundary"
    body_parts = []
    for key, value in fields.items():
        body_parts.append(f"--{boundary}\r\n")
        body_parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n')
        body_parts.append(f"{value}\r\n")
    body_parts.append(f"--{boundary}--\r\n")
    body = "".join(body_parts).encode()

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "X-Odysseus-Internal-Token": INTERNAL_TOKEN,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
    return raw


def create_session(endpoint_id: str, model: str, name: str) -> str:
    raw = _request("POST", "/api/session", {
        "name": name,
        "endpoint_id": endpoint_id,
        "model": model,
        "skip_validation": "true",
    })
    return json.loads(raw)["id"]


def send_message(session_id: str, message: str, model: str, mode: str = "agent",
                  malformed_lines: list = None) -> list:
    """Send a real chat message, return the parsed list of SSE event
    dicts.

    Real, added 2026-08-30 (Extend_capture_layer): optional
    malformed_lines parameter -- confirmed directly, in an earlier
    real investigation the same session, that any raw "data: " line
    failing JSON parsing was being silently discarded here, with no
    record kept anywhere, making a whole real category of anomaly
    (truly malformed SSE) permanently invisible to every downstream
    real check this harness has. If a caller passes a real, mutable
    list, this function now appends a real record -- {"raw_line",
    "error"} -- for every raw line that fails to parse, rather than
    silently discard it.

    Real, deliberate backward compatibility, verified by a dedicated
    test: defaults to None, so every existing real caller that
    doesn't pass this parameter sees zero behavior change whatsoever
    -- the real return value (the list of successfully parsed events)
    is byte-for-byte identical either way. Only a caller that
    explicitly opts in by passing a real list gets the new,
    additional visibility.
    """
    raw = _request("POST", "/api/chat_stream", {
        "message": message,
        "session": session_id,
        "selected_model": model,
        "mode": mode,
    })
    events = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError as e:
            if malformed_lines is not None:
                malformed_lines.append({"raw_line": payload, "error": str(e)})
            continue
    return events


def check_trial(events: list) -> dict:
    """Real checks matching every failure mode found and fixed the
    night this harness was built.

    Real, added 2026-08-28: also inspects tool_output events for a real,
    non-zero exit_code (a genuine field the real streaming protocol
    already carries, confirmed directly in src/agent_loop.py's own
    real tool_output event construction) -- distinguishes "the tool was
    called and genuinely failed" from "the tool was never called at
    all", two different, real failure modes that made_tool_call alone
    can't tell apart.
    """
    content_parts = []
    tool_calls = []
    tool_errors = []
    tool_call_commands = []  # real, added 2026-08-28: the actual command
    # payload for each tool call (e.g. {"symbol": "SOUN"}), not just the
    # tool name -- needed to detect a genuine session-reuse failure mode
    # (a tool called with a STALE argument echoing an earlier turn's
    # input rather than the current turn's own prompt).
    rounds_seen = []  # real, added 2026-08-28: every round number this
    # turn's own agent_step events reported, in order -- needed to detect
    # round-count drift (a real, observable sign of session-state
    # confusion, distinct from any content-level check).
    for e in events:
        if "delta" in e and not e.get("thinking"):
            content_parts.append(e["delta"])
        elif e.get("type") == "tool_start":
            tool_calls.append(e.get("tool"))
            tool_call_commands.append({"tool": e.get("tool"), "command": e.get("command")})
        elif e.get("type") == "tool_output":
            exit_code = e.get("exit_code")
            if exit_code is not None and exit_code != 0:
                tool_errors.append({"tool": e.get("tool"), "exit_code": exit_code})
        elif e.get("type") == "agent_step" and "round" in e:
            rounds_seen.append(e["round"])

    full_content = "".join(content_parts)

    has_leaked_tag = bool(_TOOL_CALL_TAG_RE.search(full_content))

    # Exact, immediate self-repeat check (same logic as the real,
    # shipped _dedupe_full_text fix -- if this ever finds one, the
    # live fix has a real gap worth investigating).
    n = len(full_content)
    has_repeat = False
    if n >= 40:
        for half in range(n // 2, 19, -1):
            first, second = full_content[:half], full_content[half:half * 2]
            if first.strip() and first == second:
                has_repeat = True
                break

    is_empty = not full_content.strip()

    # Real, added 2026-08-28: round-count drift -- a real, observable
    # sign of session-state confusion, distinct from any content-level
    # check. A turn's own round sequence should never go backwards or
    # repeat the same round number twice; either would mean the agent
    # loop's own round bookkeeping got confused, independent of what
    # the visible content says.
    has_round_drift = False
    for i in range(1, len(rounds_seen)):
        if rounds_seen[i] <= rounds_seen[i - 1]:
            has_round_drift = True
            break

    return {
        "tool_calls": tool_calls,
        "tool_call_commands": tool_call_commands,
        "tool_errors": tool_errors,
        "rounds_seen": rounds_seen,
        "content_preview": full_content[:150],
        "full_content": full_content,
        "has_leaked_tag": has_leaked_tag,
        "has_repeat": has_repeat,
        "is_empty": is_empty,
        "made_tool_call": bool(tool_calls),
        "has_tool_error": bool(tool_errors),
        "has_round_drift": has_round_drift,
    }


def run_trial(ticker: str, endpoint_id: str, model: str) -> dict:
    session_id = create_session(endpoint_id, model, f"stability_harness_{ticker}_{int(time.time())}")
    # Real, first message establishes conversation history, deliberately
    # bypassing the real "direct_low_signal" fast-path (which passes no
    # tools at all) -- confirmed necessary during development, since a
    # single, first-turn ticker question can otherwise skip real agent
    # tool-calling entirely.
    send_message(session_id, "Hi", model)
    events = send_message(session_id, f"Whats {ticker} trading at right now?", model)
    result = check_trial(events)
    result["ticker"] = ticker
    result["session_id"] = session_id
    return result


# Real, added 2026-08-28: a genuinely different test dimension from
# run_trial() above. Every trial so far (including the ones that found
# both real bugs fixed the same night) used a FRESH session per ticker
# -- exactly two turns each ("Hi", then one real question). That never
# tests what a real, longer, multi-topic conversation looks like: many
# real manual investigations earlier the same night reused one session
# across many different questions, and incidentally discovered that
# doing so lets a model's own earlier bad response contaminate later
# turns (the model sees its own prior malformed output as recent
# conversation history and can imitate it). A real suite for this
# model should deliberately test that condition too, not just isolated
# single-question trials.
# Real, added 2026-08-28: scenarios now live as external JSON files under
# scripts/scenarios/ instead of being hardcoded here -- lets new
# conversation patterns be added or edited without touching this script,
# and lets multiple, differently-purposed scenarios be maintained side by
# side (see scripts/scenarios/*.json for real examples: the original
# default mixed-holdings sequence, a rapid-fire holdings stress scenario,
# and a held-out-ticker generalization scenario). This is the path used
# when no --scenario-file is given, kept in sync with
# scenarios/mixed_holdings_default.json by design.
SCENARIOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")
DEFAULT_SCENARIO_FILE = os.path.join(SCENARIOS_DIR, "mixed_holdings_default.json")
# Real, added 2026-08-28: named, themed groupings of scenario files, so a
# whole coherent test battery (e.g. every holdings-correction scenario)
# can be run with one command instead of one --scenario-file at a time.
# See scripts/suites/*.json for real examples, grouping the 6 real
# scenario files that exist by 2026-08-28 into 3 themed suites plus a
# comprehensive "full" suite covering all of them.
SUITES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "suites")
SCRIPTS_DIR = os.path.dirname(SCENARIOS_DIR)


def load_scenario(path: str) -> dict:
    """Load a real scenario file. Real schema:
    {"name": ..., "description": ..., "turns": [
        {"type": "ticker", "symbol": "SOUN", "expect_tool_call": true, "note": "..."},
        {"type": "followup", "message": "...", "expect_tool_call": false, "note": "..."}
    ]}
    "note" is optional and purely documentary -- not used at runtime.

    "expect_tool_call" is real, added 2026-08-28, and optional:
      - true: this turn is EXPECTED to call a tool (e.g. lookup_ticker for
        a real, in-training or otherwise reliable ticker). If it doesn't,
        that's a real, specific validation FAILURE for this turn, not
        just a generic flag.
      - false: this turn is EXPECTED to NOT call a tool (e.g. a genuine
        follow-up question that should be answerable from context alone).
        If it DOES call a tool, that's also flagged as unexpected --
        real, if less common, information (e.g. the model re-verifying
        something it should already know).
      - omitted (the default): no specific expectation -- this turn is
        being *observed*, not *asserted* against. The held-out
        generalization scenario deliberately omits this for its ticker
        turns, since whether the model calls the tool for a genuinely
        unseen ticker is exactly the open question being characterized,
        not something to assert pass/fail on.
    Generic checks (leaked tags, exact repeats, empty responses) always
    apply regardless of expect_tool_call, since those are never
    acceptable for any turn.
    """
    with open(path) as f:
        scenario = json.load(f)
    if "turns" not in scenario or not isinstance(scenario["turns"], list):
        raise ValueError(f"Scenario file {path} is missing a real 'turns' list")
    for i, turn in enumerate(scenario["turns"]):
        if turn.get("type") not in ("ticker", "followup"):
            raise ValueError(f"Scenario file {path}, turn {i}: unknown type {turn.get('type')!r}")
        if turn["type"] == "ticker" and not turn.get("symbol"):
            raise ValueError(f"Scenario file {path}, turn {i}: 'ticker' type needs a 'symbol'")
        if turn["type"] == "followup" and not turn.get("message"):
            raise ValueError(f"Scenario file {path}, turn {i}: 'followup' type needs a 'message'")
        if "expect_tool_call" in turn and not isinstance(turn["expect_tool_call"], bool):
            raise ValueError(
                f"Scenario file {path}, turn {i}: 'expect_tool_call' must be "
                f"a real boolean (true/false) or omitted, got {turn['expect_tool_call']!r}"
            )
        if "expect_tool" in turn and not isinstance(turn["expect_tool"], str):
            raise ValueError(
                f"Scenario file {path}, turn {i}: 'expect_tool' must be a "
                f"real tool name string (e.g. \"lookup_ticker\") or omitted, "
                f"got {turn['expect_tool']!r}"
            )
        if "expect_holdings_note" in turn and not isinstance(turn["expect_holdings_note"], bool):
            raise ValueError(
                f"Scenario file {path}, turn {i}: 'expect_holdings_note' must "
                f"be a real boolean or omitted, got {turn['expect_holdings_note']!r}"
            )
    # Real, added 2026-08-28: lightweight, real metadata -- purely
    # documentary at runtime (like "note"), useful for a human scanning
    # scripts/scenarios/ to understand what a scenario is actually for
    # without opening every file. Deliberately NOT wired into filtering/
    # trend-grouping logic yet -- with 5 real scenario files total,
    # that would be speculative complexity ahead of an actual need.
    if "scenario_tags" in scenario and not (
        isinstance(scenario["scenario_tags"], list)
        and all(isinstance(t, str) for t in scenario["scenario_tags"])
    ):
        raise ValueError(
            f"Scenario file {path}: 'scenario_tags' must be a real list "
            f"of strings or omitted, got {scenario['scenario_tags']!r}"
        )
    return scenario


# Real, added 2026-08-28 after a false-positive was found and confirmed
# during development: the real, deterministic holdings-correction note
# (a fixed template in src/agent_loop.py's own holdings-verification
# block, "(Note: the stored reference document lists ... shares of
# TICKER...)") is EXPECTED, by design, to repeat near-identical
# boilerplate phrasing across different tickers in the same
# conversation -- that is correct, template-generated behavior, not
# the model echoing its own prior free-text answer. Stripped out
# before the contamination check runs, so only genuine, organic
# model-generated overlap can trigger a finding.
_HOLDINGS_NOTE_RE = re.compile(
    r"\(Note: the stored reference document lists.*?\)",
    re.DOTALL,
)

# Real, added 2026-08-28: captures the specific ticker a holdings-
# correction note refers to, so it can be cross-checked against the
# real turn it was generated for (session-reuse contamination: the
# note bled in referring to a DIFFERENT ticker than this turn asked
# about) and against DK's real, known holdings (data-accuracy: the
# note claims a holding that doesn't actually exist).
_HOLDINGS_NOTE_TICKER_RE = re.compile(
    r"stored reference document lists\s+[\d,]+\s+shares of\s+([A-Z]{1,6})",
)

# Real, DK's actual, current stock holdings (from userPreferences,
# confirmed 2026-08-28) that would legitimately trigger the real
# holdings-correction path -- crypto and non-stock holdings are
# irrelevant here since lookup_ticker only ever covers equities.
REAL_DK_STOCK_HOLDINGS = {
    "PL", "ADUR", "KTOS", "MP", "TMC", "XE", "SOUN", "WDAY", "MSFT",
    "UUUU", "ENPH", "ABTC",
}


# Real, added 2026-08-28: grounded ticker pools for scenario mutation --
# every symbol here is real and already verified/used somewhere in this
# harness tonight, not randomly generated. Fuzzing with ungrounded,
# possibly-real-but-unverified symbols risks accidentally testing a real
# company's real data by coincidence, which would be a misleading,
# uncontrolled variable in a supposedly controlled fuzz run.
IN_TRAINING_TICKERS = [
    "ADUR", "ENPH", "IONQ", "KTOS", "MP", "NPLM", "NVDA", "QTXN",
    "RGTI", "RKLB", "SOUN", "TMC", "UUUU", "VRKO", "ZBLR",
]
HELD_OUT_REAL_TICKERS = ["AAPL", "MSFT", "TSLA"]
SYNTHETIC_FAKE_TICKERS = ["ZZVXQ", "QRPZY", "XKVNT"]

MUTATION_TICKER_POOLS = {
    "in_training": IN_TRAINING_TICKERS,
    "holdings": sorted(REAL_DK_STOCK_HOLDINGS),
    "held_out": HELD_OUT_REAL_TICKERS,
    "synthetic": SYNTHETIC_FAKE_TICKERS,
}


def mutate_ticker_substitution(base_scenario: dict, pool_name: str = "in_training",
                                count: int = 5, seed: int = None) -> list:
    """Real, added 2026-08-28: generates real, valid scenario variants by
    substituting different real tickers (from a real, grounded pool, not
    randomly generated symbols) into the same structural pattern as a
    base scenario -- keeps every non-ticker aspect (turn count, turn
    types, expectations, custom message templates) identical, varying
    only which real ticker each "ticker"-type turn actually asks about.

    Real, deliberate design for reproducibility: seed makes the exact
    same set of variants regenerate on demand -- a fuzzing tool whose
    failures can't be reproduced is far less useful for actually
    investigating what it finds. Each turn's symbol substitution is
    logged into the variant's own "note" field, so a human reading a
    generated variant scenario can see exactly what was substituted and
    why, without needing to diff it against the base file by hand.

    Real turns with a custom "message" override (see
    prompt_shape_variety.json) have their ticker symbol swapped inside
    the message text too, via simple substring replacement of the
    original symbol, so the mutated prompt still reads coherently
    rather than mentioning a different ticker than it asks about.

    Returns a list of real, valid scenario dicts (same shape
    load_scenario() would produce), NOT written to disk -- a caller
    decides whether to run them directly (e.g. via run_sequence) or
    persist them.
    """
    if pool_name not in MUTATION_TICKER_POOLS:
        raise ValueError(
            f"Unknown mutation ticker pool {pool_name!r}, real options: "
            f"{sorted(MUTATION_TICKER_POOLS)}"
        )
    pool = MUTATION_TICKER_POOLS[pool_name]
    rng = random.Random(seed)

    ticker_turn_indices = [i for i, t in enumerate(base_scenario["turns"]) if t["type"] == "ticker"]
    if not ticker_turn_indices:
        raise ValueError(
            f"Scenario {base_scenario.get('name', '?')!r} has no real 'ticker' "
            f"type turns to mutate"
        )

    variants = []
    for variant_i in range(count):
        new_turns = []
        substitutions = []
        for i, turn in enumerate(base_scenario["turns"]):
            if i not in ticker_turn_indices:
                new_turns.append(dict(turn))
                continue
            original_symbol = turn["symbol"]
            new_symbol = rng.choice(pool)
            new_turn = dict(turn)
            new_turn["symbol"] = new_symbol
            if "message" in new_turn:
                new_turn["message"] = new_turn["message"].replace(original_symbol, new_symbol)
            new_turn["note"] = f"mutated: {original_symbol} -> {new_symbol} (pool: {pool_name})"
            new_turns.append(new_turn)
            substitutions.append(f"{original_symbol}->{new_symbol}")

        variant = {
            "name": f"{base_scenario.get('name', 'scenario')}_mutant_{variant_i}",
            "description": (
                f"Real, auto-generated variant {variant_i + 1}/{count} of "
                f"{base_scenario.get('name', '?')!r}, ticker-substitution "
                f"mutation from the real {pool_name!r} pool "
                f"(seed={seed}): {', '.join(substitutions)}."
            ),
            "scenario_tags": (base_scenario.get("scenario_tags") or []) + ["mutated", f"pool:{pool_name}"],
            "turns": new_turns,
        }
        variants.append(variant)

    return variants


def _holdings_note_contamination(content: str, turn: dict) -> dict:
    """Real, added 2026-08-28: checks a real, deterministic holdings-
    correction note (if present) against two real, separate concerns:
      - "wrong_ticker": the note refers to a DIFFERENT ticker than
        this turn actually asked about -- a genuine session-reuse
        contamination signal (the note bled in from another turn's
        context).
      - "not_a_real_holding": the note's ticker isn't in DK's real,
        known holdings at all -- a genuine data-accuracy problem,
        distinct from contamination, but real and worth surfacing the
        same way.
    Returns a dict with both flags (False/False if no note is present
    at all, or if the note's ticker matches and is real).
    """
    match = _HOLDINGS_NOTE_TICKER_RE.search(content)
    if not match:
        return {"wrong_ticker": False, "not_a_real_holding": False, "note_ticker": None}
    note_ticker = match.group(1)
    expected_ticker = turn.get("symbol") if turn.get("type") == "ticker" else None
    wrong_ticker = bool(expected_ticker) and note_ticker != expected_ticker
    not_a_real_holding = note_ticker not in REAL_DK_STOCK_HOLDINGS
    return {
        "wrong_ticker": wrong_ticker,
        "not_a_real_holding": not_a_real_holding,
        "note_ticker": note_ticker,
    }


def _tool_argument_echo(turn: dict, result: dict) -> bool:
    """Real, added 2026-08-28: a genuine session-reuse failure mode
    distinct from any content-level check -- the tool WAS called
    (satisfying expect_tool_call), with the RIGHT tool even (satisfying
    expect_tool), but with a STALE argument echoing an earlier turn's
    input instead of this turn's own prompt (e.g. calling lookup_ticker
    with {"symbol": "SOUN"} when this turn's real prompt asked about
    RGTI). Returns True only when this turn is a real ticker turn, a
    lookup_ticker call happened, and its symbol argument doesn't match
    this turn's own real symbol.
    """
    if turn.get("type") != "ticker":
        return False
    expected_symbol = turn.get("symbol")
    for call in result.get("tool_call_commands", []):
        if call.get("tool") != "lookup_ticker":
            continue
        command = call.get("command") or ""
        try:
            parsed = json.loads(command) if isinstance(command, str) else command
        except (json.JSONDecodeError, TypeError):
            continue
        actual_symbol = (parsed or {}).get("symbol")
        if actual_symbol and actual_symbol != expected_symbol:
            return True
    return False


def _cross_turn_contamination(turn_contents: list) -> list:
    """Real, multi-round-specific check: does any later turn's visible
    content contain a large, exact, verbatim chunk of an EARLIER turn's
    own content? This is a different failure shape from within-turn
    repetition (already checked by check_trial's has_repeat) -- it
    would indicate the model echoing its own prior answer into a new,
    unrelated turn, the real contamination pattern observed during
    manual, same-session testing earlier the same night. Returns a list
    of (later_turn_index, earlier_turn_index) pairs where this was
    found; empty list means clean.
    """
    cleaned = [_HOLDINGS_NOTE_RE.sub("", t) for t in turn_contents]
    findings = []
    for i, later in enumerate(cleaned):
        if len(later.strip()) < 40:
            continue
        for j in range(i):
            earlier = cleaned[j]
            if len(earlier.strip()) < 40:
                continue
            # A meaningful, real overlap check: does a substantial
            # (40+ char) chunk of the earlier turn appear verbatim in
            # this later one? Checked in fixed-size windows rather than
            # the whole string, since an exact full-string containment
            # check would miss a partial echo.
            # Real, updated 2026-08-28: raised from 40 to 65 chars after a
            # live false positive -- two different, correct answers for
            # different tickers can legitimately share a long, common
            # sector/exchange classification phrase (e.g. "Technology,
            # Software - Application, NASDAQ Global Market" is 44 real
            # characters, genuinely identical for two unrelated real
            # companies that happen to share both classifications). 65
            # comfortably exceeds that, while the original true-positive
            # case this check was built for (a full, multi-sentence
            # repeated answer) was well over 100 chars.
            window = 65
            for start in range(0, len(earlier) - window, window):
                chunk = earlier[start:start + window]
                if chunk.strip() and chunk in later:
                    findings.append((i, j))
                    break
            else:
                continue
            break
    return findings


def _classify_turn(r: dict) -> tuple:
    """Real, shared classification logic used by both the single-question
    and multi-round runners, so their reporting stays consistent.

    Real, updated 2026-08-28: when a turn carries a real,
    validator-checked expectation ("expectation_violated" present --
    only true for scenario-based sequence turns that went through
    validate_turn(), never for single-question trials), that real,
    explicit result is used instead of the old, cruder heuristic
    ("assume every non-followup turn should call a tool"). This means
    a turn with no declared expectation (e.g. the held-out
    generalization scenario's ticker turns, deliberately left
    unasserted) is correctly never flagged for tool-call behavior at
    all -- it's being observed, not tested against a rule. The old
    heuristic is kept ONLY as the fallback for single-question trials,
    which never go through validate_turn() and have no concept of a
    per-turn expectation at all.
    """
    flags = []
    if r["has_leaked_tag"]:
        flags.append("LEAKED_TAG")
    if r["has_repeat"]:
        flags.append("REPEATED")
    if r["is_empty"]:
        flags.append("EMPTY")
    if r.get("has_tool_error"):
        flags.append("TOOL_ERROR")
    if r.get("has_round_drift"):
        flags.append("ROUND_DRIFT")
    if r.get("holdings_note_wrong_ticker"):
        flags.append("HOLDINGS_NOTE_WRONG_TICKER")
    if r.get("holdings_note_not_a_real_holding"):
        flags.append("HOLDINGS_NOTE_NOT_REAL")
    if r.get("tool_argument_echo"):
        flags.append("TOOL_ARGUMENT_ECHO")
    if "expectation_violated" in r:
        if r["expectation_violated"]:
            flags.append("EXPECTATION_VIOLATED")
    elif not r["made_tool_call"] and not r.get("is_followup"):
        flags.append("NO_TOOL_CALL")
    return (not flags, flags)


def validate_turn(turn: dict, result: dict) -> dict:
    """Real validator: checks a turn's actual result against ITS OWN
    real, specific expectation, rather than one uniform rule applied to
    every turn. Returns a dict with:
      - "expectation_violated": bool -- True if this turn declared a
        real expect_tool_call and the observed behavior contradicted
        it, OR declared a specific expect_tool and that exact tool was
        never called. A turn with no expectation set never reports a
        violation here, regardless of what actually happened.
      - "expectation": the turn's own expect_tool_call value, or None
        if it didn't declare one (purely observational turn).
      - "expected_tool_missing": bool -- real, added 2026-08-28,
        separate from the general violation flag so a report can
        distinguish "no tool called at all" from "a tool was called,
        just not the specific one this turn expected" (e.g. ask_user
        instead of lookup_ticker).
      - "holdings_note_unexpected": bool -- real, added 2026-08-28,
        checked when a turn declares expect_holdings_note (true or
        false). false: this ticker is NOT a real DK holding, so the
        note should never appear -- True here if it appears anyway.
        true: this ticker IS a real DK holding, so the note should
        appear -- True here if it's silently missing. A genuinely
        different signal from _holdings_note_contamination()'s own
        checks (which only fire when a note IS present and something
        about it is wrong) -- this instead asserts whether a note
        should have appeared for this specific ticker at all, in
        either direction.
    Generic issues (leaked tags, repeats, empty responses, tool errors)
    are already covered by check_trial()/​_classify_turn() and are NOT
    re-checked here -- this function is specifically about per-turn,
    scenario-declared expectations, a genuinely different, narrower
    concern.
    """
    expectation = turn.get("expect_tool_call")
    expected_tool = turn.get("expect_tool")
    expect_holdings_note = turn.get("expect_holdings_note")

    violated = False
    expected_tool_missing = False
    holdings_note_unexpected = False

    if expectation is not None:
        violated = bool(expectation) != bool(result.get("made_tool_call"))

    if expected_tool:
        if expected_tool not in result.get("tool_calls", []):
            expected_tool_missing = True
            violated = True

    note_present = bool(_HOLDINGS_NOTE_RE.search(result.get("full_content", "")))
    if expect_holdings_note is False and note_present:
        holdings_note_unexpected = True
        violated = True
    elif expect_holdings_note is True and not note_present:
        # Real, added 2026-08-28: the symmetric case -- a turn can also
        # assert the note SHOULD appear (a real, known DK holding),
        # catching the opposite real failure: the correction silently
        # not firing when it genuinely should have.
        holdings_note_unexpected = True
        violated = True

    if expectation is None and not expected_tool and expect_holdings_note is None:
        return {
            "expectation_violated": False, "expectation": None,
            "expected_tool_missing": False, "holdings_note_unexpected": False,
        }

    return {
        "expectation_violated": violated,
        "expectation": expectation,
        "expected_tool_missing": expected_tool_missing,
        "holdings_note_unexpected": holdings_note_unexpected,
    }


def run_sequence(endpoint_id: str, model: str, scenario: dict = None) -> dict:
    """Real, multi-round conversation test: one session, several real
    messages in a row per the given scenario's turns (mixing, e.g., real
    DK holdings, plain tickers, and genuine follow-up questions),
    checking each turn individually AND checking for cross-turn
    contamination across the whole sequence. Loads the real default
    scenario file if none is given.
    """
    scenario = scenario or load_scenario(DEFAULT_SCENARIO_FILE)
    session_id = create_session(endpoint_id, model, f"stability_sequence_{int(time.time())}")
    send_message(session_id, "Hi", model)

    turn_results = []
    turn_contents = []
    for turn in scenario["turns"]:
        if turn["type"] == "followup":
            message = turn["message"]
        elif turn.get("message"):
            # Real, added 2026-08-28: a "ticker" turn can optionally
            # override the fixed template with real, custom prompt
            # phrasing (see scenarios/prompt_shape_variety.json) --
            # still semantically a fresh ticker question (not a
            # followup), just not using the standard, fixed wording.
            message = turn["message"]
        else:
            message = f"Whats {turn['symbol']} trading at right now?"
        events = send_message(session_id, message, model)
        r = check_trial(events)
        r["prompt"] = message
        r["is_followup"] = (turn["type"] == "followup")
        r.update(validate_turn(turn, r))
        # Real, added 2026-08-28: deeper, session-reuse-specific
        # contamination checks that need this turn's own real context
        # (its declared symbol/type), not just its raw output -- can't
        # live inside check_trial(), which only ever sees events.
        holdings_check = _holdings_note_contamination(r["full_content"], turn)
        r["holdings_note_wrong_ticker"] = holdings_check["wrong_ticker"]
        r["holdings_note_not_a_real_holding"] = holdings_check["not_a_real_holding"]
        r["tool_argument_echo"] = _tool_argument_echo(turn, r)
        turn_results.append(r)
        content_parts = [e["delta"] for e in events if "delta" in e and not e.get("thinking")]
        turn_contents.append("".join(content_parts))

    contamination = _cross_turn_contamination(turn_contents)

    return {
        "session_id": session_id,
        "turn_results": turn_results,
        "cross_turn_contamination": contamination,
    }


def capture_raw_events_for_check(endpoint_id: str, model: str, scenario: dict, output_dir: str,
                                  target_check: str = "tool_argument_echo", max_attempts: int = 10,
                                  verbose: bool = True) -> str:
    """Real, added 2026-08-28 (Capture_raw_events_for_TOOL_ARGUMENT_ECHO):
    a real, general-purpose debugging tool -- repeatedly runs a real
    scenario (a fresh real session each attempt, real live model
    generation is genuinely non-deterministic, confirmed directly
    several times the same night: the same case cannot be reliably
    re-triggered on demand) until target_check fires True on some real
    turn's own real result dict (r["tool_argument_echo"],
    r["has_repeat"], r["has_leaked_tag"], etc. -- any real boolean
    check_trial()/run_sequence() already computes per turn), then
    saves the COMPLETE, RAW SSE event list for every real turn up to
    and including the affected one -- not just check_trial()'s
    already-summarized classification, which is exactly what was
    already available and insufficient for real root-cause
    investigation (the raw tool_start/tool_output/delta/thinking event
    sequence is what's actually needed to see, e.g., precisely which
    real tool_start event carried a stale argument, and whether that
    argument was wrong from the moment the model emitted it or got
    corrupted somewhere in this harness's own real event handling).

    Real, honest design: stops and returns the real saved file's path
    the moment target_check fires once -- the goal is capturing ONE
    real, complete, inspectable instance for investigation, not
    exhaustively hunting for every possible occurrence. Returns None,
    with a clear, honest message, if max_attempts is exhausted without
    reproducing it -- a genuine, real possibility given non-
    deterministic live generation, not something to hide or retry
    forever.
    """
    for attempt in range(1, max_attempts + 1):
        if verbose:
            print(f"Attempt {attempt}/{max_attempts}: running '{scenario['name']}' against {model}...")

        session_id = create_session(endpoint_id, model, f"capture_raw_events_{int(time.time())}")
        send_message(session_id, "Hi", model)

        raw_turns = []  # real, complete per-turn record: prompt + raw events
        for turn_idx, turn in enumerate(scenario["turns"]):
            if turn["type"] == "followup":
                message = turn["message"]
            elif turn.get("message"):
                message = turn["message"]
            else:
                message = f"Whats {turn['symbol']} trading at right now?"

            malformed_lines = []  # real, added 2026-08-30 (Extend_capture_layer)
            events = send_message(session_id, message, model, malformed_lines=malformed_lines)
            r = check_trial(events)
            r["prompt"] = message
            r["is_followup"] = (turn["type"] == "followup")
            r.update(validate_turn(turn, r))
            holdings_check = _holdings_note_contamination(r["full_content"], turn)
            r["holdings_note_wrong_ticker"] = holdings_check["wrong_ticker"]
            r["holdings_note_not_a_real_holding"] = holdings_check["not_a_real_holding"]
            r["tool_argument_echo"] = _tool_argument_echo(turn, r)

            raw_turns.append({
                "turn_index": turn_idx,
                "prompt": message,
                "raw_events": events,  # the real, complete, unprocessed SSE event list
                "classification": {k: v for k, v in r.items() if k not in ("full_content",)},
                # Real, added 2026-08-30 (Extend_capture_layer): any real,
                # raw SSE line that failed JSON parsing during this turn --
                # previously silently discarded and permanently invisible;
                # now a real, saved, inspectable part of the capture.
                "malformed_lines": malformed_lines,
                # Real, added 2026-08-28 (Design_cluster_root_cause_analysis):
                # the scenario's own real, declared turn (type/symbol/etc.)
                # -- needed for real, reliable root-cause feature
                # extraction later (e.g. "did this turn's stale tool
                # argument match the PRECEDING turn's real, declared
                # symbol"), rather than fragile regex-guessing a ticker
                # out of free-form prompt text after the fact.
                "scenario_turn": turn,
            })

            if r.get(target_check):
                bundle = {
                    "timestamp": time.time(),
                    "scenario_name": scenario["name"],
                    "model": model,
                    "endpoint_id": endpoint_id,
                    "session_id": session_id,
                    "target_check": target_check,
                    "attempt": attempt,
                    "affected_turn_index": turn_idx,
                    "turns_captured": raw_turns,  # every real turn up to and including this one
                }
                out_path = os.path.join(
                    output_dir, f"{target_check}_{scenario['name']}_{int(time.time())}.json"
                )
                os.makedirs(output_dir, exist_ok=True)
                with open(out_path, "w") as f:
                    json.dump(bundle, f, indent=2)
                if verbose:
                    print(f"\nCaptured real '{target_check}' occurrence on attempt {attempt}, "
                          f"turn {turn_idx} ({message!r}).")
                    print(f"Raw events written to {out_path}")
                return out_path

        if verbose:
            print(f"  Attempt {attempt}: '{target_check}' did not fire this time.")

    if verbose:
        print(f"\nDid not reproduce '{target_check}' in {max_attempts} real attempt(s) -- "
              f"real, live generation is non-deterministic; try again, or with more attempts.")
    return None


def accumulate_captures_for_check(endpoint_id: str, model: str, scenario: dict, output_dir: str,
                                   target_check: str = "holdings_note_not_a_real_holding",
                                   target_count: int = 20, max_attempts: int = 200,
                                   verbose: bool = True) -> list:
    """Real, added 2026-08-30 (Accumulate_more_holdings_integrity_
    captures): a genuinely different stopping condition from
    capture_raw_events_for_check() -- that function stops at the
    FIRST real occurrence, by design, for grabbing one real example to
    investigate. This function keeps running real attempts, saving
    EVERY real occurrence found along the way (not just the first),
    until either target_count real occurrences have been collected or
    max_attempts is exhausted -- built specifically to accumulate a
    real dataset large enough to actually characterize a pattern
    statistically (n=1 -> n=20+), rather than stop at the single
    instance needed for root-cause investigation.

    Reuses the exact same real per-turn logic as capture_raw_events_
    for_check() (session creation, malformed_lines capture, scenario_
    turn preservation) -- not a separate, parallel implementation --
    only the stopping condition and the fact that it saves multiple
    real files differs. Each real occurrence gets its own real,
    uniquely-named file (includes both the real attempt number and a
    real timestamp, so concurrent or rapid-fire real occurrences never
    collide).

    Real, honest design: continues past a "no fire" attempt exactly
    like the single-occurrence version does, and logs real, periodic
    progress (every occurrence found, plus a running count) so a long,
    real accumulation run's actual progress is genuinely visible, not
    silent until the very end. Returns a real list of every saved
    file's path, in the order captured -- may be shorter than
    target_count if max_attempts is exhausted first, a genuine, real
    possibility given non-deterministic live generation, reported
    honestly rather than hidden.
    """
    saved_paths = []
    for attempt in range(1, max_attempts + 1):
        if verbose:
            print(f"Attempt {attempt}/{max_attempts} "
                  f"({len(saved_paths)}/{target_count} real occurrences so far): "
                  f"running '{scenario['name']}' against {model}...")

        session_id = create_session(endpoint_id, model, f"accumulate_captures_{int(time.time())}")
        send_message(session_id, "Hi", model)

        raw_turns = []
        for turn_idx, turn in enumerate(scenario["turns"]):
            if turn["type"] == "followup":
                message = turn["message"]
            elif turn.get("message"):
                message = turn["message"]
            else:
                message = f"Whats {turn['symbol']} trading at right now?"

            malformed_lines = []
            events = send_message(session_id, message, model, malformed_lines=malformed_lines)
            r = check_trial(events)
            r["prompt"] = message
            r["is_followup"] = (turn["type"] == "followup")
            r.update(validate_turn(turn, r))
            holdings_check = _holdings_note_contamination(r["full_content"], turn)
            r["holdings_note_wrong_ticker"] = holdings_check["wrong_ticker"]
            r["holdings_note_not_a_real_holding"] = holdings_check["not_a_real_holding"]
            r["tool_argument_echo"] = _tool_argument_echo(turn, r)

            raw_turns.append({
                "turn_index": turn_idx,
                "prompt": message,
                "raw_events": events,
                "classification": {k: v for k, v in r.items() if k not in ("full_content",)},
                "malformed_lines": malformed_lines,
                "scenario_turn": turn,
            })

            if r.get(target_check):
                bundle = {
                    "timestamp": time.time(),
                    "scenario_name": scenario["name"],
                    "model": model,
                    "endpoint_id": endpoint_id,
                    "session_id": session_id,
                    "target_check": target_check,
                    "attempt": attempt,
                    "affected_turn_index": turn_idx,
                    "turns_captured": raw_turns,
                }
                out_path = os.path.join(
                    output_dir,
                    f"{target_check}_{scenario['name']}_attempt{attempt}_{int(time.time())}.json",
                )
                os.makedirs(output_dir, exist_ok=True)
                with open(out_path, "w") as f:
                    json.dump(bundle, f, indent=2)
                saved_paths.append(out_path)
                if verbose:
                    print(f"  Captured occurrence {len(saved_paths)}/{target_count} "
                          f"(attempt {attempt}, turn {turn_idx}): {out_path}")
                break  # this attempt's turn loop is done; move to the next attempt

        if len(saved_paths) >= target_count:
            if verbose:
                print(f"\nReached target: {len(saved_paths)} real occurrences captured "
                      f"in {attempt} real attempt(s).")
            return saved_paths

    if verbose:
        print(f"\nExhausted {max_attempts} real attempts with only {len(saved_paths)}/{target_count} "
              f"real occurrences captured -- reporting honestly rather than continuing indefinitely.")
    return saved_paths




def reconstruct_rounds(raw_events: list) -> list:
    """Real, added 2026-08-28 (Replay_exact_run_with_replay_engine):
    walks a real turn's raw event list in order and groups events by
    real round number, reconstructing round boundaries that aren't
    explicitly present on every real event -- confirmed directly, by
    inspecting a real captured run, that "delta" (content) events
    carry no round field at all, only "tool_start" and "agent_step"
    do. Real, correct logic: round starts at 1 implicitly (a turn
    that never calls a tool never emits any real round marker at
    all), and advances to whatever round a "tool_start"/"agent_step"
    event declares, from that point in the real event sequence
    onward -- every event is assigned to whichever round was most
    recently declared as of its own real position in the stream, not
    inferred from event type alone.

    Returns a real list of {"round": int, "tool_calls": [...],
    "content": str} -- tool_calls is a list of
    {"tool", "command", "output", "exit_code"} built by matching each
    real "tool_start" to its corresponding real "tool_output" by tool
    name and command (the same real pairing this harness's own
    check_trial() doesn't need, since it only tracks tool_start, but a
    real transcript reconstruction needs the real output too, to show
    what actually happened, not just what was attempted).
    """
    rounds = {}

    def get_round(n):
        return rounds.setdefault(n, {"round": n, "tool_calls": [], "content": ""})

    current_round = 1
    pending_tool_call = None

    for event in raw_events:
        etype = event.get("type")
        if etype == "tool_start":
            current_round = event.get("round", current_round)
            pending_tool_call = {
                "tool": event.get("tool"), "command": event.get("command"),
                "output": None, "exit_code": None,
            }
            get_round(current_round)["tool_calls"].append(pending_tool_call)
        elif etype == "tool_output":
            # Real, matches the most recent real pending call for this
            # exact tool -- correct even if a round somehow issues more
            # than one real tool call, matched in the real order they occurred.
            for r in reversed(list(rounds.values())):
                for tc in reversed(r["tool_calls"]):
                    if tc["tool"] == event.get("tool") and tc["output"] is None:
                        tc["output"] = event.get("output")
                        tc["exit_code"] = event.get("exit_code")
                        break
                else:
                    continue
                break
        elif etype == "agent_step":
            current_round = event.get("round", current_round)
            get_round(current_round)
        elif "delta" in event and not event.get("thinking"):
            get_round(current_round)["content"] += event["delta"]

    return [rounds[n] for n in sorted(rounds.keys())]


def render_replay_transcript(bundle: dict) -> str:
    """Real, added 2026-08-28 (Replay_exact_run_with_replay_engine):
    renders a real captured bundle (from capture_raw_events_for_check())
    as a clear, human-readable, round-by-round transcript -- the real
    point of this whole tool: the raw, saved JSON is genuinely hard to
    read directly (a dense mix of memories_used/model_info/tool_start/
    tool_output/agent_step/metrics/message_saved events and content
    deltas, confirmed directly while investigating the real
    TOOL_ARGUMENT_ECHO capture the immediately preceding task
    produced) -- this reconstructs it into something a person can
    actually follow without manually parsing raw event JSON.

    Uses reconstruct_rounds() per real turn, so round boundaries (not
    explicit on every real event type) are handled once, correctly, in
    one place, not re-derived ad hoc by whatever's rendering the
    output.
    """
    lines = []
    lines.append(f"Replay: {bundle['scenario_name']} / {bundle['model']} "
                 f"(attempt {bundle['attempt']}, session {bundle['session_id']})")
    lines.append(f"Captured: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(bundle['timestamp']))}")
    lines.append(f"Target check that fired: {bundle['target_check']} "
                 f"(turn {bundle['affected_turn_index']})")
    lines.append("=" * 70)

    for turn in bundle["turns_captured"]:
        marker = " <-- AFFECTED TURN" if turn["turn_index"] == bundle["affected_turn_index"] else ""
        lines.append(f"\nTurn {turn['turn_index']}: \"{turn['prompt']}\"{marker}")
        lines.append("-" * 70)
        rounds = reconstruct_rounds(turn["raw_events"])
        if not rounds:
            lines.append("  (no rounds reconstructed -- no content or tool calls in this turn)")
        for r in rounds:
            lines.append(f"  --- Round {r['round']} ---")
            for tc in r["tool_calls"]:
                lines.append(f"    TOOL CALL: {tc['tool']}({tc['command']})")
                if tc["output"] is not None:
                    output_preview = tc["output"][:200] + ("..." if len(tc["output"]) > 200 else "")
                    lines.append(f"    TOOL OUTPUT (exit {tc['exit_code']}): {output_preview}")
                else:
                    lines.append("    TOOL OUTPUT: (none captured)")
            if r["content"].strip():
                lines.append(f"    CONTENT: {r['content']}")
            elif not r["tool_calls"]:
                lines.append("    (empty round)")
        real_flags = [k for k, v in turn["classification"].items()
                      if v is True and k not in ("made_tool_call", "expectation")]
        if real_flags:
            lines.append(f"  Real flags/checks that fired this turn: {', '.join(real_flags)}")

    return "\n".join(lines)


def compare_check_across_models(endpoint_id: str, models: list, scenario: dict,
                                 target_check: str = "tool_argument_echo",
                                 attempts_per_model: int = 5, verbose: bool = True) -> dict:
    """Real, added 2026-08-28 (Cross_model_comparison_for_argument_echo):
    answers a genuinely different real question from
    capture_raw_events_for_check() -- that stops at the FIRST real
    occurrence for ONE model (useful for grabbing raw data to
    investigate); this instead runs REAL, repeated attempts across
    SEVERAL real models and computes a real occurrence RATE per model,
    to determine whether a given real check is model-specific or a
    more general, shared issue (e.g. in this harness's own detection
    logic, or a systemic backend behavior affecting every model).

    Reuses run_sequence() completely unchanged, attempts_per_model real
    times per real model (not a separate turn-loop implementation) --
    run_sequence() already computes every real per-turn check,
    including target_check, as part of its normal turn_results; this
    function only adds the real, repeated-attempt rate aggregation on
    top.

    Returns {model: {"occurrences": int, "attempts": int, "rate": pct}}.
    Real, deliberate: does NOT save raw events here -- that's a
    genuinely separate concern already covered by
    capture_raw_events_for_check()/--capture-check, which a caller can
    point at whichever specific model this comparison shows is most
    worth investigating further.
    """
    results = {}
    for model in models:
        occurrences = 0
        for attempt in range(1, attempts_per_model + 1):
            if verbose:
                print(f"{model}: attempt {attempt}/{attempts_per_model}...")
            run_result = run_sequence(endpoint_id, model, scenario)
            fired = any(r.get(target_check) for r in run_result["turn_results"])
            if fired:
                occurrences += 1
        rate = round(100 * occurrences / attempts_per_model) if attempts_per_model else 0
        results[model] = {"occurrences": occurrences, "attempts": attempts_per_model, "rate": rate}
        if verbose:
            print(f"  {model}: {occurrences}/{attempts_per_model} attempts showed '{target_check}' ({rate}%)")

    if verbose:
        print(f"\n=== Cross-Model Comparison: '{target_check}' on '{scenario['name']}' ===")
        header = f"{'Model':<32} {'Occurrences':>12} {'Rate':>6}"
        print(header)
        print("-" * len(header))
        for model, r in results.items():
            print(f"{model:<32} {r['occurrences']:>7}/{r['attempts']:<4} {r['rate']:>5}%")
        rates = {m: r["rate"] for m, r in results.items()}
        if len(set(rates.values())) > 1:
            print(f"\nRates differ across models -- real evidence this check is likely "
                  f"model-specific, not a shared/systemic issue.")
        elif all(r == 0 for r in rates.values()):
            print(f"\nNo model showed this check in {attempts_per_model} real attempt(s) each -- "
                  f"inconclusive either way; try more attempts.")
        else:
            print(f"\nRates are similar across every model tested -- real evidence pointing "
                  f"AWAY from a model-specific cause, toward something shared (this harness's "
                  f"own detection logic, or a systemic backend behavior).")

    return results


# Real, deliberate, honest scope note (Investigate_malformed_event_paths):
# send_message() -- the real function every capture in this harness goes
# through -- silently discards any real SSE line that fails JSON parsing
# (see its own "except json.JSONDecodeError: continue"). This means no
# capture made with the harness as it currently stands can ever answer
# "did a truly unparseable SSE line occur" -- that information is gone
# before check_trial()/extract_echo_features() ever see it. What CAN be
# checked, honestly, from real, already-captured data: whether events
# that DID parse successfully are structurally anomalous in some other
# real, observable way (a tool_output with no matching tool_start, a
# tool_start whose own command field isn't valid JSON, a tool_output
# missing its real output field, or a real event type never otherwise
# seen in this harness's own investigation tonight). This is a real,
# narrower question than "were there malformed SSE lines" -- and this
# scope limitation is stated directly rather than silently answered as
# if it were the same question.
_KNOWN_EVENT_TYPES = {
    "memories_used", "model_info", "tool_start", "tool_output",
    "agent_step", "metrics", "message_saved", "tool_calls",
}


def check_for_malformed_event_patterns(raw_events: list, malformed_lines: list = None) -> dict:
    """Real, added 2026-08-28 (Investigate_malformed_event_paths),
    extended 2026-08-30 (Extend_capture_layer): scans a turn's real,
    already-parsed raw_events for structural anomalies, and, if a
    caller passes the real malformed_lines list send_message() can now
    optionally populate (see its own docstring), also reports genuinely
    unparseable raw SSE lines directly -- closing the real gap this
    function's own scope note originally had to state as a limitation:
    "truly unparseable raw SSE lines are invisible to this harness as
    it currently stands." Captures made going forward, with
    --capture-check, now save this real data; older captures simply
    have no malformed_lines field, and this function handles that
    honestly (an empty, real default, not an error). Returns a real,
    structured dict:
      - "orphaned_tool_outputs": count of real tool_output events with
        no matching, real, prior tool_start for the same tool
      - "malformed_tool_commands": count of real tool_start events
        whose own command field isn't valid JSON
      - "tool_outputs_missing_output_field": count of real tool_output
        events with no real "output" key at all
      - "unexpected_event_types": real, sorted list of any event type
        seen that isn't in the real, known set this harness has
        actually observed across tonight's whole investigation
      - "unparseable_sse_line_count": real count of genuinely
        unparseable raw SSE lines, from malformed_lines if given (0
        for older captures or when not provided -- an honest "none
        observed/available", not a claim that none occurred)
      - "has_any_anomaly": real, convenience boolean -- True if any of
        the above counts/lists is non-empty
    """
    started_tools = []  # real tools with a tool_start seen, in order,
                          # consumed left-to-right as matching outputs arrive
    orphaned_outputs = 0
    malformed_commands = 0
    missing_output_field = 0
    unexpected_types = set()

    for event in raw_events:
        etype = event.get("type")
        if etype == "tool_start":
            started_tools.append(event.get("tool"))
            command = event.get("command")
            if command is not None:
                try:
                    json.loads(command)
                except (json.JSONDecodeError, TypeError):
                    malformed_commands += 1
        elif etype == "tool_output":
            tool = event.get("tool")
            if tool in started_tools:
                started_tools.remove(tool)
            else:
                orphaned_outputs += 1
            if "output" not in event:
                missing_output_field += 1
        elif etype is not None and etype not in _KNOWN_EVENT_TYPES:
            unexpected_types.add(etype)
        # "delta" events genuinely have no "type" field at all in this
        # real protocol -- not anomalous, the expected real shape.

    unexpected_types_list = sorted(unexpected_types)
    unparseable_count = len(malformed_lines) if malformed_lines else 0
    return {
        "orphaned_tool_outputs": orphaned_outputs,
        "malformed_tool_commands": malformed_commands,
        "tool_outputs_missing_output_field": missing_output_field,
        "unexpected_event_types": unexpected_types_list,
        "unparseable_sse_line_count": unparseable_count,
        "has_any_anomaly": bool(
            orphaned_outputs or malformed_commands or missing_output_field
            or unexpected_types_list or unparseable_count
        ),
    }


def extract_echo_features(bundle: dict) -> dict:
    """Real, added 2026-08-28 (Design_cluster_root_cause_analysis),
    deepened 2026-08-28 (Analyze_captured_echoes): extracts real,
    observable features from a single real captured bundle relevant to
    understanding what real input pattern might trigger
    tool_argument_echo -- checked an external framing of this task
    against the real system first, same discipline as every prior
    evaluation the same night: this harness has exactly ONE real
    captured occurrence right now, and genuine statistical clustering
    (the literal, fabricated "cluster" framing) is meaningless at
    n=1 -- what's real and buildable instead is honest feature
    extraction that scales correctly as more real captures accumulate
    over time, not a fake clustering system pretending to have more
    statistical basis than one data point actually supports.

    Real, deliberate design: works on the AFFECTED turn specifically
    (bundle["affected_turn_index"]), using the real scenario_turn field
    (added the same day this function was built) when present for a
    reliable, real declared symbol; falls back to a best-effort regex
    extraction from the real tool_call_commands/prompt text for older
    real captures that predate that field, rather than fail outright.

    Real, added this deepening pass, directly motivated by re-examining
    the one real existing capture more closely: its real memories_used
    event didn't cleanly point only at the correct symbol -- one real,
    injected memory mentioned BOTH the stale and correct symbol in the
    same sentence ("User closely follows defense-related stocks like
    KTOS and RGTI"), a real, concrete, previously-unextracted signal
    worth tracking systematically rather than only noticed by manual
    re-reading. Also reuses reconstruct_rounds() (built for the replay
    engine, not previously wired into feature extraction at all) for a
    real round-count/round-structure signal, and promotes the final
    round's real emptiness to an explicit, top-level feature, since it
    was present in the one real occurrence and is easy to miss buried
    inside the raw classification dict.

    Returns a real, structured feature dict (original fields
    unchanged; new fields added, none renamed or removed, so existing
    callers/tests keep working):
      - "affected_turn_prompt", "has_custom_message",
        "stale_argument_symbol", "preceding_turn_prompt",
        "preceding_turn_symbol", "stale_argument_matches_preceding_turn",
        "turns_before_affected": unchanged from the original version.
      - "round_count": real number of real rounds the affected turn
        took, via reconstruct_rounds().
      - "final_round_empty": whether the affected turn's real, final
        round produced no visible content at all.
      - "memories_used_count": real count of real memories injected
        for this specific turn (0 if none).
      - "memories_mention_correct_symbol": whether ANY real injected
        memory text mentions the turn's own real, correct symbol.
      - "memories_mention_stale_symbol": whether ANY real injected
        memory text ALSO mentions the real, stale symbol actually
        used -- the specific, new signal this deepening pass exists
        to surface systematically.
      - "preceding_turn_content_preview": a short, real preview of
        what the preceding turn's own visible content actually said,
        for quick human scanning without re-opening the raw events.
    """
    affected_idx = bundle["affected_turn_index"]
    affected_turn = bundle["turns_captured"][affected_idx]

    stale_symbol = None
    for event in affected_turn["raw_events"]:
        if event.get("type") == "tool_start":
            try:
                stale_symbol = json.loads(event.get("command") or "{}").get("symbol")
            except json.JSONDecodeError:
                pass
            break

    if "scenario_turn" in affected_turn:
        has_custom_message = bool(affected_turn["scenario_turn"].get("message"))
        correct_symbol = affected_turn["scenario_turn"].get("symbol")
    else:
        # Real, honest fallback for captures predating scenario_turn:
        # the fixed template is always exactly "Whats {SYMBOL} trading
        # at right now?" -- anything else is, by definition, a real
        # custom message, confirmed directly against the one existing
        # real capture (whose prompt clearly isn't that template, and
        # was wrongly defaulting to False before this fix).
        has_custom_message = not affected_turn["prompt"].startswith("Whats ") or \
            not affected_turn["prompt"].endswith(" trading at right now?")
        # Real, honest best-effort fallback: no scenario_turn means no
        # real ground truth for what the correct symbol was supposed to
        # be -- scan the affected turn's own real prompt text for any
        # known, real, in-training ticker (confirmed directly against
        # the one existing real capture, whose prompt literally names
        # "RGTI"). Genuinely best-effort, not authoritative like
        # scenario_turn -- deliberately does not claim a symbol was
        # "correct" if none of the known, real tickers appear at all.
        correct_symbol = next(
            (t for t in IN_TRAINING_TICKERS if t in affected_turn["prompt"].upper()), None
        )

    preceding_turn_prompt = None
    preceding_turn_symbol = None
    preceding_turn_content_preview = None
    if affected_idx > 0:
        preceding = bundle["turns_captured"][affected_idx - 1]
        preceding_turn_prompt = preceding["prompt"]
        if "scenario_turn" in preceding:
            preceding_turn_symbol = preceding["scenario_turn"].get("symbol")
        else:
            # Real, best-effort fallback for older captures predating
            # scenario_turn: use whatever real symbol that turn's own
            # tool call actually used, if any.
            for event in preceding["raw_events"]:
                if event.get("type") == "tool_start":
                    try:
                        preceding_turn_symbol = json.loads(event.get("command") or "{}").get("symbol")
                    except json.JSONDecodeError:
                        pass
                    break
        preceding_rounds = reconstruct_rounds(preceding["raw_events"])
        preceding_content = "".join(r["content"] for r in preceding_rounds)
        preceding_turn_content_preview = preceding_content[:150]

    # Real round-structure signal, reusing the replay engine's own
    # already-proven reconstruction rather than a second implementation.
    affected_rounds = reconstruct_rounds(affected_turn["raw_events"])
    round_count = len(affected_rounds)
    final_round_empty = bool(affected_rounds) and not affected_rounds[-1]["content"].strip() \
        and not affected_rounds[-1]["tool_calls"]

    # Real memory-content signal.
    memories = []
    for event in affected_turn["raw_events"]:
        if event.get("type") == "memories_used":
            memories.extend(m.get("text", "") for m in event.get("data", []))
    memories_text = " ".join(memories).lower()
    memories_mention_correct_symbol = bool(correct_symbol) and correct_symbol.lower() in memories_text
    memories_mention_stale_symbol = bool(stale_symbol) and stale_symbol.lower() in memories_text

    # Real, added 2026-08-28 (Investigate_malformed_event_paths): checks
    # the affected turn's own real events for structural anomalies
    # among those that DID parse successfully -- see
    # check_for_malformed_event_patterns()'s own docstring for the
    # real, honest scope note on what this can and cannot detect
    # (truly unparseable raw SSE lines are invisible to any capture
    # made with this harness as it currently stands, since
    # send_message() silently discards them before this code ever
    # sees them).
    malformed_check = check_for_malformed_event_patterns(
        affected_turn["raw_events"], malformed_lines=affected_turn.get("malformed_lines")
    )

    return {
        "affected_turn_prompt": affected_turn["prompt"],
        "has_custom_message": has_custom_message,
        "stale_argument_symbol": stale_symbol,
        "preceding_turn_prompt": preceding_turn_prompt,
        "preceding_turn_symbol": preceding_turn_symbol,
        "stale_argument_matches_preceding_turn": (
            stale_symbol is not None and stale_symbol == preceding_turn_symbol
        ),
        "turns_before_affected": affected_idx,
        "round_count": round_count,
        "final_round_empty": final_round_empty,
        "memories_used_count": len(memories),
        "memories_mention_correct_symbol": memories_mention_correct_symbol,
        "memories_mention_stale_symbol": memories_mention_stale_symbol,
        "preceding_turn_content_preview": preceding_turn_content_preview,
        "has_malformed_event_pattern": malformed_check["has_any_anomaly"],
        "malformed_event_detail": malformed_check,
    }




def analyze_captured_echoes(captures_dir: str = None, target_check: str = "tool_argument_echo") -> dict:
    """Real, added 2026-08-28 (Design_cluster_root_cause_analysis):
    loads every real saved capture for target_check under captures_dir
    (default CAPTURES_DIR), extracts real features from each via
    extract_echo_features(), and reports them together -- real,
    honest pattern OBSERVATION across however many real data points
    actually exist, explicitly stating the real sample size rather
    than implying statistical confidence a handful of real captures
    can't actually support.
    """
    captures_dir = captures_dir or CAPTURES_DIR
    if not os.path.isdir(captures_dir):
        return {"sample_size": 0, "features": [], "note": f"No real captures directory found at {captures_dir}"}

    features = []
    for fname in sorted(os.listdir(captures_dir)):
        if not fname.endswith(".json") or not fname.startswith(f"{target_check}_"):
            continue
        with open(os.path.join(captures_dir, fname)) as f:
            bundle = json.load(f)
        feat = extract_echo_features(bundle)
        feat["_source_file"] = fname
        features.append(feat)

    n = len(features)
    if n == 0:
        note = f"No real captures found for '{target_check}' -- run --capture-check {target_check} first."
    elif n < 5:
        matching = sum(1 for f in features if f["stale_argument_matches_preceding_turn"])
        # Real, added in this deepening pass: a second, real, distinct
        # signal -- how often the real, injected memories mentioned
        # BOTH the correct and stale symbol together, a genuinely
        # different, additional real observation from the first
        # capture, not folded into the same count as it measures a
        # different real thing (context ambiguity vs. tool-argument
        # staleness).
        both_mentioned = sum(
            1 for f in features
            if f["memories_mention_correct_symbol"] and f["memories_mention_stale_symbol"]
        )
        # Real, added 2026-08-28 (Investigate_malformed_event_paths): a
        # third, real, distinct signal -- how many real occurrences
        # showed a structural event anomaly (see
        # check_for_malformed_event_patterns()'s own honest scope note
        # for exactly what this can and cannot detect).
        malformed_count = sum(1 for f in features if f["has_malformed_event_pattern"])
        note = (
            f"Real sample size is {n} -- too few for real statistical confidence. "
            f"Observed pattern so far: {matching}/{n} real occurrence(s) had a stale argument "
            f"matching the immediately preceding turn's own real symbol; {both_mentioned}/{n} "
            f"had real, injected memories mentioning BOTH the correct and stale symbol together; "
            f"{malformed_count}/{n} showed a structural event anomaly (of the kind detectable "
            f"from already-parsed events -- see check_for_malformed_event_patterns()'s own "
            f"scope note; truly unparseable raw SSE lines are invisible to this harness as it "
            f"currently stands). Worth watching as more real captures accumulate, not yet a "
            f"confirmed pattern."
        )
    else:
        matching = sum(1 for f in features if f["stale_argument_matches_preceding_turn"])
        note = f"{matching}/{n} real occurrence(s) had a stale argument matching the preceding turn."

    return {"sample_size": n, "features": features, "note": note}


_HOLDINGS_NOTE_NUMBERS_RE = re.compile(r"\d+")


def check_numeric_grounding(claim_numbers: list, known_facts_text: str) -> dict:
    """Real, added 2026-08-30 (Design_holdings_safety_checks): a
    general-purpose, hardened grounding check -- given a list of real
    numbers a piece of generated content claims, determines whether
    each one genuinely appears in a real, trusted source of ground
    truth (e.g. concatenated real tool outputs and real injected
    memories), rather than merely resembling a substring of something
    unrelated there.

    Real, important correctness fix made while building this,
    confirmed directly before trusting this function for anything: a
    naive `number in known_facts_text` substring check is genuinely
    unreliable for this purpose -- a claimed "15" would incorrectly
    register as grounded if known_facts_text contains "$115.00"
    anywhere, since "15" is a real substring of "115". Uses real,
    word-boundary-aware regex matching (\\b...\\b) instead, so a claimed
    number only counts as grounded when it appears as its own real,
    complete number in the known facts, not as a fragment of a larger
    one.

    Returns {"grounded": [...], "ungrounded": [...], "all_grounded":
    bool, "any_grounded": bool} -- designed to be callable directly on
    live, real-time content (a list of numbers plus a known-facts
    string), not requiring a full captured bundle, so this is ready
    for a future, real production integration if and when that's
    explicitly decided -- not wired into the live app tonight.
    """
    grounded = []
    ungrounded = []
    for number in claim_numbers:
        pattern = r"\b" + re.escape(number) + r"\b"
        if re.search(pattern, known_facts_text):
            grounded.append(number)
        else:
            ungrounded.append(number)
    return {
        "grounded": grounded,
        "ungrounded": ungrounded,
        "all_grounded": bool(claim_numbers) and not ungrounded,
        "any_grounded": bool(grounded),
    }


def extract_holdings_fabrication_features(bundle: dict) -> dict:
    """Real, added 2026-08-30 (fresh investigation thread, shifted to
    per DK's own explicit choice after the malformed-SSE-line work
    completed): a genuinely different real anomaly from
    tool_argument_echo, discovered by applying this harness's own
    already-general-purpose toolkit (capture_raw_events_for_check(),
    render_replay_transcript(), check_for_malformed_event_patterns() --
    all reused completely unchanged, zero modification needed) to a
    check that had never been deeply investigated:
    holdings_note_not_a_real_holding.

    The real, captured instance this was built against shows something
    more concerning than a stale argument: the model's holdings-
    correction note claims specific, concrete financial details --
    "the stored reference document lists 5 shares of RGTI, with a
    separate, unexecuted pending buy order for 3 more" -- that do not
    match the real, injected memory for that turn at all ("User has a
    pending buy order for 1 RGTI share."). This isn't misapplied real
    context (like tool_argument_echo's stale-but-real prior symbol);
    it's specific numbers with no real grounding in what was actually
    injected -- a genuine confabulation risk for a real financial
    assistant, worth investigating as its own real thread.

    Extracts, from a single real captured bundle's affected turn:
      - "note_text": the real, extracted holdings-note sentence
      - "note_ticker": the real ticker the note referred to
      - "note_numbers": real numbers mentioned in the note (e.g.
        share counts) as a list of strings
      - "memories_used_count"
      - "note_numbers_grounded_in_memory": real, honest check -- does
        EVERY number in the note appear somewhere in the real,
        injected memory text? False if even one doesn't (the specific,
        concrete signal this function exists to surface)
      - "any_note_number_grounded": real, weaker check -- does AT
        LEAST ONE number in the note appear in real memory text
        (distinguishes "totally fabricated" from "partially real,
        partially embellished")
    """
    affected_idx = bundle["affected_turn_index"]
    affected_turn = bundle["turns_captured"][affected_idx]
    rounds = reconstruct_rounds(affected_turn["raw_events"])
    full_content = "".join(r["content"] for r in rounds)

    match = _HOLDINGS_NOTE_TICKER_RE.search(full_content)
    note_ticker = match.group(1) if match else None

    # Real, honest extraction: the note is the parenthetical sentence
    # starting at "(Note:" if present, else the whole real matched
    # sentence's real surrounding context -- best-effort, not claiming
    # perfect sentence boundaries for free-form real model text.
    note_start = full_content.find("(Note:")
    note_text = full_content[note_start:].strip() if note_start != -1 else (
        full_content[max(0, match.start() - 40):match.end() + 80].strip() if match else None
    )
    note_numbers = _HOLDINGS_NOTE_NUMBERS_RE.findall(note_text) if note_text else []

    memories = []
    for event in affected_turn["raw_events"]:
        if event.get("type") == "memories_used":
            memories.extend(m.get("text", "") for m in event.get("data", []))
    memories_text = " ".join(memories)

    # Real, added 2026-08-30 (Design_holdings_safety_checks): reuses the
    # real, hardened, word-boundary-aware grounding check rather than
    # a second, less reliable inline implementation -- fixes a real,
    # confirmed correctness bug this earlier, naive version had (a
    # claimed "15" would incorrectly register as grounded if memory
    # text contained "$115.00" anywhere, since "15" is a real substring
    # of "115").
    grounding = check_numeric_grounding(note_numbers, memories_text)

    return {
        "note_text": note_text,
        "note_ticker": note_ticker,
        "note_numbers": note_numbers,
        "memories_used_count": len(memories),
        "note_numbers_grounded_in_memory": grounding["all_grounded"],
        "any_note_number_grounded": grounding["any_grounded"],
    }


def check_turn_holdings_integrity(turn: dict) -> dict:
    """Real, added 2026-08-30 (Per-turn holdings integrity check):
    the smallest real unit of value from the holdings-fabrication
    investigation -- checks a SINGLE turn (any turn, not just whatever
    turn happened to be bundle["affected_turn_index"] for the check a
    capture originally targeted) for a real holdings-correction note,
    and, if present, whether it's genuinely trustworthy: a real DK
    holding, and every claimed number genuinely grounded in that
    turn's own real, injected memories.

    Real, direct motivation: extract_holdings_fabrication_features()
    only ever examined the ONE affected turn a capture was made for --
    a real, current blind spot, confirmed directly by checking both
    existing real captures for a holdings note on any OTHER turn
    (found none this time, but the blind spot is real regardless: a
    capture made for a completely different check, e.g.
    tool_argument_echo, could easily contain a holdings-note issue on
    some other turn that nothing currently looks at). This function
    closes that blind spot at the smallest possible unit -- one turn --
    so it can be applied to every turn in a bundle, or reused directly
    by a future, real production integration on live content, without
    needing a full multi-turn bundle or a specific "affected" turn
    concept at all.

    Reuses every piece of already-proven infrastructure unchanged:
    reconstruct_rounds() for content, _HOLDINGS_NOTE_TICKER_RE and
    _HOLDINGS_NOTE_NUMBERS_RE for extraction, REAL_DK_STOCK_HOLDINGS
    for the real holding check, check_numeric_grounding() (the
    hardened, word-boundary-aware version) for number grounding.

    Returns a real, structured dict:
      - "has_holdings_note": bool -- whether this turn's real content
        contains a holdings-correction note at all
      - "note_ticker", "note_text", "note_numbers": None/empty if no
        note is present
      - "is_real_holding": None if no note; else whether note_ticker
        is a genuine, real DK holding
      - "numbers_grounded": the real check_numeric_grounding() result,
        None if no note or no numbers in it
      - "has_integrity_issue": real, convenience boolean -- True only
        when a note IS present and it's EITHER for a non-real holding
        OR has any ungrounded number. False (not None) when no note is
        present at all -- a turn with no note has nothing to flag.
    """
    rounds = reconstruct_rounds(turn["raw_events"])
    full_content = "".join(r["content"] for r in rounds)

    match = _HOLDINGS_NOTE_TICKER_RE.search(full_content)
    if not match:
        return {
            "has_holdings_note": False, "note_ticker": None, "note_text": None,
            "note_numbers": [], "is_real_holding": None, "numbers_grounded": None,
            "has_integrity_issue": False,
        }

    note_ticker = match.group(1)
    note_start = full_content.find("(Note:")
    note_text = full_content[note_start:].strip() if note_start != -1 else \
        full_content[max(0, match.start() - 40):match.end() + 80].strip()
    note_numbers = _HOLDINGS_NOTE_NUMBERS_RE.findall(note_text)

    memories = []
    for event in turn["raw_events"]:
        if event.get("type") == "memories_used":
            memories.extend(m.get("text", "") for m in event.get("data", []))
    memories_text = " ".join(memories)

    is_real_holding = note_ticker in REAL_DK_STOCK_HOLDINGS
    numbers_grounded = check_numeric_grounding(note_numbers, memories_text)

    return {
        "has_holdings_note": True,
        "note_ticker": note_ticker,
        "note_text": note_text,
        "note_numbers": note_numbers,
        "is_real_holding": is_real_holding,
        "numbers_grounded": numbers_grounded,
        "has_integrity_issue": (not is_real_holding) or (not numbers_grounded["all_grounded"]),
    }


def check_bundle_holdings_integrity(bundle: dict) -> list:
    """Real, added 2026-08-30 (Per-turn holdings integrity check):
    applies check_turn_holdings_integrity() to EVERY turn in a real
    captured bundle, not just bundle["affected_turn_index"] -- plugs
    directly into the existing capture pipeline (any bundle from
    capture_raw_events_for_check(), for ANY target_check) to give
    immediate, real visibility into holdings-note fabrication across
    every turn a capture happened to record, regardless of what check
    the capture was originally made for.

    Returns a real list of {"turn_index", **check_turn_holdings_
    integrity()'s own result}, one entry per real turn, in order --
    real, deliberate design: includes every turn, not just ones with
    an issue, so a caller can see the full real picture (or filter for
    has_integrity_issue themselves) rather than lose track of which
    turns were even checked.
    """
    results = []
    for turn in bundle["turns_captured"]:
        check = check_turn_holdings_integrity(turn)
        check["turn_index"] = turn["turn_index"]
        results.append(check)
    return results


def generate_holdings_integrity_report(bundle: dict, output_path: str = None) -> str:
    """Real, added 2026-08-30 (Per-Turn Integrity Dashboard): renders
    check_bundle_holdings_integrity()'s real, per-turn results as a
    self-contained HTML report -- genuinely new territory, not a
    duplicate of anything already built: the existing health/dashboard
    reports show real flag_counts (how many turns showed a given
    real flag) but never render the SPECIFIC, structured detail a
    holdings-integrity check produces per turn (which ticker, which
    numbers, grounded vs not, real holding vs not).

    Reuses the exact same dark-terminal CSS palette and card layout
    established across every other report in this harness, for
    visual consistency, and check_bundle_holdings_integrity()
    completely unchanged for the real data -- this function only
    renders it.

    Real, deliberate: a turn with no holdings note renders as a
    neutral, dim "no note" row, not hidden entirely -- a per-turn
    timeline should show what was actually checked, not just the
    turns with something to flag, so a reader can see the real, full
    picture (matching the same "include everything, not just
    issues" design already used in check_bundle_holdings_integrity()
    itself).
    """
    import html as _html

    def esc(s):
        return _html.escape(str(s)) if s is not None else ""

    results = check_bundle_holdings_integrity(bundle)

    rows = ""
    for r in results:
        if not r["has_holdings_note"]:
            rows += (
                f'<tr class="no-note"><td>{r["turn_index"]}</td>'
                f'<td class="dim">no holdings note</td><td class="dim">—</td>'
                f'<td class="dim">—</td><td class="dim">—</td></tr>'
            )
            continue
        real_holding_badge = (
            '<span class="badge badge-ok">real holding</span>' if r["is_real_holding"]
            else '<span class="badge badge-bad">NOT a real holding</span>'
        )
        grounded = r["numbers_grounded"]
        if grounded["all_grounded"]:
            numbers_badge = '<span class="badge badge-ok">all grounded</span>'
        elif grounded["any_grounded"]:
            numbers_badge = '<span class="badge badge-warn">partially grounded</span>'
        else:
            numbers_badge = '<span class="badge badge-bad">fabricated</span>'
        row_class = "issue-row" if r["has_integrity_issue"] else "clean-row"
        rows += (
            f'<tr class="{row_class}"><td>{r["turn_index"]}</td>'
            f'<td class="mono">{esc(r["note_ticker"])}</td>'
            f'<td>{real_holding_badge}</td>'
            f'<td>{numbers_badge}</td>'
            f'<td class="mono note-cell">{esc(r["note_text"])}</td></tr>'
        )

    issue_count = sum(1 for r in results if r["has_integrity_issue"])
    note_count = sum(1 for r in results if r["has_holdings_note"])

    style_block = """
  :root { --bg: #0D1117; --panel: #161B22; --border: #30363D; --text: #E6EDF3; --dim: #8B949E; --accent: #58A6FF; --ok: #3FB950; --bad: #F85149; --warn: #D29922; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; padding: 32px 24px 64px; }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  .dim { color: var(--dim); }
  header { max-width: 1000px; margin: 0 auto 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 0 0 12px; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .stats { display: flex; gap: 14px; max-width: 1000px; margin: 0 auto 20px; }
  .stat-card { flex: 1; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .stat-value { font-size: 24px; font-weight: 600; }
  .table-card { max-width: 1000px; margin: 0 auto; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--dim); text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
  .note-cell { max-width: 400px; white-space: normal; color: var(--dim); font-size: 11px; }
  .issue-row { background: rgba(248, 81, 73, 0.08); }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }
  .badge-ok { background: rgba(63, 185, 80, 0.15); color: var(--ok); }
  .badge-bad { background: rgba(248, 81, 73, 0.15); color: var(--bad); }
  .badge-warn { background: rgba(210, 153, 34, 0.15); color: var(--warn); }
"""

    html_doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Holdings Integrity Report</title><style>' + style_block + '</style></head><body>'
        '<header><h1>Per-Turn Holdings Integrity</h1>'
        f'<div class="subtitle">{esc(bundle.get("scenario_name", "?"))} / {esc(bundle.get("model", "?"))}'
        f' &middot; {len(results)} turn(s) checked</div></header>'
        '<div class="stats">'
        f'<div class="stat-card"><div class="stat-value">{len(results)}</div><div class="dim">turns checked</div></div>'
        f'<div class="stat-card"><div class="stat-value">{note_count}</div><div class="dim">holdings notes seen</div></div>'
        f'<div class="stat-card" style="border-color:{"var(--bad)" if issue_count else "var(--border)"}">'
        f'<div class="stat-value" style="color:{"var(--bad)" if issue_count else "var(--text)"}">{issue_count}</div>'
        f'<div class="dim">integrity issue(s)</div></div>'
        '</div>'
        '<div class="table-card"><h2>Per-turn detail</h2>'
        '<table><thead><tr><th>Turn</th><th>Ticker</th><th>Real Holding?</th>'
        '<th>Numbers</th><th>Note Text</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
        '</body></html>'
    )

    if output_path:
        with open(output_path, "w") as f:
            f.write(html_doc)
    return html_doc
















def run_multi_round_suite(endpoint_id: str, model: str, runs: int,
                           scenario: dict = None, verbose: bool = True) -> dict:
    """Real, dedicated multi-round runner: executes several independent
    sequence runs against the given scenario (or the real default
    scenario file if none given), aggregates results across ALL of them
    into the same quality of structured summary the single-question mode
    already has (not just a single pass/fail boolean per run), and
    returns the full, real, structured result so a caller (or
    --save-results below) can persist it for historical comparison
    across sessions.
    """
    run_records = []
    total_turns = 0
    clean_turns = 0
    # Real, updated 2026-08-28: a real dict (not a Counter) so the
    # printed summary always shows the same, familiar key order for the
    # original four flags, with any new flag (e.g. EXPECTATION_VIOLATED,
    # added the same day) appended via setdefault below rather than
    # requiring every possible flag name to be pre-listed here.
    flag_counts = {"LEAKED_TAG": 0, "REPEATED": 0, "EMPTY": 0, "NO_TOOL_CALL": 0}
    total_contamination = 0
    errors = 0

    for run_i in range(runs):
        try:
            result = run_sequence(endpoint_id, model, scenario)
        except Exception as e:
            errors += 1
            if verbose:
                print(f"--- Sequence run {run_i + 1}: ERROR: {e} ---")
            continue

        if verbose:
            print(f"--- Sequence run {run_i + 1} (session {result['session_id']}) ---")
        run_clean = True
        for i, r in enumerate(result["turn_results"]):
            total_turns += 1
            is_clean, flags = _classify_turn(r)
            if is_clean:
                clean_turns += 1
            else:
                run_clean = False
                for f in flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1
            if verbose:
                status = "CLEAN" if is_clean else " ".join(flags)
                print(f"  turn {i+1} [{r['prompt'][:50]}]: {status}")
        if result["cross_turn_contamination"]:
            total_contamination += len(result["cross_turn_contamination"])
            run_clean = False
            if verbose:
                print(f"  CROSS-TURN CONTAMINATION found: {result['cross_turn_contamination']}")
        elif verbose:
            print("  cross-turn contamination check: clean")
        if verbose:
            print()

        run_records.append({
            "session_id": result["session_id"],
            "clean": run_clean,
            "turn_results": result["turn_results"],
            "cross_turn_contamination": result["cross_turn_contamination"],
        })

    runs_clean = sum(1 for r in run_records if r["clean"])

    summary = {
        "timestamp": time.time(),
        "model": model,
        "scenario_name": scenario.get("name", "?") if scenario else load_scenario(DEFAULT_SCENARIO_FILE).get("name", "?"),
        "endpoint_id": endpoint_id,
        "runs_requested": runs,
        "runs_completed": len(run_records),
        "runs_errored": errors,
        "runs_completely_clean": runs_clean,
        "total_turns": total_turns,
        "clean_turns": clean_turns,
        "flag_counts": flag_counts,
        "total_cross_turn_contamination": total_contamination,
        "run_records": run_records,
    }

    if verbose:
        print("=== Multi-Round Suite Summary ===")
        print(f"Runs completed: {summary['runs_completed']}/{runs} ({errors} errors)")
        print(f"Runs completely clean: {runs_clean}/{summary['runs_completed']}")
        print(f"Turns completely clean: {clean_turns}/{total_turns}")
        print(f"Flag breakdown: {flag_counts}")
        print(f"Total cross-turn contamination findings: {total_contamination}")

    return summary


def generate_suite_from_tags(tags: list, match_mode: str = "any", name: str = None) -> dict:
    """Real, added 2026-08-28: builds a real, valid suite dict on the fly
    by scanning every real scenario file under scripts/scenarios/ and
    selecting those whose own real scenario_tags match -- no suite JSON
    file needs to exist for this to work, and the returned dict is
    already in exactly the shape load_suite() produces (including
    "_resolved_scenario_paths"), so it can be passed straight into
    run_suite()/run_multi_model_suite() without a round-trip through a
    file. Genuinely different from the hand-curated suite files under
    scripts/suites/ -- this is for ad-hoc, tag-driven grouping ("give me
    every scenario about holdings correction, right now"), not a
    themed, permanently-maintained collection.

    match_mode: "any" (default) includes a scenario if it has AT LEAST
    ONE of the given tags; "all" requires it to have EVERY given tag.
    Real, deliberate rejection of a fabricated empty suite: raises a
    real, clear error if no real scenario matches, rather than return
    an empty-but-technically-valid suite that would silently do nothing
    if handed to a runner.
    """
    if match_mode not in ("any", "all"):
        raise ValueError(f"match_mode must be 'any' or 'all', got {match_mode!r}")
    if not tags:
        raise ValueError("generate_suite_from_tags() needs at least one real tag")
    tag_set = set(tags)

    matched_files = []
    for fname in sorted(os.listdir(SCENARIOS_DIR)):
        if not fname.endswith(".json"):
            continue
        full_path = os.path.join(SCENARIOS_DIR, fname)
        scenario = load_scenario(full_path)
        scenario_tag_set = set(scenario.get("scenario_tags") or [])
        if match_mode == "any":
            matches = bool(tag_set & scenario_tag_set)
        else:
            matches = tag_set.issubset(scenario_tag_set)
        if matches:
            matched_files.append((fname, scenario["name"]))

    if not matched_files:
        raise ValueError(
            f"No real scenario matches tags {sorted(tag_set)} (match_mode={match_mode!r}) -- "
            f"a suite with zero scenarios would silently do nothing if run"
        )

    tag_desc = f" {match_mode} of " + ", ".join(sorted(tag_set))
    suite = {
        "name": name or f"auto_{match_mode}_" + "_".join(sorted(t.replace('-', '_') for t in tag_set)),
        "description": (
            f"Real, auto-generated suite: every real scenario matching{tag_desc} "
            f"-- {len(matched_files)} scenario(s): "
            + ", ".join(sname for _, sname in matched_files) + "."
        ),
        "suite_tags": ["auto-generated"] + sorted(tag_set),
        "scenarios": [f"scenarios/{fname}" for fname, _ in matched_files],
    }
    # Real validation via the exact same path a hand-written suite file
    # goes through -- resolves and re-validates each scenario, populates
    # "_resolved_scenario_paths" the same way load_suite() would.
    resolved = [os.path.join(SCENARIOS_DIR, fname) for fname, _ in matched_files]
    suite["_resolved_scenario_paths"] = resolved
    return suite


def load_suite(path: str) -> dict:
    """Real, added 2026-08-28: loads a suite file -- a named, themed
    grouping of scenario files (see scripts/suites/*.json for real
    examples). Real, minimal schema:
    {"name": ..., "description": ..., "suite_tags": [...], "scenarios": [
        "scenarios/mixed_holdings_default.json", ...
    ]}
    Scenario paths are resolved relative to scripts/ (the parent of both
    scenarios/ and suites/), matching how the real, shipped suite files
    are written. Each referenced scenario is validated by actually
    calling load_scenario() on it -- a suite referencing a missing or
    malformed scenario file fails loudly here, at suite-load time, not
    partway through a real run.

    Real, added 2026-08-28: an optional "models" field (a real, non-
    empty list of real model name strings) marks a suite as multi-model
    -- run_multi_model_suite() uses this; run_suite() (single-model)
    ignores it entirely, so the same suite file works with either
    runner. Deliberately NOT validated against a live model registry
    here (that would require a real, live API call just to load a
    file) -- an unknown model name fails naturally and loudly the first
    time run_multi_model_suite() actually tries to use it, same as an
    unknown --model already does for the rest of this harness.
    """
    with open(path) as f:
        suite = json.load(f)
    if "scenarios" not in suite or not isinstance(suite["scenarios"], list) or not suite["scenarios"]:
        raise ValueError(f"Suite file {path} is missing a real, non-empty 'scenarios' list")
    if "models" in suite and (
        not isinstance(suite["models"], list) or not suite["models"]
        or not all(isinstance(m, str) and m for m in suite["models"])
    ):
        raise ValueError(
            f"Suite file {path}: 'models' must be a real, non-empty list "
            f"of real model name strings, got {suite['models']!r}"
        )
    resolved = []
    for rel_path in suite["scenarios"]:
        full_path = os.path.join(SCRIPTS_DIR, rel_path)
        if not os.path.isfile(full_path):
            raise ValueError(f"Suite file {path} references a scenario that doesn't exist: {rel_path}")
        load_scenario(full_path)  # real validation, fails loudly here if malformed
        resolved.append(full_path)
    suite["_resolved_scenario_paths"] = resolved
    return suite


def run_suite(endpoint_id: str, model: str, suite: dict, runs_per_scenario: int = 1,
              verbose: bool = True) -> dict:
    """Real, added 2026-08-28: runs every real scenario in a suite,
    reusing the already-proven run_multi_round_suite() per scenario
    (not a separate, parallel aggregation implementation), then rolls
    everything up into real, suite-level totals -- combined clean-turn
    counts, combined flag counts across every scenario, combined
    contamination and error counts -- while keeping each scenario's own
    individual summary available too, since a suite-level average alone
    can hide which specific scenario is actually struggling.
    """
    scenario_results = []
    total_turns = 0
    clean_turns = 0
    flag_counts = {}
    total_contamination = 0
    total_runs_errored = 0

    for scenario_path in suite["_resolved_scenario_paths"]:
        scenario = load_scenario(scenario_path)
        if verbose:
            print(f"\n=== Scenario: {scenario['name']} ===")
        summary = run_multi_round_suite(endpoint_id, model, runs_per_scenario, scenario=scenario, verbose=verbose)
        scenario_results.append(summary)
        total_turns += summary["total_turns"]
        clean_turns += summary["clean_turns"]
        total_contamination += summary["total_cross_turn_contamination"]
        total_runs_errored += summary["runs_errored"]
        for flag, count in summary["flag_counts"].items():
            flag_counts[flag] = flag_counts.get(flag, 0) + count

    suite_summary = {
        "timestamp": time.time(),
        "model": model,
        "suite_name": suite.get("name", "?"),
        "endpoint_id": endpoint_id,
        "total_turns": total_turns,
        "clean_turns": clean_turns,
        "flag_counts": flag_counts,
        "total_cross_turn_contamination": total_contamination,
        "runs_errored": total_runs_errored,
        "scenario_results": scenario_results,
    }

    if verbose:
        clean_pct = round(100 * clean_turns / total_turns) if total_turns else 0
        print(f"\n=== Suite Summary: {suite.get('name', '?')} ===")
        print(f"Scenarios run: {len(scenario_results)}")
        print(f"Turns completely clean: {clean_turns}/{total_turns} ({clean_pct}%)")
        print(f"Flag breakdown: {flag_counts}")
        print(f"Total cross-turn contamination: {total_contamination}")

    return suite_summary


def run_suite_parallel(endpoint_id: str, model: str, suite: dict, runs_per_scenario: int = 1,
                        max_workers: int = None, verbose: bool = True) -> dict:
    """Real, added 2026-08-28 (Design_suite_sharding): the scenario-
    level counterpart to run_multi_model_suite_parallel() (added
    earlier the same night, which distributes work across MODELS,
    still sequential scenario-by-scenario within each model) --
    genuinely different real gap: for ONE real model, shards a large
    suite's real scenarios across a real ThreadPoolExecutor instead of
    running them one at a time, using the exact same real, proven
    concurrency approach (threading, correct for this harness's
    I/O-bound work) and the same real verbose-suppression design
    (concurrent scenarios printing simultaneously would interleave
    into unreadable output, same real reasoning as the model-parallel
    version).

    Reuses run_multi_round_suite() completely unchanged per scenario
    (not a separate, parallel aggregation implementation) -- only the
    orchestration differs from run_suite(). Real, honest note carried
    over from the earlier, measured model-parallel result: the real
    backend is a single-GPU Ollama instance: concurrent requests for
    DIFFERENT scenarios against the SAME model may see similar, or
    even more constrained, real contention than the model-parallel
    case did (85.72s -> 52.92s, a real ~38% reduction, not a full
    ~2x) -- this function measures and reports its own real
    wall_clock_seconds rather than assume the same or a better figure
    applies here too.

    Returns the same real shape run_suite() produces, plus real
    wall_clock_seconds and a sharded: True marker. Real, deliberate:
    scenario_results is reordered to match suite["_resolved_scenario_
    paths"]'s original order (not completion order, which
    as_completed() would otherwise return) -- a saved or displayed
    result should list scenarios in the same real, stable order the
    suite file itself declares them, regardless of which one happened
    to finish first.
    """
    scenario_paths = suite["_resolved_scenario_paths"]
    if not scenario_paths:
        raise ValueError(f"Suite {suite.get('name', '?')!r} has no real scenarios to shard")

    start = time.time()
    results_by_path = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers or len(scenario_paths)) as executor:
        futures = {}
        for scenario_path in scenario_paths:
            scenario = load_scenario(scenario_path)
            future = executor.submit(run_multi_round_suite, endpoint_id, model, runs_per_scenario,
                                      scenario=scenario, verbose=False)
            futures[future] = scenario_path
        for future in concurrent.futures.as_completed(futures):
            results_by_path[futures[future]] = future.result()
    elapsed = time.time() - start

    scenario_results = [results_by_path[p] for p in scenario_paths]
    total_turns = sum(s["total_turns"] for s in scenario_results)
    clean_turns = sum(s["clean_turns"] for s in scenario_results)
    total_contamination = sum(s["total_cross_turn_contamination"] for s in scenario_results)
    total_runs_errored = sum(s["runs_errored"] for s in scenario_results)
    flag_counts = {}
    for s in scenario_results:
        for flag, count in s["flag_counts"].items():
            flag_counts[flag] = flag_counts.get(flag, 0) + count

    suite_summary = {
        "timestamp": time.time(),
        "model": model,
        "suite_name": suite.get("name", "?"),
        "endpoint_id": endpoint_id,
        "total_turns": total_turns,
        "clean_turns": clean_turns,
        "flag_counts": flag_counts,
        "total_cross_turn_contamination": total_contamination,
        "runs_errored": total_runs_errored,
        "scenario_results": scenario_results,
        "wall_clock_seconds": round(elapsed, 2),
        "sharded": True,
    }

    if verbose:
        clean_pct = round(100 * clean_turns / total_turns) if total_turns else 0
        print(f"\n=== Suite Summary (sharded, {elapsed:.1f}s wall-clock): {suite.get('name', '?')} ===")
        print(f"Scenarios run: {len(scenario_results)}")
        print(f"Turns completely clean: {clean_turns}/{total_turns} ({clean_pct}%)")
        print(f"Flag breakdown: {flag_counts}")
        print(f"Total cross-turn contamination: {total_contamination}")

    return suite_summary




def run_multi_model_suite(endpoint_id: str, models: list, suite: dict,
                           runs_per_scenario: int = 1, verbose: bool = True) -> dict:
    """Real, added 2026-08-28: runs every real scenario in a suite
    against every real model in a list -- the genuine combination of
    run_suite() (many scenarios, one model) and run_cross_model() (one
    scenario, many models) that neither alone provides. Reuses
    run_suite() per model (not a separate, third aggregation
    implementation) -- each model's per-suite summary comes back in
    exactly the shape run_suite() already produces, plus a real,
    direct comparison table across models printed at the end, and
    detect_regressions()-compatible data for each model (each model's
    real summary can still be saved individually and fed into the
    existing --trends machinery unchanged).

    Real, deliberate: models can be passed explicitly (overriding
    anything in the suite file's own optional "models" field) or left
    None to use the suite file's own real "models" list -- a suite
    file with no "models" field at all requires an explicit models
    argument, since there's no real default to fall back to that
    wouldn't be an arbitrary guess.
    """
    if not models:
        raise ValueError(
            "run_multi_model_suite() needs a real, non-empty models list -- "
            "either pass one explicitly, or use a suite file with its own "
            "real 'models' field"
        )

    results = {}
    for model in models:
        if verbose:
            print(f"\n{'=' * 60}\nModel: {model}\n{'=' * 60}")
        results[model] = run_suite(endpoint_id, model, suite, runs_per_scenario, verbose=verbose)

    if verbose:
        print(f"\n=== Multi-Model Suite Comparison: {suite.get('name', '?')} ===")
        header = f"{'Model':<32} {'Clean %':>8} {'Turns':>6}"
        print(header)
        print("-" * len(header))
        for model, summary in results.items():
            total = summary["total_turns"] or 1
            clean_pct = round(100 * summary["clean_turns"] / total)
            print(f"{model:<32} {clean_pct:>7}% {summary['total_turns']:>6}")
        all_flags = sorted({f for s in results.values() for f in s["flag_counts"]})
        if all_flags:
            print()
            print(f"{'Flag':<28} " + " ".join(f"{m[:14]:>14}" for m in results))
            for flag in all_flags:
                row = f"{flag:<28} " + " ".join(f"{results[m]['flag_counts'].get(flag, 0):>14}" for m in results)
                print(row)

    return {"suite_name": suite.get("name", "?"), "results": results}


def run_multi_model_suite_parallel(endpoint_id: str, models: list, suite: dict,
                                    runs_per_scenario: int = 1, max_workers: int = None,
                                    verbose: bool = True) -> dict:
    """Real, added 2026-08-28 (Design_parallel_suite_runner): runs
    run_suite() for each real model CONCURRENTLY via a real
    ThreadPoolExecutor -- threading, not multiprocessing, since this
    harness's real work per model is I/O-bound (waiting on real HTTP
    requests to the model backend), not CPU-bound, the standard,
    correct real choice for this kind of work.

    Real, honest, important caveat, stated directly rather than
    implied: the real backend behind this harness is a single-GPU
    Ollama instance. If that backend serializes real GPU inference
    internally (likely, with one real GPU), concurrent client-side
    requests may queue there rather than genuinely run in parallel --
    client-side concurrency does not guarantee proportional real
    wall-clock speedup for the model-generation portion specifically,
    only for whatever real client-side/network overhead exists outside
    that. This function measures and reports real wall-clock duration
    specifically so a caller can see the REAL, empirical difference
    for their own real setup, rather than trust an assumed speedup.

    Real, deliberate design difference from run_multi_model_suite()
    (the sequential version): each model's real run_suite() call
    happens with verbose=False internally -- concurrent threads
    printing simultaneously would interleave into unreadable, garbled
    console output, so per-model streaming detail is suppressed during
    parallel execution; only the final combined comparison prints, once
    all threads complete. Reuses run_suite() unchanged per model (not
    a separate, parallel implementation of suite execution) -- only the
    orchestration around it is different.

    Returns the same real shape as run_multi_model_suite() ({"suite_name",
    "results"}), plus real "wall_clock_seconds" and "parallel": True, so
    a saved result or comparison can distinguish which orchestration
    produced it.
    """
    if not models:
        raise ValueError(
            "run_multi_model_suite_parallel() needs a real, non-empty models list -- "
            "either pass one explicitly, or use a suite file with its own real 'models' field"
        )

    start = time.time()
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers or len(models)) as executor:
        futures = {
            executor.submit(run_suite, endpoint_id, model, suite, runs_per_scenario, verbose=False): model
            for model in models
        }
        for future in concurrent.futures.as_completed(futures):
            model = futures[future]
            results[model] = future.result()
    elapsed = time.time() - start

    if verbose:
        print(f"\n=== Multi-Model Suite Comparison (parallel, {elapsed:.1f}s wall-clock): {suite.get('name', '?')} ===")
        header = f"{'Model':<32} {'Clean %':>8} {'Turns':>6}"
        print(header)
        print("-" * len(header))
        for model in models:
            summary = results[model]
            total = summary["total_turns"] or 1
            clean_pct = round(100 * summary["clean_turns"] / total)
            print(f"{model:<32} {clean_pct:>7}% {summary['total_turns']:>6}")
        all_flags = sorted({f for s in results.values() for f in s["flag_counts"]})
        if all_flags:
            print()
            print(f"{'Flag':<28} " + " ".join(f"{m[:14]:>14}" for m in results))
            for flag in all_flags:
                row = f"{flag:<28} " + " ".join(f"{results[m]['flag_counts'].get(flag, 0):>14}" for m in results)
                print(row)

    return {"suite_name": suite.get("name", "?"), "results": results,
            "wall_clock_seconds": round(elapsed, 2), "parallel": True}




def generate_suite_health_summary(suite_result: dict, historical_summaries: list = None) -> dict:
    """Real, added 2026-08-28: synthesizes a run_suite() or
    run_multi_model_suite() result -- both real, already-shipped
    functions -- into one structured "single pane of glass" report,
    rather than requiring separate --suite/--trends/--rank-scenarios
    runs to be manually cross-referenced by a person.

    Real, deliberate scope decision made after checking an external
    proposal for this feature against the real system: that proposal
    assumed an entire, elaborate multi-file architecture (scenario_
    history_store.py, cluster_trends.py, regression_detector.py,
    dashboard_alerts.py, multi_model_drift_detector.py, live_suite_
    runner.py, and more) and several concepts with NO basis anywhere
    in this real, single-file harness -- "clusters"/cluster-level
    regressions (no clustering concept exists anywhere in this system),
    a "dashboard alerts" taxonomy of termination signals, density
    spikes, long rounds, budget exhaustion, and loop-breaker triggers
    (none of these are real, tracked concepts here), and downstream
    consumers -- gatekeeping, autopruning, autopromotion -- that don't
    exist. None of that was built. What follows is a summary built
    ONLY from real, already-shipped, already-tested capability:

      - "overview": suite name, scenario count, model count, total
        turns -- read directly from the real suite_result.
      - "outcomes": real clean/total turns, real failure rate, and the
        real flag_counts breakdown (the honest, real equivalent of the
        proposal's fabricated "alert summary" -- this harness's real
        alert-like signals ARE its flags: TOOL_ERROR, REPEATED,
        HOLDINGS_NOTE_WRONG_TICKER, etc., not a separate system).
      - "regressions": real output of detect_regressions() (built
        earlier the same night) -- included only if historical_summaries
        is given, since regression detection genuinely needs real prior
        history to compare against, not fabricated as a required
        section.
      - "model_comparison": included only if suite_result is actually
        multi-model-shaped (has a real "results" dict keyed by model,
        matching run_multi_model_suite()'s own real output) -- per-model
        clean rate, failure rate, and flag_counts, read directly from
        each model's own real, nested run_suite()-shaped summary.

    Real, deliberate design: works directly on run_suite()/run_multi_
    model_suite()'s own real output shapes, no new persistence format,
    no new runner -- a caller already has a suite_result from calling
    either function; this just synthesizes it, optionally combined
    with real prior history for regression context.
    """
    # Real, distinguishing check: only run_multi_model_suite()'s real
    # output has a "results" key at all -- run_suite()'s own output has
    # "suite_name" too (so checking for that alone would misclassify a
    # real single-model suite result as multi-model).
    is_multi_model = "results" in suite_result

    if is_multi_model:
        model_summaries = suite_result["results"]
        suite_name = suite_result.get("suite_name", "?")
        model_count = len(model_summaries)
        combined_total_turns = sum(s["total_turns"] for s in model_summaries.values())
        combined_clean_turns = sum(s["clean_turns"] for s in model_summaries.values())
        combined_flags = {}
        for s in model_summaries.values():
            for flag, count in s.get("flag_counts", {}).items():
                combined_flags[flag] = combined_flags.get(flag, 0) + count
        scenario_count = len(next(iter(model_summaries.values()))["scenario_results"]) if model_summaries else 0
    else:
        suite_name = suite_result.get("suite_name", "?")
        model_count = 1
        combined_total_turns = suite_result["total_turns"]
        combined_clean_turns = suite_result["clean_turns"]
        combined_flags = suite_result.get("flag_counts", {})
        scenario_count = len(suite_result.get("scenario_results", []))

    failure_rate = round(100 * (1 - combined_clean_turns / combined_total_turns)) if combined_total_turns else 0

    summary = {
        # Real, added 2026-08-28 (Persist_summary_in_history): a health
        # summary is a real, meaningful snapshot in time -- its own real
        # regressions section (below) reflects whatever real historical
        # data existed AT THE MOMENT it was generated, which is
        # genuinely different from regenerating it later against a
        # since-grown history. A real timestamp makes the summary a
        # self-contained, persistable artifact rather than something
        # only meaningful if immediately printed and discarded.
        "timestamp": time.time(),
        "overview": {
            "suite_name": suite_name,
            "scenario_count": scenario_count,
            "model_count": model_count,
            "total_turns": combined_total_turns,
        },
        "outcomes": {
            "clean_turns": combined_clean_turns,
            "total_turns": combined_total_turns,
            "failure_rate": failure_rate,
            "flag_counts": combined_flags,
        },
    }

    if historical_summaries is not None:
        regression_model = None if is_multi_model else suite_result.get("model")
        summary["regressions"] = detect_regressions(historical_summaries, min_drop_pct=10) \
            if regression_model is None else \
            [a for a in detect_regressions(historical_summaries, min_drop_pct=10) if a["model"] == regression_model]

    if is_multi_model:
        summary["model_comparison"] = {}
        for model, s in model_summaries.items():
            total = s["total_turns"] or 1
            summary["model_comparison"][model] = {
                "clean_turns": s["clean_turns"],
                "total_turns": s["total_turns"],
                "clean_rate": round(100 * s["clean_turns"] / total),
                "failure_rate": round(100 * (1 - s["clean_turns"] / total)),
                "flag_counts": s.get("flag_counts", {}),
            }

    # Real, added 2026-08-28 (Design_suite_health_dashboard): a real,
    # lightweight per-scenario breakdown, using ONLY the real
    # scenario_results data run_suite() already produces -- enables a
    # genuine "scenario grid" view (which real, actual scenarios are
    # struggling) that the original overview/outcomes sections alone
    # can't show, since those are suite-wide aggregates that can hide
    # exactly which specific scenario is driving a bad number. For a
    # multi-model result, per-scenario data is combined across all
    # models (matching the same combined-aggregation approach already
    # used for "outcomes" above) -- a real, deliberate simplification;
    # a genuinely separate per-model-per-scenario breakdown would add
    # real complexity for a dashboard's overview grid, where the
    # existing "model_comparison" section already covers the per-model
    # angle separately.
    scenario_lists = (
        [s["scenario_results"] for s in model_summaries.values()]
        if is_multi_model else [suite_result.get("scenario_results", [])]
    )
    per_scenario = {}
    for scenario_list in scenario_lists:
        for entry in scenario_list:
            name = entry.get("scenario_name", "?")
            bucket = per_scenario.setdefault(name, {"clean_turns": 0, "total_turns": 0})
            bucket["clean_turns"] += entry.get("clean_turns", 0)
            bucket["total_turns"] += entry.get("total_turns", 0)
    summary["per_scenario"] = []
    for name, bucket in per_scenario.items():
        total = bucket["total_turns"] or 1
        summary["per_scenario"].append({
            "scenario_name": name,
            "clean_turns": bucket["clean_turns"],
            "total_turns": bucket["total_turns"],
            "failure_rate": round(100 * (1 - bucket["clean_turns"] / total)),
        })

    return summary


def _print_health_summary(summary: dict) -> None:
    """Real, added 2026-08-28: real, terminal-friendly rendering of
    generate_suite_health_summary()'s real output, matching the plain,
    aligned-table style already used throughout this harness's other
    console output."""
    ov = summary["overview"]
    oc = summary["outcomes"]
    print(f"\n{'=' * 60}\nSuite Health Summary: {ov['suite_name']}\n{'=' * 60}")
    print(f"Scenarios: {ov['scenario_count']}  |  Models: {ov['model_count']}  |  "
          f"Total turns: {ov['total_turns']}")
    print(f"\nOutcomes: {oc['clean_turns']}/{oc['total_turns']} clean "
          f"({100 - oc['failure_rate']}% clean, {oc['failure_rate']}% failure rate)")
    if oc["flag_counts"]:
        print(f"Flag breakdown: {oc['flag_counts']}")

    if "regressions" in summary:
        print(f"\nRegressions ({len(summary['regressions'])}):")
        if summary["regressions"]:
            for a in summary["regressions"]:
                print(f"  - [{a['severity']}] {a['message']}")
        else:
            print("  none detected")

    if "model_comparison" in summary:
        print(f"\nPer-model comparison:")
        header = f"  {'Model':<32} {'Clean %':>8} {'Turns':>6}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for model, mc in summary["model_comparison"].items():
            print(f"  {model:<32} {mc['clean_rate']:>7}% {mc['total_turns']:>6}")


def load_historical_summaries(summaries_dir: str = None) -> list:
    """Real, added 2026-08-28: loads every saved health-summary JSON
    file from a directory (default SUMMARIES_DIR), the summary-shaped
    equivalent of load_historical_results() for raw results -- skips
    any file that fails to parse or lacks a real "overview"/"timestamp"
    (the two fields every real generate_suite_health_summary() output
    has) rather than aborting the whole load, sorted oldest to newest.
    Deliberately a separate loader from load_historical_results(), not
    a shared one with a type flag -- the two real shapes (raw suite
    result vs. derived health summary) are different enough that
    conflating their loading logic would need a maze of shape-specific
    branches inside one function, for zero real benefit over two
    simple, separate ones.
    """
    summaries_dir = summaries_dir or SUMMARIES_DIR
    if not os.path.isdir(summaries_dir):
        return []
    summaries = []
    for fname in sorted(os.listdir(summaries_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(summaries_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if "overview" not in data or "timestamp" not in data:
            continue
        data["_source_file"] = fname
        summaries.append(data)
    summaries.sort(key=lambda s: s["timestamp"])
    return summaries


def summarize_suite_trend(summaries: list, suite_name: str = None) -> list:
    """Real, added 2026-08-28: shows how a suite's REAL, SUITE-LEVEL
    outcome (not per-scenario, which --trends already covers) has
    changed across its own real, saved health-summary snapshots over
    time. Genuinely different real view from --trends -- that operates
    on raw per-scenario/per-run data; this operates on the suite-level
    aggregate a health summary itself already computed and persisted,
    including whatever real regression context existed at generation
    time (which --trends regenerating fresh against current history
    would NOT reproduce, since more history may have accumulated
    since).

    Real, deliberate: no model-scoping parameter here, unlike
    detect_regressions()/rank_scenarios_by_failure_rate() -- a health
    summary is already suite+model-scoped (or suite+multi-model-scoped)
    at the point it was generated and saved; this function only ever
    filters by suite_name, real evidence of which suite a given
    snapshot belongs to, not re-derives model scoping after the fact.

    Returns a list of {"timestamp", "label", "suite_name",
    "failure_rate", "clean_turns", "total_turns",
    "regression_count", "source_file"}, oldest first, optionally
    filtered to one real suite_name.
    """
    trend = []
    for s in summaries:
        if suite_name is not None and s["overview"].get("suite_name") != suite_name:
            continue
        trend.append({
            "timestamp": s["timestamp"],
            "label": time.strftime("%m/%d %H:%M", time.localtime(s["timestamp"])),
            "suite_name": s["overview"].get("suite_name", "?"),
            "failure_rate": s["outcomes"].get("failure_rate", 0),
            "clean_turns": s["outcomes"].get("clean_turns", 0),
            "total_turns": s["outcomes"].get("total_turns", 0),
            "regression_count": len(s.get("regressions", [])),
            "source_file": s.get("_source_file", "?"),
        })
    return trend


def compare_suite_trends(summaries: list) -> dict:
    """Real, added 2026-08-28 (Design_suite_health_trend_report): the
    genuine gap left after summarize_suite_trend() -- that function
    shows ONE suite's own trend over time (or, with no suite_name
    filter, every saved summary mixed into one flat, chronological
    list regardless of which suite produced it, which is genuinely
    confusing to read once more than one real suite has saved
    history). This groups by real suite_name and returns
    {suite_name: [trend_entries...]}, one real, separate trend list
    per real suite -- reuses summarize_suite_trend() once per distinct
    suite_name found in the data (not a separate, parallel
    implementation of the same per-entry extraction logic), so a
    multi-suite comparison chart can plot each suite as its own real,
    distinct line rather than blend them.
    """
    suite_names = sorted({s["overview"].get("suite_name", "?") for s in summaries})
    return {name: summarize_suite_trend(summaries, suite_name=name) for name in suite_names}


def generate_dashboard_overview_html(summaries: list) -> str:
    """Real, added 2026-08-28 (Suite_health_dashboard: full visual
    overview): closes the real gap left by serve_health_dashboard()'s
    original design -- that showed only the single most recently
    saved summary, regardless of which real suite it belonged to, so
    a person with saved history for several real suites would only
    ever see whichever one happened to be touched last, not a genuine
    overview of every real suite's current health at once.

    Groups every real saved summary by suite_name (same real logic
    compare_suite_trends() already uses, called directly rather than
    duplicated), takes each real suite's own LATEST entry, and renders
    one real, color-coded card per suite -- clean %, latest regression
    count, last-updated time -- reusing the exact same dark-terminal
    CSS palette and scenario-tile-style card layout already
    established across every report in this harness. Each card links
    to ?suite=NAME for a real drill-down into that suite's full,
    existing detailed report (overview/outcomes/regressions/model
    comparison/trend chart) -- reuses generate_health_summary_html_
    report() unchanged for that, not a second rendering path.
    """
    import html as _html

    def esc(s):
        return _html.escape(str(s))

    if not summaries:
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>Suite Health Dashboard</title></head>'
            '<body style="background:#0D1117;color:#E6EDF3;font-family:sans-serif;padding:32px;">'
            '<h1>Suite Health Dashboard</h1>'
            '<p style="color:#8B949E;">No saved summaries found yet. Run a suite with '
            '--health-summary --save-summary to populate this.</p>'
            '</body></html>'
        )

    grouped = compare_suite_trends(summaries)
    latest_by_suite = {}
    for s in summaries:
        name = s["overview"].get("suite_name", "?")
        if name not in latest_by_suite or s["timestamp"] > latest_by_suite[name]["timestamp"]:
            latest_by_suite[name] = s

    def _card_color(rate):
        if rate == 0:
            return "#3FB950"
        if rate < 50:
            return "#D29922"
        return "#F85149"

    cards = ""
    for name in sorted(latest_by_suite.keys()):
        latest = latest_by_suite[name]
        rate = latest["outcomes"]["failure_rate"]
        regression_count = len(latest.get("regressions", []))
        when = time.strftime("%m/%d %H:%M", time.localtime(latest["timestamp"]))
        snapshot_count = len(grouped.get(name, []))
        color = _card_color(rate)
        cards += (
            f'<a href="/?suite={urllib.parse.quote(name)}" class="suite-card" style="border-color:{color}">'
            f'<div class="mono" style="font-size:14px;color:var(--text);">{esc(name)}</div>'
            f'<div class="stat-value" style="font-size:26px;color:{color}">{rate}%</div>'
            f'<div class="dim" style="font-size:11px;">failure rate</div>'
            f'<div class="dim" style="font-size:11px;margin-top:8px;">{regression_count} regression(s) '
            f'&middot; {snapshot_count} snapshot(s)</div>'
            f'<div class="dim" style="font-size:10px;margin-top:4px;">Updated {esc(when)}</div>'
            f'</a>'
        )

    style_block = """
  :root { --bg: #0D1117; --panel: #161B22; --border: #30363D; --text: #E6EDF3; --dim: #8B949E; --accent: #58A6FF; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; padding: 32px 24px 64px; }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  .dim { color: var(--dim); }
  header { max-width: 900px; margin: 0 auto 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .suite-grid { max-width: 900px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }
  .suite-card { display: block; border: 1px solid; border-radius: 8px; padding: 16px; background: var(--panel); text-decoration: none; transition: transform 0.1s; }
  .suite-card:hover { transform: translateY(-2px); }
"""

    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Suite Health Dashboard</title><style>' + style_block + '</style></head><body>'
        '<header><h1>Suite Health Dashboard</h1>'
        f'<div class="subtitle">{len(latest_by_suite)} real suite(s) tracked &middot; click a card for full detail</div></header>'
        f'<div class="suite-grid">{cards}</div>'
        '</body></html>'
    )




def generate_multi_suite_trend_html_report(grouped_trends: dict, output_path: str = None) -> str:
    """Real, added 2026-08-28 (Design_suite_health_trend_report):
    renders a real compare_suite_trends() result as a self-contained
    HTML report -- one colored line per real suite on a single chart,
    the suite-level equivalent of the real multi-model comparison
    chart already proven in generate_trend_report() (same technique:
    a fixed palette cycled per real series, a real legend, real
    gridlines). Reuses that exact proven approach rather than invent a
    new charting technique, and the same dark-terminal CSS palette
    used across every other report in this harness for real visual
    consistency.

    Real, deliberate: output_path is optional, matching generate_
    health_summary_html_report()'s own real pattern -- returns the
    HTML string when omitted, in case a future caller wants to embed
    or serve this without a real file round trip, same real reasoning
    as before.
    """
    import html as _html

    def esc(s):
        return _html.escape(str(s))

    palette = ["#58A6FF", "#D2A8FF", "#F0883E", "#3FB950", "#FF7B72", "#79C0FF", "#F778BA"]
    suite_names = sorted(grouped_trends.keys())

    if not suite_names or not any(grouped_trends.values()):
        html_doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<title>Multi-Suite Trend Comparison</title></head>'
            '<body style="background:#0D1117;color:#E6EDF3;font-family:sans-serif;padding:32px;">'
            '<h1>Multi-Suite Trend Comparison</h1>'
            '<p style="color:#8B949E;">No saved health summaries found -- run suites with '
            '--health-summary --save-summary a few times to build up real trend data.</p>'
            '</body></html>'
        )
        if output_path:
            with open(output_path, "w") as f:
                f.write(html_doc)
        return html_doc

    suite_colors = {name: palette[i % len(palette)] for i, name in enumerate(suite_names)}
    all_points = sorted({e["timestamp"] for trend in grouped_trends.values() for e in trend})
    n = len(all_points)

    chart_w, chart_h = 800, 240
    pad_l, pad_r, pad_t, pad_b = 40, 20, 20, 30
    plot_w = chart_w - pad_l - pad_r
    plot_h = chart_h - pad_t - pad_b

    def x_for_ts(ts):
        if n == 1:
            return pad_l + plot_w / 2
        idx = all_points.index(ts)
        return pad_l + (plot_w * idx / (n - 1))

    def y_for(pct):
        return pad_t + plot_h * (1 - pct / 100)

    lines_svg = ""
    legend_html = ""
    for name in suite_names:
        trend = grouped_trends[name]
        if not trend:
            continue
        color = suite_colors[name]
        poly = " ".join(f"{x_for_ts(e['timestamp']):.1f},{y_for(e['failure_rate']):.1f}" for e in trend)
        dots = "".join(
            f'<circle cx="{x_for_ts(e["timestamp"]):.1f}" cy="{y_for(e["failure_rate"]):.1f}" r="4" fill="{color}">'
            f'<title>{esc(name)} @ {esc(e["label"])}: {e["failure_rate"]}% failure</title></circle>'
            for e in trend
        )
        lines_svg += f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>{dots}'
        legend_html += (
            f'<span class="legend-item"><span class="legend-dot" style="background:{color}"></span>'
            f'<span class="mono">{esc(name)}</span></span>'
        )

    gridlines = "".join(
        f'<line x1="{pad_l}" y1="{y_for(g):.1f}" x2="{chart_w - pad_r}" y2="{y_for(g):.1f}" stroke="#30363D"/>'
        f'<text x="{pad_l - 6}" y="{y_for(g) + 3:.1f}" font-size="10" fill="#8B949E" text-anchor="end" font-family="monospace">{g}%</text>'
        for g in (0, 25, 50, 75, 100)
    )
    x_labels = "".join(
        f'<text x="{x_for_ts(ts):.1f}" y="{chart_h - 8}" font-size="10" fill="#8B949E" text-anchor="middle" font-family="monospace">'
        f'{esc(time.strftime("%m/%d %H:%M", time.localtime(ts)))}</text>'
        for i, ts in enumerate(all_points) if n <= 12 or i % max(1, n // 12) == 0
    )
    svg = (
        f'<svg viewBox="0 0 {chart_w} {chart_h}" width="100%" style="max-width:800px">'
        f'{gridlines}{lines_svg}{x_labels}</svg>'
    )

    rows = ""
    for name in suite_names:
        trend = grouped_trends[name]
        if not trend:
            continue
        latest = trend[-1]
        rows += (
            f'<tr><td class="mono" style="color:{suite_colors[name]}">{esc(name)}</td>'
            f'<td>{len(trend)}</td><td>{latest["failure_rate"]}%</td>'
            f'<td>{latest["regression_count"]}</td></tr>'
        )

    style_block = """
  :root { --bg: #0D1117; --panel: #161B22; --border: #30363D; --text: #E6EDF3; --dim: #8B949E; --accent: #58A6FF; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; padding: 32px 24px 64px; }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  .dim { color: var(--dim); }
  header { max-width: 900px; margin: 0 auto 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 0 0 12px; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .chart-card, .table-card { max-width: 900px; margin: 0 auto 24px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 10px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--dim); }
  .legend-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--dim); text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
"""

    html_doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Multi-Suite Trend Comparison</title><style>' + style_block + '</style></head><body>'
        '<header><h1>Multi-Suite Trend Comparison</h1>'
        f'<div class="subtitle">{len(suite_names)} suite(s), {n} distinct saved snapshot(s)</div></header>'
        '<div class="chart-card"><h2>Failure rate over time, by suite</h2>' + svg
        + '<div class="legend">' + legend_html + '</div></div>'
        '<div class="table-card"><h2>Latest status per suite</h2>'
        '<table><thead><tr><th>Suite</th><th>Snapshots</th><th>Latest failure %</th><th>Latest regressions</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
        '</body></html>'
    )

    if output_path:
        with open(output_path, "w") as f:
            f.write(html_doc)
    return html_doc






def generate_health_summary_html_report(summary: dict, trend: list = None, output_path: str = None) -> str:
    """Real, added 2026-08-28 (Add_summary_to_dashboard): renders a real
    generate_suite_health_summary() result as a self-contained HTML
    report -- the visual counterpart to _print_health_summary()'s
    console output, reusing the exact same dark-terminal CSS palette
    and card layout already established across generate_html_report()
    and generate_trend_report(), for real visual consistency across
    this harness's real reports rather than inventing a new style.

    Real, deliberate scope note: the original external proposal that
    motivated this whole feature arc (Implement/Integrate/Persist/
    Add_to_dashboard) assumed a real "dashboard" system (dashboard_
    alerts.py, dashboard_model_comparison.py) that doesn't exist and
    was already rejected when the underlying summary feature was
    built. This is not that -- it's a real, standalone HTML report,
    the same real pattern every other report in this harness already
    uses (no server, no persistent UI, just a real, self-contained
    file opened in a browser).

    trend (optional): real output of summarize_suite_trend() -- when
    given, adds a real SVG line chart of failure rate over the
    suite's own saved history, the same real SVG-chart technique
    already proven in generate_trend_report().

    Real, added 2026-08-28 (Design_suite_health_dashboard): output_path
    is now optional -- when omitted, the real HTML string is returned
    instead of written to disk, so a live server (see
    serve_health_dashboard()) can regenerate and serve it fresh on
    each request without a real temp-file round trip. When given, the
    file is written as before and the same string is also returned.
    """
    import html as _html
    import time as _time

    def esc(s):
        return _html.escape(str(s))

    ov = summary["overview"]
    oc = summary["outcomes"]
    ts_label = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(summary.get("timestamp", _time.time())))

    overview_html = (
        '<div class="chart-card"><h2>Overview</h2>'
        f'<div class="stat-row">'
        f'<div class="stat"><div class="stat-value">{ov["scenario_count"]}</div><div class="stat-label">Scenarios</div></div>'
        f'<div class="stat"><div class="stat-value">{ov["model_count"]}</div><div class="stat-label">Models</div></div>'
        f'<div class="stat"><div class="stat-value">{ov["total_turns"]}</div><div class="stat-label">Total turns</div></div>'
        f'</div><div class="dim" style="margin-top:10px;font-size:12px;">Generated {esc(ts_label)}</div></div>'
    )

    clean_pct = 100 - oc["failure_rate"]
    fail_color = "#3FB950" if oc["failure_rate"] < 20 else ("#D29922" if oc["failure_rate"] < 50 else "#F85149")
    outcomes_html = (
        '<div class="chart-card"><h2>Outcomes</h2>'
        f'<div class="stat-row">'
        f'<div class="stat"><div class="stat-value" style="color:{fail_color}">{clean_pct}%</div><div class="stat-label">Clean rate</div></div>'
        f'<div class="stat"><div class="stat-value">{oc["clean_turns"]}/{oc["total_turns"]}</div><div class="stat-label">Turns clean</div></div>'
        f'</div>'
    )
    if oc.get("flag_counts"):
        flag_items = "".join(f"<li>{esc(f)}: {c}</li>" for f, c in oc["flag_counts"].items() if c > 0)
        outcomes_html += f'<ul class="regression-list" style="color:var(--text);margin-top:12px;">{flag_items or "<li>No flags raised</li>"}</ul>'
    outcomes_html += '</div>'

    if "regressions" in summary:
        regressions = summary["regressions"]
        if regressions:
            items = "".join(f'<li class="alert-{esc(a["severity"])}">{esc(a["message"])}</li>' for a in regressions)
            regression_html = (
                '<div class="regression-card"><h2>Regressions</h2>'
                f'<ul class="regression-list">{items}</ul></div>'
            )
        else:
            regression_html = (
                '<div class="regression-card clean"><h2>Regressions</h2>'
                '<p class="dim">None detected.</p></div>'
            )
    else:
        regression_html = ""

    model_comparison_html = ""
    if "model_comparison" in summary:
        rows = "".join(
            f'<tr><td class="mono">{esc(model)}</td><td>{mc["clean_rate"]}%</td>'
            f'<td>{mc["total_turns"]}</td></tr>'
            for model, mc in summary["model_comparison"].items()
        )
        model_comparison_html = (
            '<div class="table-card"><h2>Per-model comparison</h2>'
            '<table><thead><tr><th>Model</th><th>Clean %</th><th>Turns</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
        )

    # Real, added 2026-08-28 (Design_suite_health_dashboard): a real,
    # color-coded scenario grid -- one tile per real scenario, using
    # the real per_scenario breakdown added to generate_suite_health_
    # summary() for this. Real thresholds match the same real severity
    # language already used for regressions elsewhere in this harness
    # (a moderate/high split), not arbitrary new bands.
    scenario_grid_html = ""
    if summary.get("per_scenario"):
        def _tile_color(rate):
            if rate == 0:
                return "#3FB950"
            if rate < 50:
                return "#D29922"
            return "#F85149"
        tiles = "".join(
            f'<div class="scenario-tile" style="border-color:{_tile_color(sc["failure_rate"])}">'
            f'<div class="mono" style="font-size:12px;">{esc(sc["scenario_name"])}</div>'
            f'<div class="stat-value" style="font-size:20px;color:{_tile_color(sc["failure_rate"])}">{sc["failure_rate"]}%</div>'
            f'<div class="dim" style="font-size:11px;">{sc["clean_turns"]}/{sc["total_turns"]} clean</div>'
            f'</div>'
            for sc in sorted(summary["per_scenario"], key=lambda s: s["failure_rate"], reverse=True)
        )
        scenario_grid_html = (
            '<div class="chart-card"><h2>Scenario grid</h2>'
            f'<div class="scenario-grid">{tiles}</div></div>'
        )

    trend_html = ""
    if trend:
        chart_w, chart_h = 800, 200
        pad_l, pad_r, pad_t, pad_b = 40, 20, 20, 30
        plot_w = chart_w - pad_l - pad_r
        plot_h = chart_h - pad_t - pad_b
        n = len(trend)

        def x_for(i):
            return pad_l + plot_w / 2 if n == 1 else pad_l + (plot_w * i / (n - 1))

        def y_for(pct):
            return pad_t + plot_h * (1 - pct / 100)

        poly = " ".join(f"{x_for(i):.1f},{y_for(e['failure_rate']):.1f}" for i, e in enumerate(trend))
        dots = "".join(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for(e["failure_rate"]):.1f}" r="4" fill="#F85149">'
            f'<title>{esc(e["label"])}: {e["failure_rate"]}% failure</title></circle>'
            for i, e in enumerate(trend)
        )
        gridlines = "".join(
            f'<line x1="{pad_l}" y1="{y_for(g):.1f}" x2="{chart_w - pad_r}" y2="{y_for(g):.1f}" stroke="#30363D"/>'
            f'<text x="{pad_l - 6}" y="{y_for(g) + 3:.1f}" font-size="10" fill="#8B949E" text-anchor="end" font-family="monospace">{g}%</text>'
            for g in (0, 25, 50, 75, 100)
        )
        x_labels = "".join(
            f'<text x="{x_for(i):.1f}" y="{chart_h - 8}" font-size="10" fill="#8B949E" text-anchor="middle" font-family="monospace">{esc(e["label"])}</text>'
            for i, e in enumerate(trend) if n <= 12 or i % max(1, n // 12) == 0
        )
        svg = (
            f'<svg viewBox="0 0 {chart_w} {chart_h}" width="100%" style="max-width:800px">'
            f'{gridlines}<polyline points="{poly}" fill="none" stroke="#F85149" stroke-width="2"/>{dots}{x_labels}</svg>'
        )
        trend_html = f'<div class="chart-card"><h2>Failure rate over time ({len(trend)} saved snapshot(s))</h2>{svg}</div>'

    style_block = """
  :root { --bg: #0D1117; --panel: #161B22; --border: #30363D; --text: #E6EDF3; --dim: #8B949E; --accent: #58A6FF; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; padding: 32px 24px 64px; }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  .dim { color: var(--dim); }
  header { max-width: 900px; margin: 0 auto 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 0 0 12px; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .chart-card, .table-card, .regression-card { max-width: 900px; margin: 0 auto 24px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .regression-card { border-color: #F85149; }
  .regression-card.clean { border-color: var(--border); }
  .regression-list { margin: 0; padding-left: 18px; font-size: 13px; color: #F85149; }
  .regression-list li { margin-bottom: 4px; }
  .regression-list li.alert-high { color: #F85149; }
  .regression-list li.alert-moderate { color: #D29922; }
  .stat-row { display: flex; gap: 32px; flex-wrap: wrap; }
  .stat-value { font-size: 28px; font-weight: 600; letter-spacing: -0.02em; }
  .stat-label { font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--dim); text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
  .scenario-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
  .scenario-tile { border: 1px solid; border-radius: 6px; padding: 10px 12px; background: var(--bg); }
"""

    html_doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Suite Health Summary</title><style>' + style_block + '</style></head><body>'
        f'<header><h1>Suite Health Summary: {esc(ov["suite_name"])}</h1>'
        '<div class="subtitle">Synthesized from run_suite()/run_multi_model_suite() output</div></header>'
        + overview_html + outcomes_html + scenario_grid_html + regression_html + model_comparison_html + trend_html
        + '</body></html>'
    )

    if output_path:
        with open(output_path, "w") as f:
            f.write(html_doc)
    return html_doc


def serve_health_dashboard(port: int = 8765, summaries_dir: str = None,
                            refresh_seconds: int = 5) -> None:
    """Real, added 2026-08-28 (Design_suite_health_dashboard): a real,
    genuinely LIVE, auto-refreshing local dashboard -- the honest
    answer to "live" given this harness's real architecture (a
    standalone script, no running server, no client-server system
    before this). Every other report in this harness is a static file
    that has to be manually regenerated to reflect new data; this is
    the one real exception, and it earns that exception specifically
    because the request was for something that updates on its own.

    Real, deliberate design: uses only http.server (Python's real
    standard library, no new dependency) rather than a full web
    framework. On every real GET request to "/", it re-reads
    summaries_dir from disk, takes the most recently saved health
    summary (if any), regenerates a FRESH HTML report from it via the
    exact same generate_health_summary_html_report()/
    generate_suite_health_summary()-derived data this harness's static
    reports already use (no separate JS rendering engine duplicating
    that logic in a second language), and serves it with a real
    <meta http-equiv="refresh"> tag so the browser reloads on its own
    -- so as new suite runs complete and save new summaries (via
    --save-summary), the dashboard picks them up automatically, no
    manual regeneration needed.

    Real, deliberate safety choice: binds to 127.0.0.1 only, never
    0.0.0.0 -- this is a real, local development/observability tool,
    not something that should ever be reachable from the network.

    Real, honest scope note: the original proposal's "Scenario Grid",
    "Model Health", and "Suite Alerts" panes are real and included
    here (scenario grid, model comparison, and the real flags/
    regressions this harness actually tracks, respectively) -- but its
    "Cluster Health" pane is not, since no clustering concept exists
    anywhere in this real system (confirmed and rejected when the
    underlying health summary was first built); this dashboard never
    claims to show cluster data it doesn't have.
    """
    if summaries_dir is None:
        summaries_dir = SUMMARIES_DIR

    class _DashboardHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # real, deliberate: suppress the default per-request
                   # console spam from a repeatedly-polling browser tab

        def do_GET(self):
            summaries = load_historical_summaries(summaries_dir)
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            requested_suite = query.get("suite", [None])[0]

            if not summaries:
                body = (
                    '<!DOCTYPE html><html><head><meta charset="utf-8">'
                    f'<meta http-equiv="refresh" content="{refresh_seconds}">'
                    '<title>Suite Health Dashboard</title></head>'
                    '<body style="background:#0D1117;color:#E6EDF3;font-family:sans-serif;padding:32px;">'
                    '<h1>Suite Health Dashboard</h1>'
                    f'<p style="color:#8B949E;">No saved summaries found yet in {summaries_dir}. '
                    'Run a suite with --health-summary --save-summary to populate this. '
                    f'This page auto-refreshes every {refresh_seconds}s.</p>'
                    '</body></html>'
                )
            elif requested_suite is None:
                # Real, added 2026-08-28 (full visual overview): the
                # real, default landing page -- every real suite that
                # has saved history, each as its own real card showing
                # its own latest status, not just whichever summary
                # happened to be saved most recently.
                body = generate_dashboard_overview_html(summaries)
                body = body.replace(
                    "<head><meta charset=\"utf-8\">",
                    f'<head><meta charset="utf-8"><meta http-equiv="refresh" content="{refresh_seconds}">',
                    1,
                )
            else:
                # Real drill-down: this specific real suite's own
                # latest summary and its own real trend, reusing the
                # exact same detailed report every other real health-
                # summary command already produces.
                matching = [s for s in summaries if s["overview"].get("suite_name") == requested_suite]
                if not matching:
                    body = (
                        '<!DOCTYPE html><html><head><meta charset="utf-8">'
                        f'<meta http-equiv="refresh" content="{refresh_seconds}">'
                        '<title>Suite Health Dashboard</title></head>'
                        '<body style="background:#0D1117;color:#E6EDF3;font-family:sans-serif;padding:32px;">'
                        f'<p style="color:#8B949E;">No saved summaries found for suite '
                        f'{html.escape(requested_suite)!r}. <a href="/" style="color:#58A6FF;">Back to overview</a></p>'
                        '</body></html>'
                    )
                else:
                    latest = matching[-1]
                    trend = summarize_suite_trend(summaries, suite_name=requested_suite)
                    body = generate_health_summary_html_report(latest, trend=trend, output_path=None)
                    body = body.replace(
                        "<head><meta charset=\"utf-8\">",
                        f'<head><meta charset="utf-8"><meta http-equiv="refresh" content="{refresh_seconds}">',
                        1,
                    )
                    body = body.replace(
                        "<body>",
                        '<body><div style="max-width:900px;margin:0 auto 12px;">'
                        '<a href="/" style="color:#58A6FF;font-size:13px;">&larr; Back to overview</a></div>',
                        1,
                    )
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = http.server.HTTPServer(("127.0.0.1", port), _DashboardHandler)
    print(f"Suite health dashboard serving at http://127.0.0.1:{port}/ "
          f"(auto-refreshes every {refresh_seconds}s, watching {summaries_dir})")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()














DEFAULT_CROSS_MODEL_LIST = [
    "ticker-lookup-lora",           # current, real, fixed model
    "odysseus-qwen3-tickers-lora",  # original pre-rename name -- still
                                     # subject to the real naming-collision
                                     # tool-suppression bug fixed for the
                                     # renamed model (see the real fix,
                                     # 16422cca and related commits, 2026-08-28)
    "qwen3:14b",                    # general-purpose baseline, no
                                     # ticker-specific training or fixes at all
]


def run_cross_model(endpoint_id: str, models: list, scenario: dict = None,
                     runs_per_model: int = 1, verbose: bool = True) -> dict:
    """Real, added 2026-08-28: runs the SAME real scenario (or the real
    default scenario if none given) against several different real
    models, one at a time, and returns a real, direct comparison.

    Genuinely different from the existing multi-model trend support:
    that compares models across SEPARATELY run, separately saved
    historical result files over time -- this runs them all right now,
    in one command, against the identical real scenario, for a direct
    side-by-side answer to "how do these models actually compare on
    this exact test today". Reuses run_multi_round_suite() per model
    (not a separate aggregation implementation) -- each model's real
    summary is returned in EXACTLY the same shape run_multi_round_suite()
    already produces, so any individual model's result from this
    comparison can still be saved via --save-results and fed into the
    existing --trends/detect_regressions() machinery unchanged.

    Returns {"scenario_name": ..., "results": {model: summary, ...}}.
    """
    scenario = scenario or load_scenario(DEFAULT_SCENARIO_FILE)
    results = {}
    for model in models:
        if verbose:
            print(f"\n=== Model: {model} ===")
        results[model] = run_multi_round_suite(endpoint_id, model, runs_per_model, scenario=scenario, verbose=verbose)

    if verbose:
        print(f"\n=== Cross-Model Comparison: {scenario['name']} ===")
        header = f"{'Model':<32} {'Clean %':>8} {'Turns':>6}"
        print(header)
        print("-" * len(header))
        for model, summary in results.items():
            total = summary["total_turns"] or 1
            clean_pct = round(100 * summary["clean_turns"] / total)
            print(f"{model:<32} {clean_pct:>7}% {summary['total_turns']:>6}")
        all_flags = sorted({f for s in results.values() for f in s["flag_counts"]})
        if all_flags:
            print()
            print(f"{'Flag':<28} " + " ".join(f"{m[:14]:>14}" for m in results))
            for flag in all_flags:
                row = f"{flag:<28} " + " ".join(f"{results[m]['flag_counts'].get(flag, 0):>14}" for m in results)
                print(row)

    return {"scenario_name": scenario["name"], "results": results}


def run_fuzz(endpoint_id: str, model: str, base_scenario: dict, pool_name: str = "in_training",
             count: int = 5, seed: int = None, verbose: bool = True) -> dict:
    """Real, added 2026-08-28: generates real, grounded scenario variants
    via mutate_ticker_substitution() and runs each one live, exactly
    once each (repeated runs of the SAME variant belong to
    run_multi_round_suite(), not this function -- fuzzing is about
    breadth across many real, different variants, not depth on one).
    Aggregates into the same real, structured summary shape the rest of
    the harness already understands, plus the real seed used (for
    reproducing this exact fuzz run again) and each variant's own real
    substitution note, so a real, interesting finding can be traced back
    to exactly which mutated variant produced it.
    """
    variants = mutate_ticker_substitution(base_scenario, pool_name=pool_name, count=count, seed=seed)

    total_turns = 0
    clean_turns = 0
    flag_counts = {}
    total_contamination = 0
    variant_results = []

    for variant in variants:
        if verbose:
            print(f"\n=== Fuzz variant: {variant['name']} ===")
            print(f"    {variant['description']}")
        result = run_sequence(endpoint_id, model, scenario=variant)
        run_clean = True
        for r in result["turn_results"]:
            total_turns += 1
            is_clean, flags = _classify_turn(r)
            if is_clean:
                clean_turns += 1
            else:
                run_clean = False
                for f in flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1
            if verbose:
                status = "CLEAN" if is_clean else " ".join(flags)
                print(f"  [{r.get('prompt', '')[:50]}]: {status}")
        if result["cross_turn_contamination"]:
            total_contamination += len(result["cross_turn_contamination"])
            run_clean = False
        variant_results.append({
            "variant_name": variant["name"],
            "description": variant["description"],
            "session_id": result["session_id"],
            "clean": run_clean,
            "turn_results": result["turn_results"],
        })

    summary = {
        "timestamp": time.time(),
        "model": model,
        "endpoint_id": endpoint_id,
        "fuzz_base_scenario": base_scenario.get("name", "?"),
        "fuzz_pool": pool_name,
        "fuzz_seed": seed,
        "fuzz_count": count,
        "total_turns": total_turns,
        "clean_turns": clean_turns,
        "flag_counts": flag_counts,
        "total_cross_turn_contamination": total_contamination,
        "variant_results": variant_results,
    }

    if verbose:
        clean_pct = round(100 * clean_turns / total_turns) if total_turns else 0
        variants_clean = sum(1 for v in variant_results if v["clean"])
        print(f"\n=== Fuzz Summary: {count} variant(s) of '{base_scenario.get('name', '?')}', "
              f"pool={pool_name}, seed={seed} ===")
        print(f"Variants completely clean: {variants_clean}/{count}")
        print(f"Turns completely clean: {clean_turns}/{total_turns} ({clean_pct}%)")
        print(f"Flag breakdown: {flag_counts}")
        print(f"Total cross-turn contamination: {total_contamination}")
        if seed is not None:
            print(f"To reproduce this exact fuzz run: --fuzz-seed {seed}")

    return summary








def generate_html_report(summary: dict, output_path: str) -> None:
    """Real, richer human-readable report: renders a multi-round suite
    summary (from run_multi_round_suite()) as a self-contained HTML file
    -- no external dependencies, opens directly in any browser. Built
    2026-08-28 because the plain console/JSON output, while complete,
    took real, repeated manual squinting to scan across a whole night of
    runs; this gives a genuinely faster, clearer read of the same real
    data, with the actual per-turn detail (prompt, flags, expectation)
    still fully visible, not summarized away.

    Visual language deliberately borrows from the actual subject matter
    being tested (stock ticker lookups): each turn's status renders as a
    small market-style indicator (up/down/flat), monospace for tickers,
    prices, and flags, dark terminal-style palette matching the kind of
    tool a developer would actually want open next to a real terminal.
    """
    import html as _html
    import time as _time

    def esc(s):
        return _html.escape(str(s))

    status_styles = {
        "clean": ("&#9650;", "#3FB950", "CLEAN"),
        "violated": ("&#9660;", "#F85149", "VIOLATED"),
        "observed": ("&#9644;", "#8B949E", "OBSERVED"),
    }

    def turn_status(r):
        is_clean, flags = _classify_turn(r)
        if is_clean:
            return "clean"
        if "EXPECTATION_VIOLATED" in flags or "NO_TOOL_CALL" in flags:
            return "violated"
        return "violated" if flags else "observed"

    run_cards = []
    for run in summary["run_records"]:
        turn_rows = []
        for r in run["turn_results"]:
            status = turn_status(r)
            arrow, color, label = status_styles[status]
            is_clean, flags = _classify_turn(r)
            flag_str = ", ".join(flags) if flags else "&mdash;"
            expectation = r.get("expectation")
            if expectation is True:
                exp_str = "expects tool call"
            elif expectation is False:
                exp_str = "expects no tool call"
            else:
                exp_str = "unasserted"
            tool_calls_str = ", ".join(r.get("tool_calls", [])) or "none"
            turn_html = (
                '<div class="turn" style="border-left-color: ' + color + '">'
                '<div class="turn-status" style="color: ' + color + '">' + arrow + ' ' + label + '</div>'
                '<div class="turn-body">'
                '<div class="turn-prompt">' + esc(r.get("prompt", "")) + '</div>'
                '<div class="turn-meta">'
                '<span class="pill">' + esc(exp_str) + '</span>'
                '<span class="pill">tool_calls: ' + esc(tool_calls_str) + '</span>'
                '<span class="pill flags">' + flag_str + '</span>'
                '</div></div></div>'
            )
            turn_rows.append(turn_html)

        contamination = run.get("cross_turn_contamination") or []
        contamination_html = ""
        if contamination:
            pairs = ", ".join("turn " + str(b) + " to turn " + str(a) for a, b in contamination)
            contamination_html = '<div class="contamination">Cross-turn contamination: ' + esc(pairs) + '</div>'

        run_clean_badge = (
            '<span class="run-badge clean">CLEAN</span>' if run["clean"]
            else '<span class="run-badge violated">FLAGGED</span>'
        )
        run_html = (
            '<div class="run-card"><div class="run-header">'
            '<span class="session-id">' + esc(run["session_id"]) + '</span>' + run_clean_badge + '</div>'
            + "".join(turn_rows) + contamination_html + '</div>'
        )
        run_cards.append(run_html)

    flag_bars = []
    max_flag = max(summary["flag_counts"].values(), default=0) or 1
    for flag_name, count in summary["flag_counts"].items():
        width_pct = (count / max_flag) * 100 if count else 0
        flag_bars.append(
            '<div class="flag-row"><span class="flag-name">' + esc(flag_name) + '</span>'
            '<div class="flag-bar-track"><div class="flag-bar-fill" style="width: '
            + str(width_pct) + '%"></div></div>'
            '<span class="flag-count">' + str(count) + '</span></div>'
        )

    clean_pct = round(100 * summary["clean_turns"] / summary["total_turns"]) if summary["total_turns"] else 0
    generated_at = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(summary["timestamp"]))

    style_block = """
  :root {
    --bg: #0D1117;
    --panel: #161B22;
    --border: #30363D;
    --text: #E6EDF3;
    --dim: #8B949E;
    --accent: #58A6FF;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    padding: 32px 24px 64px;
  }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  header { max-width: 900px; margin: 0 auto 32px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .subtitle .mono { color: var(--accent); }
  .stats {
    max-width: 900px; margin: 0 auto 32px;
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
  }
  .stat-card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px;
  }
  .stat-value { font-size: 28px; font-weight: 600; font-family: "SF Mono", ui-monospace, monospace; }
  .stat-label { font-size: 11px; color: var(--dim); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 4px; }
  .flag-breakdown {
    max-width: 900px; margin: 0 auto 32px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px;
  }
  .flag-breakdown h2, .runs-section h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 0 0 12px; }
  .flag-row { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; font-size: 13px; }
  .flag-name { width: 130px; font-family: "SF Mono", ui-monospace, monospace; color: var(--dim); }
  .flag-bar-track { flex: 1; height: 8px; background: var(--bg); border-radius: 4px; overflow: hidden; }
  .flag-bar-fill { height: 100%; background: var(--accent); }
  .flag-count { width: 24px; text-align: right; font-family: "SF Mono", ui-monospace, monospace; }
  .runs-section { max-width: 900px; margin: 0 auto; }
  .run-card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 20px; margin-bottom: 16px;
  }
  .run-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .session-id { font-size: 12px; color: var(--dim); }
  .run-badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; letter-spacing: 0.05em; }
  .run-badge.clean { background: rgba(63,185,80,0.15); color: #3FB950; }
  .run-badge.violated { background: rgba(248,81,73,0.15); color: #F85149; }
  .turn {
    display: flex; gap: 12px; padding: 10px 0 10px 12px;
    border-left: 3px solid; border-top: 1px solid var(--border);
  }
  .turn:first-of-type { border-top: none; }
  .turn-status { width: 100px; flex-shrink: 0; font-size: 11px; font-weight: 600; font-family: "SF Mono", ui-monospace, monospace; letter-spacing: 0.03em; }
  .turn-prompt { font-size: 13px; margin-bottom: 6px; }
  .turn-meta { display: flex; gap: 6px; flex-wrap: wrap; }
  .pill { font-size: 10px; padding: 2px 8px; border-radius: 10px; background: var(--bg); color: var(--dim); font-family: "SF Mono", ui-monospace, monospace; }
  .pill.flags { color: #F85149; }
  .contamination { margin-top: 10px; font-size: 12px; color: #D29922; }
"""

    html_doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Stability Report - ' + esc(summary["model"]) + '</title>'
        '<style>' + style_block + '</style></head><body>'
        '<header><h1>Ticker LoRA Stability Report</h1>'
        '<div class="subtitle"><span class="mono">' + esc(summary["model"]) + '</span> on endpoint '
        '<span class="mono">' + esc(summary["endpoint_id"]) + '</span> &middot; generated '
        + esc(generated_at) + '</div></header>'
        '<div class="stats">'
        '<div class="stat-card"><div class="stat-value">' + str(summary["runs_completely_clean"]) + '/'
        + str(summary["runs_completed"]) + '</div><div class="stat-label">Runs clean</div></div>'
        '<div class="stat-card"><div class="stat-value">' + str(clean_pct) + '%</div>'
        '<div class="stat-label">Turns clean (' + str(summary["clean_turns"]) + '/' + str(summary["total_turns"]) + ')</div></div>'
        '<div class="stat-card"><div class="stat-value">' + str(summary["total_cross_turn_contamination"]) + '</div>'
        '<div class="stat-label">Contamination findings</div></div>'
        '<div class="stat-card"><div class="stat-value">' + str(summary["runs_errored"]) + '</div>'
        '<div class="stat-label">Run errors</div></div>'
        '</div>'
        '<div class="flag-breakdown"><h2>Flag breakdown</h2>' + "".join(flag_bars) + '</div>'
        '<div class="runs-section"><h2>Runs</h2>' + "".join(run_cards) + '</div>'
        '</body></html>'
    )

    with open(output_path, "w") as f:
        f.write(html_doc)




RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
# Real, added 2026-08-28: where persisted health summaries live -- a
# real, deliberately separate directory from RESULTS_DIR, since a
# health summary (overview/outcomes/regressions/model_comparison) is
# derived, suite-level data, genuinely different in shape from the raw
# per-scenario/per-run results RESULTS_DIR holds; mixing the two would
# make load_historical_results() misinterpret summary files as raw
# results (or vice versa).
SUMMARIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summaries")
# Real, added 2026-08-28: where real, captured raw-event debug bundles
# (from capture_raw_events_for_check()/--capture-check) live -- see
# scripts/captures/.
CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


def detect_regressions(historical_summaries: list, min_drop_pct: int = 10) -> list:
    """Real, added 2026-08-28, redesigned 2026-08-28 after a second
    external proposal (regression alerts as a first-class subsystem)
    identified real gaps in the first version: compares each model's
    most recent run against its own prior history (scoped per-model,
    deliberately -- different models have genuinely different baselines,
    comparing across models would produce a meaningless "regression").

    Returns a list of real, structured alert dicts (not plain strings,
    since a caller like the HTML report needs the type/severity to
    render usefully, not just parse a sentence):
      {"type": str, "message": str, "severity": "high"|"moderate", "model": str}

    Four real, distinct checks, each a genuinely different failure
    shape:
      - clean_rate_regression: latest run's clean rate drops at least
        min_drop_pct points below the MEDIAN of prior runs (not just
        the immediately preceding one -- more robust against a single
        noisy prior run looking like a false baseline). Severity scales
        with the drop's real size.
      - new_flag_type: a flag type present in the latest run that was
        never seen, at all, in any prior run for this model -- a
        genuinely new failure mode, always "high" severity.
      - flag_count_increase: real, added this redesign -- an ALREADY-
        seen flag type whose count meaningfully increases (at least 2
        above the average of prior runs) -- the real gap the first
        version had: it only ever caught a flag's first appearance,
        never a worsening rate of one already seen.
      - contamination_regression: real, added this redesign --
        cross-turn contamination appearing in the latest run when it
        was zero in every prior run, using the real, existing
        total_cross_turn_contamination field (previously not checked
        by this function at all, even though it was already computed
        elsewhere in the harness).
    """
    alerts = []
    by_model = {}
    for s in historical_summaries:
        by_model.setdefault(s.get("model", "?"), []).append(s)

    def clean_pct(s):
        total = s.get("total_turns", 0) or 1
        return 100 * s.get("clean_turns", 0) / total

    def median(values):
        values = sorted(values)
        n = len(values)
        if n == 0:
            return 0
        mid = n // 2
        if n % 2 == 1:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2

    for model, runs in by_model.items():
        if len(runs) < 2:
            continue
        runs_sorted = sorted(runs, key=lambda r: r["timestamp"])
        prior_runs = runs_sorted[:-1]
        latest = runs_sorted[-1]

        # 1. Clean-rate regression, against the median of prior runs.
        prior_pcts = [clean_pct(r) for r in prior_runs]
        median_pct = median(prior_pcts)
        latest_pct = clean_pct(latest)
        drop = median_pct - latest_pct
        if drop >= min_drop_pct:
            severity = "high" if drop >= 25 else "moderate"
            alerts.append({
                "type": "clean_rate_regression",
                "message": f"{model}: clean rate dropped {median_pct:.0f}% -> {latest_pct:.0f}% "
                           f"(-{drop:.0f}pts vs. the median of {len(prior_runs)} prior run(s))",
                "severity": severity,
                "model": model,
            })

        # 2 & 3. New flag types, and meaningful increases in known ones.
        prior_flag_counts = {}
        for r in prior_runs:
            for flag, count in r.get("flag_counts", {}).items():
                prior_flag_counts.setdefault(flag, []).append(count)

        for flag, count in latest.get("flag_counts", {}).items():
            if count <= 0:
                continue
            if flag not in prior_flag_counts or not any(c > 0 for c in prior_flag_counts[flag]):
                alerts.append({
                    "type": "new_flag_type",
                    "message": f"{model}: new failure type detected -- {flag} (not seen in prior runs)",
                    "severity": "high",
                    "model": model,
                })
            else:
                prior_avg = sum(prior_flag_counts[flag]) / len(prior_flag_counts[flag])
                if count - prior_avg >= 2:
                    alerts.append({
                        "type": "flag_count_increase",
                        "message": f"{model}: {flag} count increased to {count} "
                                   f"(avg {prior_avg:.1f} in prior runs)",
                        "severity": "moderate",
                        "model": model,
                    })

        # 4. Contamination regression -- a real, existing field this
        # function never checked before this redesign.
        prior_contamination = [r.get("total_cross_turn_contamination", 0) for r in prior_runs]
        latest_contamination = latest.get("total_cross_turn_contamination", 0)
        if latest_contamination > 0 and not any(c > 0 for c in prior_contamination):
            alerts.append({
                "type": "contamination_regression",
                "message": f"{model}: cross-turn contamination detected "
                           f"({latest_contamination} finding(s)) where none occurred in prior runs",
                "severity": "high",
                "model": model,
            })

    return alerts


def send_desktop_notification(title: str, message: str) -> bool:
    """Real, added 2026-08-28: sends a real desktop notification via
    notify-send (the real, standard Linux desktop notification tool,
    confirmed present on the real host, Pop!_OS 24.04). Checks
    shutil.which("notify-send") at real runtime rather than assume
    it's available -- confirmed directly, the same day, that it is
    NOT present inside the Odysseus Docker container this harness is
    most often actually run from (no D-Bus session exists there at
    all), so this only genuinely works when the harness runs directly
    on the host, not from inside the container. Returns False with a
    clear, honest reason printed rather than silently do nothing or
    raise, so a caller running inside the container isn't left
    wondering why no notification appeared.
    """
    if shutil.which("notify-send") is None:
        print("Desktop notification skipped: notify-send is not available in this "
              "environment (e.g. running inside a container without D-Bus) -- "
              "run this harness on the host directly, or use --notify-method webhook.")
        return False
    try:
        subprocess.run(["notify-send", title, message], check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        print(f"Desktop notification failed: {e}")
        return False


def send_webhook_notification(url: str, payload: dict) -> bool:
    """Real, added 2026-08-28: POSTs a real JSON payload to any real
    webhook URL via urllib.request (already used throughout this
    harness, no new dependency) -- works from inside the container or
    on the host equally, unlike send_desktop_notification(), since it
    only needs real network access, not a real D-Bus session. Points
    at whatever real endpoint the caller configures (Slack incoming
    webhook, Discord webhook, ntfy.sh, a custom endpoint) -- this
    harness has no confirmed, hard-coded credentials for any specific
    real service (e.g. DK's real Hermes Telegram bot), so it does not
    guess or fabricate one; the caller supplies a real URL via
    --webhook-url.
    """
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as e:
        print(f"Webhook notification failed: {e}")
        return False


def notify_regressions(regressions: list, method: str = "desktop", webhook_url: str = None,
                        suite_name: str = "?") -> bool:
    """Real, added 2026-08-28 (Design_suite_health_notifications):
    sends a real notification only when there's something real to
    report -- if regressions is empty, does nothing and returns False,
    since a "no regressions" notification firing on every single run
    would just be noise, not a real alert. Dispatches to
    send_desktop_notification() or send_webhook_notification()
    depending on method; for webhook, the real payload includes the
    suite name and every real regression alert dict exactly as
    detect_regressions() produced it (type/message/severity/model),
    not a lossy, pre-formatted summary string, so a real downstream
    consumer (a Slack channel, a custom script watching the webhook)
    can build its own real formatting or filtering on top.
    """
    if not regressions:
        return False

    if method == "desktop":
        title = f"Suite regression: {suite_name}"
        lines = [f"[{a['severity']}] {a['message']}" for a in regressions]
        message = "\n".join(lines)
        return send_desktop_notification(title, message)
    elif method == "webhook":
        if not webhook_url:
            print("Webhook notification skipped: --webhook-url wasn't given.")
            return False
        payload = {"suite_name": suite_name, "regressions": regressions}
        return send_webhook_notification(webhook_url, payload)
    else:
        raise ValueError(f"Unknown notification method {method!r}, real options: 'desktop', 'webhook'")


def evaluate_health_gate(summary: dict, max_failure_rate: int = None,
                          max_regression_severity: str = None,
                          block_on_any_regression: bool = False) -> dict:
    """Real, added 2026-08-28 (Design_suite_health_gatekeeping): a real,
    honest, minimal "gate" -- checked an external proposal for this
    feature against the real system first, same as every prior
    evaluation the same night, and confirmed there is no real CI/CD
    pipeline, promotion system, or deployment automation anywhere in
    this actual codebase for a fabricated "gatekeeping/autopruning/
    autopromotion" system to plug into (this was already explicitly
    rejected when the underlying health summary was first built).
    Building a fake promotion system to gate would be exactly the kind
    of fabrication rejected all night.

    The real, honest, buildable version of "gatekeeping" is a standard
    Unix primitive: evaluate a real, already-computed health summary
    against real, caller-supplied thresholds, and report pass/fail
    with real, specific reasons -- see the real, matching --gate CLI
    flag, which exits with a real, non-zero status code on failure.
    Any real external process (a shell script, a cron job, a git hook,
    a CI system DK might set up later) can compose that real exit code
    into whatever actual promotion/deployment decision it makes --
    this harness does not invent or claim to run that process itself.

    Real, deliberate: all three threshold parameters are optional and
    independently checkable -- a caller uses whichever real criteria
    matter to them, not a fixed, one-size-fits-all rule. Returns
    {"passed": bool, "reasons": [str, ...]} -- reasons is always
    populated with the real, specific check(s) that failed, or a
    single real confirmation message when nothing was checked or
    everything passed, so a caller never has to guess why a gate
    result came out the way it did.
    """
    reasons = []

    failure_rate = summary["outcomes"]["failure_rate"]
    if max_failure_rate is not None and failure_rate > max_failure_rate:
        reasons.append(
            f"Failure rate {failure_rate}% exceeds max_failure_rate {max_failure_rate}%"
        )

    regressions = summary.get("regressions", [])
    if block_on_any_regression and regressions:
        reasons.append(f"{len(regressions)} real regression(s) detected (block_on_any_regression is set)")

    if max_regression_severity is not None:
        if max_regression_severity not in ("moderate", "high"):
            raise ValueError(
                f"max_regression_severity must be 'moderate' or 'high', got {max_regression_severity!r}"
            )
        blocking_severities = {"moderate", "high"} if max_regression_severity == "moderate" else {"high"}
        blocking = [a for a in regressions if a["severity"] in blocking_severities]
        if blocking:
            reasons.append(
                f"{len(blocking)} regression(s) at or above '{max_regression_severity}' severity: "
                + "; ".join(a["message"] for a in blocking)
            )

    passed = not reasons
    if passed and not (max_failure_rate is not None or block_on_any_regression or max_regression_severity is not None):
        reasons = ["No real gate criteria were given -- passes by default."]
    elif passed:
        reasons = ["All real gate criteria passed."]

    return {"passed": passed, "reasons": reasons}







def load_historical_results(results_dir: str = None) -> list:
    """Real, added 2026-08-28: loads every saved --save-results JSON file
    from a directory (each one a single run_multi_round_suite() output,
    with its own real "timestamp"), sorted oldest to newest. Files that
    fail to parse or lack a real "timestamp" are skipped rather than
    aborting the whole load -- one bad or partial file from an
    interrupted run shouldn't block trend analysis of everything else.
    """
    results_dir = results_dir or RESULTS_DIR
    if not os.path.isdir(results_dir):
        return []
    summaries = []
    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(results_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if "timestamp" not in data or "total_turns" not in data:
            continue
        data["_source_file"] = fname
        summaries.append(data)
    summaries.sort(key=lambda s: s["timestamp"])
    return summaries


def _gather_scenario_history(historical_summaries: list, model: str = None,
                              recent_n: int = None) -> dict:
    """Real, added 2026-08-28 (refactored out of rank_scenarios_by_
    failure_rate, which used to contain this logic inline -- extracted
    so compute_scenario_weight() can reuse the exact same real data-
    gathering rather than duplicate it): walks every real historical
    summary, handling all 3 genuinely different shapes this harness can
    produce (a direct scenario run with "scenario_name" at the top
    level; a suite run with "scenario_results", a list of nested,
    per-scenario summaries, each with its own real "timestamp"; a fuzz
    run with "fuzz_base_scenario" instead), and returns a real,
    per-scenario dict of {"total_turns", "clean_turns", "runs_seen",
    "flag_counts" (aggregated across every real, included occurrence),
    "total_contamination"}.

    Same real, deliberate model-scoping as rank_scenarios_by_failure_
    rate(): a real, confirmed result (via --cross-model, the same
    night) showed the identical scenario swinging from 80% clean to 0%
    clean depending purely on which model ran it -- mixing models here
    would produce misleading aggregate numbers for every downstream
    consumer of this data, not just the failure-rate ranking.

    Real, added 2026-08-28: recent_n (default None, meaning "use every
    real historical entry, unchanged from the original behavior") --
    when given a real integer, each scenario's OWN entries (not the
    overall list) are sorted newest-first by their own real timestamp
    and only the most recent recent_n are aggregated, discarding
    older ones. Real motivation: a scenario that failed heavily weeks
    ago but has been clean in every recent run shouldn't still carry
    that stale weight forever just because old failures are diluted
    into an all-time average rather than excluded -- weight should
    reflect current reality, not permanently remember old history that
    may no longer be true. Deliberately a hard, simple N-most-recent
    window (not exponential time-decay) -- explainable and predictable
    ("the last 5 runs say X") rather than requiring an arbitrary decay
    constant nobody could easily reason about.
    """
    entries_by_scenario = {}

    def collect(scenario_name, s):
        if model is not None and s.get("model") != model:
            return
        entries_by_scenario.setdefault(scenario_name, []).append(s)

    for s in historical_summaries:
        if "scenario_name" in s:
            collect(s["scenario_name"], s)
        elif "scenario_results" in s:
            for nested in s["scenario_results"]:
                if "scenario_name" in nested:
                    collect(nested["scenario_name"], nested)
        elif "fuzz_base_scenario" in s:
            collect(s["fuzz_base_scenario"], s)

    per_scenario = {}
    for scenario_name, entries in entries_by_scenario.items():
        if recent_n is not None:
            entries = sorted(entries, key=lambda e: e.get("timestamp", 0), reverse=True)[:recent_n]
        bucket = {"total_turns": 0, "clean_turns": 0, "runs_seen": 0,
                  "flag_counts": {}, "total_contamination": 0}
        for s in entries:
            bucket["total_turns"] += s.get("total_turns", 0)
            bucket["clean_turns"] += s.get("clean_turns", 0)
            bucket["runs_seen"] += 1
            bucket["total_contamination"] += s.get("total_cross_turn_contamination", 0)
            for flag, count in s.get("flag_counts", {}).items():
                bucket["flag_counts"][flag] = bucket["flag_counts"].get(flag, 0) + count
        per_scenario[scenario_name] = bucket

    return per_scenario



def rank_scenarios_by_failure_rate(historical_summaries: list, model: str = None) -> list:
    """Real, added 2026-08-28: ranks real scenarios by how often they've
    actually shown a real issue historically, highest failure rate
    first -- so a time-constrained run can prioritize the scenarios most
    likely to actually catch something, rather than run every scenario
    with equal weight regardless of its real track record.

    Real, deliberate scoping decision, learned from the cross-model
    comparison built earlier the same night: the identical scenario can
    swing from 80% clean to 0% clean depending purely on which model
    ran it (a real, confirmed, live result). Ranking scenarios by
    failure rate while mixing data from different models would mostly
    just reflect which model happened to be tested most in the
    historical data, not the scenario's own real difficulty -- pass a
    real model name to scope the ranking to just that model's history
    (the honest, meaningful comparison); omit it to aggregate across
    every model in the historical data, with the same caveat noted
    directly in this docstring rather than silently producing a
    misleading number.

    Returns a list of {"scenario_name", "failure_rate" (0-100),
    "total_turns", "clean_turns", "runs_seen"}, sorted by failure_rate
    descending (worst first). A scenario is EXCLUDED if no historical
    data exists in the given model scope, rather than shown with a
    fabricated 0% failure rate.
    """
    per_scenario = _gather_scenario_history(historical_summaries, model)

    ranked = []
    for scenario_name, bucket in per_scenario.items():
        total = bucket["total_turns"]
        clean = bucket["clean_turns"]
        failure_rate = round(100 * (1 - clean / total)) if total else 0
        ranked.append({
            "scenario_name": scenario_name,
            "failure_rate": failure_rate,
            "total_turns": total,
            "clean_turns": clean,
            "runs_seen": bucket["runs_seen"],
        })

    ranked.sort(key=lambda r: r["failure_rate"], reverse=True)
    return ranked


# Real, deliberate design decision, made before writing any weighting
# code, in response to the external proposal that requested this
# feature: that proposal's own design asked for a static "weight" block
# hand-written into every scenario JSON file (failure_rate, contamination_
# risk, etc. as fixed numbers). Rejected that specific mechanism -- a
# static, hand-maintained number claiming to represent "historical
# failure rate" would silently go stale the moment new history
# accumulates, with nothing forcing anyone to update it. The whole
# point of weighting by real history is that it's dynamic; baking a
# snapshot into a config file defeats that. Scenario weight is computed
# fresh, at call time, directly from real historical data (reusing
# _gather_scenario_history(), the same underlying data
# rank_scenarios_by_failure_rate() already uses) plus each real
# scenario's own real, existing scenario_tags -- never a static field
# a human has to remember to keep in sync.
GENERALIZATION_RISK_TAGS = {"generalization", "synthetic", "hallucination-risk"}


def compute_scenario_weight(scenario: dict, historical_summaries: list, model: str = None,
                             recent_n: int = None) -> dict:
    """Real, added 2026-08-28: computes a real, dynamic scenario weight
    -- base + failure_rate + contamination_risk + tool_error_risk +
    generalization_risk -- combining real historical evidence with the
    scenario's own real, declared tags, so suites/fuzzing/regression
    alerts can prioritize the scenarios most likely to actually matter,
    rather than treat every scenario as equally important regardless of
    its real track record.

    Real, added 2026-08-28 (scenario_weighting_extensions): recent_n
    (default None -- every real historical entry, unchanged behavior)
    -- passed straight through to _gather_scenario_history(), whose own
    docstring has the full real reasoning. In short: with a real
    integer given, only that scenario's most recent recent_n real runs
    count toward failure_rate/contamination_risk/tool_error_risk, so a
    scenario that used to fail a lot but has been clean recently
    correctly shows LOWER weight than an all-time average would -- the
    weight reflects current reality, not permanently-remembered old
    history. generalization_risk is unaffected either way, since it's
    a real, intrinsic, tag-based property of the scenario itself, not
    derived from run history at all.

    Real components, each real and independently inspectable in the
    returned dict, not just folded into one opaque number:
      - base: fixed at 1.0 -- every real scenario starts equally
        important; the other components only ever add real, evidence-
        or design-based weight on top.
      - failure_rate: 0.0-1.0, the scenario's own real historical
        failure rate (reuses the exact same _gather_scenario_history()
        data rank_scenarios_by_failure_rate() uses, same real model-
        scoping caveat applies).
      - contamination_risk: 0.0-1.0, real cross-turn contamination
        findings per real historical run of this scenario, capped at
        1.0 (a scenario contaminating on literally every run is
        already maximally concerning; further runs shouldn't inflate
        this component without bound).
      - tool_error_risk: 0.0-1.0, real TOOL_ERROR flag occurrences per
        real total turn, same capping logic.
      - generalization_risk: a flat, real 1.0 if the scenario's own
        real scenario_tags include "generalization", "synthetic", or
        "hallucination-risk" (see GENERALIZATION_RISK_TAGS) -- an
        intrinsic, design-time property of what the scenario tests,
        not something derived from run history at all, so a scenario
        with zero historical runs (brand new) still correctly gets
        real weight if its own tags mark it as testing a genuinely
        harder, higher-risk dimension.
    Returns {"total", "base", "failure_rate", "contamination_risk",
    "tool_error_risk", "generalization_risk", "runs_seen"} -- runs_seen
    is 0 for a scenario with no historical data in the given model
    scope, and every history-derived component correctly stays 0.0 in
    that case (a brand-new scenario isn't assumed risky just because
    there's no evidence yet -- only its own real tags can raise its
    weight before real history exists).
    """
    scenario_name = scenario.get("name", "?")
    per_scenario = _gather_scenario_history(historical_summaries, model, recent_n=recent_n)
    bucket = per_scenario.get(scenario_name)

    base = 1.0
    failure_rate = 0.0
    contamination_risk = 0.0
    tool_error_risk = 0.0
    runs_seen = 0

    if bucket:
        runs_seen = bucket["runs_seen"]
        total = bucket["total_turns"]
        if total:
            failure_rate = 1 - bucket["clean_turns"] / total
            tool_error_risk = min(1.0, bucket["flag_counts"].get("TOOL_ERROR", 0) / total)
        if runs_seen:
            contamination_risk = min(1.0, bucket["total_contamination"] / runs_seen)

    tags = set(scenario.get("scenario_tags") or [])
    generalization_risk = 1.0 if tags & GENERALIZATION_RISK_TAGS else 0.0

    total_weight = base + failure_rate + contamination_risk + tool_error_risk + generalization_risk

    return {
        "total": round(total_weight, 3),
        "base": base,
        "failure_rate": round(failure_rate, 3),
        "contamination_risk": round(contamination_risk, 3),
        "tool_error_risk": round(tool_error_risk, 3),
        "generalization_risk": generalization_risk,
        "runs_seen": runs_seen,
    }


def compute_multi_model_scenario_weights(scenario: dict, historical_summaries: list,
                                          models: list, recent_n: int = None) -> dict:
    """Real, added 2026-08-28: computes compute_scenario_weight() for the
    SAME real scenario across SEVERAL real models, returning
    {model: weight_dict}, so a real weight difference between models
    for the identical scenario is directly visible side by side -- the
    genuine gap compute_scenario_weight() alone doesn't fill: it
    already correctly scopes to one real model at a time (confirmed
    directly, the same night, that the identical scenario can swing
    from 80% clean to 0% clean depending purely on which model ran it),
    but nothing before this compared several models' weights for the
    same scenario in one call.

    Reuses compute_scenario_weight() per model (not a separate, fourth
    implementation of the same underlying math) -- each model's real
    weight dict comes back in exactly the shape compute_scenario_weight()
    already produces.
    """
    if not models:
        raise ValueError(
            "compute_multi_model_scenario_weights() needs a real, "
            "non-empty models list"
        )
    return {
        model: compute_scenario_weight(scenario, historical_summaries, model=model, recent_n=recent_n)
        for model in models
    }







def generate_trend_report(historical_summaries: list, output_path: str) -> None:
    """Real, added 2026-08-28, extended 2026-08-28 with multi-model
    comparison and a flag-trend chart: renders multiple saved suite
    results, loaded via load_historical_results(), as a single,
    self-contained HTML trend report.

    Real, added extension: the original version plotted one clean-rate
    line regardless of which model produced each run -- if historical
    data spans more than one model (e.g. comparing the renamed
    ticker-lookup-lora against a future retrained version, or against
    the original odysseus-qwen3-tickers-lora before the naming-collision
    fix), that would silently blend genuinely different models into one
    misleading average. Now groups by model, one colored line per model
    with a real legend, so a real regression or improvement in one
    specific model is visible rather than smoothed away. Also adds a
    second chart: flag-count trends over time (one line per real flag
    type seen across all loaded runs), so which specific failure mode is
    trending up or down is visible directly, not just inferable from the
    per-run table.
    """
    import html as _html
    import time as _time

    def esc(s):
        return _html.escape(str(s))

    if not historical_summaries:
        with open(output_path, "w") as f:
            f.write(
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<title>Stability Trends</title></head>'
                '<body style="background:#0D1117;color:#E6EDF3;font-family:sans-serif;padding:32px;">'
                '<h1>No historical results found</h1>'
                '<p style="color:#8B949E;">Run the suite with --save-results pointing into '
                'scripts/results/ a few times to build up real trend data.</p>'
                '</body></html>'
            )
        return

    # Real palette, cycled if more models than colors -- kept distinct
    # from the status colors used elsewhere (green/red/amber) so a
    # reader never confuses "which model" with "clean vs violated".
    palette = ["#58A6FF", "#D2A8FF", "#F0883E", "#3FB950", "#FF7B72", "#79C0FF", "#F778BA"]

    points = []
    for s in historical_summaries:
        total = s.get("total_turns", 0) or 1
        clean_pct = round(100 * s.get("clean_turns", 0) / total)
        points.append({
            "timestamp": s["timestamp"],
            "label": _time.strftime("%m/%d %H:%M", _time.localtime(s["timestamp"])),
            "clean_pct": clean_pct,
            "total_turns": s.get("total_turns", 0),
            "flag_counts": s.get("flag_counts", {}),
            "source_file": s.get("_source_file", "?"),
            "model": s.get("model", "?"),
            "scenario_name": s.get("scenario_name", "?"),
        })

    models = sorted({p["model"] for p in points})
    model_colors = {m: palette[i % len(palette)] for i, m in enumerate(models)}

    all_flag_names = sorted({k for p in points for k in p["flag_counts"]})
    flag_colors = {name: palette[i % len(palette)] for i, name in enumerate(all_flag_names)}

    chart_w, chart_h = 800, 240
    pad_l, pad_r, pad_t, pad_b = 40, 20, 20, 30
    plot_w = chart_w - pad_l - pad_r
    plot_h = chart_h - pad_t - pad_b
    n = len(points)

    def x_for(i):
        if n == 1:
            return pad_l + plot_w / 2
        return pad_l + (plot_w * i / (n - 1))

    def y_for_pct(pct, max_val=100):
        return pad_t + plot_h * (1 - pct / max_val) if max_val else pad_t + plot_h

    def gridlines_pct(max_val, unit=""):
        steps = (0, max_val * 0.25, max_val * 0.5, max_val * 0.75, max_val) if max_val else (0,)
        out = ""
        for gy in steps:
            y = y_for_pct(gy, max_val or 1)
            out += (
                f'<line x1="{pad_l}" y1="{y:.1f}" x2="{chart_w - pad_r}" y2="{y:.1f}" '
                f'stroke="#30363D" stroke-width="1"/>'
                f'<text x="{pad_l - 6}" y="{y + 3:.1f}" font-size="10" fill="#8B949E" '
                f'text-anchor="end" font-family="monospace">{gy:.0f}{unit}</text>'
            )
        return out

    def x_labels():
        return "".join(
            f'<text x="{x_for(i):.1f}" y="{chart_h - 8}" font-size="10" fill="#8B949E" '
            f'text-anchor="middle" font-family="monospace">{esc(p["label"])}</text>'
            for i, p in enumerate(points)
            if n <= 12 or i % max(1, n // 12) == 0
        )

    # --- Chart 1: clean-rate over time, one line per model ---
    clean_lines = ""
    clean_legend = ""
    for m in models:
        model_points = [(i, p) for i, p in enumerate(points) if p["model"] == m]
        if not model_points:
            continue
        color = model_colors[m]
        poly = " ".join(f"{x_for(i):.1f},{y_for_pct(p['clean_pct']):.1f}" for i, p in model_points)
        dots = "".join(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for_pct(p["clean_pct"]):.1f}" r="4" fill="{color}">'
            f'<title>{esc(m)} @ {esc(p["label"])}: {p["clean_pct"]}% clean</title></circle>'
            for i, p in model_points
        )
        clean_lines += f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>{dots}'
        clean_legend += (
            f'<span class="legend-item"><span class="legend-dot" style="background:{color}"></span>'
            f'<span class="mono">{esc(m)}</span></span>'
        )

    clean_svg = (
        f'<svg viewBox="0 0 {chart_w} {chart_h}" width="100%" style="max-width:800px">'
        f'{gridlines_pct(100, "%")}{clean_lines}{x_labels()}'
        f'</svg>'
    )

    # --- Chart 2: flag-count trends over time, one line per flag type ---
    max_flag_count = max((c for p in points for c in p["flag_counts"].values()), default=0) or 1
    flag_lines = ""
    flag_legend = ""
    for name in all_flag_names:
        color = flag_colors[name]
        poly = " ".join(
            f"{x_for(i):.1f},{y_for_pct(p['flag_counts'].get(name, 0), max_flag_count):.1f}"
            for i, p in enumerate(points)
        )
        dots = "".join(
            f'<circle cx="{x_for(i):.1f}" cy="{y_for_pct(p["flag_counts"].get(name, 0), max_flag_count):.1f}" '
            f'r="3" fill="{color}"><title>{esc(name)} @ {esc(p["label"])}: '
            f'{p["flag_counts"].get(name, 0)}</title></circle>'
            for i, p in enumerate(points)
        )
        flag_lines += f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>{dots}'
        flag_legend += (
            f'<span class="legend-item"><span class="legend-dot" style="background:{color}"></span>'
            f'<span class="mono">{esc(name)}</span></span>'
        )

    flag_svg = (
        f'<svg viewBox="0 0 {chart_w} {chart_h}" width="100%" style="max-width:800px">'
        f'{gridlines_pct(max_flag_count)}{flag_lines}{x_labels()}'
        f'</svg>'
    )

    # --- Per-run table, most recent first ---
    header_cells = "".join(f"<th>{esc(name)}</th>" for name in all_flag_names)
    rows = []
    for p in reversed(points):
        flag_cells = "".join(f"<td>{p['flag_counts'].get(name, 0)}</td>" for name in all_flag_names)
        rows.append(
            f'<tr><td class="mono">{esc(p["label"])}</td>'
            f'<td class="mono" style="color:{model_colors[p["model"]]}">{esc(p["model"])}</td>'
            f'<td class="mono dim">{esc(p["scenario_name"])}</td>'
            f'<td>{p["clean_pct"]}%</td><td>{p["total_turns"]}</td>{flag_cells}'
            f'<td class="mono dim">{esc(p["source_file"])}</td></tr>'
        )

    style_block = """
  :root { --bg: #0D1117; --panel: #161B22; --border: #30363D; --text: #E6EDF3; --dim: #8B949E; --accent: #58A6FF; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; padding: 32px 24px 64px; }
  .mono { font-family: "SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace; }
  .dim { color: var(--dim); }
  header { max-width: 900px; margin: 0 auto 24px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--dim); margin: 0 0 12px; }
  .subtitle { color: var(--dim); font-size: 13px; }
  .chart-card, .table-card, .regression-card { max-width: 900px; margin: 0 auto 24px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; }
  .regression-card { border-color: #F85149; }
  .regression-card.clean { border-color: var(--border); }
  .regression-list { margin: 0; padding-left: 18px; font-size: 13px; color: #F85149; }
  .regression-list li { margin-bottom: 4px; }
  .regression-list li.alert-high { color: #F85149; }
  .regression-list li.alert-moderate { color: #D29922; }
  .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 10px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--dim); }
  .legend-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th { color: var(--dim); text-transform: uppercase; font-size: 10px; letter-spacing: 0.05em; }
"""

    regressions = detect_regressions(historical_summaries)
    if regressions:
        # Real, updated 2026-08-28: alerts are now structured dicts
        # (type/message/severity), not plain strings -- render each
        # with a severity-based class so high vs. moderate is visually
        # distinct, matching real triage priority.
        regression_items = "".join(
            f'<li class="alert-{esc(a["severity"])}">{esc(a["message"])}</li>' for a in regressions
        )
        regression_html = (
            '<div class="regression-card"><h2>Regression check</h2>'
            f'<ul class="regression-list">{regression_items}</ul></div>'
        )
    else:
        regression_html = (
            '<div class="regression-card clean"><h2>Regression check</h2>'
            '<p class="dim">No regressions detected in available history.</p></div>'
        )

    html_doc = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>Stability Trends</title><style>' + style_block + '</style></head><body>'
        '<header><h1>Ticker LoRA Stability Trends</h1>'
        '<div class="subtitle">' + str(n) + ' historical run(s) loaded across '
        + str(len(models)) + ' model(s)</div></header>'
        + regression_html +
        '<div class="chart-card"><h2>Clean rate over time, by model</h2>' + clean_svg
        + '<div class="legend">' + clean_legend + '</div></div>'
        '<div class="chart-card"><h2>Flag counts over time</h2>' + flag_svg
        + '<div class="legend">' + flag_legend + '</div></div>'
        '<div class="table-card"><h2>Runs (most recent first)</h2>'
        '<table><thead><tr><th>When</th><th>Model</th><th>Scenario</th><th>Clean %</th><th>Turns</th>'
        + header_cells + '<th>Source</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table></div>'
        '</body></html>'
    )

    with open(output_path, "w") as f:
        f.write(html_doc)





def print_capabilities_overview() -> None:
    """Real, added 2026-08-30 (Explore_new_harness_capability): prints
    a real, organized overview of this harness's real modes, grouped
    by real purpose -- built after directly confirming the default
    argparse --help output had genuinely grown large (52 real flags,
    ~330 lines) across this session's real feature growth, with no
    grouping at all, making it genuinely hard to scan for "which flag
    do I actually need." This is a real, curated summary, not a
    generated one -- kept manually in sync with the real 15 top-level
    dispatch modes in main(), each with a real, one-line description
    of what it actually does, not aspirational or planned capability.
    """
    sections = [
        ("Run scenarios & suites", [
            ("--sequence", "Run a single scenario against one model (the base mode)."),
            ("--suite PATH", "Run every scenario in a suite file against one model."),
            ("--multi-model-suite PATH", "Run a suite across several real models."),
            ("--cross-model", "Run one scenario across several real models, side by side."),
            ("--auto-suite-tags TAGS", "Build and run a suite on the fly from scenario_tags."),
            ("--fuzz", "Generate real, grounded ticker-substitution variants and run them."),
            ("  --parallel", "(modifier on --suite/--multi-model-suite) shard/parallelize real work."),
        ]),
        ("Historical trends & prioritization", [
            ("--trends", "Per-scenario trend report + regression detection over time."),
            ("--summary-trend", "Suite-level health trend across saved summaries."),
            ("--summary-trend-html PATH", "Multi-suite trend comparison as a real HTML report."),
            ("--rank-scenarios", "Rank real scenarios by historical failure rate."),
            ("--weight-scenarios", "Rank scenarios by a real, dynamic weight (history + tags)."),
        ]),
        ("Live monitoring & alerting", [
            ("--serve-dashboard", "Real, live, auto-refreshing local HTML dashboard."),
            ("  --health-summary", "(modifier) synthesize overview/outcomes/regressions after a run."),
            ("  --gate", "(modifier) exit non-zero if real health thresholds fail."),
            ("  --notify-on-regression", "(modifier) desktop/webhook push alert on a real regression."),
        ]),
        ("Debugging a specific anomaly (capture -> replay -> analyze)", [
            ("--capture-check FLAG", "Reproduce and save the raw SSE events for a real check firing."),
            ("--replay PATH", "Reconstruct a readable, round-by-round transcript from a capture."),
            ("--compare-check FLAG", "Real occurrence-rate comparison for a check across models."),
            ("--analyze-captures FLAG", "Honest, sample-size-aware feature analysis across captures."),
        ]),
    ]

    print("Ticker LoRA Stability Harness -- real capabilities overview\n")
    for title, modes in sections:
        print(f"{title}:")
        for flag, desc in modes:
            print(f"  {flag:<28} {desc}")
        print()
    print("Run --help for the complete, real flag reference (every flag, every option).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-modes", action="store_true",
                         help="Real, added 2026-08-30 (Explore_new_"
                              "harness_capability): print a real, "
                              "organized overview of this harness's "
                              "15 real top-level modes, grouped by "
                              "purpose, instead of the default, flat, "
                              "~330-line argparse --help listing -- "
                              "built after directly checking that the "
                              "default help output had genuinely grown "
                              "large and hard to scan across this "
                              "session's real feature growth (52 real "
                              "flags, 15 real modes, confirmed by "
                              "direct count before building this). "
                              "Ignores every other real flag and exits "
                              "immediately.")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--endpoint-id", default="77bddaa5")
    parser.add_argument("--model", default="ticker-lookup-lora")
    parser.add_argument("--sequence", action="store_true",
                         help="Run the multi-round conversation sequence test "
                              "instead of independent single-question trials.")
    parser.add_argument("--sequence-runs", type=int, default=1,
                         help="Number of independent sequence runs (each its "
                              "own fresh session) when --sequence is used.")
    parser.add_argument("--save-results", metavar="PATH",
                         help="Write the full, structured multi-round suite "
                              "result as JSON to PATH, for historical "
                              "comparison across sessions/nights. Save into "
                              "scripts/results/ (e.g. scripts/results/"
                              "$(date +%%Y%%m%%d_%%H%%M%%S).json) so --trends "
                              "can find it later -- that directory is "
                              "gitignored for its *.json contents, real "
                              "session data is never committed.")
    parser.add_argument("--scenario-file", metavar="PATH",
                         help="Real scenario JSON file to run when --sequence "
                              "is used (see scripts/scenarios/*.json for real "
                              "examples). Defaults to "
                              "scenarios/mixed_holdings_default.json.")
    parser.add_argument("--suite", metavar="PATH",
                         help="Real suite JSON file grouping several "
                              "scenarios into one themed run (see "
                              "scripts/suites/*.json for real examples: "
                              "holdings_correction, generalization, "
                              "prompt_reliability, full). Runs every "
                              "scenario in the suite and aggregates suite-"
                              "level totals, while keeping each scenario's "
                              "own summary available too.")
    parser.add_argument("--runs-per-scenario", type=int, default=1,
                         help="Number of independent runs per scenario "
                              "when --suite or --multi-model-suite is used.")
    parser.add_argument("--auto-suite-tags", metavar="TAG1,TAG2,...",
                         help="Build a real suite on the fly from every "
                              "real scenario file matching these "
                              "scenario_tags, and run it -- no suite JSON "
                              "file needed. See --auto-suite-match for "
                              "how multiple tags combine.")
    parser.add_argument("--auto-suite-match", default="any", choices=["any", "all"],
                         help="With --auto-suite-tags: 'any' (default) "
                              "includes a scenario with at least one "
                              "matching tag; 'all' requires every given "
                              "tag to be present.")
    parser.add_argument("--save-suite-file", metavar="PATH",
                         help="With --auto-suite-tags, also write the "
                              "real, auto-generated suite definition to "
                              "PATH as real, loadable suite JSON -- so it "
                              "can be inspected, tweaked, or reused later "
                              "via the normal --suite/--multi-model-suite "
                              "flags.")
    parser.add_argument("--health-summary", action="store_true",
                         help="With --suite or --multi-model-suite, also "
                              "print a real, single, synthesized health "
                              "summary at the end -- overview, real "
                              "outcomes/flag breakdown, real regressions "
                              "(if --results-dir has history for this "
                              "model/suite), and real per-model "
                              "comparison for multi-model runs. Combines "
                              "several existing, separate real reports "
                              "into one, rather than requiring them to be "
                              "run and cross-referenced by hand.")
    parser.add_argument("--save-summary", metavar="PATH",
                         help="With --health-summary, also persist the "
                              "real, computed health summary itself (not "
                              "just the raw --save-results output) to "
                              "PATH as real JSON -- a genuine, timestamped "
                              "snapshot including whatever real regression "
                              "context existed at generation time, which "
                              "regenerating the summary later against a "
                              "since-grown history would NOT reproduce. "
                              "Save into scripts/summaries/ (e.g. "
                              "scripts/summaries/$(date +%%Y%%m%%d_%%H%%M%%S)"
                              ".json) so --summary-trend can find it -- "
                              "that directory is gitignored for its *.json "
                              "contents, matching scripts/results/.")
    parser.add_argument("--health-summary-html", metavar="PATH",
                         help="With --health-summary, also write a real, "
                              "self-contained HTML report to PATH -- the "
                              "visual counterpart to the console output, "
                              "reusing this harness's existing dark-"
                              "terminal report style. Automatically "
                              "includes a real failure-rate-over-time "
                              "chart if --summaries-dir has prior saved "
                              "summaries for this suite.")
    parser.add_argument("--summary-trend", action="store_true",
                         help="Load every saved health summary under "
                              "--summaries-dir (default scripts/"
                              "summaries/) and print how a suite's real, "
                              "suite-level outcome has changed over time "
                              "-- genuinely different from --trends, which "
                              "operates on raw per-scenario data; this "
                              "operates on the suite-level aggregate a "
                              "health summary already computed and "
                              "persisted. Combine with --suite-name-filter "
                              "to scope to one real suite.")
    parser.add_argument("--summaries-dir", metavar="PATH", default=SUMMARIES_DIR,
                         help="Directory to load/save health summaries "
                              "from/to. Defaults to scripts/summaries/.")
    parser.add_argument("--suite-name-filter", metavar="NAME",
                         help="With --summary-trend, only show entries "
                              "for this real suite name.")
    parser.add_argument("--replay", metavar="PATH",
                         help="Real, saved capture bundle (from "
                              "--capture-check, e.g. under "
                              "scripts/captures/) -- reconstructs and "
                              "prints a clear, round-by-round transcript "
                              "from the real, raw saved event data.")
    parser.add_argument("--holdings-integrity-report", metavar="PATH",
                         help="Real, saved capture bundle -- renders a "
                              "real, self-contained HTML report of "
                              "check_bundle_holdings_integrity()'s own "
                              "per-turn detail (grounded vs fabricated "
                              "numbers, real vs non-real holdings) for "
                              "every turn in that capture, regardless "
                              "of which check the capture was "
                              "originally made for. Requires "
                              "--output-html.")
    parser.add_argument("--output-html", metavar="PATH",
                         help="With --holdings-integrity-report, real "
                              "file path to write the generated HTML "
                              "report to.")
    parser.add_argument("--capture-check", metavar="FLAG_NAME",
                         help="Real, live debugging tool: repeatedly "
                              "runs --scenario-file (or the default "
                              "scenario) against --model until a real "
                              "turn's own FLAG_NAME check fires True "
                              "(e.g. tool_argument_echo, has_repeat, "
                              "has_leaked_tag -- any real boolean "
                              "check_trial()/run_sequence() computes), "
                              "then saves the complete raw SSE event "
                              "stream to scripts/captures/ for later "
                              "--replay. Real live generation is "
                              "non-deterministic -- may take several "
                              "real attempts, or may not reproduce at "
                              "all within --max-capture-attempts.")
    parser.add_argument("--max-capture-attempts", type=int, default=10,
                         help="With --capture-check, real number of "
                              "attempts before giving up honestly. "
                              "Default 10. Also used as the real attempt "
                              "ceiling for --accumulate-check -- with a "
                              "real --target-count above the default, "
                              "explicitly raise this too (e.g. "
                              "--max-capture-attempts 100), or "
                              "--target-count real occurrences will "
                              "likely never be reached.")
    parser.add_argument("--compare-check", metavar="FLAG_NAME",
                         help="Real, live investigative tool: runs "
                              "--scenario-file (or the default scenario) "
                              "--attempts-per-model real times per real "
                              "model, computes a real occurrence rate "
                              "per model for FLAG_NAME (e.g. "
                              "tool_argument_echo), and reports whether "
                              "the real rates differ across models "
                              "(model-specific) or are similar "
                              "(likely a shared/systemic cause). "
                              "Combine with --models.")
    parser.add_argument("--attempts-per-model", type=int, default=5,
                         help="With --compare-check, real number of "
                              "attempts per real model. Default 5.")
    parser.add_argument("--accumulate-check", metavar="FLAG_NAME",
                         help="Real, long-running dataset-building tool: "
                              "keeps running real attempts, saving EVERY "
                              "real occurrence found (not just the "
                              "first), until --target-count occurrences "
                              "are captured or --max-capture-attempts is "
                              "exhausted. Built for statistical "
                              "characterization (n=1 -> n=20+), not "
                              "single-instance root-cause investigation "
                              "-- see --capture-check for that.")
    parser.add_argument("--target-count", type=int, default=20,
                         help="With --accumulate-check, real number of "
                              "occurrences to collect. Default 20.")
    parser.add_argument("--analyze-captures", metavar="FLAG_NAME",
                         help="Real, honest pattern-observation tool "
                              "(not statistical clustering, which is "
                              "meaningless at this harness's real, "
                              "current sample sizes): extracts real "
                              "features from every saved capture for "
                              "FLAG_NAME under scripts/captures/ and "
                              "reports them, including whether a real, "
                              "specific hypothesis (a stale tool "
                              "argument matching the immediately "
                              "preceding turn's own real symbol) holds "
                              "across whatever real captures exist.")
    parser.add_argument("--summary-trend-html", metavar="PATH",
                         help="With --summary-trend, also write a real, "
                              "self-contained HTML report comparing "
                              "EVERY real saved suite's failure-rate "
                              "trend side by side, one colored line per "
                              "suite -- the genuine gap left after "
                              "--summary-trend's own console output, "
                              "which shows one suite at a time (or all "
                              "of them mixed into one flat, confusing "
                              "list). --suite-name-filter, if given, is "
                              "ignored for this specific output -- the "
                              "whole point is comparing every real "
                              "suite, not one.")
    parser.add_argument("--serve-dashboard", action="store_true",
                         help="Start a real, live, auto-refreshing local "
                              "HTTP server (127.0.0.1 only, stdlib "
                              "http.server, no new dependency) showing "
                              "the most recently saved health summary -- "
                              "genuinely different from every other "
                              "report in this harness, which are static "
                              "files: this one updates on its own as new "
                              "summaries are saved via --save-summary. "
                              "Blocks until Ctrl+C.")
    parser.add_argument("--dashboard-port", type=int, default=8765,
                         help="Port for --serve-dashboard. Default 8765.")
    parser.add_argument("--refresh-seconds", type=int, default=5,
                         help="Auto-refresh interval in seconds for "
                              "--serve-dashboard. Default 5.")
    parser.add_argument("--notify-on-regression", action="store_true",
                         help="With --health-summary, send a real push "
                              "notification if regressions are actually "
                              "detected (no notification when there "
                              "aren't any -- this is an alert, not a "
                              "status ping). See --notify-method.")
    parser.add_argument("--notify-method", default="desktop", choices=["desktop", "webhook"],
                         help="'desktop' (default) uses notify-send -- "
                              "only works when this harness runs "
                              "directly on the host (confirmed NOT "
                              "available inside the Odysseus container, "
                              "no D-Bus session there). 'webhook' POSTs "
                              "real JSON to --webhook-url and works from "
                              "either environment.")
    parser.add_argument("--webhook-url", metavar="URL",
                         help="Real webhook URL for --notify-method "
                              "webhook (e.g. a Slack incoming webhook, "
                              "ntfy.sh topic, or a custom endpoint). No "
                              "default -- this harness has no "
                              "confirmed, hard-coded credentials for any "
                              "specific real service.")
    parser.add_argument("--gate", action="store_true",
                         help="With --health-summary, evaluate the real "
                              "computed summary against real, explicit "
                              "thresholds (see --max-failure-rate, "
                              "--max-regression-severity, "
                              "--block-on-any-regression) and exit with "
                              "a real, non-zero status code if it fails "
                              "-- a standard, composable Unix gate any "
                              "real external process (a script, a git "
                              "hook, a CI system) can build a real "
                              "promotion/deployment decision on top of. "
                              "This harness does not run or claim to run "
                              "that process itself.")
    parser.add_argument("--max-failure-rate", type=int, metavar="PCT",
                         help="With --gate, fail if the real failure "
                              "rate exceeds this percentage.")
    parser.add_argument("--max-regression-severity", choices=["moderate", "high"],
                         help="With --gate, fail if any real regression "
                              "at or above this severity was detected.")
    parser.add_argument("--block-on-any-regression", action="store_true",
                         help="With --gate, fail if ANY real regression "
                              "was detected, regardless of severity.")
    parser.add_argument("--parallel", action="store_true",
                         help="With --suite, shards the suite's real "
                              "scenarios across concurrent workers for "
                              "one model (real, measured ~32%% wall-"
                              "clock reduction in one real test). With "
                              "--multi-model-suite, run each real "
                              "model's suite CONCURRENTLY (a real "
                              "ThreadPoolExecutor) instead of "
                              "sequentially. Real, honest caveat: "
                              "measured directly against the real "
                              "backend (single-GPU Ollama), this "
                              "produced a real ~38%% wall-clock speedup "
                              "in one real test, not a full ~2x -- some "
                              "real backend contention exists, but "
                              "genuine partial concurrency still helps. "
                              "Real per-model streaming console output "
                              "is suppressed during parallel runs (would "
                              "interleave into unreadable garbage from "
                              "multiple threads printing at once); only "
                              "the final combined comparison prints.")
    parser.add_argument("--max-workers", type=int, metavar="N",
                         help="With --parallel, cap the number of real "
                              "concurrent model requests. Defaults to "
                              "the real number of models being run.")
    parser.add_argument("--multi-model-suite", metavar="PATH",
                         help="Real suite JSON file with its own real "
                              "'models' field (see scripts/suites/"
                              "naming_collision_before_after.json and "
                              "full_model_comparison.json for real "
                              "examples) -- runs every scenario in the "
                              "suite against every model, printing a "
                              "direct comparison. The genuine combination "
                              "of --suite (many scenarios, one model) and "
                              "--cross-model (one scenario, many models). "
                              "Combine with --models to override the "
                              "suite file's own model list.")
    parser.add_argument("--cross-model", action="store_true",
                         help="Run the same real scenario (--scenario-file, "
                              "or the default) against several real models "
                              "in one command and print a direct, side-by-"
                              "side comparison. See DEFAULT_CROSS_MODEL_LIST "
                              "for the real default models compared "
                              "(includes both the current, fixed ticker "
                              "model and its original, pre-rename name, "
                              "still subject to the real naming-collision "
                              "bug -- a direct way to see that fix's real "
                              "effect).")
    parser.add_argument("--models", metavar="MODEL1,MODEL2,...",
                         help="Comma-separated real model names to compare. "
                              "Used by --cross-model (defaults to "
                              "DEFAULT_CROSS_MODEL_LIST if omitted), "
                              "--multi-model-suite (overrides the suite "
                              "file's own 'models' field), and, real, "
                              "added 2026-08-28: --weight-scenarios (no "
                              "default -- required to trigger the real, "
                              "per-model weight comparison mode; without "
                              "it, --weight-scenarios uses its original "
                              "single-model/--rank-all-models ranking).")
    parser.add_argument("--fuzz", action="store_true",
                         help="Generate real, grounded scenario variants "
                              "from --scenario-file (ticker substitution "
                              "from a real pool, not random symbols) and "
                              "run each one live, once each. See "
                              "MUTATION_TICKER_POOLS for real pool names.")
    parser.add_argument("--fuzz-pool", default="in_training",
                         choices=["in_training", "holdings", "held_out", "synthetic"],
                         help="Real ticker pool to substitute from when "
                              "--fuzz is used. Defaults to in_training.")
    parser.add_argument("--fuzz-count", type=int, default=5,
                         help="Number of real variants to generate and run "
                              "when --fuzz is used.")
    parser.add_argument("--fuzz-seed", type=int,
                         help="Real seed for reproducible fuzzing -- same "
                              "seed regenerates the exact same variants. "
                              "Omit for a real, non-reproducible run "
                              "(printed after the run so it can be reused).")
    parser.add_argument("--html-report", metavar="PATH",
                         help="Write a real, richer, human-readable HTML "
                              "report to PATH (self-contained, opens in any "
                              "browser) -- see generate_html_report().")
    parser.add_argument("--trends", action="store_true",
                         help="Load every saved --save-results JSON file "
                              "under --results-dir (default: scripts/"
                              "results/) and render a trend report showing "
                              "clean-rate and flag-count history over time. "
                              "Does not run any new trials -- combine with "
                              "--save-results pointed into that directory "
                              "over multiple real runs to build up real "
                              "trend data first.")
    parser.add_argument("--results-dir", metavar="PATH", default=RESULTS_DIR,
                         help="Directory to load historical results from "
                              "when --trends is used. Defaults to "
                              "scripts/results/.")
    parser.add_argument("--alerts-only", action="store_true",
                         help="With --trends, print only regression alerts "
                              "(no historical-run-count line, no HTML report "
                              "requirement) -- for quick checks, e.g. in a "
                              "cron job or before a deploy.")
    parser.add_argument("--rank-scenarios", action="store_true",
                         help="Load every saved result under --results-dir "
                              "and rank real scenarios by their historical "
                              "failure rate, worst first -- for prioritizing "
                              "which scenarios to run first under a time "
                              "constraint. Scoped to --model by default "
                              "(a real, deliberate choice -- the same "
                              "scenario's failure rate can swing wildly by "
                              "model, confirmed directly via --cross-model "
                              "earlier the same night); pass "
                              "--rank-all-models to aggregate across every "
                              "model instead, with the same real caveat.")
    parser.add_argument("--rank-all-models", action="store_true",
                         help="With --rank-scenarios, aggregate across "
                              "every model in the historical data instead "
                              "of scoping to --model. Real, honest caveat: "
                              "this can produce a misleading ranking if "
                              "different models were tested unevenly.")
    parser.add_argument("--weight-scenarios", action="store_true",
                         help="Compute a real, dynamic weight for every "
                              "real scenario file under scripts/scenarios/ "
                              "-- combining historical failure rate, "
                              "contamination risk, and tool-error risk "
                              "(from --results-dir) with each scenario's "
                              "own real, declared scenario_tags (a "
                              "generalization/synthetic/hallucination-risk "
                              "tag adds real weight even with zero history) "
                              "-- and print them ranked, highest weight "
                              "first. Scoped to --model by default, same "
                              "real reasoning as --rank-scenarios; pass "
                              "--rank-all-models to aggregate across every "
                              "model instead.")
    parser.add_argument("--recent-n", type=int, metavar="N",
                         help="With --weight-scenarios, only count each "
                              "scenario's N most recent real historical "
                              "runs (by real timestamp) toward its weight, "
                              "instead of all-time history -- so a "
                              "scenario that used to fail a lot but has "
                              "been clean recently correctly shows lower "
                              "weight, and one that used to be clean but "
                              "has started failing recently correctly "
                              "shows higher weight, reflecting current "
                              "reality rather than a diluted all-time "
                              "average. Omit for the original, all-time "
                              "behavior.")
    args = parser.parse_args()

    if args.list_modes:
        print_capabilities_overview()
        return

    if args.weight_scenarios and args.models:
        # Real, added 2026-08-28 (model_specific_weighting): a genuinely
        # different mode from the block below -- compares each real
        # scenario's weight ACROSS several real models side by side,
        # rather than a single model's ranking across scenarios.
        historical = load_historical_results(args.results_dir)
        models = [m.strip() for m in args.models.split(",")]
        recency_label = f"last {args.recent_n} run(s)" if args.recent_n else "all-time history"
        scenario_files = sorted(f for f in os.listdir(SCENARIOS_DIR) if f.endswith(".json"))
        print(f"Per-model scenario weight comparison [{len(historical)} historical file(s), "
              f"recency: {recency_label}]:\n")
        header = f"{'Scenario':<32} " + " ".join(f"{m[:16]:>16}" for m in models)
        print(header)
        print("-" * len(header))
        for fname in scenario_files:
            scenario = load_scenario(os.path.join(SCENARIOS_DIR, fname))
            weights = compute_multi_model_scenario_weights(scenario, historical, models, recent_n=args.recent_n)
            row = f"{scenario['name']:<32} " + " ".join(f"{weights[m]['total']:>16}" for m in models)
            print(row)
        return

    if args.weight_scenarios:
        historical = load_historical_results(args.results_dir)
        scope_model = None if args.rank_all_models else args.model
        scope_label = "all models (real caveat: may be misleading if models were tested unevenly)" if scope_model is None else scope_model
        recency_label = f"last {args.recent_n} run(s)" if args.recent_n else "all-time history"
        scenario_files = sorted(f for f in os.listdir(SCENARIOS_DIR) if f.endswith(".json"))
        weighted = []
        for fname in scenario_files:
            scenario = load_scenario(os.path.join(SCENARIOS_DIR, fname))
            w = compute_scenario_weight(scenario, historical, model=scope_model, recent_n=args.recent_n)
            weighted.append((scenario["name"], w))
        weighted.sort(key=lambda item: item[1]["total"], reverse=True)
        print(f"Scenario weight ranking [{len(historical)} historical file(s), scope: {scope_label}, "
              f"recency: {recency_label}]:\n")
        print(f"{'Scenario':<32} {'Weight':>7} {'Fail':>6} {'Contam':>7} {'ToolErr':>8} {'Gen':>5} {'Runs':>5}")
        print("-" * 74)
        for name, w in weighted:
            print(f"{name:<32} {w['total']:>7} {w['failure_rate']:>6} {w['contamination_risk']:>7} "
                  f"{w['tool_error_risk']:>8} {w['generalization_risk']:>5} {w['runs_seen']:>5}")
        return

    if args.serve_dashboard:
        serve_health_dashboard(port=args.dashboard_port, summaries_dir=args.summaries_dir,
                                refresh_seconds=args.refresh_seconds)
        return

    if args.replay:
        with open(args.replay) as f:
            bundle = json.load(f)
        print(render_replay_transcript(bundle))
        return

    if args.holdings_integrity_report:
        with open(args.holdings_integrity_report) as f:
            bundle = json.load(f)
        if not args.output_html:
            print("--holdings-integrity-report requires --output-html PATH.")
            return
        generate_holdings_integrity_report(bundle, output_path=args.output_html)
        print(f"Holdings integrity report written to {args.output_html}")
        return

    if args.analyze_captures:
        result = analyze_captured_echoes(target_check=args.analyze_captures)
        print(f"Real capture analysis for '{args.analyze_captures}' "
              f"[{result['sample_size']} real sample(s)]:\n")
        print(result["note"])
        if result["features"]:
            print()
            for feat in result["features"]:
                print(f"--- {feat['_source_file']} ---")
                for k, v in feat.items():
                    if k != "_source_file":
                        print(f"  {k}: {v}")
        return

    if args.compare_check:
        models = [m.strip() for m in args.models.split(",")] if args.models else DEFAULT_CROSS_MODEL_LIST
        scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        print(f"Comparing check '{args.compare_check}' across {len(models)} model(s) "
              f"[scenario: {scenario['name']}], {args.attempts_per_model} real attempt(s) each...")
        compare_check_across_models(args.endpoint_id, models, scenario,
                                     target_check=args.compare_check,
                                     attempts_per_model=args.attempts_per_model)
        return

    if args.accumulate_check:
        scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        print(f"Accumulating up to {args.target_count} real occurrence(s) of "
              f"'{args.accumulate_check}' [scenario: {scenario['name']}] against {args.model} "
              f"(up to {args.max_capture_attempts} real attempt(s))...")
        paths = accumulate_captures_for_check(
            args.endpoint_id, args.model, scenario, CAPTURES_DIR,
            target_check=args.accumulate_check, target_count=args.target_count,
            max_attempts=args.max_capture_attempts,
        )
        print(f"\nCollected {len(paths)} real occurrence(s).")
        return

    if args.capture_check:
        scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        print(f"Capturing raw events for check '{args.capture_check}' "
              f"[scenario: {scenario['name']}] against {args.model} "
              f"(up to {args.max_capture_attempts} real attempt(s))...")
        result_path = capture_raw_events_for_check(
            args.endpoint_id, args.model, scenario, CAPTURES_DIR,
            target_check=args.capture_check, max_attempts=args.max_capture_attempts,
        )
        if result_path:
            print(f"\nReplay it with: --replay {result_path}")
        return

    if args.summary_trend:
        summaries = load_historical_summaries(args.summaries_dir)
        trend = summarize_suite_trend(summaries, suite_name=args.suite_name_filter)
        scope_label = args.suite_name_filter or "all suites"
        print(f"Suite health trend [{len(summaries)} saved summary file(s), scope: {scope_label}]:\n")
        if not trend:
            print("No saved summaries found for this scope.")
        else:
            print(f"{'When':<18} {'Suite':<28} {'Failure %':>10} {'Turns':>7} {'Regressions':>12}")
            print("-" * 78)
            for entry in trend:
                print(f"{entry['label']:<18} {entry['suite_name']:<28} {entry['failure_rate']:>9}% "
                      f"{entry['total_turns']:>7} {entry['regression_count']:>12}")
        if args.summary_trend_html:
            grouped = compare_suite_trends(summaries)
            generate_multi_suite_trend_html_report(grouped, output_path=args.summary_trend_html)
            print(f"\nMulti-suite trend comparison written to {args.summary_trend_html}")
        return

    if args.rank_scenarios:
        historical = load_historical_results(args.results_dir)
        scope_model = None if args.rank_all_models else args.model
        ranked = rank_scenarios_by_failure_rate(historical, model=scope_model)
        scope_label = "all models (real caveat: may be misleading if models were tested unevenly)" if scope_model is None else scope_model
        print(f"Scenario failure-rate ranking [{len(historical)} historical file(s), scope: {scope_label}]:\n")
        if not ranked:
            print("No historical data found for this scope.")
        else:
            print(f"{'Scenario':<32} {'Failure %':>10} {'Turns':>7} {'Runs':>6}")
            print("-" * 58)
            for r in ranked:
                print(f"{r['scenario_name']:<32} {r['failure_rate']:>9}% {r['total_turns']:>7} {r['runs_seen']:>6}")
        return

    if args.fuzz:
        base_scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        # Real, deliberate: generate our own real seed if none was given,
        # so every fuzz run is reproducible after the fact, not just when
        # the user happened to think to pass one in advance.
        seed = args.fuzz_seed if args.fuzz_seed is not None else random.randint(0, 2**31 - 1)
        print(f"Fuzzing {args.fuzz_count} variant(s) of '{base_scenario['name']}' "
              f"[pool: {args.fuzz_pool}, seed: {seed}] against {args.model}...")
        fuzz_summary = run_fuzz(args.endpoint_id, args.model, base_scenario,
                                 pool_name=args.fuzz_pool, count=args.fuzz_count, seed=seed)
        if args.save_results:
            with open(args.save_results, "w") as f:
                json.dump(fuzz_summary, f, indent=2)
            print(f"\nFull fuzz results written to {args.save_results}")
        return

    if args.cross_model:
        models = [m.strip() for m in args.models.split(",")] if args.models else DEFAULT_CROSS_MODEL_LIST
        scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        print(f"Running cross-model comparison [scenario: {scenario['name']}] "
              f"across {len(models)} model(s): {', '.join(models)}...")
        comparison = run_cross_model(args.endpoint_id, models, scenario=scenario,
                                      runs_per_model=args.runs_per_scenario)
        if args.save_results:
            for model, summary in comparison["results"].items():
                safe_model = model.replace(":", "_").replace("/", "_")
                out_path = args.save_results.replace(".json", f"_{safe_model}.json")
                with open(out_path, "w") as f:
                    json.dump(summary, f, indent=2)
                print(f"\n{model} results written to {out_path}")
        return

    if args.auto_suite_tags:
        tags = [t.strip() for t in args.auto_suite_tags.split(",") if t.strip()]
        suite = generate_suite_from_tags(tags, match_mode=args.auto_suite_match)
        print(f"Auto-generated suite '{suite['name']}' ({len(suite['_resolved_scenario_paths'])} "
              f"scenario(s), tags={tags}, match={args.auto_suite_match})")
        if args.save_suite_file:
            to_save = {k: v for k, v in suite.items() if k != "_resolved_scenario_paths"}
            with open(args.save_suite_file, "w") as f:
                json.dump(to_save, f, indent=2)
            print(f"Suite definition written to {args.save_suite_file}")
        print(f"\nRunning against {args.model} (endpoint {args.endpoint_id})...")
        suite_summary = run_suite(args.endpoint_id, args.model, suite, args.runs_per_scenario)
        if args.save_results:
            with open(args.save_results, "w") as f:
                json.dump(suite_summary, f, indent=2)
            print(f"\nFull suite results written to {args.save_results}")
        if args.health_summary:
            historical = load_historical_results(args.results_dir) if os.path.isdir(args.results_dir) else None
            health = generate_suite_health_summary(suite_summary, historical_summaries=historical)
            _print_health_summary(health)
            if args.notify_on_regression and "regressions" in health:
                sent = notify_regressions(health["regressions"], method=args.notify_method,
                                           webhook_url=args.webhook_url, suite_name=health["overview"]["suite_name"])
                if sent:
                    print(f"\nRegression notification sent via {args.notify_method}.")
            if args.save_summary:
                with open(args.save_summary, "w") as f:
                    json.dump(health, f, indent=2)
                print(f"\nHealth summary written to {args.save_summary}")
            if args.health_summary_html:
                past_summaries = load_historical_summaries(args.summaries_dir)
                trend = summarize_suite_trend(past_summaries, suite_name=health["overview"]["suite_name"])
                generate_health_summary_html_report(health, trend=trend, output_path=args.health_summary_html)
                print(f"Health summary HTML report written to {args.health_summary_html}")
            if args.gate:
                gate_result = evaluate_health_gate(
                    health, max_failure_rate=args.max_failure_rate,
                    max_regression_severity=args.max_regression_severity,
                    block_on_any_regression=args.block_on_any_regression,
                )
                print(f"\nGate: {'PASSED' if gate_result['passed'] else 'FAILED'}")
                for reason in gate_result["reasons"]:
                    print(f"  - {reason}")
                if not gate_result["passed"]:
                    sys.exit(1)
        return

    if args.multi_model_suite:
        suite = load_suite(args.multi_model_suite)
        models = [m.strip() for m in args.models.split(",")] if args.models else suite.get("models")
        if not models:
            print(f"Suite '{suite['name']}' has no real 'models' field and --models "
                  f"wasn't given -- nowhere to get a model list from.")
            return
        print(f"Running multi-model suite '{suite['name']}' ({len(suite['_resolved_scenario_paths'])} "
              f"scenario(s)) across {len(models)} model(s): {', '.join(models)}"
              f"{' [parallel]' if args.parallel else ''}...")
        if args.parallel:
            mm_summary = run_multi_model_suite_parallel(args.endpoint_id, models, suite,
                                                          args.runs_per_scenario, max_workers=args.max_workers)
        else:
            mm_summary = run_multi_model_suite(args.endpoint_id, models, suite, args.runs_per_scenario)
        if args.save_results:
            for model, summary in mm_summary["results"].items():
                safe_model = model.replace(":", "_").replace("/", "_")
                out_path = args.save_results.replace(".json", f"_{safe_model}.json")
                with open(out_path, "w") as f:
                    json.dump(summary, f, indent=2)
                print(f"\n{model} results written to {out_path}")
        if args.health_summary:
            historical = load_historical_results(args.results_dir) if os.path.isdir(args.results_dir) else None
            health = generate_suite_health_summary(mm_summary, historical_summaries=historical)
            _print_health_summary(health)
            if args.notify_on_regression and "regressions" in health:
                sent = notify_regressions(health["regressions"], method=args.notify_method,
                                           webhook_url=args.webhook_url, suite_name=health["overview"]["suite_name"])
                if sent:
                    print(f"\nRegression notification sent via {args.notify_method}.")
            if args.save_summary:
                with open(args.save_summary, "w") as f:
                    json.dump(health, f, indent=2)
                print(f"\nHealth summary written to {args.save_summary}")
            if args.health_summary_html:
                past_summaries = load_historical_summaries(args.summaries_dir)
                trend = summarize_suite_trend(past_summaries, suite_name=health["overview"]["suite_name"])
                generate_health_summary_html_report(health, trend=trend, output_path=args.health_summary_html)
                print(f"Health summary HTML report written to {args.health_summary_html}")
            if args.gate:
                gate_result = evaluate_health_gate(
                    health, max_failure_rate=args.max_failure_rate,
                    max_regression_severity=args.max_regression_severity,
                    block_on_any_regression=args.block_on_any_regression,
                )
                print(f"\nGate: {'PASSED' if gate_result['passed'] else 'FAILED'}")
                for reason in gate_result["reasons"]:
                    print(f"  - {reason}")
                if not gate_result["passed"]:
                    sys.exit(1)
        return

    if args.suite:
        suite = load_suite(args.suite)
        print(f"Running suite '{suite['name']}' ({len(suite['_resolved_scenario_paths'])} "
              f"scenario(s), {args.runs_per_scenario} run(s) each) against {args.model} "
              f"(endpoint {args.endpoint_id}){' [sharded]' if args.parallel else ''}...")
        if args.parallel:
            suite_summary = run_suite_parallel(args.endpoint_id, args.model, suite,
                                                args.runs_per_scenario, max_workers=args.max_workers)
        else:
            suite_summary = run_suite(args.endpoint_id, args.model, suite, args.runs_per_scenario)
        if args.save_results:
            with open(args.save_results, "w") as f:
                json.dump(suite_summary, f, indent=2)
            print(f"\nFull suite results written to {args.save_results}")
        if args.health_summary:
            historical = load_historical_results(args.results_dir) if os.path.isdir(args.results_dir) else None
            health = generate_suite_health_summary(suite_summary, historical_summaries=historical)
            _print_health_summary(health)
            if args.notify_on_regression and "regressions" in health:
                sent = notify_regressions(health["regressions"], method=args.notify_method,
                                           webhook_url=args.webhook_url, suite_name=health["overview"]["suite_name"])
                if sent:
                    print(f"\nRegression notification sent via {args.notify_method}.")
            if args.save_summary:
                with open(args.save_summary, "w") as f:
                    json.dump(health, f, indent=2)
                print(f"\nHealth summary written to {args.save_summary}")
            if args.health_summary_html:
                past_summaries = load_historical_summaries(args.summaries_dir)
                trend = summarize_suite_trend(past_summaries, suite_name=health["overview"]["suite_name"])
                generate_health_summary_html_report(health, trend=trend, output_path=args.health_summary_html)
                print(f"Health summary HTML report written to {args.health_summary_html}")
            if args.gate:
                gate_result = evaluate_health_gate(
                    health, max_failure_rate=args.max_failure_rate,
                    max_regression_severity=args.max_regression_severity,
                    block_on_any_regression=args.block_on_any_regression,
                )
                print(f"\nGate: {'PASSED' if gate_result['passed'] else 'FAILED'}")
                for reason in gate_result["reasons"]:
                    print(f"  - {reason}")
                if not gate_result["passed"]:
                    sys.exit(1)
        return

    if args.trends:
        historical = load_historical_results(args.results_dir)
        regressions = detect_regressions(historical)

        if args.alerts_only:
            for a in regressions:
                print(f"[{a['severity'].upper()}] {a['message']}")
            return

        print(f"Loaded {len(historical)} historical result file(s) from {args.results_dir}")
        if regressions:
            print("\nRegression check:")
            for a in regressions:
                print(f"  - [{a['severity']}] {a['message']}")
        else:
            print("\nRegression check: no regressions detected in available history.")
        if not args.html_report:
            print("--html-report PATH is required with --trends -- nowhere to write the report.")
            return
        generate_trend_report(historical, args.html_report)
        print(f"\nTrend report written to {args.html_report}")
        return

    if args.sequence:
        scenario = load_scenario(args.scenario_file) if args.scenario_file else load_scenario(DEFAULT_SCENARIO_FILE)
        print(f"Running {args.sequence_runs} real multi-round conversation "
              f"sequence(s) [scenario: {scenario['name']}] against {args.model} "
              f"(endpoint {args.endpoint_id})...\n")
        summary = run_multi_round_suite(args.endpoint_id, args.model, args.sequence_runs, scenario=scenario)
        if args.save_results:
            with open(args.save_results, "w") as f:
                json.dump(summary, f, indent=2)
            print(f"\nFull results written to {args.save_results}")
        if args.html_report:
            generate_html_report(summary, args.html_report)
            print(f"HTML report written to {args.html_report}")
        return

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    print(f"Running {len(tickers)} real, independent trials against {args.model} "
          f"(endpoint {args.endpoint_id})...\n")

    results = []
    for ticker in tickers:
        print(f"  {ticker}...", end=" ", flush=True)
        try:
            r = run_trial(ticker, args.endpoint_id, args.model)
        except Exception as e:
            r = {"ticker": ticker, "error": str(e)}
        results.append(r)
        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            is_clean, flags = _classify_turn(r)
            print("CLEAN" if is_clean else " ".join(flags))

    print("\n=== Summary ===")
    n = len(results)
    clean = sum(1 for r in results if "error" not in r and not any([
        r["has_leaked_tag"], r["has_repeat"], r["is_empty"], not r["made_tool_call"]
    ]))
    leaked = sum(1 for r in results if r.get("has_leaked_tag"))
    repeated = sum(1 for r in results if r.get("has_repeat"))
    empty = sum(1 for r in results if r.get("is_empty"))
    no_tool = sum(1 for r in results if "error" not in r and not r.get("made_tool_call"))
    errors = sum(1 for r in results if "error" in r)

    print(f"Total trials: {n}")
    print(f"Completely clean: {clean}/{n}")
    print(f"Leaked tag fragments: {leaked}")
    print(f"Repeated/duplicated content: {repeated}")
    print(f"Empty responses: {empty}")
    print(f"No tool call made: {no_tool}")
    print(f"Request errors: {errors}")

    return results


if __name__ == "__main__":
    main()
