"""Regression tests for the citation validator in DeepResearcher.

Live evaluation of Qwen2.5-7B inside the real research loop found a real
failure mode even after the citation-enforcement prompt fix: the model can
fabricate a plausible-looking citation for a fact it invented itself,
apparently to fill category-template sections the real evidence didn't
cover. A cited hallucination is worse than an uncited one -- the citation
falsely signals real grounding -- so _validate_citations() strips the link
syntax (keeping the visible label) for any citation whose URL was never
actually in self.findings.

These tests pin that behavior directly, without needing a real LLM call.
"""


def _make_researcher(findings):
    # Build the object without running the heavy __init__ (which wires up an
    # LLM caller etc.); _validate_citations only touches self.findings and
    # self.fabricated_citations.
    from src.deep_research import DeepResearcher
    r = DeepResearcher.__new__(DeepResearcher)
    r.findings = findings
    r.fabricated_citations = []
    return r


def test_real_citation_is_kept_unchanged():
    findings = [{"url": "https://example.com/real-page", "title": "Real Page"}]
    r = _make_researcher(findings)
    text = "The response time is under 200ms [Real Page](https://example.com/real-page)."

    result = r._validate_citations(text)

    assert result == text
    assert r.fabricated_citations == []


def test_fabricated_citation_is_stripped_but_label_kept():
    findings = [{"url": "https://example.com/real-page", "title": "Real Page"}]
    r = _make_researcher(findings)
    text = (
        "The response time is under 200ms [Real Page](https://example.com/real-page). "
        "It also supports GPU acceleration [Fake Source](https://nvidia.com/fake-claim)."
    )

    result = r._validate_citations(text)

    # Real citation untouched.
    assert "[Real Page](https://example.com/real-page)" in result
    # Fabricated citation's link syntax is gone, but the readable label survives
    # so the sentence still reads naturally -- just no longer falsely sourced.
    assert "[Fake Source](https://nvidia.com/fake-claim)" not in result
    assert "Fake Source" in result
    assert r.fabricated_citations == ["https://nvidia.com/fake-claim"]


def test_multiple_fabricated_citations_all_stripped():
    findings = [{"url": "https://real.example/a"}]
    r = _make_researcher(findings)
    text = (
        "[A](https://real.example/a) is real. "
        "[B](https://fake.example/b) is not. "
        "[C](https://fake.example/c) is also not."
    )

    result = r._validate_citations(text)

    assert "[A](https://real.example/a)" in result
    assert "https://fake.example/b" not in result
    assert "https://fake.example/c" not in result
    assert set(r.fabricated_citations) == {
        "https://fake.example/b",
        "https://fake.example/c",
    }


def test_no_citations_at_all_is_a_no_op():
    r = _make_researcher([{"url": "https://example.com"}])
    text = "This report has no markdown links in it at all."

    result = r._validate_citations(text)

    assert result == text
    assert r.fabricated_citations == []


def test_wikipedia_style_url_with_parens_is_not_broken():
    # A real, common case: URLs that themselves contain parentheses, e.g.
    # Wikipedia disambiguation pages. A naive "stop at the first )" regex
    # would mis-parse this and either break the link or wrongly flag it
    # as fabricated.
    url = "https://en.wikipedia.org/wiki/Performance_(disambiguation)"
    findings = [{"url": url}]
    r = _make_researcher(findings)
    text = f"See the overview [Performance]({url})."

    result = r._validate_citations(text)

    assert result == text
    assert r.fabricated_citations == []


def test_findings_without_url_are_never_treated_as_valid():
    # A finding missing its own url field must not accidentally make an
    # empty-string citation URL "valid".
    findings = [{"title": "No URL here"}]
    r = _make_researcher(findings)
    text = "A vague claim [Some Label]()."

    result = r._validate_citations(text)

    assert "Some Label" in result
    assert "]()" not in result
