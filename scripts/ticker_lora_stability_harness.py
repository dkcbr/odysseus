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
import re
import time
import urllib.request
import urllib.parse

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


def send_message(session_id: str, message: str, model: str, mode: str = "agent") -> list:
    """Send a real chat message, return the parsed list of SSE event dicts."""
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
        except json.JSONDecodeError:
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





def main():
    parser = argparse.ArgumentParser()
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
    args = parser.parse_args()

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
