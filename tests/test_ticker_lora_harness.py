"""
Real, deterministic unit tests for scripts/ticker_lora_stability_harness.py's
pure logic (contamination detection, turn classification, expectation
validation, and scenario file loading/validation) -- everything in that
script that doesn't require a live model, a live Ollama instance, or a
live Odysseus session.

Real, honest scope note: the harness's own network-dependent functions
(create_session, send_message, run_trial, run_sequence,
run_multi_round_suite) are deliberately NOT tested here. This repo has no
self-hosted GitHub Actions runner (confirmed directly via the GitHub API
on 2026-08-28: zero runners registered), and the ticker LoRA is a local,
custom fine-tune that only exists on the machine it was trained on --
standard, cloud-hosted CI runners have no way to reach a live Ollama
instance or this specific model. Running the harness's real, live,
multi-round tests against the actual model remains a manual step (see
scripts/ticker_lora_stability_harness.py's own module docstring for
usage), same as it has been throughout the session this harness was
built in. This test file covers what genuinely CAN run automatically on
every push: the harness's own deterministic, network-free logic, which
is exactly the part most prone to silent regression (as the false
positives found and fixed live on 2026-08-28 -- the holdings-note
template match, and the sector/exchange boilerplate match -- both
demonstrate real).
"""
import importlib.util
import json
import os
import inspect
import tempfile
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS_PATH = os.path.join(ROOT, "scripts", "ticker_lora_stability_harness.py")

_spec = importlib.util.spec_from_file_location("ticker_lora_stability_harness", HARNESS_PATH)
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)


# ---------------------------------------------------------------------------
# _cross_turn_contamination
# ---------------------------------------------------------------------------

def test_cross_turn_contamination_detects_genuine_echo():
    """A later turn verbatim-repeating a substantial chunk of an earlier
    turn's own free-text content is real contamination and must be caught."""
    earlier = (
        "RGTI is Rigetti Computing, Inc. Current price: $15.89, down 3.35% "
        "today, trading strongly on momentum this week amid continued "
        "quantum computing sector interest."
    )
    later = "As I mentioned, " + earlier
    result = _harness._cross_turn_contamination([earlier, later])
    assert result == [(1, 0)]


def test_cross_turn_contamination_ignores_holdings_note_template():
    """Real, fixed 2026-08-28: the deterministic holdings-correction note
    template is expected, by design, to repeat similar boilerplate phrasing
    across different tickers -- must never be flagged as contamination."""
    turn_a = (
        "SOUN is currently trading at $7.07 USD, down 2.08% today.\n\n"
        "(Note: the stored reference document lists 50 shares of SOUN. "
        "This document may not reflect the most recent trades -- ask for "
        "live verification if this matters for a real decision.)"
    )
    turn_b = (
        "IONQ is currently trading at $38.65, down 8.97% today.\n\n"
        "(Note: the stored reference document lists 0 shares of IONQ, with "
        "a separate, unexecuted pending buy order for 1 more. This document "
        "may not reflect the most recent trades -- ask for live "
        "verification if this matters for a real decision.)"
    )
    assert _harness._cross_turn_contamination([turn_a, turn_b]) == []


def test_cross_turn_contamination_ignores_shared_classification_boilerplate():
    """Real, fixed 2026-08-28: two different, correct answers can
    legitimately share a long sector/exchange classification phrase --
    must never be flagged as contamination."""
    turn_a = (
        "SOUN (SoundHound AI, Inc.) is trading at $7.06 right now, down "
        "2.22% today (Technology, Software - Application, NASDAQ Global "
        "Market)."
    )
    turn_b = (
        "IONQ is trading at $15.86, up 1.34% today (Technology, Software - "
        "Application, NASDAQ Global Market)."
    )
    assert _harness._cross_turn_contamination([turn_a, turn_b]) == []


def test_cross_turn_contamination_ignores_short_content():
    """Turns under the real minimum length threshold are never compared,
    real short answers shouldn't trip a coincidental short-string match."""
    assert _harness._cross_turn_contamination(["short one", "short two"]) == []


# ---------------------------------------------------------------------------
# _classify_turn
# ---------------------------------------------------------------------------

def test_classify_turn_clean():
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is True
    assert flags == []


def test_classify_turn_flags_leaked_tag_repeat_and_empty():
    r = {"has_leaked_tag": True, "has_repeat": True, "is_empty": True,
         "made_tool_call": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is False
    assert set(flags) == {"LEAKED_TAG", "REPEATED", "EMPTY"}


def test_classify_turn_followup_exception_for_legacy_no_tool_call():
    """Real, single-question-mode fallback path: a followup turn with no
    declared expectation (no 'expectation_violated' key at all) should not
    be flagged for lacking a tool call."""
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": False, "is_followup": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is True
    assert flags == []


def test_classify_turn_no_tool_call_flagged_without_followup_or_expectation():
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": False}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is False
    assert flags == ["NO_TOOL_CALL"]


def test_classify_turn_uses_real_expectation_violation_when_present():
    """Real, added 2026-08-28: when a turn went through validate_turn(),
    that real, explicit result takes precedence over the older heuristic."""
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": False, "expectation_violated": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is False
    assert flags == ["EXPECTATION_VIOLATED"]


def test_classify_turn_unasserted_expectation_never_flags_tool_call():
    """Real, added 2026-08-28: a turn with a declared-but-satisfied (False)
    expectation_violated must never be flagged for its tool-call behavior,
    even if made_tool_call is False -- this is the exact mechanism the
    held-out generalization scenario relies on."""
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": False, "expectation_violated": False}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is True
    assert flags == []


# ---------------------------------------------------------------------------
# validate_turn
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expect,made_call,expected_violation", [
    (True, False, True),
    (True, True, False),
    (False, True, True),
    (False, False, False),
])
def test_validate_turn_all_real_combinations(expect, made_call, expected_violation):
    turn = {"type": "ticker", "symbol": "SOUN", "expect_tool_call": expect}
    result = {"made_tool_call": made_call}
    v = _harness.validate_turn(turn, result)
    assert v["expectation_violated"] is expected_violation
    assert v["expectation"] is expect


def test_validate_turn_no_expectation_declared():
    turn = {"type": "ticker", "symbol": "AAPL"}
    result = {"made_tool_call": False}
    v = _harness.validate_turn(turn, result)
    assert v["expectation_violated"] is False
    assert v["expectation"] is None


# ---------------------------------------------------------------------------
# load_scenario
# ---------------------------------------------------------------------------

def _write_scenario(tmp_path, data):
    path = os.path.join(tmp_path, "scenario.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def test_load_scenario_valid_default_file():
    """Real, actual default scenario file this session built -- must stay
    loadable and valid."""
    scenario = _harness.load_scenario(_harness.DEFAULT_SCENARIO_FILE)
    assert scenario["name"]
    assert scenario["turns"]
    for turn in scenario["turns"]:
        assert turn["type"] in ("ticker", "followup")


def test_load_scenario_all_shipped_scenario_files_are_valid():
    """Real, all three scenario files built this session must all load and
    validate cleanly -- a real regression guard against a future edit
    accidentally breaking one of them."""
    scenarios_dir = os.path.join(ROOT, "scripts", "scenarios")
    files = [f for f in os.listdir(scenarios_dir) if f.endswith(".json")]
    assert len(files) >= 3
    for fname in files:
        scenario = _harness.load_scenario(os.path.join(scenarios_dir, fname))
        assert scenario["turns"]


def test_load_scenario_rejects_missing_turns(tmp_path):
    path = _write_scenario(tmp_path, {"name": "bad"})
    with pytest.raises(ValueError, match="turns"):
        _harness.load_scenario(path)


def test_load_scenario_rejects_unknown_turn_type(tmp_path):
    path = _write_scenario(tmp_path, {"name": "bad", "turns": [{"type": "bogus"}]})
    with pytest.raises(ValueError, match="unknown type"):
        _harness.load_scenario(path)


def test_load_scenario_rejects_ticker_turn_without_symbol(tmp_path):
    path = _write_scenario(tmp_path, {"name": "bad", "turns": [{"type": "ticker"}]})
    with pytest.raises(ValueError, match="symbol"):
        _harness.load_scenario(path)


def test_load_scenario_rejects_followup_turn_without_message(tmp_path):
    path = _write_scenario(tmp_path, {"name": "bad", "turns": [{"type": "followup"}]})
    with pytest.raises(ValueError, match="message"):
        _harness.load_scenario(path)


def test_load_scenario_rejects_non_boolean_expect_tool_call(tmp_path):
    path = _write_scenario(tmp_path, {
        "name": "bad",
        "turns": [{"type": "ticker", "symbol": "SOUN", "expect_tool_call": "yes"}],
    })
    with pytest.raises(ValueError, match="boolean"):
        _harness.load_scenario(path)


def test_load_scenario_accepts_omitted_expect_tool_call(tmp_path):
    """Real, deliberate design: omitting expect_tool_call entirely must be
    valid -- it's the mechanism the held-out generalization scenario uses
    to mark a turn as observational rather than asserted."""
    path = _write_scenario(tmp_path, {
        "name": "ok",
        "turns": [{"type": "ticker", "symbol": "AAPL"}],
    })
    scenario = _harness.load_scenario(path)
    assert "expect_tool_call" not in scenario["turns"][0]


# ---------------------------------------------------------------------------
# check_trial
# ---------------------------------------------------------------------------

def test_check_trial_detects_leaked_tag():
    events = [{"delta": "</tool_call>"}]
    r = _harness.check_trial(events)
    assert r["has_leaked_tag"] is True


def test_check_trial_detects_repeat():
    text = "SOUN trades at $7.24 right now, up 0.26% today, with a 3.15B market cap."
    events = [{"delta": text + text}]
    r = _harness.check_trial(events)
    assert r["has_repeat"] is True


def test_check_trial_detects_empty():
    events = [{"delta": "   "}]
    r = _harness.check_trial(events)
    assert r["is_empty"] is True


def test_check_trial_detects_tool_call():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker"},
        {"delta": "RGTI is Rigetti Computing, Inc."},
    ]
    r = _harness.check_trial(events)
    assert r["made_tool_call"] is True
    assert r["tool_calls"] == ["lookup_ticker"]


def test_check_trial_clean_real_looking_response():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker"},
        {"delta": "RGTI is Rigetti Computing, Inc. Current price: $15.89, down 3.35% today."},
    ]
    r = _harness.check_trial(events)
    assert r["has_leaked_tag"] is False
    assert r["has_repeat"] is False
    assert r["is_empty"] is False
    assert r["made_tool_call"] is True


# ---------------------------------------------------------------------------
# check_trial -- real tool-error detection (added 2026-08-28, alongside the
# tool-call-reliability extensions: TOOL_ERROR classification, expect_tool
# validation, and cross-run regression detection)
# ---------------------------------------------------------------------------

def test_check_trial_detects_tool_error_on_nonzero_exit_code():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker"},
        {"type": "tool_output", "tool": "lookup_ticker", "exit_code": 1},
    ]
    r = _harness.check_trial(events)
    assert r["has_tool_error"] is True
    assert r["tool_errors"] == [{"tool": "lookup_ticker", "exit_code": 1}]


def test_check_trial_no_tool_error_on_zero_exit_code():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker"},
        {"type": "tool_output", "tool": "lookup_ticker", "exit_code": 0},
        {"delta": "RGTI is Rigetti Computing, Inc. Current price: $15.89."},
    ]
    r = _harness.check_trial(events)
    assert r["has_tool_error"] is False
    assert r["tool_errors"] == []


def test_classify_turn_flags_tool_error():
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": True, "has_tool_error": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is False
    assert "TOOL_ERROR" in flags


# ---------------------------------------------------------------------------
# validate_turn -- real expect_tool (specific tool name) checking
# ---------------------------------------------------------------------------

def test_validate_turn_expect_tool_satisfied():
    turn = {"type": "ticker", "symbol": "SOUN", "expect_tool": "lookup_ticker"}
    result = {"tool_calls": ["lookup_ticker"], "made_tool_call": True}
    v = _harness.validate_turn(turn, result)
    assert v["expectation_violated"] is False
    assert v["expected_tool_missing"] is False


def test_validate_turn_expect_tool_violated_wrong_tool_called():
    """Real, added 2026-08-28: catches a genuinely different failure shape
    from expect_tool_call alone -- a tool WAS called (so expect_tool_call:
    true would be satisfied), just not the specific, correct one."""
    turn = {"type": "ticker", "symbol": "SOUN", "expect_tool": "lookup_ticker"}
    result = {"tool_calls": ["ask_user"], "made_tool_call": True}
    v = _harness.validate_turn(turn, result)
    assert v["expectation_violated"] is True
    assert v["expected_tool_missing"] is True


def test_validate_turn_expect_tool_violated_no_tool_at_all():
    turn = {"type": "ticker", "symbol": "SOUN", "expect_tool": "lookup_ticker"}
    result = {"tool_calls": [], "made_tool_call": False}
    v = _harness.validate_turn(turn, result)
    assert v["expectation_violated"] is True
    assert v["expected_tool_missing"] is True


def test_load_scenario_rejects_non_string_expect_tool(tmp_path):
    path = _write_scenario(tmp_path, {
        "name": "bad",
        "turns": [{"type": "ticker", "symbol": "SOUN", "expect_tool": 123}],
    })
    with pytest.raises(ValueError, match="tool name string"):
        _harness.load_scenario(path)


def test_load_scenario_updated_rapid_holdings_stress_has_expect_tool():
    """Real, actual shipped scenario file this session updated -- must
    still load cleanly with the new field."""
    path = os.path.join(ROOT, "scripts", "scenarios", "rapid_holdings_stress.json")
    scenario = _harness.load_scenario(path)
    ticker_turns = [t for t in scenario["turns"] if t["type"] == "ticker"]
    assert all(t.get("expect_tool") == "lookup_ticker" for t in ticker_turns)


# ---------------------------------------------------------------------------
# detect_regressions
# ---------------------------------------------------------------------------

def test_detect_regressions_flags_clean_rate_drop():
    """Real, updated 2026-08-28: alerts are now structured dicts
    (type/message/severity), not plain strings."""
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 2, "flag_counts": {}, "total_cross_turn_contamination": 0},
    ]
    alerts = _harness.detect_regressions(historical)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "clean_rate_regression"
    assert "dropped" in alerts[0]["message"]
    assert alerts[0]["severity"] in ("high", "moderate")


def test_detect_regressions_severity_scales_with_drop_size():
    """Real, added in the 2026-08-28 redesign: a small drop is
    moderate, a large drop is high -- distinct real triage priority."""
    small_drop = [
        {"model": "m1", "timestamp": 1000, "total_turns": 10, "clean_turns": 10, "flag_counts": {}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 10, "clean_turns": 8, "flag_counts": {}, "total_cross_turn_contamination": 0},
    ]
    large_drop = [
        {"model": "m1", "timestamp": 1000, "total_turns": 10, "clean_turns": 10, "flag_counts": {}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 10, "clean_turns": 3, "flag_counts": {}, "total_cross_turn_contamination": 0},
    ]
    assert _harness.detect_regressions(small_drop)[0]["severity"] == "moderate"
    assert _harness.detect_regressions(large_drop)[0]["severity"] == "high"


def test_detect_regressions_uses_median_of_prior_runs():
    """Real, added in the 2026-08-28 redesign: comparison is against the
    median of ALL prior runs, not just the immediately preceding one --
    a single noisy prior run shouldn't set a false baseline."""
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 10, "clean_turns": 10, "flag_counts": {}, "total_cross_turn_contamination": 0},  # 100%
        {"model": "m1", "timestamp": 2000, "total_turns": 10, "clean_turns": 3, "flag_counts": {}, "total_cross_turn_contamination": 0},   # 30%, one noisy outlier
        {"model": "m1", "timestamp": 3000, "total_turns": 10, "clean_turns": 9, "flag_counts": {}, "total_cross_turn_contamination": 0},   # 90%, real latest
    ]
    # Median of [100, 30] = 65; latest (90) is not a real regression
    # against that median, even though it's a big jump vs. the single
    # immediately-preceding noisy run.
    alerts = _harness.detect_regressions(historical)
    assert not any(a["type"] == "clean_rate_regression" for a in alerts)


def test_detect_regressions_flags_new_failure_type():
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {"EMPTY": 0}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 5, "flag_counts": {"EMPTY": 0, "TOOL_ERROR": 2}, "total_cross_turn_contamination": 0},
    ]
    alerts = _harness.detect_regressions(historical)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "new_flag_type"
    assert "TOOL_ERROR" in alerts[0]["message"]
    assert alerts[0]["severity"] == "high"


