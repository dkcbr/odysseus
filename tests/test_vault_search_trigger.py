"""Real, direct tests for detect_vault_search_trigger (added 2026-08-19):
a deterministic, model-independent alternative to relying on the model
to call search_vault, after confirming that unreliable via a real,
live test earlier the same night. Matches process_correction_command's
own established pattern in src/memory.py."""
from src.tool_execution import detect_vault_search_trigger


def test_search_vault_for_matches():
    result = detect_vault_search_trigger("search my vault for Man in the Car Paradox")
    assert result == "Man in the Car Paradox"


def test_search_notes_for_matches():
    result = detect_vault_search_trigger("search notes for compounding")
    assert result == "compounding"


def test_what_does_vault_say_about_matches():
    result = detect_vault_search_trigger("What does my vault say about the Man in the Car Paradox chapter?")
    assert result == "the Man in the Car Paradox chapter"


def test_what_does_obsidian_say_about_matches():
    result = detect_vault_search_trigger("what does obsidian say about savings rate")
    assert result == "savings rate"


def test_find_in_vault_matches():
    result = detect_vault_search_trigger("find in my vault: Kybalion principles")
    assert result == "Kybalion principles"


# Real, added 2026-09-20 following the assumption audit: the original
# three patterns were genuinely anchored with "^" and matched via
# .match(), so any leading text before the trigger phrase (politeness,
# a lead-in sentence) was silently missed -- confirmed directly, live.
# Anchors removed, switched to .search(); a fourth "look/check" pattern
# added too, covering real phrasings the original three didn't attempt
# at all.
def test_matches_with_leading_polite_text():
    result = detect_vault_search_trigger("Can you search my vault for the ticker-lora work?")
    assert result == "the ticker-lora work"


def test_matches_look_in_notes_phrasing():
    result = detect_vault_search_trigger("Look in my notes for the game mode script")
    assert result == "the game mode script"


def test_matches_check_obsidian_notes_without_duplicating_notes_word():
    # Real, caught live while building this fix: "obsidian notes" has
    # two adjacent trigger words: a naive pattern captured "notes about
    # portfolio" instead of just "portfolio". Pins the fix.
    result = detect_vault_search_trigger("Check my obsidian notes about portfolio")
    assert result == "portfolio"


def test_unrelated_message_does_not_match():
    result = detect_vault_search_trigger("How many shares of KTOS do I own?")
    assert result is None


def test_generic_search_word_alone_does_not_match():
    """Real, deliberate negative test: 'search' alone, without an
    explicit vault/notes/obsidian reference, should NOT trigger --
    this is the real, important false-positive guard the trigger
    is designed around."""
    result = detect_vault_search_trigger("search for the best pizza place")
    assert result is None


def test_too_short_query_rejected():
    result = detect_vault_search_trigger("search my vault for ab")
    assert result is None


def test_case_insensitive():
    result = detect_vault_search_trigger("SEARCH MY VAULT FOR compounding interest")
    assert result == "compounding interest"


def test_trailing_punctuation_stripped():
    result = detect_vault_search_trigger("search my notes for luck and risk??")
    assert result == "luck and risk"
