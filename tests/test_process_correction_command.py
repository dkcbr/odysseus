"""Real, direct tests for MemoryManager.process_correction_command
(added 2026-08-17): a deterministic, model-independent alternative to
relying on the model to call manage_memory with category="correction",
after confirming that approach unreliable (0/4 real trials, 2 models,
even with an explicit persona instruction)."""
import tempfile

from src.memory import MemoryManager


def _mgr():
    return MemoryManager(tempfile.mkdtemp())


def test_correction_prefix_matches():
    ok, text = _mgr().process_correction_command("Correction: always answer in bullet points")
    assert ok is True
    assert text == "always answer in bullet points"


def test_correction_prefix_case_insensitive():
    ok, text = _mgr().process_correction_command("CORRECTION: never use emoji")
    assert ok is True
    assert text == "never use emoji"


def test_note_for_future_prefix_matches():
    ok, text = _mgr().process_correction_command("Note for future: I prefer metric units")
    assert ok is True
    assert text == "I prefer metric units"


def test_always_remember_prefix_matches():
    ok, text = _mgr().process_correction_command("Always remember: sign off emails with cheers")
    assert ok is True
    assert text == "sign off emails with cheers"


def test_no_prefix_does_not_match():
    ok, text = _mgr().process_correction_command("I prefer metric units, actually")
    assert ok is False
    assert text == ""


def test_too_short_capture_rejected():
    ok, text = _mgr().process_correction_command("Correction: ok")
    assert ok is False


def test_too_long_capture_rejected():
    long_text = "x" * 301
    ok, text = _mgr().process_correction_command(f"Correction: {long_text}")
    assert ok is False


def test_trivial_acknowledgment_rejected():
    for phrase in ["Correction: I misspoke", "Correction: never mind", "Correction: my mistake"]:
        ok, text = _mgr().process_correction_command(phrase)
        assert ok is False, f"expected rejection for: {phrase!r}"


def test_secret_pattern_rejected():
    ok, text = _mgr().process_correction_command("Correction: my api key is abc123xyz456")
    assert ok is False


def test_multisentence_correction_still_accepted_if_within_length():
    ok, text = _mgr().process_correction_command(
        "Correction: always use metric units. Also prefer 24-hour time format."
    )
    assert ok is True
    assert "metric units" in text
