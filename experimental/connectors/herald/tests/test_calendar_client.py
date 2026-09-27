"""
Real integration tests against the actual live Odysseus app -- not mocked.
Requires the real app running on localhost:7000 and a real, valid session
cookie in HERALD_CALENDAR_COOKIE. If the app isn't reachable, tests are
skipped rather than failing (this is an integration test, not a unit test
that should run in total isolation).
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import pytest
from calendar_client import fetch_upcoming_events, build_calendar_summary

REAL_APP_URL = "http://localhost:7000"


def _app_reachable():
    try:
        httpx.get(REAL_APP_URL, timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


requires_live_app = pytest.mark.skipif(not _app_reachable(), reason="real Odysseus app not reachable on localhost:7000")


def test_no_cookie_skips(monkeypatch):
    monkeypatch.delenv("HERALD_CALENDAR_COOKIE", raising=False)
    assert fetch_upcoming_events() is None


@requires_live_app
def test_invalid_cookie_fails_closed_real(monkeypatch):
    """Real test against the real app -- a garbage cookie value, not mocked."""
    monkeypatch.setenv("HERALD_CALENDAR_COOKIE", "definitely-not-a-real-session-value")
    assert fetch_upcoming_events() is None


@requires_live_app
def test_valid_cookie_returns_real_events(monkeypatch):
    """Real test against the real app with the real session cookie, requires
    HERALD_CALENDAR_COOKIE to actually be set to a valid value when running."""
    real_cookie = os.environ.get("HERALD_CALENDAR_COOKIE")
    if not real_cookie:
        pytest.skip("HERALD_CALENDAR_COOKIE not set for this real test run")
    events = fetch_upcoming_events()
    assert events is not None
    assert isinstance(events, list)


def test_build_summary_empty_list():
    assert build_calendar_summary([]) is None


def test_build_summary_real_field_names():
    """Uses the exact real event shape confirmed from the actual API response."""
    events = [{
        "uid": "fb2b9b13-1fba-4809-9703-0f3939ef040d",
        "summary": "Herald calendar-context validation test",
        "dtstart": "2026-08-08T15:00:00Z",
    }]
    summary = build_calendar_summary(events)
    assert summary is not None
    assert "Herald calendar-context validation test" in summary
    assert "2026-08-08T15:00:00Z" in summary


def test_build_summary_truncation(monkeypatch):
    monkeypatch.setattr("calendar_client.SUMMARY_MAX_CHARS", 30)
    events = [{"summary": "A" * 100, "dtstart": "2026-01-01T00:00:00Z"}]
    summary = build_calendar_summary(events)
    assert len(summary) <= 34
    assert summary.endswith("...")
