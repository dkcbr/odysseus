# Herald Enrichments

Herald can optionally prepend short, token-friendly context to the
forwarded message string. Enrichments are independent, opt-in, and
always fail closed when credentials are missing or invalid.

## Enrichment sources

**Local context file**
- Path: `HERALD_CONTEXT_FILE` env var, defaults to `context.md` next to `receiver.py`.
- Behavior: if present, content is truncated (`HERALD_CONTEXT_MAX_CHARS`, default 500) and prepended.

**Calendar**
- Enable: set `HERALD_CALENDAR_COOKIE` to the real session cookie *value*
  (just the raw value -- e.g. what you'd see after `odysseus_session=` in
  a real browser cookie; the code builds the `odysseus_session=` cookie
  itself, do not include the key name yourself).
- Behavior: calls the real `GET /api/calendar/events` and prepends a
  summary of events in the next `HERALD_CAL_LOOKAHEAD_DAYS` (default 7).
- Fail closed: missing/invalid cookie, or any non-200 response -> skipped.
- Uses a full session cookie, not a scoped API token -- these routes
  explicitly reject API tokens (confirmed in `src/auth_helpers.py`). This
  is a broader-privilege credential than the scoped tokens used
  elsewhere; see HOLD_NOTE.md for the real reasoning.

**ClickUp**
- Enable: set `CLICKUP_API_TOKEN` (a real personal API token from
  ClickUp Settings -> Apps) and `CLICKUP_LIST_ID` (the specific list to read).
- Behavior: fetches tasks from that list and prepends a summary of the
  **open** ones (status type != "closed" -- not "recent" tasks; ClickUp
  has no recency-based endpoint used here).
- Fail closed: missing token/list id, or any non-200 response -> skipped.

## Behavioral guarantees
- Herald's real message schema (`{"message": <string>}`, matching the
  real `/api/v1/chat` `SyncChatRequest.message: str` field) is unchanged.
  Enrichments are prepended text, not new fields.
- Enrichments are fully independent: missing/invalid credentials for one
  source never affect the others.
- Tests requiring real credentials skip cleanly (not fail) when the
  corresponding env var is unset.

## Security guidance
- Never commit tokens or cookies to the repo.
- Use environment variables for local testing, unset after use:
  ```bash
  export CLICKUP_API_TOKEN="pk_..."
  export CLICKUP_LIST_ID="..."
  export HERALD_CALENDAR_COOKIE="<raw cookie value only>"
  # ... run tests / Herald ...
  unset CLICKUP_API_TOKEN CLICKUP_LIST_ID HERALD_CALENDAR_COOKIE
  ```
- If a token or cookie may have been exposed (e.g. pasted into a chat
  log), rotate/revoke it immediately in the provider's own settings.

## Running tests
```bash
cd experimental/connectors/herald
python3 -m pytest tests/ -v
```
Tests requiring real credentials are automatically skipped when the
relevant env vars aren't set -- the full suite is safe to run with no
credentials present at all.
