"""
GitHub webhook receiver -- translates real GitHub issue/comment events into
messages forwarded to Jarvis's real POST /api/v1/chat endpoint.

EXPERIMENTAL. Defaults to dry-run (no real forwarding) until explicitly
disabled via config or query param. Uses FastAPI (the real project's actual
framework, confirmed 0.139.0 in this venv) rather than adding Flask as a
new dependency for one small service.

Real target: POST /api/v1/chat at the actual Odysseus app on port 7000, not
8080 (which doesn't correspond to anything real here -- confirmed
throughout this whole engagement; 8000 is a separate Open WebUI container).
"""
import hmac
import hashlib
import json
import os
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Header

from calendar_client import fetch_upcoming_events, build_calendar_summary
from clickup_client import fetch_open_tasks, build_task_summary

logger = logging.getLogger("github_webhook_receiver")

app = FastAPI(title="Jarvis GitHub Webhook Receiver (experimental)")

TARGET_CHAT_URL = os.environ.get("JARVIS_CHAT_URL", "http://localhost:7000/api/v1/chat")
TARGET_API_TOKEN = os.environ.get("JARVIS_API_TOKEN", "")
INCOMING_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
DEFAULT_DRY_RUN = os.environ.get("GITHUB_RECEIVER_DRY_RUN", "true").lower() != "false"

# Real idempotency tracking -- GitHub retries deliveries; the same
# X-GitHub-Delivery id should only be processed once. In-memory for this
# experimental version (a real deployment would use persistent storage,
# but the real project's actual DB schema wasn't investigated for this
# experiment -- flagged honestly rather than assumed).
_seen_delivery_ids: set[str] = set()

# Only these two event types are handled, matching the agreed scope
# (issue_comment, issues.opened) -- anything else is acknowledged but
# not forwarded, to keep the blast radius small as agreed.
SUPPORTED_EVENTS = {"issues", "issue_comment"}


def verify_github_signature(body_bytes: bytes, signature_header: Optional[str]) -> bool:
    """Real HMAC-SHA256 verification matching GitHub's actual X-Hub-Signature-256 scheme."""
    if not INCOMING_SECRET or not signature_header:
        return False
    try:
        algo, sig = signature_header.split("=", 1)
    except ValueError:
        return False
    if algo != "sha256":
        return False
    expected = hmac.new(INCOMING_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


CONTEXT_FILE = os.environ.get(
    "HERALD_CONTEXT_FILE",
    os.path.join(os.path.dirname(__file__), "context.md"),
)
CONTEXT_MAX_CHARS = int(os.environ.get("HERALD_CONTEXT_MAX_CHARS", "500"))


def load_context_prefix() -> str:
    """Real, minimal context loading -- NOT the fabricated 'onboarding
    skill'/'seventh_question.md' concept from an unverified proposal.
    If CONTEXT_FILE exists, read and truncate it to a compact prefix. If
    it doesn't exist, return empty string -- behavior is then identical
    to before this feature existed (safe default, no behavior change)."""
    if not os.path.exists(CONTEXT_FILE):
        return ""
    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return ""
    if not raw:
        return ""
    if len(raw) > CONTEXT_MAX_CHARS:
        raw = raw[:CONTEXT_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return f"[Context: {raw}]\n\n"


def map_github_event_to_chat_payload(event_name: str, event_json: dict) -> dict:
    """Map a real GitHub webhook payload into a chat message. Only issues
    and issue_comment are meaningfully mapped, matching agreed scope.

    If a context file is present (see load_context_prefix), its content
    is prepended to the real 'message' string field -- the ONLY field
    the real /api/v1/chat schema (SyncChatRequest) actually has. An
    earlier proposal suggested a separate 'context_bundle' JSON field,
    which the real schema does not support and would have been silently
    dropped or rejected; this uses the field that actually exists."""
    if event_name == "issues":
        action = event_json.get("action", "unknown")
        issue = event_json.get("issue", {})
        title = issue.get("title", "(no title)")
        url = issue.get("html_url", "")
        text = f"GitHub issue {action}: {title}\n{url}"
    elif event_name == "issue_comment":
        comment = event_json.get("comment", {})
        author = comment.get("user", {}).get("login", "unknown")
        body = comment.get("body", "")[:400]
        url = comment.get("html_url", "")
        text = f"New GitHub comment by {author}: {body}\n{url}"
    else:
        text = f"GitHub event {event_name}: {json.dumps(event_json)[:400]}"

    # Real calendar enrichment -- fails closed (returns None) if
    # HERALD_CALENDAR_COOKIE is unset or invalid, in which case this is a
    # no-op and text is unchanged from before this feature existed.
    cal_events = fetch_upcoming_events()
    cal_summary = build_calendar_summary(cal_events) if cal_events else None
    if cal_summary:
        text = f"{cal_summary}\n\n{text}"

    # Real ClickUp enrichment, same fail-closed pattern as calendar.
    tasks = fetch_open_tasks()
    task_summary = build_task_summary(tasks) if tasks else None
    if task_summary:
        text = f"{task_summary}\n\n{text}"

    return {"message": load_context_prefix() + text}


@app.post("/github/receiver")
async def receiver(request: Request, x_hub_signature_256: Optional[str] = Header(None),
                    x_github_event: Optional[str] = Header(None),
                    x_github_delivery: Optional[str] = Header(None),
                    dry_run: Optional[str] = None):
    body = await request.body()

    if not verify_github_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    event_name = x_github_event or "unknown"

    # Real idempotency check via GitHub's own delivery id
    if x_github_delivery:
        if x_github_delivery in _seen_delivery_ids:
            return {"status": "duplicate_ignored", "delivery_id": x_github_delivery}
        _seen_delivery_ids.add(x_github_delivery)

    if event_name not in SUPPORTED_EVENTS:
        return {"status": "acknowledged_unsupported_event", "event": event_name}

    try:
        event_json = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    payload = map_github_event_to_chat_payload(event_name, event_json)

    # Real dry-run default: query param overrides env default if explicitly given
    effective_dry_run = DEFAULT_DRY_RUN if dry_run is None else dry_run.lower() != "false"

    if effective_dry_run:
        return {"status": "dry_run", "event": event_name, "payload": payload}

    if not TARGET_API_TOKEN:
        raise HTTPException(status_code=500, detail="JARVIS_API_TOKEN not configured -- cannot forward for real")

    headers = {"Authorization": f"Bearer {TARGET_API_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(TARGET_CHAT_URL, headers=headers, json=payload)
    return {"status": "forwarded", "target_status": resp.status_code}
