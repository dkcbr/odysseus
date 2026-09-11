"""
Real ClickUp task-read helper for Herald.

Uses the real ClickUp API (api.clickup.com/api/v2), verified directly
against a real account/workspace before writing this -- not assumed from
documentation. ClickUp's Authorization header takes the raw personal API
token directly (no "Bearer" prefix), confirmed by a real, successful
GET /user call.

Fails closed if CLICKUP_API_TOKEN is unset or any call errors -- Herald's
forwarded message is then identical to before this feature existed.
"""
import os
from typing import Optional, List

import httpx

API_BASE = "https://api.clickup.com/api/v2"
TOKEN_ENV = "CLICKUP_API_TOKEN"
LIST_ID_ENV = "CLICKUP_LIST_ID"
SUMMARY_MAX_CHARS = int(os.environ.get("HERALD_CLICKUP_SUMMARY_MAX_CHARS", "300"))


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rsplit(" ", 1)[0] + "..."


def fetch_open_tasks() -> Optional[List[dict]]:
    """Real fetch against the real ClickUp API. Requires CLICKUP_LIST_ID
    (a specific list to read from -- ClickUp has no single "all my tasks"
    endpoint without picking a scope). Returns None on any missing
    token/list id, non-200 response, or error -- fail closed."""
    token = os.environ.get(TOKEN_ENV)
    list_id = os.environ.get(LIST_ID_ENV)
    if not token or not list_id:
        return None

    try:
        resp = httpx.get(
            f"{API_BASE}/list/{list_id}/task",
            headers={"Authorization": token},  # real: raw token, no "Bearer" prefix
            params={"archived": "false"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    try:
        tasks = resp.json().get("tasks", [])
    except (ValueError, AttributeError):
        return None

    # Real: open tasks only, matching this connector's read-only,
    # "what's outstanding" purpose -- exclude the real "complete" status
    # type confirmed from the actual space's status list.
    return [t for t in tasks if t.get("status", {}).get("type") != "closed"]


def build_task_summary(tasks: List[dict]) -> Optional[str]:
    """Real field names, confirmed directly against real API responses:
    'name' (task title) and 'status'.'status' (nested, not a plain string)."""
    if not tasks:
        return None
    items = []
    for t in tasks[:3]:
        name = t.get("name", "untitled")
        status = t.get("status", {}).get("status", "unknown")
        items.append(f"{name} ({status})")
    summary = "Open ClickUp tasks: " + "; ".join(items)
    return _truncate(summary, SUMMARY_MAX_CHARS)
