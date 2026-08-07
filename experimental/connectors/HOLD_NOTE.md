Status: FULLY VALIDATED end-to-end, including the real forward-to-/api/v1/chat hop

Real validation, 2026-08-06, in two parts:

Part 1 -- signature/mapping validated against 3 real live GitHub deliveries
(ping, issues.opened, issue_comment) via a real cloudflared tunnel and a
real webhook on the dkcbr/odysseus fork. All returned 200, dry-run only.
Fully cleaned up after (issue closed, webhook deleted, tunnel/server killed).

Part 2 -- the actual forward-to-chat hop, tested for real:
- Found and fixed a real bug first: the code assumed the chat endpoint
  was at /v1/chat; the real path (confirmed directly against the running
  app) is /api/v1/chat (routes/webhook/webhook_routes.py mounts its
  router with prefix="/api"). Fixed in receiver.py and config.example.yaml.
- Created a real chat-scoped API token via POST /api/tokens (admin auth
  required). Verified it directly against /api/v1/chat first, standalone
  -- got a real LLM response (model: inclusionai/ling-3.0-tiny:free).
- Ran the real receiver locally with GITHUB_RECEIVER_DRY_RUN=false and
  the real token, sent a real correctly-signed synthetic GitHub event.
- First two attempts returned target_status: 429 -- a real, external
  OpenRouter rate limit on the free model tier, confirmed by checking the
  actual response body ("OpenRouter rate-limited the request (429)").
  Not a Herald bug -- Herald correctly relayed the real upstream status.
- Third attempt, after a short wait: target_status: 200. Full real
  success, entire chain confirmed working: real signature verification,
  real event mapping, real HTTP forward, real 200 from the actual chat
  pipeline.
- Cleaned up: killed the local server, revoked the test API token via
  DELETE /api/tokens/{id}, confirmed revocation for real (retried the
  token afterward, got a genuine 401).

Nothing left untested in the core connector logic. Not deployed anywhere
long-term -- this was real, one-time, verified validation, not a
standing service. If used for real going forward, a persistent API
token would need to be created and stored properly (not hardcoded), and
a real deployment target decided.

---

## Calendar enrichment addition (2026-08-06, continued)

Real, tested addition: Herald can now optionally prepend a summary of
upcoming calendar events (from the real, already-existing
routes/calendar_routes.py / src/caldav_sync.py infrastructure) to
forwarded messages.

Credential model: uses the real session cookie via HERALD_CALENDAR_COOKIE
(not a scoped API token -- confirmed directly that require_user() in
src/auth_helpers.py explicitly rejects API tokens with a 403 on these
routes). Fails closed: missing or invalid cookie -> no calendar
enrichment, message unchanged. This is a genuinely broader-privilege
credential than the scoped chat-only tokens used elsewhere in Herald --
documented here explicitly rather than treated as equivalent.

Validated for real: created a real temporary calendar event via the live
API, confirmed the real client (calendar_client.py) fetches and correctly
summarizes it using the real field names (summary/dtstart, not the
title/start guessed by an earlier draft), confirmed it flows through into
Herald's forwarded message correctly, then deleted the test event and
confirmed deletion via a follow-up fetch.

An earlier proposal for this feature had four real bugs, all caught
before building: wrong query param names (from/to vs real start/end),
wrong cookie name (session vs real odysseus_session), wrong event field
names (title/start vs real summary/dtstart), and a placeholder base URL.
Corrected using values verified directly against the real running app,
not assumed from the draft.

17/17 tests passing with the real cookie set; 15 passed + 2 correctly
skipped without it (the two that specifically require a live cookie to
mean anything).

---

## ClickUp connector addition (2026-08-06, continued)

Real, tested addition: Herald can now optionally prepend a summary of
open ClickUp tasks (from a specific list, via CLICKUP_LIST_ID) to
forwarded messages.

Real credential: CLICKUP_API_TOKEN (a real personal API token, provided
and validated directly against the real ClickUp API -- confirmed via a
real GET /user call before building anything). ClickUp's Authorization
header takes the raw token directly, no "Bearer" prefix -- confirmed
empirically, not assumed. Fails closed on missing token/list id or any
API error.

Validated for real, full lifecycle: explored the real workspace (named
"Herald"), found real spaces/lists, created a real temporary task
("Herald connector validation test", id 86bb9yw14) in list 901418883702
("Project 1"), confirmed the real client fetches and summarizes it
correctly using verified real field names (name, status.status -- a
nested object, not a plain string), confirmed it flows through Herald's
forwarded message, then deleted the task and confirmed deletion via a
follow-up fetch (real 404: "Task not found, deleted").

Building this surfaced a real, pre-existing test fragility (not a new
bug): test_calendar_enrichment_when_cookie_present depended on a
specific real calendar event from an earlier session that had since been
properly cleaned up, so it started failing once run again days later --
correctly indicating the code (no events -> no summary) was right and
the test was stale. Fixed by making that test fully self-contained
(creates and deletes its own real event within the test itself).

Also fixed real 3-way test isolation across all three optional Herald
features (context file, calendar, ClickUp) -- several earlier tests only
accounted for one or two of the three possibly-active features.

27/27 tests passing with all three real credentials set (context file
N/A by default, calendar cookie, ClickUp token+list). No credentials
present -> all three enrichments correctly absent, verified by dedicated
tests for each.

---

## Transcript ingestion addition (2026-08-06, continued)

Real, mostly-validated addition: `experimental/connectors/transcripts/`
implements ingest -> chunk -> redact -> store -> summarize for
transcript text, built and tested against a real (not synthetic)
transcript excerpt.

Real data used: a genuine partial excerpt (~150 of 432 real lines,
intro + closing chapters) of the actual "Agent Native" podcast
transcript already used earlier this session for Herald's context/
calendar work. Honestly labeled as a partial excerpt, not the complete
file (practical limit on transferring the full file between
environments at once), in sample_data/README.md.

Real findings while building:
- Source format has no real speaker diarization at all, even for a
  two-person interview -- did not fabricate a 'speaker' field an earlier
  proposal's schema assumed.
- Timestamps and text are concatenated with no separator in the real
  scraped data ("0:077 secondsexample..." = "0:07" + "7 seconds" +
  "example..."); built a real regex parser for this exact format,
  verified against the real file (63 segments, exact text match, correct
  chapter tracking across all 3 real chapters in the excerpt).
- Chunking (4 real chunks, ~500 words each with 100-word overlap) and
  redaction (email/hex-token patterns) fully validated.
- Storage: real save/load/delete cycle tested with a real temp directory.

Summarizer: reuses Herald's own already-validated /api/v1/chat endpoint
rather than building a new provider-agnostic LLM adapter (no new
credential system needed). Real, direct verification confirmed it
reaches the real endpoint with correct auth and correctly returns None
on error. However, a full clean end-to-end AI-generated summary could
NOT be demonstrated this session: hit a real, persistent OpenRouter
rate limit on the free model tier. Tried at 20s, 60s, and 90s wait
intervals, and with a minimal test message (ruling out prompt size as
the cause) -- all still 429. This is an honest, real limitation of the
current session's environment, not a code defect; documented rather
than worked around with a mock summary.

9/10 tests pass; the 10th (summarizer real-request test) correctly skips
without a live token, and its assertion is designed to accept either a
real summary or None -- it guards against exceptions escaping the
fail-closed path, not against the external rate limit.
