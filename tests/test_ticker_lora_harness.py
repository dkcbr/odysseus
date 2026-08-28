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