def test_detect_regressions_flags_increase_in_known_flag():
    """Real, added in the 2026-08-28 redesign -- the real gap the first
    version had: it only ever caught a flag's FIRST appearance, never a
    meaningful worsening of a flag already seen in prior runs."""
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 4, "flag_counts": {"EMPTY": 1}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 4, "flag_counts": {"EMPTY": 1}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 3000, "total_turns": 5, "clean_turns": 0, "flag_counts": {"EMPTY": 5}, "total_cross_turn_contamination": 0},
    ]
    alerts = _harness.detect_regressions(historical)
    increase_alerts = [a for a in alerts if a["type"] == "flag_count_increase"]
    assert len(increase_alerts) == 1
    assert "EMPTY" in increase_alerts[0]["message"]


def test_detect_regressions_no_increase_alert_for_stable_known_flag():
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 4, "flag_counts": {"EMPTY": 1}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 4, "flag_counts": {"EMPTY": 1}, "total_cross_turn_contamination": 0},
    ]
    alerts = _harness.detect_regressions(historical)
    assert not any(a["type"] == "flag_count_increase" for a in alerts)


def test_detect_regressions_flags_new_contamination():
    """Real, added in the 2026-08-28 redesign: cross-turn contamination
    is a real, existing field this function never checked before."""
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}, "total_cross_turn_contamination": 0},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}, "total_cross_turn_contamination": 2},
    ]
    alerts = _harness.detect_regressions(historical)
    assert len(alerts) == 1
    assert alerts[0]["type"] == "contamination_regression"
    assert alerts[0]["severity"] == "high"


def test_detect_regressions_no_contamination_alert_when_already_present():
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}, "total_cross_turn_contamination": 1},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}, "total_cross_turn_contamination": 1},
    ]
    alerts = _harness.detect_regressions(historical)
    assert not any(a["type"] == "contamination_regression" for a in alerts)


def test_detect_regressions_no_findings_when_stable():
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}},
        {"model": "m1", "timestamp": 2000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}},
    ]
    assert _harness.detect_regressions(historical) == []


def test_detect_regressions_scopes_comparison_per_model():
    """Real, deliberate design: comparing across different models would
    produce a meaningless 'regression' -- a genuinely worse-performing new
    model must never be reported as a regression of an unrelated,
    better-performing one."""
    historical = [
        {"model": "model-a", "timestamp": 1000, "total_turns": 5, "clean_turns": 5, "flag_counts": {}},
        {"model": "model-b", "timestamp": 2000, "total_turns": 5, "clean_turns": 0, "flag_counts": {}},
    ]
    assert _harness.detect_regressions(historical) == []


def test_detect_regressions_ignores_models_with_only_one_run():
    historical = [
        {"model": "m1", "timestamp": 1000, "total_turns": 5, "clean_turns": 1, "flag_counts": {}},
    ]
    assert _harness.detect_regressions(historical) == []


# ---------------------------------------------------------------------------
# check_trial -- real round-count drift detection (added 2026-08-28,
# session-reuse-specific contamination checks)
# ---------------------------------------------------------------------------

def test_check_trial_no_round_drift_on_normal_sequence():
    events = [{"type": "agent_step", "round": 2}, {"delta": "clean content"}]
    r = _harness.check_trial(events)
    assert r["has_round_drift"] is False
    assert r["rounds_seen"] == [2]


def test_check_trial_detects_round_drift_on_duplicate_round():
    events = [{"type": "agent_step", "round": 2}, {"type": "agent_step", "round": 2}]
    r = _harness.check_trial(events)
    assert r["has_round_drift"] is True


def test_check_trial_detects_round_drift_on_backwards_round():
    events = [{"type": "agent_step", "round": 3}, {"type": "agent_step", "round": 2}]
    r = _harness.check_trial(events)
    assert r["has_round_drift"] is True


def test_classify_turn_flags_round_drift():
    r = {"has_leaked_tag": False, "has_repeat": False, "is_empty": False,
         "made_tool_call": True, "has_round_drift": True}
    is_clean, flags = _harness._classify_turn(r)
    assert is_clean is False
    assert "ROUND_DRIFT" in flags


# ---------------------------------------------------------------------------
# _holdings_note_contamination
# ---------------------------------------------------------------------------

def test_holdings_note_contamination_matching_real_holding_is_clean():
    turn = {"type": "ticker", "symbol": "SOUN"}
    content = ("SOUN is trading at $7. (Note: the stored reference document "
               "lists 50 shares of SOUN. more text)")
    result = _harness._holdings_note_contamination(content, turn)
    assert result == {"wrong_ticker": False, "not_a_real_holding": False, "note_ticker": "SOUN"}


def test_holdings_note_contamination_detects_wrong_ticker():
    """Real, session-reuse contamination signal: the note refers to a
    different ticker than this turn actually asked about."""
    turn = {"type": "ticker", "symbol": "RGTI"}
    content = ("RGTI is trading at $15. (Note: the stored reference document "
               "lists 50 shares of SOUN. more text)")
    result = _harness._holdings_note_contamination(content, turn)
    assert result["wrong_ticker"] is True


def test_holdings_note_contamination_detects_not_a_real_holding():
    """Real, distinct data-accuracy signal: the ticker matches this turn,
    but isn't in DK's real, known holdings at all."""
    turn = {"type": "ticker", "symbol": "IONQ"}
    content = ("IONQ is trading at $38. (Note: the stored reference document "
               "lists 10 shares of IONQ. more text)")
    result = _harness._holdings_note_contamination(content, turn)
    assert result["not_a_real_holding"] is True
    assert result["wrong_ticker"] is False


def test_holdings_note_contamination_no_note_present():
    turn = {"type": "ticker", "symbol": "RGTI"}
    content = "RGTI is Rigetti Computing, Inc. Current price: $15.89."
    result = _harness._holdings_note_contamination(content, turn)
    assert result == {"wrong_ticker": False, "not_a_real_holding": False, "note_ticker": None}


# ---------------------------------------------------------------------------
# _tool_argument_echo
# ---------------------------------------------------------------------------

def test_tool_argument_echo_detects_stale_symbol():
    turn = {"type": "ticker", "symbol": "RGTI"}
    result = {"tool_call_commands": [
        {"tool": "lookup_ticker", "command": json.dumps({"symbol": "SOUN"})}
    ]}
    assert _harness._tool_argument_echo(turn, result) is True


def test_tool_argument_echo_no_echo_when_symbol_matches():
    turn = {"type": "ticker", "symbol": "RGTI"}
    result = {"tool_call_commands": [
        {"tool": "lookup_ticker", "command": json.dumps({"symbol": "RGTI"})}
    ]}
    assert _harness._tool_argument_echo(turn, result) is False


def test_tool_argument_echo_ignores_followup_turns():
    turn = {"type": "followup", "message": "what about that?"}
    result = {"tool_call_commands": [
        {"tool": "lookup_ticker", "command": json.dumps({"symbol": "SOUN"})}
    ]}
    assert _harness._tool_argument_echo(turn, result) is False


def test_tool_argument_echo_handles_no_tool_calls():
    turn = {"type": "ticker", "symbol": "RGTI"}
    result = {"tool_call_commands": []}
    assert _harness._tool_argument_echo(turn, result) is False


# ---------------------------------------------------------------------------
# validate_turn -- real expect_holdings_note (symmetric) checking, and
# load_scenario -- scenario_tags / expect_holdings_note validation
# (added 2026-08-28 as part of scenario expansion)
# ---------------------------------------------------------------------------

def test_validate_turn_expect_holdings_note_false_violated():
    turn = {"type": "ticker", "symbol": "IONQ", "expect_holdings_note": False}
    result = {"made_tool_call": True, "tool_calls": ["lookup_ticker"],
              "full_content": "IONQ is at $38. (Note: the stored reference document lists 5 shares of IONQ. more)"}
    v = _harness.validate_turn(turn, result)
    assert v["holdings_note_unexpected"] is True
    assert v["expectation_violated"] is True


def test_validate_turn_expect_holdings_note_false_satisfied():
    turn = {"type": "ticker", "symbol": "IONQ", "expect_holdings_note": False}
    result = {"made_tool_call": True, "tool_calls": ["lookup_ticker"],
              "full_content": "IONQ is at $38, up 2% today."}
    v = _harness.validate_turn(turn, result)
    assert v["holdings_note_unexpected"] is False
    assert v["expectation_violated"] is False


def test_validate_turn_expect_holdings_note_true_violated():
    """Real, symmetric case added 2026-08-28: catches the note silently
    NOT appearing for a ticker where it genuinely should."""
    turn = {"type": "ticker", "symbol": "KTOS", "expect_holdings_note": True}
    result = {"made_tool_call": True, "tool_calls": ["lookup_ticker"],
              "full_content": "KTOS is at $52."}
    v = _harness.validate_turn(turn, result)
    assert v["holdings_note_unexpected"] is True


def test_validate_turn_expect_holdings_note_true_satisfied():
    turn = {"type": "ticker", "symbol": "KTOS", "expect_holdings_note": True}
    result = {"made_tool_call": True, "tool_calls": ["lookup_ticker"],
              "full_content": "KTOS is at $52. (Note: the stored reference document lists 16 shares of KTOS. more)"}
    v = _harness.validate_turn(turn, result)
    assert v["holdings_note_unexpected"] is False


def test_load_scenario_rejects_non_list_scenario_tags(tmp_path):
    path = _write_scenario(tmp_path, {
        "name": "bad", "scenario_tags": "not-a-list",
        "turns": [{"type": "ticker", "symbol": "SOUN"}],
    })
    with pytest.raises(ValueError, match="scenario_tags"):
        _harness.load_scenario(path)


def test_load_scenario_rejects_non_boolean_expect_holdings_note(tmp_path):
    path = _write_scenario(tmp_path, {
        "name": "bad",
        "turns": [{"type": "ticker", "symbol": "SOUN", "expect_holdings_note": "yes"}],
    })
    with pytest.raises(ValueError, match="expect_holdings_note"):
        _harness.load_scenario(path)


def test_load_scenario_accepts_real_scenario_tags(tmp_path):
    path = _write_scenario(tmp_path, {
        "name": "ok", "scenario_tags": ["prompt-shape", "reliability"],
        "turns": [{"type": "ticker", "symbol": "SOUN"}],
    })
    scenario = _harness.load_scenario(path)
    assert scenario["scenario_tags"] == ["prompt-shape", "reliability"]


def test_load_scenario_all_shipped_scenario_files_including_new_ones_are_valid():
    """Real, updated 2026-08-28: now 6 real scenario files after scenario
    expansion -- must all still load and validate cleanly."""
    scenarios_dir = os.path.join(ROOT, "scripts", "scenarios")
    files = [f for f in os.listdir(scenarios_dir) if f.endswith(".json")]
    assert len(files) >= 6
    for fname in files:
        scenario = _harness.load_scenario(os.path.join(scenarios_dir, fname))
        assert scenario["turns"]


def test_run_sequence_ticker_turn_message_override_documented_in_schema():
    """Real, added 2026-08-28: confirms the shipped prompt_shape_variety
    scenario actually uses the new message-override mechanism, not just
    that the mechanism exists in isolation."""
    path = os.path.join(ROOT, "scripts", "scenarios", "prompt_shape_variety.json")
    scenario = _harness.load_scenario(path)
    ticker_turns = [t for t in scenario["turns"] if t["type"] == "ticker"]
    assert all("message" in t for t in ticker_turns)
    assert all(t["message"] != f"Whats {t['symbol']} trading at right now?" for t in ticker_turns)


# ---------------------------------------------------------------------------
# load_suite (added 2026-08-28 as part of scenario suites)
# ---------------------------------------------------------------------------

def test_load_suite_all_shipped_suite_files_are_valid():
    """Real, all 4 shipped suite files must load and resolve every
    referenced scenario cleanly -- a real regression guard against a
    future edit breaking a suite's own scenario references."""
    suites_dir = os.path.join(ROOT, "scripts", "suites")
    files = [f for f in os.listdir(suites_dir) if f.endswith(".json")]
    assert len(files) >= 4
    for fname in files:
        suite = _harness.load_suite(os.path.join(suites_dir, fname))
        assert suite["_resolved_scenario_paths"]
        for p in suite["_resolved_scenario_paths"]:
            assert os.path.isfile(p)


def test_load_suite_full_suite_covers_every_real_scenario_file():
    """Real, the 'full' suite is meant to be comprehensive -- must
    reference every real scenario file that actually exists, not a
    stale subset from before a scenario was added."""
    scenarios_dir = os.path.join(ROOT, "scripts", "scenarios")
    real_scenario_files = {f for f in os.listdir(scenarios_dir) if f.endswith(".json")}
    full_suite = _harness.load_suite(os.path.join(ROOT, "scripts", "suites", "full.json"))
    referenced = {os.path.basename(p) for p in full_suite["_resolved_scenario_paths"]}
    assert referenced == real_scenario_files


def test_load_suite_rejects_missing_scenarios_list(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "bad"}))
    with pytest.raises(ValueError, match="scenarios"):
        _harness.load_suite(str(path))


def test_load_suite_rejects_empty_scenarios_list(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "bad", "scenarios": []}))
    with pytest.raises(ValueError, match="scenarios"):
        _harness.load_suite(str(path))


def test_load_suite_fails_loudly_on_missing_scenario_file(tmp_path):
    """Real, deliberate design: a suite referencing a scenario that
    doesn't exist must fail at load time, not partway through a real,
    possibly expensive live run."""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"name": "bad", "scenarios": ["scenarios/does_not_exist.json"]}))
    with pytest.raises(ValueError, match="doesn't exist"):
        _harness.load_suite(str(path))


# ---------------------------------------------------------------------------
# DEFAULT_CROSS_MODEL_LIST (added 2026-08-28 as part of cross-model
# scenario comparison)
# ---------------------------------------------------------------------------

def test_default_cross_model_list_is_real_and_well_formed():
    assert isinstance(_harness.DEFAULT_CROSS_MODEL_LIST, list)
    assert len(_harness.DEFAULT_CROSS_MODEL_LIST) >= 2
    assert all(isinstance(m, str) and m for m in _harness.DEFAULT_CROSS_MODEL_LIST)


def test_default_cross_model_list_includes_both_ticker_lora_names():
    """Real, deliberate design: the default list includes both the
    current, fixed model name and its original, pre-rename name (still
    subject to the real naming-collision bug), specifically so a
    cross-model run can directly demonstrate that fix's real effect."""
    assert "ticker-lookup-lora" in _harness.DEFAULT_CROSS_MODEL_LIST
    assert "odysseus-qwen3-tickers-lora" in _harness.DEFAULT_CROSS_MODEL_LIST


# ---------------------------------------------------------------------------
# mutate_ticker_substitution (added 2026-08-28 as part of scenario mutation)
# ---------------------------------------------------------------------------

def _real_base_scenario_for_mutation():
    return _harness.load_scenario(
        os.path.join(ROOT, "scripts", "scenarios", "prompt_shape_variety.json")
    )


def test_mutate_ticker_substitution_is_reproducible_with_same_seed():
    base = _real_base_scenario_for_mutation()
    v1 = _harness.mutate_ticker_substitution(base, pool_name="in_training", count=3, seed=42)
    v2 = _harness.mutate_ticker_substitution(base, pool_name="in_training", count=3, seed=42)
    assert v1 == v2


def test_mutate_ticker_substitution_differs_with_different_seed():
    base = _real_base_scenario_for_mutation()
    v1 = _harness.mutate_ticker_substitution(base, pool_name="in_training", count=3, seed=1)
    v2 = _harness.mutate_ticker_substitution(base, pool_name="in_training", count=3, seed=2)
    assert v1 != v2


def test_mutate_ticker_substitution_updates_message_text_to_match_symbol():
    """Real, important correctness check: a message override containing
    the original ticker must have it replaced too, or the mutated
    prompt would ask about a different ticker than it names in text."""
    base = _real_base_scenario_for_mutation()
    variants = _harness.mutate_ticker_substitution(base, pool_name="in_training", count=5, seed=7)
    for variant in variants:
        for turn in variant["turns"]:
            if turn["type"] == "ticker" and "message" in turn:
                assert turn["symbol"] in turn["message"]


def test_mutate_ticker_substitution_rejects_unknown_pool():
    base = _real_base_scenario_for_mutation()
    with pytest.raises(ValueError, match="Unknown mutation ticker pool"):
        _harness.mutate_ticker_substitution(base, pool_name="not-a-real-pool", count=1)


def test_mutate_ticker_substitution_rejects_scenario_with_no_ticker_turns():
    base = {"name": "no_tickers", "turns": [{"type": "followup", "message": "hi"}]}
    with pytest.raises(ValueError, match="no real 'ticker' type turns"):
        _harness.mutate_ticker_substitution(base, count=1)


