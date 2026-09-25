# `data/holdings_freshness.json`

Real, runtime-generated file tracking when each ticker's live holdings
were last checked against the actual brokerage account (via
`public_com`, server id `74167655`). Added 2026-08-17 alongside
`src/portfolio_parser.py`'s `PublicComWrapper` and
`get_freshness_recommendation()`.

## Why this file exists, and why it's separate from `portfolio_context.md`

`data/portfolio_context.md` is a hand-maintained reference document.
Adding per-holding freshness metadata directly into its tables would
mean restructuring something actively edited by hand, for every real
row, every time it's checked. Instead, this file is written and
maintained entirely by code -- `PublicComWrapper` updates it
automatically on every successful live check. No manual editing is
ever needed or expected.

This file is not committed to git -- it's covered by the existing,
blanket `data/` rule in `.gitignore`. It's local, runtime state, not
source.

## Format

A flat JSON object, one entry per ticker:

```json
{
  "KTOS": {
    "last_checked": "2026-08-17T10:33:24.167265+00:00",
    "source": "live",
    "qty_at_check": 16.0
  }
}
```

- `last_checked`: ISO 8601 UTC timestamp of the real check (via
  `datetime.now(timezone.utc).isoformat()`).
- `source`: currently always `"live"` -- reserved as a real field in
  case a different source (e.g. a different brokerage connector) is
  added later.
- `qty_at_check`: the real, actual share quantity confirmed at that
  moment. `null` is never written here -- a ticker with no confirmed
  position simply doesn't get an entry (see `PublicComWrapper`'s
  `_persist_freshness`, only called on a successful check with real
  data).

## Lifecycle

- **Written**: automatically, by `PublicComWrapper._persist_freshness()`,
  every time `get_holdings()` completes a real, successful live call
  (not on a cache hit, not on failure).
- **Read**: by `get_freshness_recommendation(ticker, doc_confirmed_qty)`
  -- a fast, synchronous, real file read, no network call. This is
  why it's safe to call from the streaming response path (see
  `src/agent_loop.py`'s holdings-correction logic), unlike a live
  `mcp.call_tool()`, which has an unresolved async-safety question
  (GitHub issue #12).
- **Staleness threshold**: `get_freshness_recommendation()` takes a
  real `max_age_hours` parameter, default `24.0`. A record older than
  this is treated the same as no record at all (`needs_live_check`).
- **Rotation/retention**: none currently. The file only grows to the
  number of distinct tickers ever checked (real, naturally bounded --
  matches the real portfolio's own ticker count, not unbounded
  growth). No cleanup job exists or is currently needed at this scale.
- **Recovery**: if the file is missing, corrupted, or unreadable,
  `get_freshness_recommendation()` fails safe and returns
  `needs_live_check` (confirmed via
  `test_freshness_recommendation_handles_missing_file_gracefully` in
  `tests/test_portfolio_parser.py`). Deleting this file entirely is
  always safe -- it will be recreated automatically as real checks
  happen again.

## Seeding for local testing

Tests use `tempfile.TemporaryDirectory()` and pass an explicit
`freshness_path` to `PublicComWrapper`/`get_freshness_recommendation()`
rather than touching the real, live file -- see
`tests/test_portfolio_parser.py` for real, working examples of both
the happy path and the stale/missing/corrupted cases.

To manually seed the real, live file for a manual check (e.g. testing
the `agent_loop.py` correction logic against a specific real
scenario), write a real entry matching the format above directly:

```python
import json
from datetime import datetime, timezone

path = "data/holdings_freshness.json"
try:
    with open(path) as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}

data["TICKER"] = {
    "last_checked": datetime.now(timezone.utc).isoformat(),
    "source": "live",
    "qty_at_check": 20.0,
}

with open(path, "w") as f:
    json.dump(data, f, indent=2)
```
