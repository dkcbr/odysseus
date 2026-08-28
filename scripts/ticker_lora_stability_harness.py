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
    night this harness was built."""
    content_parts = []
    tool_calls = []
    for e in events:
        if "delta" in e and not e.get("thinking"):
            content_parts.append(e["delta"])
        elif e.get("type") == "tool_start":
            tool_calls.append(e.get("tool"))

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

    return {
        "tool_calls": tool_calls,
        "content_preview": full_content[:150],
        "has_leaked_tag": has_leaked_tag,
        "has_repeat": has_repeat,
        "is_empty": is_empty,
        "made_tool_call": bool(tool_calls),
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
      - "expectation_violated": bool -- True only if this turn declared
        a real expect_tool_call and the observed behavior contradicted
        it. A turn with no expectation set never reports a violation
        here, regardless of whether it made a tool call.
      - "expectation": the turn's own expect_tool_call value, or None
        if it didn't declare one (purely observational turn).
    Generic issues (leaked tags, repeats, empty responses) are already
    covered by check_trial()/​_classify_turn() and are NOT re-checked
    here -- this function is specifically about per-turn, scenario-
    declared expectations, a genuinely different, narrower concern.
    """
    expectation = turn.get("expect_tool_call")
    if expectation is None:
        return {"expectation_violated": False, "expectation": None}
    violated = bool(expectation) != bool(result.get("made_tool_call"))
    return {"expectation_violated": violated, "expectation": expectation}


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
        else:
            message = f"Whats {turn['symbol']} trading at right now?"
        events = send_message(session_id, message, model)
        r = check_trial(events)
        r["prompt"] = message
        r["is_followup"] = (turn["type"] == "followup")
        r.update(validate_turn(turn, r))
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
                              "comparison across sessions/nights.")
    parser.add_argument("--scenario-file", metavar="PATH",
                         help="Real scenario JSON file to run when --sequence "
                              "is used (see scripts/scenarios/*.json for real "
                              "examples). Defaults to "
                              "scenarios/mixed_holdings_default.json.")
    parser.add_argument("--html-report", metavar="PATH",
                         help="Write a real, richer, human-readable HTML "
                              "report to PATH (self-contained, opens in any "
                              "browser) -- see generate_html_report().")
    args = parser.parse_args()

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