def test_mutate_ticker_substitution_produces_real_valid_scenarios():
    """Real, round-trip check: every generated variant must actually be
    a loadable, valid scenario per the real schema, not just a
    plausible-looking dict."""
    base = _real_base_scenario_for_mutation()
    variants = _harness.mutate_ticker_substitution(base, pool_name="holdings", count=2, seed=3)
    for variant in variants:
        path = None
        try:
            fd, path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(variant, f)
            loaded = _harness.load_scenario(path)
            assert loaded["turns"]
        finally:
            if path:
                os.remove(path)


def test_mutation_ticker_pools_are_real_and_grounded():
    """Real, deliberate design: every pool is grounded in real, already-
    verified symbols used elsewhere this session, not arbitrary strings."""
    assert _harness.MUTATION_TICKER_POOLS["in_training"] == _harness.IN_TRAINING_TICKERS
    assert _harness.MUTATION_TICKER_POOLS["holdings"] == sorted(_harness.REAL_DK_STOCK_HOLDINGS)
    assert set(_harness.MUTATION_TICKER_POOLS["synthetic"]).isdisjoint(_harness.IN_TRAINING_TICKERS)


# ---------------------------------------------------------------------------
# rank_scenarios_by_failure_rate (added 2026-08-28 as part of scenario
# prioritization)
# ---------------------------------------------------------------------------

def test_rank_scenarios_sorts_worst_first():
    historical = [
        {"model": "m1", "scenario_name": "clean_one", "total_turns": 10, "clean_turns": 10},
        {"model": "m1", "scenario_name": "bad_one", "total_turns": 10, "clean_turns": 2},
    ]
    ranked = _harness.rank_scenarios_by_failure_rate(historical, model="m1")
    assert [r["scenario_name"] for r in ranked] == ["bad_one", "clean_one"]
    assert ranked[0]["failure_rate"] == 80
    assert ranked[1]["failure_rate"] == 0


def test_rank_scenarios_handles_direct_suite_and_fuzz_shapes():
    """Real, the three genuinely different real summary shapes this
    harness can produce and load_historical_results() can load."""
    direct_run = {"model": "m1", "scenario_name": "a", "total_turns": 10, "clean_turns": 8}
    suite_run = {
        "model": "m1", "suite_name": "full",
        "scenario_results": [
            {"scenario_name": "b", "model": "m1", "total_turns": 5, "clean_turns": 1},
            {"scenario_name": "c", "model": "m1", "total_turns": 5, "clean_turns": 5},
        ],
    }
    fuzz_run = {"model": "m1", "fuzz_base_scenario": "a", "total_turns": 12, "clean_turns": 6}
    ranked = _harness.rank_scenarios_by_failure_rate([direct_run, suite_run, fuzz_run], model="m1")
    by_name = {r["scenario_name"]: r for r in ranked}
    assert by_name["a"]["total_turns"] == 22  # direct (10) + fuzz (12), correctly combined
    assert by_name["a"]["runs_seen"] == 2
    assert by_name["b"]["failure_rate"] == 80
    assert by_name["c"]["failure_rate"] == 0


def test_rank_scenarios_model_scoping_prevents_misleading_conflation():
    """Real, deliberate design: the same scenario's failure rate can
    swing wildly by model (confirmed directly via --cross-model earlier
    the same night) -- scoping by model must produce genuinely
    different, correct results, not an averaged, misleading one."""
    historical = [
        {"model": "good_model", "scenario_name": "x", "total_turns": 10, "clean_turns": 10},
        {"model": "bad_model", "scenario_name": "x", "total_turns": 10, "clean_turns": 0},
    ]
    ranked_good = _harness.rank_scenarios_by_failure_rate(historical, model="good_model")
    ranked_bad = _harness.rank_scenarios_by_failure_rate(historical, model="bad_model")
    ranked_all = _harness.rank_scenarios_by_failure_rate(historical)
    assert ranked_good[0]["failure_rate"] == 0
    assert ranked_bad[0]["failure_rate"] == 100
    assert ranked_all[0]["failure_rate"] == 50


def test_rank_scenarios_excludes_scenarios_with_no_data_in_scope():
    historical = [{"model": "other_model", "scenario_name": "x", "total_turns": 10, "clean_turns": 5}]
    ranked = _harness.rank_scenarios_by_failure_rate(historical, model="ticker-lookup-lora")
    assert ranked == []


def test_rank_scenarios_empty_history_returns_empty_list():
    assert _harness.rank_scenarios_by_failure_rate([]) == []


# ---------------------------------------------------------------------------
# Multi-model suites: load_suite's optional "models" field, and
# run_multi_model_suite (added 2026-08-28)
# ---------------------------------------------------------------------------

def test_load_suite_accepts_real_models_field():
    path = os.path.join(ROOT, "scripts", "suites", "naming_collision_before_after.json")
    suite = _harness.load_suite(path)
    assert suite["models"] == ["ticker-lookup-lora", "odysseus-qwen3-tickers-lora"]


def test_load_suite_models_field_is_optional():
    """Real, existing single-model suites (no 'models' field) must
    still load fine -- the field is additive, not required."""
    path = os.path.join(ROOT, "scripts", "suites", "holdings_correction.json")
    suite = _harness.load_suite(path)
    assert "models" not in suite


def test_load_suite_rejects_non_list_models_field(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "name": "bad", "models": "not-a-list",
        "scenarios": ["scenarios/mixed_holdings_default.json"],
    }))
    with pytest.raises(ValueError, match="models"):
        _harness.load_suite(str(path))


def test_load_suite_rejects_empty_models_list(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "name": "bad", "models": [],
        "scenarios": ["scenarios/mixed_holdings_default.json"],
    }))
    with pytest.raises(ValueError, match="models"):
        _harness.load_suite(str(path))


def test_both_real_multi_model_suite_files_are_valid():
    suites_dir = os.path.join(ROOT, "scripts", "suites")
    for fname in ["naming_collision_before_after.json", "full_model_comparison.json"]:
        suite = _harness.load_suite(os.path.join(suites_dir, fname))
        assert len(suite["models"]) >= 2
        assert suite["_resolved_scenario_paths"]


def test_run_multi_model_suite_rejects_empty_models():
    """Real, deliberate design: no arbitrary default model list to fall
    back to -- either an explicit models list or the suite's own real
    'models' field is required."""
    with pytest.raises(ValueError, match="models"):
        _harness.run_multi_model_suite("77bddaa5", [], {"name": "x", "_resolved_scenario_paths": []})


# ---------------------------------------------------------------------------
# compute_scenario_weight and _gather_scenario_history (added 2026-08-28
# as part of scenario weighting)
# ---------------------------------------------------------------------------

def test_compute_scenario_weight_no_history_no_risky_tags():
    scenario = {"name": "brand_new", "scenario_tags": ["reliability"]}
    w = _harness.compute_scenario_weight(scenario, [])
    assert w["total"] == 1.0
    assert w["runs_seen"] == 0


def test_compute_scenario_weight_generalization_tag_adds_weight_with_no_history():
    """Real, deliberate design: an intrinsic, design-time property (the
    scenario's own real tags) can raise weight even with zero
    historical runs -- a brand-new, harder scenario isn't underweighted
    just because it hasn't run yet."""
    scenario = {"name": "brand_new_gen", "scenario_tags": ["generalization"]}
    w = _harness.compute_scenario_weight(scenario, [])
    assert w["total"] == 2.0
    assert w["generalization_risk"] == 1.0


def test_compute_scenario_weight_combines_real_history_components():
    scenario = {"name": "risky", "scenario_tags": []}
    historical = [{
        "model": "m1", "scenario_name": "risky",
        "total_turns": 10, "clean_turns": 4,
        "flag_counts": {"TOOL_ERROR": 2},
        "total_cross_turn_contamination": 1,
    }]
    w = _harness.compute_scenario_weight(scenario, historical, model="m1")
    assert w["runs_seen"] == 1
    assert w["failure_rate"] == 0.6
    assert w["tool_error_risk"] == 0.2
    assert w["contamination_risk"] == 1.0
    assert w["total"] == round(1.0 + 0.6 + 1.0 + 0.2 + 0.0, 3)


def test_compute_scenario_weight_respects_model_scope():
    scenario = {"name": "risky", "scenario_tags": []}
    historical = [{
        "model": "m1", "scenario_name": "risky", "total_turns": 10, "clean_turns": 0,
    }]
    w_wrong_model = _harness.compute_scenario_weight(scenario, historical, model="different_model")
    assert w_wrong_model["runs_seen"] == 0
    assert w_wrong_model["total"] == 1.0


def test_compute_scenario_weight_contamination_and_tool_error_risk_are_capped():
    """Real, deliberate design: a scenario contaminating or erroring on
    literally every run is already maximally concerning -- further
    identical runs shouldn't inflate the component without bound."""
    scenario = {"name": "always_bad", "scenario_tags": []}
    historical = [{
        "model": "m1", "scenario_name": "always_bad",
        "total_turns": 2, "clean_turns": 0,
        "flag_counts": {"TOOL_ERROR": 2},
        "total_cross_turn_contamination": 5,
    }]
    w = _harness.compute_scenario_weight(scenario, historical, model="m1")
    assert w["contamination_risk"] == 1.0
    assert w["tool_error_risk"] == 1.0


def test_all_real_scenario_files_have_real_scenario_tags():
    """Real, added 2026-08-28 after a genuine gap was found and fixed
    during this same task: 3 of 6 real scenario files predated the
    scenario_tags convention and had none at all, silently
    underweighting them regardless of what they actually test. Guards
    against this happening again for any future scenario."""
    scenarios_dir = os.path.join(ROOT, "scripts", "scenarios")
    for fname in os.listdir(scenarios_dir):
        if not fname.endswith(".json"):
            continue
        scenario = _harness.load_scenario(os.path.join(scenarios_dir, fname))
        assert scenario.get("scenario_tags"), f"{fname} is missing real scenario_tags"


def test_rank_scenarios_by_failure_rate_unaffected_by_the_refactor():
    """Real, regression guard: rank_scenarios_by_failure_rate() was
    refactored to share _gather_scenario_history() with the new
    compute_scenario_weight() -- its own, already-established real
    behavior must be completely unchanged."""
    historical = [
        {"model": "m1", "scenario_name": "clean_one", "total_turns": 10, "clean_turns": 10},
        {"model": "m1", "scenario_name": "bad_one", "total_turns": 10, "clean_turns": 2},
    ]
    ranked = _harness.rank_scenarios_by_failure_rate(historical, model="m1")
    assert [r["scenario_name"] for r in ranked] == ["bad_one", "clean_one"]
    assert ranked[0]["failure_rate"] == 80


# ---------------------------------------------------------------------------
# scenario_weighting_extensions: recency-windowed weighting (added
# 2026-08-28)
# ---------------------------------------------------------------------------

def _real_history_with_old_bad_recent_good(scenario_name, model="m1"):
    now = time.time()
    return [
        {"model": model, "scenario_name": scenario_name, "timestamp": now - 30 * 86400, "total_turns": 10, "clean_turns": 0},
        {"model": model, "scenario_name": scenario_name, "timestamp": now - 29 * 86400, "total_turns": 10, "clean_turns": 1},
        {"model": model, "scenario_name": scenario_name, "timestamp": now - 28 * 86400, "total_turns": 10, "clean_turns": 0},
        {"model": model, "scenario_name": scenario_name, "timestamp": now - 2 * 86400, "total_turns": 10, "clean_turns": 10},
        {"model": model, "scenario_name": scenario_name, "timestamp": now - 1 * 86400, "total_turns": 10, "clean_turns": 10},
        {"model": model, "scenario_name": scenario_name, "timestamp": now, "total_turns": 10, "clean_turns": 10},
    ]


def test_gather_scenario_history_default_unchanged_by_recent_n_refactor():
    """Real, critical regression guard: _gather_scenario_history() was
    substantially restructured (entry-collect-then-window-then-
    aggregate instead of immediate accumulation) to support recency
    windowing -- the default (recent_n=None) behavior must be
    byte-identical to before the restructuring."""
    historical = [
        {"model": "m1", "scenario_name": "a", "total_turns": 10, "clean_turns": 8},
        {"model": "m1", "scenario_name": "a", "total_turns": 5, "clean_turns": 5},
    ]
    result = _harness._gather_scenario_history(historical, model="m1")
    assert result["a"]["total_turns"] == 15
    assert result["a"]["clean_turns"] == 13
    assert result["a"]["runs_seen"] == 2


def test_gather_scenario_history_recent_n_keeps_newest_first():
    now = time.time()
    historical = [
        {"model": "m1", "scenario_name": "a", "timestamp": now - 100, "total_turns": 10, "clean_turns": 0},
        {"model": "m1", "scenario_name": "a", "timestamp": now - 50, "total_turns": 10, "clean_turns": 5},
        {"model": "m1", "scenario_name": "a", "timestamp": now, "total_turns": 10, "clean_turns": 10},
    ]
    result = _harness._gather_scenario_history(historical, model="m1", recent_n=2)
    # Should keep only the 2 newest: timestamp now-50 (5 clean) and now (10 clean)
    assert result["a"]["total_turns"] == 20
    assert result["a"]["clean_turns"] == 15
    assert result["a"]["runs_seen"] == 2


def test_compute_scenario_weight_recent_n_shows_lower_weight_for_improving_scenario():
    scenario = {"name": "improving", "scenario_tags": []}
    historical = _real_history_with_old_bad_recent_good("improving")
    w_all_time = _harness.compute_scenario_weight(scenario, historical, model="m1")
    w_recent_3 = _harness.compute_scenario_weight(scenario, historical, model="m1", recent_n=3)
    assert w_recent_3["failure_rate"] == 0.0
    assert w_recent_3["total"] < w_all_time["total"]


def test_compute_scenario_weight_recent_n_shows_higher_weight_for_regressing_scenario():
    now = time.time()
    scenario = {"name": "regressing", "scenario_tags": []}
    historical = [
        {"model": "m1", "scenario_name": "regressing", "timestamp": now - 30 * 86400, "total_turns": 10, "clean_turns": 10},
        {"model": "m1", "scenario_name": "regressing", "timestamp": now - 29 * 86400, "total_turns": 10, "clean_turns": 10},
        {"model": "m1", "scenario_name": "regressing", "timestamp": now - 28 * 86400, "total_turns": 10, "clean_turns": 10},
        {"model": "m1", "scenario_name": "regressing", "timestamp": now - 2 * 86400, "total_turns": 10, "clean_turns": 0},
        {"model": "m1", "scenario_name": "regressing", "timestamp": now - 1 * 86400, "total_turns": 10, "clean_turns": 0},
        {"model": "m1", "scenario_name": "regressing", "timestamp": now, "total_turns": 10, "clean_turns": 0},
    ]
    w_all_time = _harness.compute_scenario_weight(scenario, historical, model="m1")
    w_recent_3 = _harness.compute_scenario_weight(scenario, historical, model="m1", recent_n=3)
    assert w_recent_3["failure_rate"] == 1.0
    assert w_recent_3["total"] > w_all_time["total"]


def test_compute_scenario_weight_recent_n_larger_than_history_uses_all_of_it():
    scenario = {"name": "a", "scenario_tags": []}
    historical = _real_history_with_old_bad_recent_good("a")
    w_recent_100 = _harness.compute_scenario_weight(scenario, historical, model="m1", recent_n=100)
    w_all_time = _harness.compute_scenario_weight(scenario, historical, model="m1")
    assert w_recent_100 == w_all_time


def test_rank_scenarios_by_failure_rate_still_uses_full_history_not_recent_n():
    """Real, deliberate design: rank_scenarios_by_failure_rate()'s own
    purpose is genuinely all-time failure rate -- it must never be
    silently affected by the recent_n mechanism added for the newer
    weight calculator."""
    historical = _real_history_with_old_bad_recent_good("a")
    ranked = _harness.rank_scenarios_by_failure_rate(historical, model="m1")
    # All 6 runs (1+1+1+10+10+10 clean out of 60) should be reflected,
    # not just the most recent ones.
    assert ranked[0]["runs_seen"] == 6


# ---------------------------------------------------------------------------
# generate_suite_from_tags (added 2026-08-28 as part of suite
# auto-generation)
# ---------------------------------------------------------------------------

def test_generate_suite_from_tags_matches_hand_curated_holdings_correction():
    """Real, direct correctness check against an already-shipped,
    hand-curated suite file -- the auto-generated result for the
    equivalent tag must match it exactly."""
    auto = _harness.generate_suite_from_tags(["holdings-correction"])
    hand = _harness.load_suite(os.path.join(ROOT, "scripts", "suites", "holdings_correction.json"))
    auto_names = sorted(os.path.basename(p) for p in auto["_resolved_scenario_paths"])
    hand_names = sorted(os.path.basename(p) for p in hand["_resolved_scenario_paths"])
    assert auto_names == hand_names


