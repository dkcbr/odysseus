import pytest
from src.text_helpers import strip_think

def test_strip_think_cases():
    # 1. Mid-text unclosed leak (fails before fix)
    assert strip_think("Hello! <think> I am thinking.") == "Hello!"
    assert strip_think("Sure.\n<think>\nLet me reconsider...") == "Sure."
    assert strip_think("Sure.\n<thinking>\nLet me reconsider...") == "Sure."

    # 2. Start-anchored unclosed
    assert strip_think("<think> unclosed from start") == ""
    assert strip_think("   <thinking> thinking at start") == ""

    # 3. Closed block
    assert strip_think("Hello! <think> closed </think> Here is the answer.") == "Hello! Here is the answer."
    assert strip_think("Hello! <thinking> closed </thinking> Here is the answer.") == "Hello! Here is the answer."

    # 4. No-tag passthrough
    assert strip_think("No tags here.") == "No tags here."

    # 5. Content-before-opener preserved (part of mid-text unclosed)
    assert strip_think("Prefix text <think> trailing thoughts") == "Prefix text"
    
    # 6. Multiple blocks (closed + unclosed)
    assert strip_think("Hello! <think> closed </think> Here is the answer. <think> unclosed") == "Hello! Here is the answer."


def test_strip_think_handles_thought_tags():
    assert strip_think("<thought>internal reasoning</thought>Final answer.") == "Final answer."


def test_strip_think_handles_gemma4_thought_channel():
    text = "<|channel>thought\ninternal reasoning<channel|>Final answer."
    assert strip_think(text) == "Final answer."


def test_strip_think_handles_empty_gemma4_thought_channel():
    text = "<|channel>thought\n<channel|>Final answer."
    assert strip_think(text) == "Final answer."


def test_strip_think_unwraps_gemma4_response_channel():
    text = "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>"
    assert strip_think(text) == "Final answer."


# --- ReasoningGate (streaming-safe prose heuristic) -------------------------

from src.text_helpers import ReasoningGate


def _feed_all(gate, chunks):
    """Feed a list of delta chunks through a gate, return (flushed_text, was_ever_open_before_flush)."""
    out = ""
    for c in chunks:
        out += gate.feed(c)
    out += gate.flush()
    return out


def test_reasoning_gate_drops_leaked_trace_style_reasoning():
    chunks = [
        "We need to answer recommendation of buy rungs for SPCX. ",
        "Use proper approach: verify skills existence.\n\n",
        "Let me think about which skill applies here before I proceed ",
        "with the actual calculation.\n\n",
        "Based on the current pre-market price of $4.12, here are the buy rungs:\n"
        "1. $4.00\n2. $3.85\n3. $3.70",
    ]
    result = _feed_all(ReasoningGate(), chunks)
    assert "We need to answer" not in result
    assert "Let me think" not in result
    assert result.startswith("Based on the current pre-market price")
    assert "$4.00" in result


def test_reasoning_gate_never_touches_short_normal_answer():
    result = _feed_all(ReasoningGate(), ["The answer is 42."])
    assert result == "The answer is 42."


def test_reasoning_gate_opens_fast_for_long_answer_with_no_paragraph_break():
    # Long enough to clear EARLY_CHECK_CHARS, no blank line anywhere, and
    # doesn't start with a reasoning-prefix phrase -- should NOT wait for
    # end-of-round; feed() itself should return non-empty before flush().
    long_answer = "Your Oracle Cloud account was suspended for a Terms of Service violation. " * 5
    gate = ReasoningGate()
    got_early_output = False
    out = ""
    for i in range(0, len(long_answer), 10):
        piece = gate.feed(long_answer[i:i + 10])
        if piece:
            got_early_output = True
        out += piece
    out += gate.flush()
    assert got_early_output, "should have opened well before end-of-round"
    assert out == long_answer


def test_reasoning_gate_never_swallows_single_reasoning_flavored_paragraph():
    # Matches _strip_reasoning_prose's own rule: a single paragraph (no
    # blank line) is never stripped, even if it starts with a reasoning
    # phrase -- there's no way to know it isn't actually the real answer.
    text = "I need to think about this differently than I initially assumed, and here is my final answer: 42."
    result = _feed_all(ReasoningGate(), [text])
    assert result == text


def test_reasoning_gate_safety_cap_flushes_long_reasoning_looking_paragraph():
    # Starts with a reasoning phrase AND is long AND has no paragraph break
    # anywhere -- should eventually flush via the safety cap rather than
    # buffer forever.
    long_reasoning = "I need to consider several different factors before responding. " * 40
    assert len(long_reasoning) > ReasoningGate.SAFETY_CAP_CHARS
    gate = ReasoningGate()
    out = ""
    got_output_before_end = False
    for i in range(0, len(long_reasoning), 50):
        piece = gate.feed(long_reasoning[i:i + 50])
        if piece:
            got_output_before_end = True
        out += piece
    out += gate.flush()
    assert got_output_before_end
    assert out == long_reasoning


def test_reasoning_gate_handles_delta_split_across_keyword_boundary():
    # The reasoning-prefix keyword itself is split across two separate
    # feed() calls -- must still classify correctly since the gate
    # accumulates internally regardless of chunk boundaries.
    chunks = ["I ne", "ed to check something first.\n\n", "Here is the real answer."]
    result = _feed_all(ReasoningGate(), chunks)
    assert result == "Here is the real answer."


def test_reasoning_gate_caps_stripped_paragraphs():
    # More leading reasoning-flavored paragraphs than MAX_STRIPPED_PARAGRAPHS
    # -- should give up stripping and flush rather than hold forever.
    paras = ["I need to check point number {}.".format(i) for i in range(10)]
    chunks = ["\n\n".join(paras) + "\n\nFinal answer here."]
    result = _feed_all(ReasoningGate(), chunks)
    assert "Final answer here." in result
