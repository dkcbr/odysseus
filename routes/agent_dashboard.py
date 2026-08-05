"""
routes/agent_dashboard.py

Minimal, real agent-activity dashboard backend. Tracks recent /api/mcp/call
invocations in an in-memory ring buffer (no persistence -- resets on
restart) so there's something genuine to query, rather than a stub.

To actually populate this, routes/mcp_routes.py's call_mcp_tool() needs to
call record_agent_call(...) after each invocation (see the companion edit
to mcp_routes.py).
"""

import json
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Request
from core.middleware import require_admin

router = APIRouter(prefix="/api/agents", tags=["agents"])

_MAX_HISTORY = 200
_recent_calls: deque = deque(maxlen=_MAX_HISTORY)


def record_agent_call(server: str, tool: str, arguments: dict, result: dict, duration_ms: float) -> None:
    """Append one MCP tool invocation to the in-memory recent-activity log.

    Called from routes/mcp_routes.py's /api/mcp/call handler after each
    real tool execution.
    """
    _recent_calls.append({
        "timestamp": time.time(),
        "server": server,
        "tool": tool,
        "arguments": arguments,
        "success": result.get("exit_code", 1) == 0,
        "exit_code": result.get("exit_code"),
        "duration_ms": round(duration_ms, 1),
    })


@router.get("/recent")
async def get_recent_agent_calls(request: Request, limit: int = 50):
    """Return the most recent MCP tool invocations, newest first.

    Args:
        limit: Max number of entries to return (default 50, capped at the
               in-memory buffer size of 200).
    """
    require_admin(request)
    limit = max(1, min(limit, _MAX_HISTORY))
    calls = list(_recent_calls)[-limit:]
    calls.reverse()
    return {"count": len(calls), "calls": calls}


@router.get("/stats")
async def get_agent_call_stats(request: Request):
    """Summary stats over everything currently in the in-memory buffer."""
    require_admin(request)
    calls = list(_recent_calls)
    total = len(calls)
    successes = sum(1 for c in calls if c["success"])
    by_server: dict[str, int] = {}
    for c in calls:
        by_server[c["server"]] = by_server.get(c["server"], 0) + 1

    return {
        "total_calls": total,
        "successes": successes,
        "failures": total - successes,
        "by_server": by_server,
        "buffer_capacity": _MAX_HISTORY,
    }


@router.get("/errors")
async def get_recent_errors(request: Request, limit: int = 50):
    """Return the most recent FAILED tool invocations, newest first.

    Args:
        limit: Max number of entries to return (default 50)
    """
    require_admin(request)
    limit = max(1, min(limit, _MAX_HISTORY))
    failed = [c for c in _recent_calls if not c["success"]]
    failed = failed[-limit:]
    failed.reverse()
    return {"count": len(failed), "calls": failed}


@router.get("/agent/{server_name}")
async def get_agent_history(server_name: str, request: Request, limit: int = 50):
    """Return recent calls for ONE specific server/agent, newest first.

    Args:
        server_name: The MCP server name to filter by, e.g. 'jarvis_browser'
        limit: Max number of entries to return (default 50)
    """
    require_admin(request)
    limit = max(1, min(limit, _MAX_HISTORY))
    matching = [c for c in _recent_calls if c["server"] == server_name]
    matching = matching[-limit:]
    matching.reverse()

    if not matching:
        # Distinguish "agent exists but has no calls yet" from "never heard
        # of this agent" isn't possible from the in-memory buffer alone
        # (we don't track the server registry here) -- return an empty,
        # valid result either way rather than guessing at a 404.
        return {"server": server_name, "count": 0, "calls": []}

    successes = sum(1 for c in matching if c["success"])
    return {
        "server": server_name,
        "count": len(matching),
        "successes": successes,
        "failures": len(matching) - successes,
        "calls": matching,
    }


@router.get("/status")
async def get_agent_status(request: Request):
    """Real, read-only passthrough of the supervisor's live health state
    (written every 5s by agent_supervisor.py on the host, visible here via
    the existing ./data:/app/data mount -- no new plumbing required)."""
    require_admin(request)
    state_file = "/app/data/supervisor_state.json"
    try:
        with open(state_file, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "agent_state_unavailable", "detail": "supervisor_state.json not found"}
    except json.JSONDecodeError as e:
        return {"error": "agent_state_unavailable", "detail": f"malformed JSON: {e}"}