def test_generate_suite_from_tags_matches_hand_curated_generalization():
    auto = _harness.generate_suite_from_tags(["generalization"])
    hand = _harness.load_suite(os.path.join(ROOT, "scripts", "suites", "generalization.json"))
    auto_names = sorted(os.path.basename(p) for p in auto["_resolved_scenario_paths"])
    hand_names = sorted(os.path.basename(p) for p in hand["_resolved_scenario_paths"])
    assert auto_names == hand_names


def test_generate_suite_from_tags_any_mode_unions():
    auto = _harness.generate_suite_from_tags(
        ["holdings-correction", "generalization"], match_mode="any"
    )
    assert len(auto["_resolved_scenario_paths"]) == 5  # 3 holdings + 2 generalization


def test_generate_suite_from_tags_all_mode_intersects():
    """synthetic_ticker_generalization.json has all of generalization,
    synthetic, and hallucination-risk -- the only real scenario that
    should match "all" of generalization + synthetic."""
    auto = _harness.generate_suite_from_tags(
        ["generalization", "synthetic"], match_mode="all"
    )
    names = [os.path.basename(p) for p in auto["_resolved_scenario_paths"]]
    assert names == ["synthetic_ticker_generalization.json"]


def test_generate_suite_from_tags_all_mode_raises_when_no_overlap():
    with pytest.raises(ValueError, match="No real scenario matches"):
        _harness.generate_suite_from_tags(
            ["holdings-correction", "generalization"], match_mode="all"
        )


def test_generate_suite_from_tags_rejects_invalid_match_mode():
    with pytest.raises(ValueError, match="match_mode"):
        _harness.generate_suite_from_tags(["x"], match_mode="bogus")


def test_generate_suite_from_tags_rejects_empty_tags():
    with pytest.raises(ValueError, match="at least one real tag"):
        _harness.generate_suite_from_tags([])


def test_generate_suite_from_tags_rejects_unmatched_tag():
    with pytest.raises(ValueError, match="No real scenario matches"):
        _harness.generate_suite_from_tags(["this-tag-does-not-exist-anywhere"])


def test_generate_suite_from_tags_produces_a_real_loadable_suite():
    """Real round-trip check: the generated dict, saved and reloaded via
    the real load_suite() path (like --save-suite-file does), must
    still be valid and resolve the same scenarios."""
    auto = _harness.generate_suite_from_tags(["prompt-shape"])
    to_save = {k: v for k, v in auto.items() if k != "_resolved_scenario_paths"}
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_save, f)
        reloaded = _harness.load_suite(path)
        assert reloaded["_resolved_scenario_paths"] == auto["_resolved_scenario_paths"]
    finally:
        os.remove(path)


# ---------------------------------------------------------------------------
# compute_multi_model_scenario_weights (added 2026-08-28 as part of
# model-specific weighting)
# ---------------------------------------------------------------------------

def test_compute_multi_model_scenario_weights_shows_real_per_model_differences():
    scenario = {"name": "x", "scenario_tags": []}
    historical = [
        {"model": "good_model", "scenario_name": "x", "total_turns": 10, "clean_turns": 10},
        {"model": "bad_model", "scenario_name": "x", "total_turns": 10, "clean_turns": 0},
    ]
    weights = _harness.compute_multi_model_scenario_weights(
        scenario, historical, models=["good_model", "bad_model"]
    )
    assert weights["good_model"]["total"] == 1.0
    assert weights["bad_model"]["total"] == 2.0
    assert weights["good_model"]["total"] != weights["bad_model"]["total"]


def test_compute_multi_model_scenario_weights_matches_single_model_calls():
    """Real, direct correctness check: the multi-model function must
    reuse compute_scenario_weight() exactly, not a separate
    implementation that could silently drift from it."""
    scenario = {"name": "x", "scenario_tags": ["generalization"]}
    historical = [
        {"model": "m1", "scenario_name": "x", "total_turns": 10, "clean_turns": 5,
         "flag_counts": {"TOOL_ERROR": 1}, "total_cross_turn_contamination": 1},
    ]
    multi = _harness.compute_multi_model_scenario_weights(scenario, historical, models=["m1", "m2"])
    single_m1 = _harness.compute_scenario_weight(scenario, historical, model="m1")
    single_m2 = _harness.compute_scenario_weight(scenario, historical, model="m2")
    assert multi["m1"] == single_m1
    assert multi["m2"] == single_m2


def test_compute_multi_model_scenario_weights_respects_recent_n():
    scenario = {"name": "x", "scenario_tags": []}
    historical = _real_history_with_old_bad_recent_good("x", model="m1")
    multi_all_time = _harness.compute_multi_model_scenario_weights(scenario, historical, models=["m1"])
    multi_recent = _harness.compute_multi_model_scenario_weights(
        scenario, historical, models=["m1"], recent_n=3
    )
    assert multi_recent["m1"]["total"] < multi_all_time["m1"]["total"]


def test_compute_multi_model_scenario_weights_rejects_empty_models():
    with pytest.raises(ValueError, match="models list"):
        _harness.compute_multi_model_scenario_weights({"name": "x"}, [], models=[])


# ---------------------------------------------------------------------------
# generate_suite_health_summary (added 2026-08-28)
# ---------------------------------------------------------------------------

def test_health_summary_single_model_shape():
    result = {
        "timestamp": 1000, "model": "m1", "suite_name": "x",
        "total_turns": 20, "clean_turns": 15,
        "flag_counts": {"EMPTY": 2, "TOOL_ERROR": 3},
        "scenario_results": [{"scenario_name": "a"}, {"scenario_name": "b"}, {"scenario_name": "c"}],
    }
    summary = _harness.generate_suite_health_summary(result)
    assert summary["overview"] == {
        "suite_name": "x", "scenario_count": 3, "model_count": 1, "total_turns": 20,
    }
    assert summary["outcomes"]["failure_rate"] == 25
    assert "model_comparison" not in summary
    assert "regressions" not in summary


def test_health_summary_multi_model_shape():
    result = {
        "suite_name": "x",
        "results": {
            "good": {"total_turns": 10, "clean_turns": 8, "flag_counts": {"EMPTY": 2},
                      "scenario_results": [{"scenario_name": "a"}, {"scenario_name": "b"}]},
            "bad": {"total_turns": 10, "clean_turns": 0, "flag_counts": {"REPEATED": 10},
                    "scenario_results": [{"scenario_name": "a"}, {"scenario_name": "b"}]},
        },
    }
    summary = _harness.generate_suite_health_summary(result)
    assert summary["overview"]["model_count"] == 2
    assert summary["overview"]["scenario_count"] == 2
    assert summary["outcomes"]["total_turns"] == 20
    assert summary["outcomes"]["clean_turns"] == 8
    assert summary["model_comparison"]["good"]["clean_rate"] == 80
    assert summary["model_comparison"]["bad"]["clean_rate"] == 0


def test_health_summary_includes_regressions_when_history_given():
    historical = [
        {"model": "m1", "timestamp": 100, "total_turns": 10, "clean_turns": 10, "flag_counts": {}},
        {"model": "m1", "timestamp": 200, "total_turns": 10, "clean_turns": 2, "flag_counts": {}},
    ]
    result = {"model": "m1", "suite_name": "x", "total_turns": 10, "clean_turns": 2,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result, historical_summaries=historical)
    assert len(summary["regressions"]) == 1
    assert summary["regressions"][0]["type"] == "clean_rate_regression"


def test_health_summary_omits_regressions_when_no_history_given():
    result = {"model": "m1", "suite_name": "x", "total_turns": 10, "clean_turns": 10,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result)
    assert "regressions" not in summary


def test_health_summary_regressions_scoped_to_the_right_model():
    """Real, deliberate design: a single-model suite result's regression
    check must only reflect that model's own history, not blend in
    regressions from a different model in the same historical data."""
    historical = [
        {"model": "m1", "timestamp": 100, "total_turns": 10, "clean_turns": 10, "flag_counts": {}},
        {"model": "m1", "timestamp": 200, "total_turns": 10, "clean_turns": 2, "flag_counts": {}},
        {"model": "m2", "timestamp": 100, "total_turns": 10, "clean_turns": 5, "flag_counts": {}},
        {"model": "m2", "timestamp": 200, "total_turns": 10, "clean_turns": 5, "flag_counts": {}},
    ]
    result = {"model": "m1", "suite_name": "x", "total_turns": 10, "clean_turns": 2,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result, historical_summaries=historical)
    assert all(a["model"] == "m1" for a in summary["regressions"])


# ---------------------------------------------------------------------------
# load_historical_summaries and summarize_suite_trend (added 2026-08-28
# as part of Persist_summary_in_history)
# ---------------------------------------------------------------------------

def test_generate_suite_health_summary_includes_timestamp():
    """Real, added 2026-08-28: a health summary must carry its own real
    timestamp to be a self-contained, persistable snapshot."""
    result = {"model": "m1", "suite_name": "x", "total_turns": 5, "clean_turns": 5,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result)
    assert "timestamp" in summary
    assert isinstance(summary["timestamp"], float)


def test_load_historical_summaries_loads_and_sorts(tmp_path):
    now = time.time()
    s1 = {"timestamp": now - 3600, "overview": {"suite_name": "x"}, "outcomes": {"failure_rate": 10}}
    s2 = {"timestamp": now, "overview": {"suite_name": "x"}, "outcomes": {"failure_rate": 60}}
    (tmp_path / "b.json").write_text(json.dumps(s2))
    (tmp_path / "a.json").write_text(json.dumps(s1))
    loaded = _harness.load_historical_summaries(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[0]["timestamp"] < loaded[1]["timestamp"]
    assert loaded[0]["outcomes"]["failure_rate"] == 10


def test_load_historical_summaries_skips_malformed_and_incomplete(tmp_path):
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "incomplete.json").write_text(json.dumps({"overview": {}}))  # missing timestamp
    (tmp_path / "good.json").write_text(json.dumps({
        "timestamp": time.time(), "overview": {"suite_name": "x"}, "outcomes": {"failure_rate": 0},
    }))
    loaded = _harness.load_historical_summaries(str(tmp_path))
    assert len(loaded) == 1


def test_load_historical_summaries_missing_dir_returns_empty():
    assert _harness.load_historical_summaries("/tmp/does-not-exist-at-all-real-check") == []


def test_summarize_suite_trend_shows_regression_count():
    now = time.time()
    summaries = [
        {"timestamp": now - 3600, "overview": {"suite_name": "x"},
         "outcomes": {"failure_rate": 10, "clean_turns": 9, "total_turns": 10}, "regressions": []},
        {"timestamp": now, "overview": {"suite_name": "x"},
         "outcomes": {"failure_rate": 60, "clean_turns": 4, "total_turns": 10},
         "regressions": [{"type": "clean_rate_regression"}]},
    ]
    trend = _harness.summarize_suite_trend(summaries)
    assert trend[0]["failure_rate"] == 10
    assert trend[1]["failure_rate"] == 60
    assert trend[1]["regression_count"] == 1


def test_summarize_suite_trend_filters_by_suite_name():
    now = time.time()
    summaries = [
        {"timestamp": now, "overview": {"suite_name": "x"}, "outcomes": {"failure_rate": 10}, "regressions": []},
        {"timestamp": now, "overview": {"suite_name": "y"}, "outcomes": {"failure_rate": 90}, "regressions": []},
    ]
    trend = _harness.summarize_suite_trend(summaries, suite_name="x")
    assert len(trend) == 1
    assert trend[0]["suite_name"] == "x"


def test_summarize_suite_trend_empty_input_returns_empty_list():
    assert _harness.summarize_suite_trend([]) == []


# ---------------------------------------------------------------------------
# generate_health_summary_html_report (added 2026-08-28, Add_summary_to_dashboard)
# ---------------------------------------------------------------------------

def test_health_summary_html_report_renders_core_sections(tmp_path):
    summary = {
        "timestamp": 1000,
        "overview": {"suite_name": "x", "scenario_count": 2, "model_count": 1, "total_turns": 10},
        "outcomes": {"clean_turns": 6, "total_turns": 10, "failure_rate": 40, "flag_counts": {"TOOL_ERROR": 2}},
        "regressions": [{"type": "clean_rate_regression", "message": "x: dropped 90% -> 60%",
                          "severity": "moderate", "model": "m1"}],
    }
    out_path = str(tmp_path / "report.html")
    _harness.generate_health_summary_html_report(summary, output_path=out_path)
    with open(out_path) as f:
        html = f.read()
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "dropped 90%" in html
    assert "TOOL_ERROR: 2" in html


def test_health_summary_html_report_no_regressions_shows_honest_none_detected(tmp_path):
    summary = {
        "timestamp": 1000,
        "overview": {"suite_name": "x", "scenario_count": 1, "model_count": 1, "total_turns": 5},
        "outcomes": {"clean_turns": 5, "total_turns": 5, "failure_rate": 0, "flag_counts": {}},
        "regressions": [],
    }
    out_path = str(tmp_path / "report.html")
    _harness.generate_health_summary_html_report(summary, output_path=out_path)
    with open(out_path) as f:
        html = f.read()
    assert "None detected" in html


def test_health_summary_html_report_includes_model_comparison_table(tmp_path):
    summary = {
        "timestamp": 1000,
        "overview": {"suite_name": "y", "scenario_count": 2, "model_count": 2, "total_turns": 20},
        "outcomes": {"clean_turns": 10, "total_turns": 20, "failure_rate": 50, "flag_counts": {}},
        "model_comparison": {
            "good": {"clean_turns": 10, "total_turns": 10, "clean_rate": 100, "failure_rate": 0, "flag_counts": {}},
            "bad": {"clean_turns": 0, "total_turns": 10, "clean_rate": 0, "failure_rate": 100, "flag_counts": {}},
        },
    }
    out_path = str(tmp_path / "report.html")
    _harness.generate_health_summary_html_report(summary, output_path=out_path)
    with open(out_path) as f:
        html = f.read()
    assert "good" in html and "bad" in html
    assert "<table>" in html


def test_health_summary_html_report_includes_real_trend_chart_when_given():
    summary = {
        "timestamp": 2000,
        "overview": {"suite_name": "y", "scenario_count": 1, "model_count": 1, "total_turns": 10},
        "outcomes": {"clean_turns": 5, "total_turns": 10, "failure_rate": 50, "flag_counts": {}},
    }
    trend = [
        {"timestamp": 1000, "label": "08/29 09:00", "suite_name": "y", "failure_rate": 20,
         "clean_turns": 8, "total_turns": 10, "regression_count": 0, "source_file": "a.json"},
        {"timestamp": 2000, "label": "08/29 10:00", "suite_name": "y", "failure_rate": 50,
         "clean_turns": 5, "total_turns": 10, "regression_count": 0, "source_file": "b.json"},
    ]
    fd, out_path = tempfile.mkstemp(suffix=".html")
    try:
        os.close(fd)
        _harness.generate_health_summary_html_report(summary, trend=trend, output_path=out_path)
        with open(out_path) as f:
            html = f.read()
        assert "<svg" in html
        assert "<polyline" in html
        assert html.count("<circle") == 2
    finally:
        os.remove(out_path)


def test_health_summary_html_report_no_trend_chart_when_omitted(tmp_path):
    summary = {
        "timestamp": 1000,
        "overview": {"suite_name": "x", "scenario_count": 1, "model_count": 1, "total_turns": 5},
        "outcomes": {"clean_turns": 5, "total_turns": 5, "failure_rate": 0, "flag_counts": {}},
    }
    out_path = str(tmp_path / "report.html")
    _harness.generate_health_summary_html_report(summary, output_path=out_path)
    with open(out_path) as f:
        html = f.read()
    assert "Failure rate over time" not in html


# ---------------------------------------------------------------------------
# Design_suite_health_dashboard: per_scenario breakdown, string-return
# mode, and scenario grid rendering (added 2026-08-28). The live server
# itself (serve_health_dashboard) is verified live, not by CI unit test
# -- an actually-listening HTTP server isn't meaningfully unit-testable
# without spinning up a real socket, which real, deliberate live
# verification (start server, real HTTP requests, confirm live pickup
# of new data, stop server) already covered thoroughly and directly.
# ---------------------------------------------------------------------------

def test_health_summary_includes_per_scenario_single_model():
    result = {
        "model": "m1", "suite_name": "x", "total_turns": 15, "clean_turns": 10,
        "flag_counts": {"TOOL_ERROR": 2},
        "scenario_results": [
            {"scenario_name": "a", "clean_turns": 5, "total_turns": 5},
            {"scenario_name": "b", "clean_turns": 5, "total_turns": 10},
        ],
    }
    summary = _harness.generate_suite_health_summary(result)
    by_name = {s["scenario_name"]: s for s in summary["per_scenario"]}
    assert by_name["a"]["failure_rate"] == 0
    assert by_name["b"]["failure_rate"] == 50


