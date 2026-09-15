"""Regression tests for the citation validator in DeepResearcher.

Live evaluation of Qwen2.5-7B inside the real research loop found a real
failure mode even after the citation-enforcement prompt fix: the model can
fabricate a plausible-looking citation for a fact it invented itself,
apparently to fill category-template sections the real evidence didn't
cover. A cited hallucination is worse than an uncited one -- the citation
falsely signals real grounding -- so _validate_citations() strips the link
entirely (not just the [] () syntax) for any citation whose URL was never
actually in self.findings.

Real, updated 2026-09-11: an earlier version kept the bare citation label
as dangling, unlinked text after stripping the link. Confirmed directly,
live, that this reads badly to a real reader -- an orphaned, capitalized
phrase with no grammatical connection to the rest of the sentence (it was
written as a link's title, not as prose). Now removes the whole fabricated
citation, including its label, and cleans up the whitespace left behind.

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


def test_fabricated_citation_is_removed_entirely():
    findings = [{"url": "https://example.com/real-page", "title": "Real Page"}]
    r = _make_researcher(findings)
    text = (
        "The response time is under 200ms [Real Page](https://example.com/real-page). "
        "It also supports GPU acceleration [Fake Source](https://nvidia.com/fake-claim)."
    )

    result = r._validate_citations(text)

    # Real citation untouched.
    assert "[Real Page](https://example.com/real-page)" in result
    # Fabricated citation is gone completely -- not de-linked-but-kept,
    # removed -- so no dangling, unlinked title is left behind either.
    assert "[Fake Source](https://nvidia.com/fake-claim)" not in result
    assert "Fake Source" not in result
    assert r.fabricated_citations == ["https://nvidia.com/fake-claim"]


def test_fabricated_citation_removal_cleans_up_whitespace():
    findings = [{"url": "https://example.com/real-page"}]
    r = _make_researcher(findings)
    # Real, live-observed pattern: the fabricated citation sits mid-sentence,
    # with a space on each side and the sentence's own trailing period right
    # after it -- removing it naively would leave "systems  ." (double space,
    # then a stray space before the period).
    text = (
        "It has compatibility issues with different systems "
        "[Fake Guide](https://fake.example/guide)."
    )

    result = r._validate_citations(text)

    assert result == "It has compatibility issues with different systems."
    assert "  " not in result
    assert " ." not in result


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

    assert "Some Label" not in result
    assert "]()" not in result


def test_cite_tag_title_containing_a_literal_less_than_is_still_parsed():
    # Real, live-caught bug, 2026-09-11: a real citation title read
    # "...faster (<200ms)" -- the embedded "<" broke an earlier version of
    # _CITE_TAG_RE (which used [^<]* for the title), letting the whole raw
    # <cite> tag survive completely unprocessed into the final output --
    # bypassing both tag-stripping and fabricated-citation validation
    # entirely, a real safety-net gap, not a cosmetic one. This must be
    # caught by _strip_cite_tags (which depends on the same regex), not
    # just _validate_citations.
    findings = [{"url": "https://example.com/real-page", "title": "Real Page"}]
    r = _make_researcher(findings)
    text = (
        'Local models are faster '
        '<cite id="1" url="https://example.com/real-page">faster (<200ms)</cite>.'
    )

    stripped = r._strip_cite_tags(text)

    assert "<cite" not in stripped
    assert "</cite>" not in stripped
    assert "[faster (<200ms)](https://example.com/real-page)" in stripped


def test_fabricated_cite_tag_with_less_than_in_title_is_still_validated():
    # The same real bug, but confirming the end of the real pipeline order
    # (strip then validate) actually catches a FABRICATED citation whose
    # title happens to contain a "<" -- the exact real case that let a
    # fabricated URL slip through completely unfiltered before this fix.
    findings = [{"url": "https://real.example/page"}]
    r = _make_researcher(findings)
    text = (
        '<cite id="1" url="https://fake.example/page">faster (<200ms)</cite>'
    )

    stripped = r._strip_cite_tags(text)
    validated = r._validate_citations(stripped)

    assert "fake.example" not in validated
    assert r.fabricated_citations == ["https://fake.example/page"]
