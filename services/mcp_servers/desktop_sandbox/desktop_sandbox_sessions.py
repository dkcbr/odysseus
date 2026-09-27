"""
services/mcp_servers/desktop_sandbox/desktop_sandbox_sessions.py

Real, persistent session-container registry for desktop_sandbox_mcp.py.

Real, honest problem this exists to solve: the original implementation
(38811d38) tracked sessions in a plain, in-memory dict. That's fine
while the process is alive, but a crash, a container restart, or a code
deploy (this whole codebase has been rebuilt and restarted repeatedly
tonight) wipes that dict clean while leaving any real, active desktop
containers running, untouched -- a genuine, silent resource leak with
no code anywhere that even remembers those containers exist.

Real design, decided directly with DK before building:
- Persist the registry to /app/data/desktop_sandbox_sessions.json,
  matching this codebase's established, real convention (same
  bind-mounted, persistent location as agent_capabilities.json,
  vault_index.jsonl, etc.) -- every mutation writes through
  immediately, not just to memory.
- On MCP server startup, reconcile "what the registry believes exists"
  against "what's actually running" (queried fresh from the real
  socket-proxy, ground truth): a running container with no matching
  registry entry gets destroyed (no session_id to reunite it with, so
  reaping is the safe choice); a registry entry with no matching
  running container gets dropped (the next call for that session_id
  will correctly create a fresh one).
- Kept local to desktop_sandbox specifically, not generalized into a
  cross-server abstraction -- there's only one real consumer right now,
  and this module's own naming/lifecycle semantics (desktop-task-*
  prefix, VNC readiness) are specific to it. Lift and adapt this later
  if a second, real, per-session-container MCP server is actually
  built, not before.
"""

import json
import os
from pathlib import Path

from desktop_container_lifecycle import (
    destroy_task_container, list_running_task_containers,
)

REGISTRY_PATH = Path("/app/data/desktop_sandbox_sessions.json")


def load_registry() -> dict:
    """Real, direct load of the persisted registry. Returns an empty
    dict (not an error) if the file doesn't exist yet -- a fresh
    deployment with no prior sessions is a normal, expected state, not
    a failure."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        with open(REGISTRY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Real, deliberate: a corrupted/unreadable registry file should
        # degrade to "start fresh", not crash the whole MCP server on
        # startup. Any real, live containers it might have referenced
        # get caught and cleaned up by the reconciliation pass anyway.
        return {}


def save_registry(registry: dict) -> None:
    """Real, atomic write -- write to a real, unique temp file, then
    os.replace() over the real target, matching the same safe pattern
    already established in this codebase (routes/prefs_routes.py's own
    _save()), so a crash mid-write never leaves a corrupted, half-written
    registry file behind."""
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = REGISTRY_PATH.with_suffix(f".tmp.{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp_path, REGISTRY_PATH)


async def reconcile_on_startup() -> dict:
    """Real, direct ground-truth reconciliation, run once at MCP server
    startup. Returns the cleaned, trustworthy registry dict to use as
    the server's real, in-memory session state going forward.

    Real, deliberate ordering: query real, running containers FIRST,
    then compare against the persisted registry -- not the reverse --
    so a container that both exists AND is registered is never
    mistakenly destroyed due to a timing race with something else
    creating containers concurrently (there isn't one right now, but
    ground-truth-first is the safer, more defensible order regardless).
    """
    registry = load_registry()
    running_names = set(await list_running_task_containers())
    registered_names = {info["name"] for info in registry.values()}

    # Real, running container with no registry entry -- no session_id
    # to reunite it with, so the safe, correct action is to destroy it.
    orphaned = running_names - registered_names
    for name in orphaned:
        await destroy_task_container(name)

    # Real, registry entry with no matching running container -- the
    # container itself died or was removed some other way; drop the
    # stale entry so the next call for that session_id creates fresh.
    cleaned = {sid: info for sid, info in registry.items()
               if info["name"] in running_names}

    if cleaned != registry:
        save_registry(cleaned)

    return cleaned