def test_health_summary_per_scenario_combines_across_models():
    """Real, deliberate design: for a multi-model result, per-scenario
    data is combined across all models -- must genuinely sum, not just
    reflect one model's data."""
    mm_result = {
        "suite_name": "y",
        "results": {
            "m1": {"total_turns": 5, "clean_turns": 5, "flag_counts": {},
                   "scenario_results": [{"scenario_name": "a", "clean_turns": 5, "total_turns": 5}]},
            "m2": {"total_turns": 5, "clean_turns": 0, "flag_counts": {},
                   "scenario_results": [{"scenario_name": "a", "clean_turns": 0, "total_turns": 5}]},
        },
    }
    summary = _harness.generate_suite_health_summary(mm_result)
    assert summary["per_scenario"][0]["total_turns"] == 10
    assert summary["per_scenario"][0]["clean_turns"] == 5


def test_health_summary_html_report_returns_string_when_no_output_path():
    result = {"model": "m1", "suite_name": "x", "total_turns": 5, "clean_turns": 5,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result)
    html_str = _harness.generate_health_summary_html_report(summary)
    assert isinstance(html_str, str)
    assert html_str.startswith("<!DOCTYPE html>")


def test_health_summary_html_report_still_writes_file_when_path_given(tmp_path):
    """Real regression guard: making output_path optional must not
    break the existing behavior of writing to disk when it IS given."""
    result = {"model": "m1", "suite_name": "x", "total_turns": 5, "clean_turns": 5,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result)
    out_path = str(tmp_path / "report.html")
    returned = _harness.generate_health_summary_html_report(summary, output_path=out_path)
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        written = f.read()
    assert written == returned


def test_health_summary_html_report_scenario_grid_color_coding():
    result = {
        "model": "m1", "suite_name": "x", "total_turns": 10, "clean_turns": 5, "flag_counts": {},
        "scenario_results": [
            {"scenario_name": "clean_one", "clean_turns": 5, "total_turns": 5},
            {"scenario_name": "broken_one", "clean_turns": 0, "total_turns": 5},
        ],
    }
    summary = _harness.generate_suite_health_summary(result)
    html_str = _harness.generate_health_summary_html_report(summary)
    assert "border-color:#3FB950" in html_str  # clean_one, 0% failure -> green
    assert "border-color:#F85149" in html_str  # broken_one, 100% failure -> red


def test_health_summary_html_report_no_scenario_grid_when_empty():
    result = {"model": "m1", "suite_name": "x", "total_turns": 0, "clean_turns": 0,
              "flag_counts": {}, "scenario_results": []}
    summary = _harness.generate_suite_health_summary(result)
    html_str = _harness.generate_health_summary_html_report(summary)
    assert "Scenario grid" not in html_str


# ---------------------------------------------------------------------------
# Design_suite_health_notifications: notify_regressions,
# send_desktop_notification, send_webhook_notification (added 2026-08-28)
# ---------------------------------------------------------------------------

def test_notify_regressions_sends_nothing_on_empty_regressions():
    """Real, deliberate design: no notification is a "no regressions"
    ping -- would just be noise, not a real alert."""
    assert _harness.notify_regressions([], method="desktop", suite_name="x") is False


def test_notify_regressions_rejects_unknown_method():
    regressions = [{"type": "x", "message": "y", "severity": "high", "model": "m1"}]
    with pytest.raises(ValueError, match="Unknown notification method"):
        _harness.notify_regressions(regressions, method="bogus", suite_name="x")


def test_notify_regressions_webhook_without_url_fails_gracefully():
    regressions = [{"type": "x", "message": "y", "severity": "high", "model": "m1"}]
    assert _harness.notify_regressions(regressions, method="webhook", suite_name="x") is False


def test_send_desktop_notification_handles_unavailable_notify_send(monkeypatch):
    """Real, deliberate design: confirmed directly that notify-send is
    unavailable inside the real Odysseus container this harness most
    often runs from (no D-Bus session) -- must fail gracefully with a
    clear message, not crash."""
    monkeypatch.setattr(_harness.shutil, "which", lambda name: None)
    assert _harness.send_desktop_notification("title", "message") is False


def test_send_webhook_notification_real_live_post_to_local_receiver():
    """Real, live check: spins up a genuine local HTTP server and
    confirms send_webhook_notification() actually POSTs the real,
    correct JSON payload to it -- not mocked."""
    received = {}

    class Handler(_harness.http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["body"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = _harness.http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        result = _harness.send_webhook_notification(
            f"http://127.0.0.1:{port}/", {"suite_name": "x", "regressions": [{"a": 1}]}
        )
        thread.join(timeout=5)
        assert result is True
        assert received["body"] == {"suite_name": "x", "regressions": [{"a": 1}]}
    finally:
        server.server_close()


def test_send_webhook_notification_unreachable_url_fails_gracefully():
    assert _harness.send_webhook_notification("http://127.0.0.1:1/nonexistent", {"x": 1}) is False


def test_notify_regressions_webhook_payload_includes_full_alert_dicts():
    """Real, deliberate design: the webhook payload includes every real
    regression alert dict exactly as detect_regressions() produced it,
    not a lossy, pre-formatted summary string."""
    received = {}

    class Handler(_harness.http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["body"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = _harness.http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    regressions = [
        {"type": "clean_rate_regression", "message": "x dropped", "severity": "high", "model": "m1"},
    ]
    try:
        _harness.notify_regressions(regressions, method="webhook",
                                     webhook_url=f"http://127.0.0.1:{port}/", suite_name="real_suite")
        thread.join(timeout=5)
        assert received["body"] == {"suite_name": "real_suite", "regressions": regressions}
    finally:
        server.server_close()


# ---------------------------------------------------------------------------
# Design_suite_health_trend_report: compare_suite_trends,
# generate_multi_suite_trend_html_report (added 2026-08-28)
# ---------------------------------------------------------------------------

def _real_multi_suite_summaries():
    now = time.time()
    return [
        {"timestamp": now - 7200, "overview": {"suite_name": "holdings_correction"},
         "outcomes": {"failure_rate": 10, "clean_turns": 9, "total_turns": 10}, "regressions": []},
        {"timestamp": now - 3600, "overview": {"suite_name": "holdings_correction"},
         "outcomes": {"failure_rate": 20, "clean_turns": 8, "total_turns": 10}, "regressions": [{"type": "x"}]},
        {"timestamp": now - 5400, "overview": {"suite_name": "generalization"},
         "outcomes": {"failure_rate": 50, "clean_turns": 5, "total_turns": 10}, "regressions": []},
        {"timestamp": now, "overview": {"suite_name": "generalization"},
         "outcomes": {"failure_rate": 30, "clean_turns": 7, "total_turns": 10}, "regressions": []},
    ]


def test_compare_suite_trends_groups_correctly():
    grouped = _harness.compare_suite_trends(_real_multi_suite_summaries())
    assert set(grouped.keys()) == {"holdings_correction", "generalization"}
    assert len(grouped["holdings_correction"]) == 2
    assert len(grouped["generalization"]) == 2
    assert grouped["holdings_correction"][0]["failure_rate"] == 10
    assert grouped["holdings_correction"][1]["failure_rate"] == 20


def test_compare_suite_trends_empty_input():
    assert _harness.compare_suite_trends([]) == {}


def test_multi_suite_trend_html_report_renders_one_line_per_suite():
    grouped = _harness.compare_suite_trends(_real_multi_suite_summaries())
    html_str = _harness.generate_multi_suite_trend_html_report(grouped)
    assert html_str.startswith("<!DOCTYPE html>")
    assert html_str.count("<polyline") == 2
    assert "holdings_correction" in html_str
    assert "generalization" in html_str


def test_multi_suite_trend_html_report_empty_shows_honest_message():
    html_str = _harness.generate_multi_suite_trend_html_report({})
    assert "No saved health summaries found" in html_str


def test_multi_suite_trend_html_report_writes_file_when_path_given(tmp_path):
    grouped = _harness.compare_suite_trends(_real_multi_suite_summaries())
    out_path = str(tmp_path / "report.html")
    returned = _harness.generate_multi_suite_trend_html_report(grouped, output_path=out_path)
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        written = f.read()
    assert written == returned


def test_multi_suite_trend_html_report_table_matches_latest_entry_per_suite():
    grouped = _harness.compare_suite_trends(_real_multi_suite_summaries())
    html_str = _harness.generate_multi_suite_trend_html_report(grouped)
    # holdings_correction's latest (most recent) entry has failure_rate 20
    # and 1 regression; generalization's latest has failure_rate 30, 0 regressions.
    assert "<td>20%</td><td>1</td>" in html_str
    assert "<td>30%</td><td>0</td>" in html_str


# ---------------------------------------------------------------------------
# evaluate_health_gate (added 2026-08-28, Design_suite_health_gatekeeping)
# ---------------------------------------------------------------------------

def test_gate_no_criteria_passes_by_default():
    summary = {"outcomes": {"failure_rate": 40}, "regressions": []}
    result = _harness.evaluate_health_gate(summary)
    assert result["passed"] is True
    assert "No real gate criteria" in result["reasons"][0]


def test_gate_max_failure_rate_passes_within_bound():
    summary = {"outcomes": {"failure_rate": 40}, "regressions": []}
    result = _harness.evaluate_health_gate(summary, max_failure_rate=50)
    assert result["passed"] is True


def test_gate_max_failure_rate_fails_when_exceeded():
    summary = {"outcomes": {"failure_rate": 40}, "regressions": []}
    result = _harness.evaluate_health_gate(summary, max_failure_rate=30)
    assert result["passed"] is False
    assert "exceeds max_failure_rate" in result["reasons"][0]


def test_gate_block_on_any_regression():
    summary = {"outcomes": {"failure_rate": 10},
               "regressions": [{"type": "x", "message": "y", "severity": "moderate", "model": "m1"}]}
    assert _harness.evaluate_health_gate(summary, block_on_any_regression=True)["passed"] is False
    clean_summary = {"outcomes": {"failure_rate": 10}, "regressions": []}
    assert _harness.evaluate_health_gate(clean_summary, block_on_any_regression=True)["passed"] is True


def test_gate_max_regression_severity_high_ignores_moderate():
    """Real, deliberate design: a 'high' severity threshold should not
    block on a merely moderate regression."""
    summary = {"outcomes": {"failure_rate": 10},
               "regressions": [{"type": "x", "message": "y", "severity": "moderate", "model": "m1"}]}
    result = _harness.evaluate_health_gate(summary, max_regression_severity="high")
    assert result["passed"] is True


def test_gate_max_regression_severity_moderate_blocks_moderate():
    summary = {"outcomes": {"failure_rate": 10},
               "regressions": [{"type": "x", "message": "y", "severity": "moderate", "model": "m1"}]}
    result = _harness.evaluate_health_gate(summary, max_regression_severity="moderate")
    assert result["passed"] is False


def test_gate_combines_multiple_criteria_with_all_reasons():
    summary = {"outcomes": {"failure_rate": 10},
               "regressions": [{"type": "x", "message": "y", "severity": "moderate", "model": "m1"}]}
    result = _harness.evaluate_health_gate(summary, max_failure_rate=5, max_regression_severity="moderate")
    assert result["passed"] is False
    assert len(result["reasons"]) == 2


def test_gate_rejects_invalid_severity_string():
    summary = {"outcomes": {"failure_rate": 10}, "regressions": []}
    with pytest.raises(ValueError, match="max_regression_severity must be"):
        _harness.evaluate_health_gate(summary, max_regression_severity="bogus")


# ---------------------------------------------------------------------------
# run_multi_model_suite_parallel (added 2026-08-28,
# Design_parallel_suite_runner). The real, live model-calling behavior
# itself is verified live, not by CI unit test -- see the real,
# measured wall-clock comparison covered directly during development
# (85.72s sequential vs 52.92s parallel on the same 2 real models/suite,
# and a second live run at 43.9s through the actual CLI), a stronger,
# more honest check for real concurrent-request behavior than a mocked
# unit test could provide.
# ---------------------------------------------------------------------------

def test_run_multi_model_suite_parallel_rejects_empty_models():
    with pytest.raises(ValueError, match="models list"):
        _harness.run_multi_model_suite_parallel(
            "77bddaa5", [], {"name": "x", "_resolved_scenario_paths": []}
        )


# ---------------------------------------------------------------------------
# run_suite_parallel (added 2026-08-28, Design_suite_sharding). Same real
# verification split as run_multi_model_suite_parallel(): input
# validation and the ordering guarantee are genuinely unit-testable (the
# ordering test uses monkeypatched, deterministically-staggered fake
# durations to force completion order to differ from submission order,
# same real technique already proven live during development); the real
# live model-calling/timing behavior itself is covered by direct live
# measurement instead (189.65s sequential vs 129.27s sharded on the same
# real 3-scenario suite, a real ~32% reduction, plus a second live CLI
# run confirming clean, non-garbled concurrent output).
# ---------------------------------------------------------------------------

def test_run_suite_parallel_rejects_empty_scenarios():
    with pytest.raises(ValueError, match="no real scenarios to shard"):
        _harness.run_suite_parallel("77bddaa5", "m1", {"name": "x", "_resolved_scenario_paths": []})


def test_run_suite_parallel_preserves_submission_order_despite_reversed_completion(tmp_path, monkeypatch):
    """Real, deterministic proof the reordering logic works: forces
    completion order to be the exact reverse of submission order via
    staggered fake delays, and confirms the output still matches
    submission order -- not a lucky real-world coincidence."""
    scenario_files = {}
    for name, delay in [("scenario_a", 0.06), ("scenario_b", 0.03), ("scenario_c", 0.0)]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "turns": [{"type": "ticker", "symbol": "SOUN"}]}))
        scenario_files[str(path)] = (name, delay)

    def fake_run_multi_round_suite(endpoint_id, model, runs, scenario=None, verbose=True):
        for _, (n, d) in scenario_files.items():
            if n == scenario["name"]:
                time.sleep(d)
                break
        return {
            "total_turns": 1, "clean_turns": 1, "flag_counts": {},
            "total_cross_turn_contamination": 0, "runs_errored": 0,
            "scenario_name": scenario["name"], "run_records": [],
        }

    monkeypatch.setattr(_harness, "run_multi_round_suite", fake_run_multi_round_suite)

    suite = {"name": "order_test", "_resolved_scenario_paths": list(scenario_files.keys())}
    result = _harness.run_suite_parallel("77bddaa5", "m1", suite, verbose=False)
    actual_order = [s["scenario_name"] for s in result["scenario_results"]]
    assert actual_order == ["scenario_a", "scenario_b", "scenario_c"]


def test_run_suite_parallel_aggregates_correctly(tmp_path, monkeypatch):
    scenario_paths = []
    for name in ["a", "b"]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "turns": [{"type": "ticker", "symbol": "SOUN"}]}))
        scenario_paths.append(str(path))

    def fake_run_multi_round_suite(endpoint_id, model, runs, scenario=None, verbose=True):
        if scenario["name"] == "a":
            return {"total_turns": 5, "clean_turns": 4, "flag_counts": {"EMPTY": 1},
                     "total_cross_turn_contamination": 0, "runs_errored": 0,
                     "scenario_name": "a", "run_records": []}
        return {"total_turns": 3, "clean_turns": 3, "flag_counts": {},
                 "total_cross_turn_contamination": 1, "runs_errored": 0,
                 "scenario_name": "b", "run_records": []}

    monkeypatch.setattr(_harness, "run_multi_round_suite", fake_run_multi_round_suite)
    suite = {"name": "agg_test", "_resolved_scenario_paths": scenario_paths}
    result = _harness.run_suite_parallel("77bddaa5", "m1", suite, verbose=False)
    assert result["total_turns"] == 8
    assert result["clean_turns"] == 7
    assert result["flag_counts"] == {"EMPTY": 1}
    assert result["total_cross_turn_contamination"] == 1
    assert result["sharded"] is True


# ---------------------------------------------------------------------------
# generate_dashboard_overview_html (added 2026-08-28, Suite_health_
# dashboard: full visual overview). The live server routing itself
# (overview page, ?suite= drill-down, unknown-suite handling, live
# pickup of newly-added suites without restart) is verified live, not
# by CI unit test -- covered directly during development: seeded 2 real
# suites, confirmed both real cards render with correct, distinct
# status; drilled into one and confirmed its own real detail and
# regression; confirmed an unknown suite shows an honest error with a
# working back-link; then, without restarting the running server,
# added a THIRD real suite's data and confirmed the overview picked it
# up live on the next request.
# ---------------------------------------------------------------------------

