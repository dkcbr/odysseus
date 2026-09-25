"""Real, added 2026-09-25: a genuine stress test of
_looks_like_inferred_pattern against cases deliberately chosen to be
harder than the original 20-case suite -- ambiguous phrasing,
adversarial rephrasing of the two known bad patterns, and legitimate
content with coincidental number/word patterns.

This is what actually found the two real, concrete gaps fixed the
same day: a false positive ("power tools" as a hobby, wrongly caught
by the original generic "tools?" word match) and several false
negatives (adversarial rephrasing that dropped the word "tool"
entirely, alternate coordinate notations, and frequency synonyms not
in the original word list).
"""
import pytest

from src.ai_interaction import _looks_like_inferred_pattern


# Adversarial rephrasings of the two original real, confirmed bad
# examples -- different wording, same real underlying problem. All
# must still be caught.
ADVERSARIAL_MUST_CATCH = [
    "This user commonly makes use of gods_eye_view",
    "It has been observed that the user often utilizes the bash tool",
    "Repeatedly, the user has targeted coordinates near 40.712, -74.006",
    "The user consistently requests the web_search tool",
    "The user's preferred map location: 40.7128N -74.0060W",
    "The user keeps targeting the same spot: lat 40.7128 long -74.0060",
]

# Legitimate content deliberately chosen to be adjacent to the bad
# patterns -- contains decimal numbers, "the user" + frequency words,
# or snake_case-adjacent content, but must NOT be caught.
LEGITIMATE_MUST_PASS = [
    "The user often uses power tools for woodworking as a hobby",
    "User's GPA was 3.75 out of 4.0",
    "The user mentioned they scored 89.5, 92.3 on their two exams",
    "User's preferred coordinate system for work is WGS84",
    "The user typically responds within a few minutes",
    "The user's workout tracked 5.821 miles at 8.234 minute pace",
    "This is the user's favorite restaurant",
    "The user's github username is dev_dk_2024",
    "The user's email is john_doe@example.com",
]


@pytest.mark.parametrize("text", ADVERSARIAL_MUST_CATCH)
def test_adversarial_rephrasing_still_caught(text):
    assert _looks_like_inferred_pattern(text) is not None


@pytest.mark.parametrize("text", LEGITIMATE_MUST_PASS)
def test_legitimate_adjacent_content_not_caught(text):
    assert _looks_like_inferred_pattern(text) is None
