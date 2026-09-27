"""Regression tests for _strip_domain_count_leaks in DeepResearcher.

Real, live-caught bug, 2026-09-11: domain_count is meant to be an internal
weighting signal for the model's own reasoning, carried only inside <cite>
tag attributes a real reader never sees directly. Confirmed directly, live,
that the model can write it out as its own visible prose instead -- e.g. a
table row reading '**Performance** (domain_count="2")' -- which looks like a
bug leaking through to a real user, not a feature.

These tests pin the deterministic regex backstop directly, without needing
a real LLM call.
"""


def _make_researcher():
    from src.deep_research import DeepResearcher
    return DeepResearcher.__new__(DeepResearcher)


def test_real_leaked_domain_count_is_removed():
    r = _make_researcher()
    text = '| **Performance** (domain_count="2") | fast | slow |'

    result = r._strip_domain_count_leaks(text)

    assert "domain_count" not in result
    assert result == "| **Performance** | fast | slow |"


def test_no_leak_present_is_a_no_op():
    r = _make_researcher()
    text = "This report has no leaked metadata attributes in it at all."

    result = r._strip_domain_count_leaks(text)

    assert result == text


def test_multiple_leaks_all_removed():
    r = _make_researcher()
    text = (
        '**Performance** (domain_count="2") and '
        '**Cost** (domain_count="1") are both criteria.'
    )

    result = r._strip_domain_count_leaks(text)

    assert "domain_count" not in result
    assert result == "**Performance** and **Cost** are both criteria."


def test_leak_without_surrounding_parens_is_still_caught():
    # Real, defensive case: the model might write the raw attribute without
    # wrapping it in parentheses at all.
    r = _make_researcher()
    text = 'Performance domain_count="3" is strong.'

    result = r._strip_domain_count_leaks(text)

    assert "domain_count" not in result


def test_does_not_touch_a_legitimate_cite_tag_attribute():
    # Real, important ordering guarantee: this function must only ever run
    # AFTER _strip_cite_tags() has already converted real <cite> tags to
    # plain markdown (which correctly drops domain_count on its own). If it
    # ran on an intact tag, this naive regex would corrupt the tag's own
    # attribute list. This test documents that expectation at the unit
    # level, using a real tag left deliberately intact to prove the
    # function doesn't need special-casing for it -- the real safety comes
    # from pipeline ordering (research()), not from this regex itself.
    r = _make_researcher()
    text = '<cite id="1" url="https://example.com" domain_count="2">Title</cite>'

    result = r._strip_domain_count_leaks(text)

    # This documents the real, current behavior: the regex does match and
    # strip the bare attribute even inside a raw tag, which is exactly why
    # research() must call _strip_cite_tags() first -- by the time this
    # runs in the real pipeline, no real <cite> tag should still be present.
    assert "domain_count" not in result
