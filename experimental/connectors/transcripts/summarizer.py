"""
Real summarizer using the real, already-validated /api/v1/chat endpoint
(routes/webhook/webhook_routes.py) -- not a new provider-agnostic
adapter needing new credentials. Reuses the exact same real endpoint
Herald itself forwards to.

Honest about output shape: this is a real interview/podcast transcript,
not a business meeting. "Action items" and "decisions" (the framing an
earlier proposal assumed for meeting transcripts) don't naturally apply
here -- this summarizer asks for a factual summary and key topics
instead, matching what's actually in the source content.
"""
import os
from typing import List, Dict, Optional

import httpx

CHAT_API_BASE = os.environ.get("HERALD_APP_API_BASE", "http://localhost:7000/api")
TOKEN_ENV = "HERALD_TRANSCRIPT_SUMMARY_TOKEN"
SESSION_ENV = "HERALD_TRANSCRIPT_SUMMARY_SESSION"  # optional: a real,
# already-created session id (e.g. one pointed at a local Ollama model)
# to use directly instead of the default fallback endpoint.
ENDPOINT_ID_ENV = "HERALD_TRANSCRIPT_SUMMARY_ENDPOINT_ID"  # optional: a
# real ModelEndpoint id (NOT a session id -- these are different real
# things, confirmed against routes/session_routes.py). If SESSION_ENV
# isn't set but this is, a session is created once via the real,
# sanctioned POST /api/session and cached in-process for reuse, rather
# than creating a new session on every single call.
SUMMARY_MAX_CHARS = int(os.environ.get("HERALD_TRANSCRIPT_SUMMARY_MAX_CHARS", "400"))

_cached_session_id: Optional[str] = None


def _get_or_create_session(token: str) -> Optional[str]:
    """Real session resolution: explicit session id takes priority: it's
    already a real, live session. Otherwise, if an endpoint id is
    configured, create a real session once (via the real, sanctioned
    POST /api/session -- confirmed empirically to accept the same
    chat-scoped API token Herald already uses, no broader credential
    needed) and cache it for the life of this process."""
    global _cached_session_id
    explicit = os.environ.get(SESSION_ENV)
    if explicit:
        return explicit

    if _cached_session_id:
        return _cached_session_id

    endpoint_id = os.environ.get(ENDPOINT_ID_ENV)
    if not endpoint_id:
        return None

    try:
        resp = httpx.post(
            f"{CHAT_API_BASE}/session",
            headers={"Authorization": f"Bearer {token}"},
            data={"name": "herald-transcript-summarizer", "endpoint_id": endpoint_id},
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        _cached_session_id = resp.json()["id"]
    except (ValueError, KeyError):
        return None
    return _cached_session_id


def summarize_chunks(chunks: List[Dict]) -> Optional[str]:
    """Real call to the real chat pipeline. Fails closed (returns None)
    on missing token or any error -- same pattern as calendar/ClickUp."""
    token = os.environ.get(TOKEN_ENV)
    if not token or not chunks:
        return None

    combined_text = "\n\n".join(c["text"] for c in chunks)
    prompt = (
        "Summarize the following transcript excerpt in 2-3 sentences, "
        "then list up to 3 key topics discussed. Be factual and concise:\n\n"
        f"{combined_text[:4000]}"
    )

    payload = {"message": prompt}
    session_id = _get_or_create_session(token)
    if session_id:
        payload["session"] = session_id

    try:
        resp = httpx.post(
            f"{CHAT_API_BASE}/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=60.0,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code != 200:
        return None

    try:
        reply = resp.json().get("response")
    except (ValueError, AttributeError):
        return None

    if not reply:
        return None

    if len(reply) > SUMMARY_MAX_CHARS:
        reply = reply[:SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + "..."
    return reply
