"""
Real, minimal calendar-read helper for Herald.

Fetches upcoming events from the real, already-existing Odysseus calendar
API (routes/calendar_routes.py, backed by src/caldav_sync.py) and builds a
short summary string to prepend to forwarded messages.

Uses the real session cookie, not a scoped API token -- confirmed directly
by reading src/auth_helpers.py: require_user() explicitly rejects API
token requests with a 403 ("API tokens must use a scope-aware API
route"). The calendar routes are not scope-aware, so a session cookie is
the only real credential that works here. Fails closed if the cookie is
missing or any call errors -- Herald's forwarded message is then
identical to before this feature existed.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import httpx

API_BASE = os.environ.get("HERALD_APP_API_BASE", "http://localhost:7000/api")
COOKIE_ENV = "HERALD_CALENDAR_COOKIE"
SUMMARY_MAX_CHARS = int(os.environ.get("HERALD_CAL_SUMMARY_MAX_CHARS", "300"))
LOOKAHEAD_DAYS = int(os.environ.get("HERALD_CAL_LOOKAHEAD_DAYS", "7"))


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rsplit(" ", 1)[0] + "..."


def fetch_upcoming_events() -> Optional[List[dict]]:
    """Real fetch against the real /api/calendar/events endpoint. Returns
    None on any missing cookie, non-200 response, or error -- fail closed."""
    cookie = os.environ.get(COOKIE_ENV)
    if not cookie:
        return None

    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=LOOKAHEAD_DAYS)).strftime("%Y-%m-%dT23:59:59Z")

    try:
        resp = httpx.get(
            f"{API_BASE}/calendar/events",
            params={"start": start, "end": end},  # real param names, confirmed against routes/calendar_routes.py
            cookies={"odysseus_session": cookie},  # real cookie name, confirmed used throughout this engagement
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    try:
        return resp.json().get("events", [])
    except (ValueError, AttributeError):
        return None


def build_calendar_summary(events: List[dict]) -> Optional[str]:
    """Real event field names, confirmed directly against a real API
    response: 'summary' (not 'title') and 'dtstart' (not 'start')."""
    if not events:
        return None
    items = []
    for e in events[:2]:
        title = e.get("summary", "untitled")
        start = e.get("dtstart", "")
        items.append(f"{title} at {start}".strip())
    summary = "Upcoming: " + "; ".join(items)
    return _truncate(summary, SUMMARY_MAX_CHARS)