def _real_multi_suite_summaries_with_detail():
    now = time.time()
    return [
        {"timestamp": now - 7200, "overview": {"suite_name": "holdings_correction"},
         "outcomes": {"failure_rate": 10, "clean_turns": 9, "total_turns": 10}, "regressions": []},
        {"timestamp": now - 3600, "overview": {"suite_name": "holdings_correction"},
         "outcomes": {"failure_rate": 20, "clean_turns": 8, "total_turns": 10}, "regressions": [{"type": "x"}]},
        {"timestamp": now, "overview": {"suite_name": "generalization"},
         "outcomes": {"failure_rate": 60, "clean_turns": 4, "total_turns": 10}, "regressions": []},
    ]


def test_dashboard_overview_shows_every_suite():
    html_str = _harness.generate_dashboard_overview_html(_real_multi_suite_summaries_with_detail())
    assert "holdings_correction" in html_str
    assert "generalization" in html_str


def test_dashboard_overview_shows_latest_not_first_entry_per_suite():
    """Real, deliberate check: holdings_correction has 2 real entries
    (10%, then 20%) -- the overview must show the latest (20%), not
    the first."""
    html_str = _harness.generate_dashboard_overview_html(_real_multi_suite_summaries_with_detail())
    assert "20%" in html_str


def test_dashboard_overview_has_drill_down_links():
    html_str = _harness.generate_dashboard_overview_html(_real_multi_suite_summaries_with_detail())
    assert 'href="/?suite=holdings_correction"' in html_str
    assert 'href="/?suite=generalization"' in html_str


def test_dashboard_overview_empty_shows_honest_message():
    html_str = _harness.generate_dashboard_overview_html([])
    assert "No saved summaries found" in html_str


def test_dashboard_overview_shows_correct_snapshot_count_per_suite():
    html_str = _harness.generate_dashboard_overview_html(_real_multi_suite_summaries_with_detail())
    assert "2 snapshot(s)" in html_str  # holdings_correction has 2 real entries
    assert "1 snapshot(s)" in html_str  # generalization has 1


# ---------------------------------------------------------------------------
# capture_raw_events_for_check (added 2026-08-28,
# Capture_raw_events_for_TOOL_ARGUMENT_ECHO). Verified live for real:
# used directly against the real backend to actually reproduce and
# capture a genuine, live TOOL_ARGUMENT_ECHO occurrence (3 real attempts
# against prompt_shape_variety.json) -- the model called lookup_ticker
# with the previous turn's stale KTOS argument instead of the current
# turn's real RGTI subject, and produced an empty final response. The
# real, saved capture (including injected real memories_used context
# suggesting the model may have been anchored by heavy recent KTOS
# discussion) is preserved in scripts/captures/ for direct inspection.
# These tests cover the function's own real logic deterministically,
# via monkeypatched create_session/send_message, since real live
# generation is (confirmed, repeatedly, the same night) genuinely
# non-deterministic and can't be relied on to reproduce a specific
# real finding on every CI run.
# ---------------------------------------------------------------------------

def _real_scenario_for_capture_tests():
    return {
        "name": "capture_test_scenario",
        "turns": [
            {"type": "ticker", "symbol": "KTOS"},
            {"type": "ticker", "symbol": "RGTI"},
        ],
    }


def test_capture_raw_events_saves_bundle_on_first_matching_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(_harness, "create_session", lambda *a, **kw: "fake_session")

    def fake_send_message(session_id, message, model, malformed_lines=None):
        return [{"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
                {"delta": "KTOS is at $52."}]

    monkeypatch.setattr(_harness, "send_message", fake_send_message)
    # Force the second (RGTI) turn's own real check to report echo, so
    # this test exercises the real save-bundle path deterministically.
    monkeypatch.setattr(_harness, "_tool_argument_echo", lambda turn, r: turn["symbol"] == "RGTI")

    result_path = _harness.capture_raw_events_for_check(
        "77bddaa5", "m1", _real_scenario_for_capture_tests(), str(tmp_path),
        target_check="tool_argument_echo", max_attempts=5, verbose=False,
    )
    assert result_path is not None
    assert os.path.isfile(result_path)
    with open(result_path) as f:
        bundle = json.load(f)
    assert bundle["target_check"] == "tool_argument_echo"
    assert bundle["affected_turn_index"] == 1
    assert bundle["attempt"] == 1
    assert len(bundle["turns_captured"]) == 2
    # The real, raw events must be preserved verbatim, not summarized.
    assert bundle["turns_captured"][1]["raw_events"][0]["command"] == '{"symbol": "KTOS"}'


def test_capture_raw_events_returns_none_honestly_when_never_reproduced(tmp_path, monkeypatch):
    monkeypatch.setattr(_harness, "create_session", lambda *a, **kw: "fake_session")
    monkeypatch.setattr(_harness, "send_message", lambda *a, **kw: [{"delta": "clean answer"}])
    monkeypatch.setattr(_harness, "_tool_argument_echo", lambda turn, r: False)

    result_path = _harness.capture_raw_events_for_check(
        "77bddaa5", "m1", _real_scenario_for_capture_tests(), str(tmp_path),
        target_check="tool_argument_echo", max_attempts=2, verbose=False,
    )
    assert result_path is None
    assert os.listdir(tmp_path) == []


def test_capture_raw_events_stops_at_first_reproduction_not_later_attempts(tmp_path, monkeypatch):
    """Real, deliberate design: the function stops the moment the
    target check fires once -- must not keep running additional real
    attempts (or additional turns) after a successful capture."""
    monkeypatch.setattr(_harness, "create_session", lambda *a, **kw: "fake_session")
    call_count = {"n": 0}

    def fake_send_message(session_id, message, model, malformed_lines=None):
        call_count["n"] += 1
        return [{"delta": "answer"}]

    monkeypatch.setattr(_harness, "send_message", fake_send_message)
    monkeypatch.setattr(_harness, "_tool_argument_echo", lambda turn, r: turn["symbol"] == "KTOS")

    _harness.capture_raw_events_for_check(
        "77bddaa5", "m1", _real_scenario_for_capture_tests(), str(tmp_path),
        target_check="tool_argument_echo", max_attempts=10, verbose=False,
    )
    # KTOS is the FIRST turn -- should stop immediately: 1 real "Hi"
    # greeting call (sent before the turn loop starts) + 1 real call
    # for the first turn itself = 2 total, not 3 (which a second turn
    # or a second attempt would add).
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# reconstruct_rounds, render_replay_transcript (added 2026-08-28,
# Replay_exact_run_with_replay_engine). Verified live against the
# real, actual captured bundle from the immediately preceding task
# (tool_argument_echo_prompt_shape_variety_1788042034.json,
# scripts/captures/) -- confirmed the reconstructed transcript
# correctly showed round 1's stale KTOS tool call and round 2's empty
# content for the real affected turn, and additionally surfaced a
# second, distinct real finding along the way: a raw </think> tag
# leaking into turn 1's visible content, another instance of the
# reasoning-leak pattern noted earlier in the same session. These
# tests cover the reconstruction logic deterministically with real,
# controlled event sequences matching the exact real shapes confirmed
# by inspecting that real capture directly.
# ---------------------------------------------------------------------------

def test_reconstruct_rounds_groups_by_explicit_round_markers():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}', "round": 1},
        {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 52", "exit_code": 0},
        {"type": "agent_step", "round": 2},
        {"delta": "KTOS is at $52."},
    ]
    rounds = _harness.reconstruct_rounds(events)
    assert len(rounds) == 2
    assert rounds[0]["round"] == 1
    assert rounds[0]["tool_calls"][0]["tool"] == "lookup_ticker"
    assert rounds[0]["tool_calls"][0]["output"] == "price: 52"
    assert rounds[1]["round"] == 2
    assert rounds[1]["content"] == "KTOS is at $52."


def test_reconstruct_rounds_content_before_any_marker_is_implicit_round_1():
    """Real, confirmed directly from a real capture: a turn that never
    calls a tool never emits any real round marker at all -- its
    content must still be correctly attributed to round 1."""
    events = [{"delta": "Just a direct answer, no tool call."}]
    rounds = _harness.reconstruct_rounds(events)
    assert len(rounds) == 1
    assert rounds[0]["round"] == 1
    assert rounds[0]["content"] == "Just a direct answer, no tool call."


def test_reconstruct_rounds_empty_events_returns_empty_list():
    assert _harness.reconstruct_rounds([]) == []


def test_reconstruct_rounds_ignores_thinking_deltas():
    events = [
        {"delta": "internal reasoning", "thinking": True},
        {"delta": "the real visible answer"},
    ]
    rounds = _harness.reconstruct_rounds(events)
    assert rounds[0]["content"] == "the real visible answer"


def test_render_replay_transcript_includes_key_real_details():
    bundle = {
        "scenario_name": "test_scenario", "model": "m1", "attempt": 1,
        "session_id": "sess1", "timestamp": time.time(),
        "target_check": "tool_argument_echo", "affected_turn_index": 0,
        "turns_captured": [{
            "turn_index": 0, "prompt": "Whats RGTI trading at?",
            "raw_events": [
                {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}', "round": 1},
                {"type": "tool_output", "tool": "lookup_ticker", "output": "wrong data", "exit_code": 0},
            ],
            "classification": {"tool_argument_echo": True, "is_empty": True, "made_tool_call": True},
        }],
    }
    transcript = _harness.render_replay_transcript(bundle)
    assert "test_scenario" in transcript
    assert "Whats RGTI trading at?" in transcript
    assert '{"symbol": "KTOS"}' in transcript
    assert "AFFECTED TURN" in transcript
    assert "tool_argument_echo" in transcript


def test_render_replay_transcript_real_captured_bundle_reconstructs_correctly():
    """Real, direct verification against the actual, real captured
    bundle saved during the immediately preceding task -- not
    synthetic data."""
    real_capture_path = os.path.join(ROOT, "scripts", "captures",
                                      "tool_argument_echo_prompt_shape_variety_1788042034.json")
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    transcript = _harness.render_replay_transcript(bundle)
    assert "RGTI" in transcript
    assert '{"symbol": "KTOS"}' in transcript
    assert "tool_argument_echo" in transcript
    assert "AFFECTED TURN" in transcript


# ---------------------------------------------------------------------------
# compare_check_across_models (added 2026-08-28,
# Cross_model_comparison_for_argument_echo). Verified live: a real,
# completed 3-attempts-per-model run against ticker-lookup-lora and
# qwen3:14b on prompt_shape_variety.json (0/3 for both -- honest,
# inconclusive given the confirmed-low base rate of this rare event; a
# larger, 6-attempts-per-model follow-up run was still executing at
# the time this task wrapped, left running in the background rather
# than killed, since it's using real backend resources productively
# and will complete and write real results on its own). These tests
# cover the function's own real aggregation logic deterministically,
# via monkeypatched run_sequence(), the same real reasoning as every
# other live-model-calling function tested tonight: real generation
# timing/outcomes can't be relied on for CI.
# ---------------------------------------------------------------------------

def test_compare_check_across_models_distinguishes_real_per_model_rates(monkeypatch):
    def fake_run_sequence(endpoint_id, model, scenario=None):
        fired = model == "model_a"
        return {"turn_results": [{"tool_argument_echo": fired}],
                "session_id": "fake", "cross_turn_contamination": []}

    monkeypatch.setattr(_harness, "run_sequence", fake_run_sequence)
    scenario = {"name": "test", "turns": [{"type": "ticker", "symbol": "X"}]}
    results = _harness.compare_check_across_models(
        "77bddaa5", ["model_a", "model_b"], scenario, attempts_per_model=3, verbose=False,
    )
    assert results["model_a"]["occurrences"] == 3
    assert results["model_a"]["rate"] == 100
    assert results["model_b"]["occurrences"] == 0
    assert results["model_b"]["rate"] == 0


def test_compare_check_across_models_checks_any_turn_not_just_first(monkeypatch):
    """Real, deliberate design: a real run should count as an
    occurrence if the target check fires on ANY turn, not only the
    first one."""
    def fake_run_sequence(endpoint_id, model, scenario=None):
        return {"turn_results": [
            {"tool_argument_echo": False},
            {"tool_argument_echo": False},
            {"tool_argument_echo": True},  # fires on the third turn
        ], "session_id": "fake", "cross_turn_contamination": []}

    monkeypatch.setattr(_harness, "run_sequence", fake_run_sequence)
    scenario = {"name": "test", "turns": [{"type": "ticker", "symbol": "X"}]}
    results = _harness.compare_check_across_models(
        "77bddaa5", ["m1"], scenario, attempts_per_model=2, verbose=False,
    )
    assert results["m1"]["occurrences"] == 2


def test_compare_check_across_models_makes_correct_number_of_real_calls(monkeypatch):
    call_log = []

    def fake_run_sequence(endpoint_id, model, scenario=None):
        call_log.append(model)
        return {"turn_results": [{"tool_argument_echo": False}],
                "session_id": "fake", "cross_turn_contamination": []}

    monkeypatch.setattr(_harness, "run_sequence", fake_run_sequence)
    scenario = {"name": "test", "turns": [{"type": "ticker", "symbol": "X"}]}
    _harness.compare_check_across_models(
        "77bddaa5", ["m1", "m2"], scenario, attempts_per_model=4, verbose=False,
    )
    assert call_log.count("m1") == 4
    assert call_log.count("m2") == 4


# ---------------------------------------------------------------------------
# extract_echo_features, analyze_captured_echoes (added 2026-08-28,
# Design_cluster_root_cause_analysis). Verified live against the REAL,
# actual captured bundle from earlier the same session -- confirmed
# programmatically what had already been observed manually: the stale
# tool argument exactly matches the immediately preceding turn's own
# real symbol (stale_argument_matches_preceding_turn: True). Also
# caught and fixed a real bug during this same live verification: the
# has_custom_message fallback (for captures predating the new
# scenario_turn field) was defaulting to False for a prompt that
# clearly wasn't the fixed template -- fixed to compare against the
# real, exact template string instead.
# ---------------------------------------------------------------------------

