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
import tempfile
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
