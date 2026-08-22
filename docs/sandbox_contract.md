# Per-agent sandbox contract

Real, written 2026-08-21, as part of the per-agent isolated sandboxes
thread (see `jarvis-todo.md`). This is the actual contract between
`mcp_manager.py`'s `_do_call()` (the injector) and any individual tool
implementation that wants to use its own, isolated sandbox space.

## What's provided

When a tool call is made with a known `agent_name` (i.e. the `/call`
API request includes `"agent": "<name>"`), `_do_call()` injects a real,
extra key into the arguments dict passed to the tool, before the
underlying MCP protocol call:

```json
{
  "...(the tool's own real arguments, unchanged)...": "...",
  "_jarvis_sandbox": {
    "agent": "browser_agent",
    "tmp_dir": "/home/dk/jarvis/projects/odysseus/data/agent_sandboxes/browser_agent/tmp",
    "logs_dir": "/home/dk/jarvis/projects/odysseus/data/agent_sandboxes/browser_agent/logs",
    "state_dir": "/home/dk/jarvis/projects/odysseus/data/agent_sandboxes/browser_agent/state"
  }
}
```

## Real, explicit rules

1. **This key is optional-in, optional-out.** If `agent_name` is `None`
   (the overwhelming majority of calls today -- e.g. this claude.ai
   conversation's own direct tool use, which never passes an `agent`
   parameter at all), `_jarvis_sandbox` is not added at all. A tool
   must not assume it's always present.

2. **A tool that doesn't know about this key must be unaffected.**
   Confirmed directly (2026-08-21): `tradingview`'s `market_snapshot`,
   called with `agent=market_agent`, still returned correct, valid
   results with the extra key present and unused. Any real tool
   implementation is free to simply ignore `_jarvis_sandbox` if it has
   no sandbox behavior to implement -- this is not a breaking change
   for anyone.

3. **The three directories are guaranteed to exist, but are NOT
   guaranteed to be writable-checked, cleaned up, or size-limited yet.**
   `tmp_dir`/`logs_dir`/`state_dir` exist on disk for all 5 real,
   current agent profiles (browser_agent, filesystem_agent,
   system_agent, market_agent, memory_agent) as of 2026-08-21, but
   there is currently no real cleanup lifecycle, no resource limits,
   and no enforcement that a tool actually writes only within its own
   sandbox rather than elsewhere. This contract defines where a
   cooperative tool _should_ write; it does not yet _enforce_ it.

4. **This is not process isolation.** All tools, regardless of
   `agent_name`, still run inside the same, single, shared Odysseus
   Python process. `_jarvis_sandbox` gives a tool a real, conventional
   place to put its own files -- it does not sandbox CPU, memory,
   network, or crash behavior at all. See the real, honest
   architecture notes in `jarvis-todo.md`'s "per-agent isolated
   sandboxes" entry for the full, current scope and what's still not
   built.

5. **`_jarvis_sandbox` should never be treated as a real tool argument
   to validate/require.** It's injected by the dispatcher, not
   supplied by the caller of the `/call` API -- a tool's own input
   schema/validation should not require or reference this key
   directly; it should be read defensively (`arguments.get("_jarvis_sandbox")`),
   never assumed present.

## What a cooperative tool implementation should actually do

```python
sandbox = arguments.get("_jarvis_sandbox")
if sandbox:
    # Use sandbox["tmp_dir"] for any real, temporary files this call
    # creates, instead of a shared /tmp or a hardcoded path.
    ...
else:
    # No agent context -- fall back to existing, pre-sandbox behavior.
    ...
```

## Status as of 2026-08-21

- Injection mechanism: done (`a10724c3`), verified live.
- Sandbox directories: created for all 5 real agent profiles.
- Tool-level adoption: **not started for any real tool yet.** Every
  existing tool implementation currently ignores `_jarvis_sandbox`
  entirely (correctly, per rule 2 above) -- adopting it per-tool is
  real, separate, future work, tool by tool, not a single change.
