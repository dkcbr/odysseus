"""Regression tests for the evidence-adaptive final report length behavior.

Live evaluation of Qwen2.5-7B inside the real research loop found that a
rigid "at MINIMUM 1500 words" requirement, combined with thin real evidence,
pressured the model into inventing unsupported sections (a specific NVIDIA
A100 claim, an OpenAI research claim) to hit the length bar. The prompt was
changed to let the evidence set the length, and the automatic "expand if
under 400 words" retry -- which used the same "target at least 1000 words"
pressure -- was made conditional on there being enough real findings to
justify it, rather than always firing.

These tests pin that _final_report() skips the forced-expansion retry when
findings are genuinely thin (accepting a short, honest report instead), but
still attempts expansion when there's enough real material to draw on.
"""
import asyncio


def _make_researcher(findings, category=None):
    from src.deep_research import DeepResearcher
    r = DeepResearcher.__new__(DeepResearcher)
    r.findings = findings
    r.category = category
    r.max_report_tokens = 8192
    r._progress = None
    return r


def test_thin_evidence_skips_forced_expansion(monkeypatch):
    # Only 2 findings -- below the real, chosen "genuinely thin" threshold.
    findings = [{"url": "https://a.example"}, {"url": "https://b.example"}]
    r = _make_researcher(findings)

    calls = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        calls.append(messages)
        return "Short report. " * 10  # well under 400 words

    r._llm = fake_llm

    result = asyncio.run(r._final_report("What is X?", "some evolving report"))

    assert "Short report." in result
    # Only the first, real generation call -- no forced-expansion follow-up.
    assert len(calls) == 1


def test_rich_evidence_still_attempts_expansion(monkeypatch):
    # 6 findings -- above the threshold, enough real material to justify
    # asking for more detail if the first draft came out short.
    findings = [{"url": f"https://example.com/{i}"} for i in range(6)]
    r = _make_researcher(findings)

    calls = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        calls.append(messages)
        if len(calls) == 1:
            return "Short report. " * 10  # under 400 words -> should trigger expansion
        return "Expanded report. " * 500  # well over 400 words

    r._llm = fake_llm

    result = asyncio.run(r._final_report("What is X?", "some evolving report"))

    assert "Expanded report." in result
    # First (short) draft, then a real expansion attempt.
    assert len(calls) == 2


def test_thin_evidence_report_that_is_already_long_is_untouched():
    # Thin findings, but the model still wrote a long-enough report on its
    # own -- nothing should be skipped or retried either way.
    findings = [{"url": "https://a.example"}]
    r = _make_researcher(findings)

    calls = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        calls.append(messages)
        return "word " * 500  # well over 400 words

    r._llm = fake_llm

    result = asyncio.run(r._final_report("What is X?", "some evolving report"))

    assert result.count("word") == 500
    assert len(calls) == 1
