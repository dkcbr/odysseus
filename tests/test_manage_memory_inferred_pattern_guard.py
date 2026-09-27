"""Real, added 2026-09-25: tests for the code-level guard against
saving inferred usage patterns as if they were user-stated memories.

See ai_interaction.py's _looks_like_inferred_pattern for the full,
real background: a prompt-only instruction (agent_loop.py, added
2026-09-22) was found, live, to not reliably stop small/local models
from re-poisoning memory with this exact class of bad entry within
about an hour of that fix. This adds a second, structural, code-level
layer.
"""
import pytest

from src.ai_interaction import _looks_like_inferred_pattern


# The two real, live-confirmed bad entries that motivated this fix.
REAL_CONFIRMED_BAD = [
    "The user frequently uses gods_eye_view tools",
    "The user targets latitude 40.7128, longitude -74.0060",
]

# Additional, plausible variations of the same real failure classes.
OTHER_BAD_PATTERNS = [
    "Coordinates observed repeatedly: 34.0522, -118.2437",
    "The user frequently calls the web_search tool",
    "The user usually uses the bash tool for this",
    "The user typically targets 40.7128, -74.0060",
]

# Legitimate memories that must NOT be caught -- including several
# deliberately chosen to be adjacent to the bad patterns (contain
# "often"/"frequently", contain decimal numbers, third-person "the
# user" phrasing) to specifically guard against an over-broad filter.
LEGITIMATE_MEMORIES = [
    "User's name is DK",
    "User lives in Charlotte, NC",
    "User prefers concise replies",
    "User often works late",
    "User's phone number is 555-1234",
    "User's address is 123 Main St, Charlotte NC 28202",
    "User's height is 5.9 feet",
    "User weighs 175.5 pounds",
    "User's account balance was $1234.56 as of last check",
    "User's zip code is 28202",
    "The user asked to be called DK",
    "The user's dog is named Rex",
    "The user's favorite restaurant is in downtown Charlotte",
]


@pytest.mark.parametrize("text", REAL_CONFIRMED_BAD)
def test_real_confirmed_bad_patterns_are_caught(text):
    assert _looks_like_inferred_pattern(text) is not None


@pytest.mark.parametrize("text", OTHER_BAD_PATTERNS)
def test_other_bad_pattern_variations_are_caught(text):
    assert _looks_like_inferred_pattern(text) is not None


@pytest.mark.parametrize("text", LEGITIMATE_MEMORIES)
def test_legitimate_memories_are_not_caught(text):
    assert _looks_like_inferred_pattern(text) is None


def test_empty_string_is_not_caught():
    assert _looks_like_inferred_pattern("") is None
