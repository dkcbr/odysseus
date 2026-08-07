"""
src/agents/odysseus_dashboard.py

Thin wrapper around Odysseus's real /api/agents/* and /api/agent-tasks/*
endpoints (routes/agent_dashboard.py, routes/tasks.py). Requires an
authenticated requests.Session -- get one from
src.agents.odysseus_auth.get_session().

Usage:
    from src.agents.odysseus_auth import get_session
    from src.agents.odysseus_dashboard import OdysseusDashboard

    session = get_session()
    dash = OdysseusDashboard(session)

    print(dash.recent())
    print(dash.stats())
    print(dash.errors())
    print(dash.agent_history("jarvis_browser"))

Can also be run directly (python3 path/to/this_file.py), not just as
`python3 -m src.agents.odysseus_dashboard` -- the sys.path bootstrap below
handles that.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Running as a plain script: only this file's own directory is on
    # sys.path by default, so the `src.` prefix below can't resolve.
    # Add the project root (two levels up: src/agents/ -> src/ -> root).
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

from src.agents.odysseus_auth import DEFAULT_BASE_URL


class OdysseusDashboard:
    def __init__(self, session: requests.Session, base_url: str = DEFAULT_BASE_URL):
        self.session = session
        self.base_url = base_url.rstrip("/")

    def recent(self, limit: int = 50) -> dict:
        """Most recent MCP tool calls across all servers, newest first."""
        resp = self.session.get(f"{self.base_url}/api/agents/recent",
                                 params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def stats(self) -> dict:
        """Summary counts: total calls, successes/failures, per-server breakdown."""
        resp = self.session.get(f"{self.base_url}/api/agents/stats", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def errors(self, limit: int = 50) -> dict:
        """Most recent FAILED tool calls only, newest first."""
        resp = self.session.get(f"{self.base_url}/api/agents/errors",
                                 params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def agent_history(self, server_name: str, limit: int = 50) -> dict:
        """Recent calls for one specific server/agent, newest first."""
        resp = self.session.get(f"{self.base_url}/api/agents/agent/{server_name}",
                                 params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def call_tool(self, server: str, tool: str, arguments: dict | None = None,
                  agent: str | None = None) -> dict:
        """Invoke a real MCP tool via POST /api/mcp/call.

        Args:
            agent: optional agent role (e.g. "browser_agent") to enforce
                   against src/agents/capabilities.py. If omitted, no
                   role-based restriction is applied.
        """
        body = {"server": server, "tool": tool, "arguments": arguments or {}}
        if agent is not None:
            body["agent"] = agent
        resp = self.session.post(f"{self.base_url}/api/mcp/call", json=body, timeout=60)
        resp.raise_for_status()
        return resp.json()

    # Task queue -- mounted at /api/agent-tasks (NOT /api/tasks, which is
    # Odysseus's own pre-existing scheduled-task system in task_routes.py).

    def create_task(self, agent: str, server: str, tool: str, arguments: dict | None = None,
                    priority: int = 5, max_retries: int = 3, schedule_at: float | None = None) -> dict:
        """Enqueue a task for an agent to pick up later.

        Args:
            priority: 1-10, higher runs sooner (default 5)
            max_retries: how many times to retry on failure before it's
                         permanently marked "failed" (default 3)
            schedule_at: unix timestamp -- task stays invisible to workers
                         until this time. None (default) = eligible immediately.
        """
        resp = self.session.post(
            f"{self.base_url}/api/agent-tasks",
            json={"agent": agent, "server": server, "tool": tool, "arguments": arguments or {},
                  "priority": priority, "max_retries": max_retries, "schedule_at": schedule_at},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_pending_tasks(self, agent: str | None = None) -> list[dict]:
        """Claim and return ALL eligible tasks for this agent, priority-sorted
        (highest first). Calling this also claims the returned tasks
        (marks them 'running')."""
        params = {"agent": agent} if agent else {}
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/pending", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_queue(self) -> dict:
        """Full queue snapshot: pending/running/failed/success tasks, priority-
        sorted, with agent health merged in."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/queue", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_queue_dashboard(self) -> dict:
        """Alias for get_queue() -- same data, /dashboard endpoint."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/dashboard", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_agent_registry(self) -> dict:
        """Declarative registry: which agents should exist, enabled/disabled,
        description, and real capability data (servers/allowed_tools)
        sourced live from src/agents/capabilities.py."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/registry", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_failed_tasks(self, agent: str | None = None) -> list[dict]:
        """All failed tasks, including ones rejected at claim time by
        capability enforcement (never appear in dash.errors(), which only
        covers actual tool-call attempts)."""
        params = {"agent": agent} if agent else {}
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/failed", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def agent_heartbeat(self, agent: str) -> dict:
        """Record a liveness heartbeat for this agent. Call once per poll loop."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/heartbeat/{agent}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_agent_health(self) -> dict:
        """Health snapshot for every agent that has sent a heartbeat --
        each entry includes last_seen, seconds_since_heartbeat, and a
        computed 'alive'/'stale' status."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/health", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def restart_agent(self, agent: str) -> dict:
        """Request a restart for an agent. This only marks the request server-
        side -- see routes/tasks.py's restart_agent() docstring for why an
        actual process restart still needs to happen wherever the worker
        really runs (currently: your host terminals, not this container)."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/restart/{agent}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def enable_agent(self, agent: str) -> dict:
        """Enable an agent -- it can claim tasks and be auto-restarted again."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/registry/{agent}/enable", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def disable_agent(self, agent: str) -> dict:
        """Disable an agent -- its tasks fail immediately at claim time,
        and the host supervisor will never restart it."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/registry/{agent}/disable", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_agent_task_history(self, agent: str) -> dict:
        """Pure, read-only history of every task ever assigned to this
        agent (any status), newest first. Never claims or mutates
        anything -- safe to call repeatedly, e.g. for UI polling."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/history/{agent}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_task(self, task_id: str) -> dict:
        """Look up a single task's current status/result."""
        resp = self.session.get(f"{self.base_url}/api/agent-tasks/{task_id}", timeout=15)
        resp.raise_for_status()
        return resp.json()

    def complete_task(self, task_id: str, result: dict) -> dict:
        """Mark a task as successfully completed."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/{task_id}/complete",
                                  json={"result": result}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def fail_task(self, task_id: str, result: dict) -> dict:
        """Mark a task as failed."""
        resp = self.session.post(f"{self.base_url}/api/agent-tasks/{task_id}/fail",
                                  json={"result": result}, timeout=15)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    import json
    from src.agents.odysseus_auth import get_session

    session = get_session()
    dash = OdysseusDashboard(session)

    print(json.dumps(dash.stats(), indent=2))
    print(json.dumps(dash.recent(limit=5), indent=2))