def _real_bundle_with_scenario_turn(matching=True):
    stale_arg = "KTOS" if matching else "RGTI"
    return {
        "affected_turn_index": 1,
        "turns_captured": [
            {"turn_index": 0, "prompt": "Whats KTOS trading at right now?",
             "raw_events": [{"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'}],
             "classification": {}, "scenario_turn": {"type": "ticker", "symbol": "KTOS"}},
            {"turn_index": 1, "prompt": "What about RGTI though?",
             "raw_events": [{"type": "tool_start", "tool": "lookup_ticker",
                              "command": json.dumps({"symbol": stale_arg})}],
             "classification": {}, "scenario_turn": {"type": "ticker", "symbol": "RGTI", "message": "What about RGTI though?"}},
        ],
    }


def test_extract_echo_features_detects_matching_stale_argument():
    bundle = _real_bundle_with_scenario_turn(matching=True)
    features = _harness.extract_echo_features(bundle)
    assert features["stale_argument_symbol"] == "KTOS"
    assert features["preceding_turn_symbol"] == "KTOS"
    assert features["stale_argument_matches_preceding_turn"] is True
    assert features["has_custom_message"] is True
    assert features["turns_before_affected"] == 1


def test_extract_echo_features_correctly_reports_non_matching_case():
    bundle = _real_bundle_with_scenario_turn(matching=False)
    features = _harness.extract_echo_features(bundle)
    assert features["stale_argument_symbol"] == "RGTI"
    assert features["stale_argument_matches_preceding_turn"] is False


def test_extract_echo_features_fallback_without_scenario_turn():
    """Real, deliberate fallback for captures predating the
    scenario_turn field -- must still correctly extract has_custom_
    message by comparing against the real, exact fixed template."""
    bundle = {
        "affected_turn_index": 0,
        "turns_captured": [{
            "turn_index": 0, "prompt": "So, what's KTOS at these days?",
            "raw_events": [{"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "SOUN"}'}],
            "classification": {},
        }],
    }
    features = _harness.extract_echo_features(bundle)
    assert features["has_custom_message"] is True  # doesn't match the fixed template
    assert features["stale_argument_symbol"] == "SOUN"


def test_extract_echo_features_standard_template_is_not_custom():
    bundle = {
        "affected_turn_index": 0,
        "turns_captured": [{
            "turn_index": 0, "prompt": "Whats SOUN trading at right now?",
            "raw_events": [], "classification": {},
        }],
    }
    features = _harness.extract_echo_features(bundle)
    assert features["has_custom_message"] is False


def test_analyze_captured_echoes_real_capture_file(tmp_path):
    """Real, direct verification against the actual, real captured
    bundle saved during an earlier task -- not synthetic data."""
    real_captures_dir = os.path.join(ROOT, "scripts", "captures")
    if not any(f.startswith("tool_argument_echo_") for f in os.listdir(real_captures_dir)):
        pytest.skip("no real tool_argument_echo capture present in this checkout")
    result = _harness.analyze_captured_echoes(real_captures_dir)
    assert result["sample_size"] >= 1
    assert result["features"][0]["stale_argument_matches_preceding_turn"] is True


def test_analyze_captured_echoes_empty_directory_is_honest(tmp_path):
    result = _harness.analyze_captured_echoes(str(tmp_path))
    assert result["sample_size"] == 0
    assert "No real captures found" in result["note"]


def test_analyze_captured_echoes_small_sample_note_is_honest_about_confidence(tmp_path):
    bundle = _real_bundle_with_scenario_turn(matching=True)
    path = tmp_path / "tool_argument_echo_test_1.json"
    path.write_text(json.dumps(bundle))
    result = _harness.analyze_captured_echoes(str(tmp_path))
    assert result["sample_size"] == 1
    assert "too few for real statistical confidence" in result["note"]


# ---------------------------------------------------------------------------
# extract_echo_features deepening (added 2026-08-28, Analyze_captured_
# echoes). Verified live against the REAL, actual captured bundle --
# caught and fixed a real bug during this same verification (the
# memories_mention_correct_symbol fallback was defaulting to False for
# the one existing real capture, which genuinely predates scenario_turn,
# rather than genuinely reflecting that the memory text didn't mention
# RGTI -- it does). The fixed extraction now correctly surfaces a real,
# previously only-manually-noticed finding: the real, injected memory
# text mentioned BOTH the correct (RGTI) and stale (KTOS) symbol
# together in the same real capture.
# ---------------------------------------------------------------------------

def _real_bundle_for_deepened_features():
    return {
        "affected_turn_index": 1,
        "turns_captured": [
            {"turn_index": 0, "prompt": "Whats KTOS trading at right now?",
             "raw_events": [
                 {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
                 {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 52", "exit_code": 0},
                 {"delta": "KTOS is at $52."},
             ],
             "classification": {}, "scenario_turn": {"type": "ticker", "symbol": "KTOS"}},
            {"turn_index": 1, "prompt": "What about RGTI though?",
             "raw_events": [
                 {"type": "memories_used", "data": [
                     {"text": "User follows KTOS and RGTI closely.", "category": "preference", "type": "recalled"},
                 ]},
                 {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
                 {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 52", "exit_code": 0},
                 {"type": "agent_step", "round": 2},
             ],
             "classification": {}, "scenario_turn": {"type": "ticker", "symbol": "RGTI", "message": "What about RGTI though?"}},
        ],
    }


def test_extract_echo_features_round_count_and_final_round_empty():
    bundle = _real_bundle_for_deepened_features()
    features = _harness.extract_echo_features(bundle)
    assert features["round_count"] == 2
    assert features["final_round_empty"] is True  # round 2 has no content, no tool calls


def test_extract_echo_features_memory_signals_with_scenario_turn():
    bundle = _real_bundle_for_deepened_features()
    features = _harness.extract_echo_features(bundle)
    assert features["memories_used_count"] == 1
    assert features["memories_mention_correct_symbol"] is True  # mentions RGTI
    assert features["memories_mention_stale_symbol"] is True    # also mentions KTOS


def test_extract_echo_features_memory_signals_false_when_absent():
    bundle = _real_bundle_for_deepened_features()
    bundle["turns_captured"][1]["raw_events"] = [
        e for e in bundle["turns_captured"][1]["raw_events"] if e.get("type") != "memories_used"
    ]
    features = _harness.extract_echo_features(bundle)
    assert features["memories_used_count"] == 0
    assert features["memories_mention_correct_symbol"] is False
    assert features["memories_mention_stale_symbol"] is False


def test_extract_echo_features_preceding_turn_content_preview():
    bundle = _real_bundle_for_deepened_features()
    features = _harness.extract_echo_features(bundle)
    assert "KTOS is at $52" in features["preceding_turn_content_preview"]


def test_extract_echo_features_correct_symbol_fallback_from_prompt_text():
    """Real, deliberate fallback for captures predating scenario_turn:
    best-effort extraction of the real, correct symbol from the
    affected turn's own real prompt text, using the real, known
    IN_TRAINING_TICKERS pool."""
    bundle = {
        "affected_turn_index": 0,
        "turns_captured": [{
            "turn_index": 0,
            "prompt": "So I was just reading an article and it made me think about RGTI -- anyway, what's it trading at?",
            "raw_events": [
                {"type": "memories_used", "data": [{"text": "User follows RGTI.", "category": "fact", "type": "recalled"}]},
                {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
            ],
            "classification": {},
        }],
    }
    features = _harness.extract_echo_features(bundle)
    assert features["memories_mention_correct_symbol"] is True


def test_extract_echo_features_real_capture_reproduces_manual_finding():
    """Real, direct verification against the actual, real captured
    bundle -- the deepened extraction must correctly reproduce what
    was manually observed by re-reading the raw JSON: the real memory
    text mentions BOTH the correct and stale symbol together."""
    real_capture_path = os.path.join(ROOT, "scripts", "captures",
                                      "tool_argument_echo_prompt_shape_variety_1788042034.json")
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    features = _harness.extract_echo_features(bundle)
    assert features["memories_mention_correct_symbol"] is True
    assert features["memories_mention_stale_symbol"] is True
    assert features["round_count"] == 2
    assert features["final_round_empty"] is True


def test_analyze_captured_echoes_note_includes_memory_overlap_signal(tmp_path):
    bundle = _real_bundle_for_deepened_features()
    path = tmp_path / "tool_argument_echo_test_1.json"
    path.write_text(json.dumps(bundle))
    result = _harness.analyze_captured_echoes(str(tmp_path))
    assert "1/1 had real, injected memories mentioning BOTH" in result["note"]


# ---------------------------------------------------------------------------
# check_for_malformed_event_patterns (added 2026-08-28,
# Investigate_malformed_event_paths). Real, honest scope note carried
# into these tests: send_message() silently discards any raw SSE line
# that fails JSON parsing before this harness ever sees it -- no capture
# made with this harness can detect truly unparseable SSE lines, only
# structural anomalies among events that DID parse successfully.
# Verified live against the REAL, actual captured bundle -- confirmed a
# genuinely clean, honest negative result: no structural anomalies in
# any turn, including the affected one, ruling out (at this level of
# observation) any correlation between malformed event delivery and the
# real tool_argument_echo occurrence.
# ---------------------------------------------------------------------------

def test_check_for_malformed_event_patterns_clean_events():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
        {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 52", "exit_code": 0},
        {"delta": "KTOS is at $52."},
    ]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["has_any_anomaly"] is False
    assert result["orphaned_tool_outputs"] == 0
    assert result["malformed_tool_commands"] == 0
    assert result["tool_outputs_missing_output_field"] == 0
    assert result["unexpected_event_types"] == []


def test_check_for_malformed_event_patterns_detects_orphaned_tool_output():
    events = [
        {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 52", "exit_code": 0},
    ]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["orphaned_tool_outputs"] == 1
    assert result["has_any_anomaly"] is True


def test_check_for_malformed_event_patterns_detects_malformed_command():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker", "command": "{not valid json"},
    ]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["malformed_tool_commands"] == 1
    assert result["has_any_anomaly"] is True


def test_check_for_malformed_event_patterns_detects_missing_output_field():
    events = [
        {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "KTOS"}'},
        {"type": "tool_output", "tool": "lookup_ticker", "exit_code": 0},  # no "output" key
    ]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["tool_outputs_missing_output_field"] == 1
    assert result["has_any_anomaly"] is True


def test_check_for_malformed_event_patterns_detects_unexpected_event_type():
    events = [{"type": "some_never_before_seen_event_type"}]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["unexpected_event_types"] == ["some_never_before_seen_event_type"]
    assert result["has_any_anomaly"] is True


def test_check_for_malformed_event_patterns_matches_tool_start_and_output_correctly():
    """Real, deliberate design: a real tool_output correctly consumes
    its matching real tool_start, even with multiple real calls to the
    same tool in one turn -- must not falsely flag the second, real,
    correctly-paired output as orphaned."""
    events = [
        {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "A"}'},
        {"type": "tool_output", "tool": "lookup_ticker", "output": "a", "exit_code": 0},
        {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "B"}'},
        {"type": "tool_output", "tool": "lookup_ticker", "output": "b", "exit_code": 0},
    ]
    result = _harness.check_for_malformed_event_patterns(events)
    assert result["orphaned_tool_outputs"] == 0


def test_check_for_malformed_event_patterns_empty_events():
    result = _harness.check_for_malformed_event_patterns([])
    assert result["has_any_anomaly"] is False


def test_extract_echo_features_includes_malformed_event_check():
    bundle = _real_bundle_for_deepened_features()
    features = _harness.extract_echo_features(bundle)
    assert "has_malformed_event_pattern" in features
    assert "malformed_event_detail" in features


def test_extract_echo_features_real_capture_shows_no_malformed_events():
    """Real, direct verification against the actual, real captured
    bundle -- confirms the real, honest negative finding: no
    structural event anomalies correlate with the real
    tool_argument_echo occurrence."""
    real_capture_path = os.path.join(ROOT, "scripts", "captures",
                                      "tool_argument_echo_prompt_shape_variety_1788042034.json")
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    features = _harness.extract_echo_features(bundle)
    assert features["has_malformed_event_pattern"] is False


# ---------------------------------------------------------------------------
# print_capabilities_overview (added 2026-08-30,
# Explore_new_harness_capability). Real, direct trigger: confirmed the
# default argparse --help output had genuinely grown large (52 real
# flags, ~330 lines) across this session's real feature growth, with
# no grouping at all -- built a real, curated, organized overview
# instead. Verified live end-to-end via --list-modes against the real,
# deployed harness, including confirming it takes priority over other
# flags and exits cleanly with no side effects.
# ---------------------------------------------------------------------------

def test_print_capabilities_overview_runs_without_error(capsys):
    _harness.print_capabilities_overview()
    captured = capsys.readouterr()
    assert "capabilities overview" in captured.out.lower()
    assert "Run scenarios & suites" in captured.out
    assert "Debugging a specific anomaly" in captured.out


def test_print_capabilities_overview_flags_all_exist_in_real_parser():
    """Real, deliberate guard against documentation drift: every
    top-level flag named in the curated overview must actually exist
    as a real, registered argparse flag in this harness, so the
    overview can never silently reference a real flag that got
    renamed or removed."""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _harness.print_capabilities_overview()
    output = buf.getvalue()

    # Extract every "--flag-name" token mentioned, ignoring the
    # indented "(modifier)" lines' own flags which are checked too.
    import re
    mentioned_flags = set(re.findall(r"--[a-z][a-z0-9-]*", output))
    # "--help" is argparse's own automatic, built-in flag -- never
    # explicitly add_argument()'d, so it's real but correctly excluded
    # from this specific drift check.
    mentioned_flags.discard("--help")
    assert mentioned_flags, "overview should mention at least one real flag"

    real_parser_source = inspect.getsource(_harness.main)
    for flag in mentioned_flags:
        assert f'"{flag}"' in real_parser_source, f"{flag} is not a real, registered argparse flag"


# ---------------------------------------------------------------------------
# send_message() malformed_lines parameter, and its threading through
# check_for_malformed_event_patterns()/extract_echo_features() (added
# 2026-08-30, Extend_capture_layer). Real, direct continuation of the
# real, honest scope limit found in Investigate_malformed_event_paths:
# genuinely unparseable raw SSE lines were previously invisible to this
# harness entirely -- now, when a caller opts in, they're captured and
# saved as real, inspectable data. Verified live with a real, controlled,
# deliberately malformed raw response (not reproducible on demand from
# the actual backend), and verified backward compatibility directly:
# every existing real call site was confirmed, by running this harness's
# own full test suite immediately after the change, to produce byte-for-
# byte identical output when the new parameter is omitted.
# ---------------------------------------------------------------------------

def test_send_message_captures_malformed_line_when_opted_in(monkeypatch):
    def fake_request(method, path, body):
        return (
            'data: {"type": "model_info", "model": "x"}\n'
            'data: {broken json here\n'
            'data: {"delta": "hello"}\n'
            'data: [DONE]\n'
        )

    monkeypatch.setattr(_harness, "_request", fake_request)
    malformed = []
    events = _harness.send_message("sess", "hi", "model", malformed_lines=malformed)
    assert len(events) == 2
    assert len(malformed) == 1
    assert malformed[0]["raw_line"] == "{broken json here"
    assert "error" in malformed[0]


def test_send_message_backward_compatible_when_not_opted_in(monkeypatch):
    """Real, deliberate backward-compatibility check: omitting
    malformed_lines must produce identical parsed events to before."""
    def fake_request(method, path, body):
        return (
            'data: {"type": "model_info", "model": "x"}\n'
            'data: {broken json here\n'
            'data: {"delta": "hello"}\n'
            'data: [DONE]\n'
        )

    monkeypatch.setattr(_harness, "_request", fake_request)
    events = _harness.send_message("sess", "hi", "model")
    assert len(events) == 2  # identical to the opted-in case above


def test_send_message_no_malformed_lines_when_all_valid(monkeypatch):
    def fake_request(method, path, body):
        return 'data: {"delta": "hello"}\ndata: [DONE]\n'

    monkeypatch.setattr(_harness, "_request", fake_request)
    malformed = []
    _harness.send_message("sess", "hi", "model", malformed_lines=malformed)
    assert malformed == []


def test_check_for_malformed_event_patterns_reports_unparseable_line_count():
    result = _harness.check_for_malformed_event_patterns(
        [], malformed_lines=[{"raw_line": "bad", "error": "x"}, {"raw_line": "bad2", "error": "y"}]
    )
    assert result["unparseable_sse_line_count"] == 2
    assert result["has_any_anomaly"] is True


def test_check_for_malformed_event_patterns_honest_default_without_malformed_lines():
    """Real, honest handling for older captures predating this field:
    an empty/absent malformed_lines means 0 reported, not an error --
    an honest "none observed/available", not a false claim of zero
    occurrences."""
    result = _harness.check_for_malformed_event_patterns([])
    assert result["unparseable_sse_line_count"] == 0


def test_extract_echo_features_threads_malformed_lines_through():
    bundle = _real_bundle_for_deepened_features()
    bundle["turns_captured"][1]["malformed_lines"] = [{"raw_line": "bad", "error": "x"}]
    features = _harness.extract_echo_features(bundle)
    assert features["malformed_event_detail"]["unparseable_sse_line_count"] == 1
    assert features["has_malformed_event_pattern"] is True


# ---------------------------------------------------------------------------
# extract_holdings_fabrication_features (added 2026-08-30, a fresh
# investigation thread -- shifted here per explicit choice after the
# malformed-SSE-line capture-layer work completed). Discovered by
# applying this harness's already-general-purpose toolkit
# (capture_raw_events_for_check(), render_replay_transcript(),
# check_for_malformed_event_patterns() -- ALL reused completely
# unchanged, zero modification needed) to a real check
# (holdings_note_not_a_real_holding) that had never been deeply
# investigated. Real, captured, and more concerning than
# tool_argument_echo: the model's holdings note claimed specific
# financial numbers ("5 shares... pending buy order for 3 more") with
# ZERO grounding in the real, injected memory ("a pending buy order
# for 1 RGTI share") -- confirmed programmatically, not just by eye.
# ---------------------------------------------------------------------------

def _real_holdings_fabrication_bundle(grounded_numbers=False):
    memory_text = "User has a pending buy order for 5 RGTI shares." if grounded_numbers \
        else "User has a pending buy order for 1 RGTI share."
    return {
        "affected_turn_index": 0,
        "turns_captured": [{
            "turn_index": 0,
            "prompt": "Whats RGTI trading at right now?",
            "raw_events": [
                {"type": "memories_used", "data": [{"text": memory_text, "category": "project", "type": "recalled"}]},
                {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "RGTI"}'},
                {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 15.59", "exit_code": 0},
                {"type": "agent_step", "round": 2},
                {"delta": "RGTI is at $15.59. (Note: the stored reference document lists 5 shares "
                          "of RGTI, with a separate, unexecuted pending buy order for 3 more.)"},
            ],
            "classification": {},
        }],
    }


def test_extract_holdings_fabrication_features_detects_ungrounded_numbers():
    bundle = _real_holdings_fabrication_bundle(grounded_numbers=False)
    features = _harness.extract_holdings_fabrication_features(bundle)
    assert features["note_ticker"] == "RGTI"
    assert set(features["note_numbers"]) == {"5", "3"}
    assert features["note_numbers_grounded_in_memory"] is False
    assert features["any_note_number_grounded"] is False


def test_extract_holdings_fabrication_features_detects_partially_grounded_numbers():
    """Real, deliberate check: one of the note's two numbers (5)
    genuinely appears in real memory text this time, distinguishing
    'partially real' from 'totally fabricated'."""
    bundle = _real_holdings_fabrication_bundle(grounded_numbers=True)
    features = _harness.extract_holdings_fabrication_features(bundle)
    assert features["any_note_number_grounded"] is True
    assert features["note_numbers_grounded_in_memory"] is False  # "3" still isn't grounded


def test_extract_holdings_fabrication_features_extracts_note_text():
    bundle = _real_holdings_fabrication_bundle()
    features = _harness.extract_holdings_fabrication_features(bundle)
    assert features["note_text"].startswith("(Note:")
    assert "5 shares" in features["note_text"]


def test_extract_holdings_fabrication_features_real_capture_confirms_total_fabrication():
    """Real, direct verification against the actual, real captured
    bundle from this fresh investigation thread -- not synthetic
    data. Programmatically confirms what was found by direct
    inspection: zero grounding for either number in the note."""
    real_capture_path = os.path.join(
        ROOT, "scripts", "captures",
        "holdings_note_not_a_real_holding_mixed_holdings_default_1788124453.json"
    )
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    features = _harness.extract_holdings_fabrication_features(bundle)
    assert features["note_ticker"] == "RGTI"
    assert set(features["note_numbers"]) == {"5", "3"}
    assert features["note_numbers_grounded_in_memory"] is False
    assert features["any_note_number_grounded"] is False


# ---------------------------------------------------------------------------
# check_numeric_grounding (added 2026-08-30, Design_holdings_safety_
# checks). Real, general-purpose, hardened version of the grounding
# logic extract_holdings_fabrication_features() used inline -- built
# after directly confirming a real correctness bug in the original,
# naive substring-based check (a claimed "15" would incorrectly
# register as grounded if the known facts contained "$115.00" anywhere,
# since "15" is a real substring of "115"). extract_holdings_
# fabrication_features() now calls this hardened function instead of
# duplicating the logic; verified the real captured finding is
# unchanged after this refactor (the collision case didn't happen to
# occur in that specific real capture, but the check is now reliable
# rather than coincidentally correct).
# ---------------------------------------------------------------------------

def test_check_numeric_grounding_rejects_substring_false_positive():
    """The real bug this function was built to fix, tested directly."""
    result = _harness.check_numeric_grounding(["15"], "SOUN is trading at 115.00 today")
    assert result["ungrounded"] == ["15"]
    assert result["any_grounded"] is False


def test_check_numeric_grounding_detects_genuine_grounding():
    result = _harness.check_numeric_grounding(["15"], "The price is 15 dollars")
    assert result["grounded"] == ["15"]
    assert result["all_grounded"] is True


def test_check_numeric_grounding_mixed_grounded_and_ungrounded():
    result = _harness.check_numeric_grounding(["5", "99"], "User has 5 shares total")
    assert result["grounded"] == ["5"]
    assert result["ungrounded"] == ["99"]
    assert result["all_grounded"] is False
    assert result["any_grounded"] is True


def test_check_numeric_grounding_empty_claim_numbers():
    result = _harness.check_numeric_grounding([], "some real facts text")
    assert result["all_grounded"] is False  # honest: no claim to ground at all
    assert result["any_grounded"] is False


def test_check_numeric_grounding_all_grounded():
    result = _harness.check_numeric_grounding(["1", "2"], "quantities are 1 and 2")
    assert result["all_grounded"] is True


def test_extract_holdings_fabrication_features_still_correct_after_refactor():
    """Real, direct regression check: the refactor to reuse
    check_numeric_grounding() must not change extract_holdings_
    fabrication_features()'s own real, already-tested behavior."""
    bundle = _real_holdings_fabrication_bundle(grounded_numbers=False)
    features = _harness.extract_holdings_fabrication_features(bundle)
    assert features["note_numbers_grounded_in_memory"] is False
    assert features["any_note_number_grounded"] is False


# ---------------------------------------------------------------------------
# check_turn_holdings_integrity, check_bundle_holdings_integrity (added
# 2026-08-30, Per-turn holdings integrity check). Real, direct
# motivation: extract_holdings_fabrication_features() only ever
# examined bundle["affected_turn_index"] -- a real blind spot for any
# OTHER turn in the same capture, confirmed directly by checking both
# existing real captures for a holdings note on any non-affected turn
# (found none this specific time, but the blind spot is real
# regardless -- a capture made for a completely different check could
# easily contain an unnoticed holdings issue elsewhere). Verified live
# against BOTH real, actual captured bundles: correctly flags the one
# known real issue (turn 4, RGTI) and correctly shows every other real
# turn across both captures as clean, with no false positives.
# ---------------------------------------------------------------------------

def _turn_with_holdings_note(ticker, numbers, memory_text, turn_index=0):
    note = f"(Note: the stored reference document lists {numbers[0]} shares of {ticker}, " \
           f"with a separate, unexecuted pending buy order for {numbers[1]} more.)"
    return {
        "turn_index": turn_index,
        "prompt": f"Whats {ticker} trading at right now?",
        "raw_events": [
            {"type": "memories_used", "data": [{"text": memory_text, "category": "project", "type": "recalled"}]},
            {"type": "tool_start", "tool": "lookup_ticker", "command": f'{{"symbol": "{ticker}"}}'},
            {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 10", "exit_code": 0},
            {"type": "agent_step", "round": 2},
            {"delta": f"{ticker} is at $10. {note}"},
        ],
    }


def _clean_turn_no_note(turn_index=0):
    return {
        "turn_index": turn_index, "prompt": "Whats SOUN trading at right now?",
        "raw_events": [
            {"type": "tool_start", "tool": "lookup_ticker", "command": '{"symbol": "SOUN"}'},
            {"type": "tool_output", "tool": "lookup_ticker", "output": "price: 7.11", "exit_code": 0},
            {"delta": "SOUN is at $7.11."},
        ],
    }


def test_check_turn_holdings_integrity_no_note_is_clean():
    result = _harness.check_turn_holdings_integrity(_clean_turn_no_note())
    assert result["has_holdings_note"] is False
    assert result["has_integrity_issue"] is False


def test_check_turn_holdings_integrity_flags_non_real_holding():
    turn = _turn_with_holdings_note("RGTI", ["5", "3"], "User has a pending buy order for 1 RGTI share.")
    result = _harness.check_turn_holdings_integrity(turn)
    assert result["has_holdings_note"] is True
    assert result["is_real_holding"] is False  # RGTI is not in REAL_DK_STOCK_HOLDINGS
    assert result["has_integrity_issue"] is True


def test_check_turn_holdings_integrity_flags_ungrounded_numbers_for_real_holding():
    """Real, deliberate check: even a note for a genuine DK holding
    still gets flagged if its claimed numbers aren't grounded."""
    turn = _turn_with_holdings_note("KTOS", ["99", "50"], "User follows KTOS closely.")
    result = _harness.check_turn_holdings_integrity(turn)
    assert result["is_real_holding"] is True  # KTOS IS a real DK holding
    assert result["numbers_grounded"]["all_grounded"] is False
    assert result["has_integrity_issue"] is True  # still flagged, despite being a real holding


def test_check_turn_holdings_integrity_clean_when_real_holding_and_grounded():
    turn = _turn_with_holdings_note("KTOS", ["16", "1"], "User holds 16 shares of KTOS, pending 1 more.")
    result = _harness.check_turn_holdings_integrity(turn)
    assert result["is_real_holding"] is True
    assert result["numbers_grounded"]["all_grounded"] is True
    assert result["has_integrity_issue"] is False


def test_check_bundle_holdings_integrity_checks_every_turn_not_just_affected():
    """Real, direct proof this closes the actual blind spot: a bundle
    with the issue on turn 2, while affected_turn_index points at turn
    0 (a different, clean turn) -- the old function would have missed
    this entirely."""
    bundle = {
        "affected_turn_index": 0,
        "turns_captured": [
            _clean_turn_no_note(turn_index=0),
            _clean_turn_no_note(turn_index=1),
            _turn_with_holdings_note("RGTI", ["5", "3"], "User has a pending buy order for 1 RGTI share.",
                                      turn_index=2),
        ],
    }
    results = _harness.check_bundle_holdings_integrity(bundle)
    assert len(results) == 3
    assert results[0]["has_integrity_issue"] is False
    assert results[1]["has_integrity_issue"] is False
    assert results[2]["has_integrity_issue"] is True
    assert results[2]["turn_index"] == 2


def test_check_bundle_holdings_integrity_real_capture_matches_known_finding():
    """Real, direct verification against the actual, real captured
    bundle -- confirms the exact known result and, critically, that
    every other real turn in the same capture is correctly clean."""
    real_capture_path = os.path.join(
        ROOT, "scripts", "captures",
        "holdings_note_not_a_real_holding_mixed_holdings_default_1788124453.json"
    )
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    results = _harness.check_bundle_holdings_integrity(bundle)
    issues = [r for r in results if r["has_integrity_issue"]]
    assert len(issues) == 1
    assert issues[0]["turn_index"] == 4
    assert issues[0]["note_ticker"] == "RGTI"


# ---------------------------------------------------------------------------
# generate_holdings_integrity_report (added 2026-08-30, Per-Turn
# Integrity Dashboard). Genuinely new territory: existing reports show
# real flag_counts, but never the specific, structured per-turn detail
# a holdings-integrity check produces. Reuses check_bundle_holdings_
# integrity() and the established dark-terminal CSS unchanged. Verified
# live against BOTH real captured bundles.
# ---------------------------------------------------------------------------

def test_holdings_integrity_report_renders_real_issue():
    bundle = {
        "scenario_name": "test", "model": "m1", "affected_turn_index": 0,
        "turns_captured": [
            _turn_with_holdings_note("RGTI", ["5", "3"], "User has a pending buy order for 1 RGTI share."),
        ],
    }
    html = _harness.generate_holdings_integrity_report(bundle)
    assert html.startswith("<!DOCTYPE html>")
    assert '<tr class="issue-row">' in html
    assert "RGTI" in html
    assert "NOT a real holding" in html
    assert ">fabricated<" in html


def test_holdings_integrity_report_renders_clean_no_note_turns():
    bundle = {
        "scenario_name": "test", "model": "m1", "affected_turn_index": 0,
        "turns_captured": [_clean_turn_no_note(), _clean_turn_no_note(turn_index=1)],
    }
    html = _harness.generate_holdings_integrity_report(bundle)
    assert html.count('<tr class="no-note">') == 2
    assert '<tr class="issue-row">' not in html


def test_holdings_integrity_report_shows_correct_issue_count():
    bundle = {
        "scenario_name": "test", "model": "m1", "affected_turn_index": 0,
        "turns_captured": [
            _clean_turn_no_note(),
            _turn_with_holdings_note("RGTI", ["5", "3"], "no relevant memory", turn_index=1),
        ],
    }
    html = _harness.generate_holdings_integrity_report(bundle)
    assert "<div class=\"stat-value\" style=\"color:var(--bad)\">1</div>" in html


def test_holdings_integrity_report_writes_file_when_path_given(tmp_path):
    bundle = {
        "scenario_name": "test", "model": "m1", "affected_turn_index": 0,
        "turns_captured": [_clean_turn_no_note()],
    }
    out_path = str(tmp_path / "report.html")
    returned = _harness.generate_holdings_integrity_report(bundle, output_path=out_path)
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        written = f.read()
    assert written == returned


def test_holdings_integrity_report_real_capture_with_issue():
    """Real, direct verification against the actual captured bundle
    with a known real issue."""
    real_capture_path = os.path.join(
        ROOT, "scripts", "captures",
        "holdings_note_not_a_real_holding_mixed_holdings_default_1788124453.json"
    )
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    html = _harness.generate_holdings_integrity_report(bundle)
    assert html.count('<tr class="issue-row">') == 1
    assert "RGTI" in html


def test_holdings_integrity_report_real_capture_all_clean():
    """Real, direct verification against the actual, unrelated
    tool_argument_echo capture, which has no holdings notes at all --
    must render zero issue rows, not a false positive."""
    real_capture_path = os.path.join(
        ROOT, "scripts", "captures",
        "tool_argument_echo_prompt_shape_variety_1788042034.json"
    )
    if not os.path.isfile(real_capture_path):
        pytest.skip("real capture file not present in this checkout")
    with open(real_capture_path) as f:
        bundle = json.load(f)
    html = _harness.generate_holdings_integrity_report(bundle)
    assert html.count('<tr class="issue-row">') == 0
    assert html.count('<tr class="no-note">') == 3


# ---------------------------------------------------------------------------
# accumulate_captures_for_check (added 2026-08-30, Accumulate_more_
# holdings_integrity_captures). Real, deterministic verification via
# monkeypatched _holdings_note_contamination() -- confirmed, during
# testing, that check_trial() alone isn't the function whose result
# determines r["holdings_note_not_a_real_holding"] in the real code
# flow: _holdings_note_contamination()'s own result overwrites it
# afterward, so mocking check_trial() alone silently fails to control
# the real outcome -- caught this directly by investigating an
# unexpected test result rather than assume the mock was correct.
# ---------------------------------------------------------------------------

def _mock_accumulate_dependencies(monkeypatch, fire_on_attempts):
    monkeypatch.setattr(_harness, "create_session", lambda *a, **kw: "fake_session")
    monkeypatch.setattr(_harness, "send_message", lambda *a, **kw: [{"delta": "x"}])
    monkeypatch.setattr(_harness, "check_trial", lambda events: {"full_content": "", "made_tool_call": True})
    monkeypatch.setattr(_harness, "validate_turn", lambda turn, r: {})
    monkeypatch.setattr(_harness, "_tool_argument_echo", lambda turn, r: False)

    attempt_n = {"n": 0}

    def deterministic_contamination(content, turn):
        attempt_n["n"] += 1
        return {"wrong_ticker": False, "not_a_real_holding": attempt_n["n"] in fire_on_attempts}

    monkeypatch.setattr(_harness, "_holdings_note_contamination", deterministic_contamination)


def test_accumulate_captures_stops_at_target_count(tmp_path, monkeypatch):
    _mock_accumulate_dependencies(monkeypatch, fire_on_attempts={2, 3, 5})
    scenario = {"name": "mock_scenario", "turns": [{"type": "ticker", "symbol": "X"}]}
    paths = _harness.accumulate_captures_for_check(
        "77bddaa5", "m1", scenario, str(tmp_path),
        target_check="holdings_note_not_a_real_holding", target_count=3, max_attempts=10, verbose=False,
    )
    assert len(paths) == 3
    for p in paths:
        assert os.path.isfile(p)


def test_accumulate_captures_saves_each_occurrence_distinctly(tmp_path, monkeypatch):
    _mock_accumulate_dependencies(monkeypatch, fire_on_attempts={1, 2, 3})
    scenario = {"name": "mock_scenario", "turns": [{"type": "ticker", "symbol": "X"}]}
    paths = _harness.accumulate_captures_for_check(
        "77bddaa5", "m1", scenario, str(tmp_path),
        target_check="holdings_note_not_a_real_holding", target_count=3, max_attempts=5, verbose=False,
    )
    assert len(set(paths)) == 3  # all distinct real files, no overwriting


def test_accumulate_captures_honest_partial_result_when_exhausted(tmp_path, monkeypatch):
    """Real, honest design: if max_attempts runs out before target_count
    is reached, returns whatever was actually captured, not an error."""
    _mock_accumulate_dependencies(monkeypatch, fire_on_attempts={2})
    scenario = {"name": "mock_scenario", "turns": [{"type": "ticker", "symbol": "X"}]}
    paths = _harness.accumulate_captures_for_check(
        "77bddaa5", "m1", scenario, str(tmp_path),
        target_check="holdings_note_not_a_real_holding", target_count=5, max_attempts=4, verbose=False,
    )
    assert len(paths) == 1  # only 1 real occurrence within the 4 real attempts allowed


def test_accumulate_captures_zero_occurrences_returns_empty_list(tmp_path, monkeypatch):
    _mock_accumulate_dependencies(monkeypatch, fire_on_attempts=set())
    scenario = {"name": "mock_scenario", "turns": [{"type": "ticker", "symbol": "X"}]}
    paths = _harness.accumulate_captures_for_check(
        "77bddaa5", "m1", scenario, str(tmp_path),
        target_check="holdings_note_not_a_real_holding", target_count=3, max_attempts=3, verbose=False,
    )
    assert paths == []
