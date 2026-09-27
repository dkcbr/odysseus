"""
systemd_logs_tools.py

ReadSystemdLogsTool -- calls the real, separate, host-level systemctl_agent.py's
/logs endpoint. Matches the exact, same real pattern as
RestartServiceTool (systemctl_tools.py): a plain Python class with an
execute() method, a real HTTP call via httpx, returning
{"output"/"error", "exit_code"}.

ServiceStatusTool -- pairs with the above two, calls the same real
agent's /status endpoint. Same real pattern, broad scope (any real,
existing systemd unit) like ReadSystemdLogsTool, since status/state
metadata is even less sensitive than real log content.

Real, deliberate, explicit scope difference from RestartServiceTool:
DK's own, direct, explicit choice was the BROAD scope here -- any
real, existing systemd unit, not the narrow 4-service allowlist
restart_service uses. This is a genuine, accepted, real security
trade-off (this tool can read logs for sensitive services too, e.g.
sshd) -- the host-side agent itself enforces the real existence check
and the real line-count cap; this tool adds no further restriction on
top, matching the explicitly broad real design.
"""

import asyncio
import json
import os

SYSTEMCTL_AGENT_LOGS_URL = "http://100.93.206.89:9001/logs"
SYSTEMCTL_AGENT_STATUS_URL = "http://100.93.206.89:9001/status"
SYSTEMCTL_AGENT_DEPS_URL = "http://100.93.206.89:9001/dependencies"


async def _call_agent(url: str, payload: dict, tool_label: str, timeout: float = 30) -> dict:
    """Real, shared HTTP-call helper -- both real tools in this file
    hit the same real host agent, just different real endpoints, so
    this avoids duplicating the same real error handling twice."""
    token = os.environ.get("SYSTEMCTL_AGENT_TOKEN")
    if not token:
        return {
            "error": (
                f"{tool_label}: SYSTEMCTL_AGENT_TOKEN is not configured. "
                "Do not attempt this again this turn -- tell the user "
                "the agent isn't reachable right now."
            ),
            "exit_code": 1,
        }

    loop = asyncio.get_running_loop()
    try:
        import httpx

        def _post():
            return httpx.post(
                url, json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout - 5,
            )

        resp = await asyncio.wait_for(loop.run_in_executor(None, _post), timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"{tool_label}: request timed out", "exit_code": 1}
    except Exception as e:
        return {"error": f"{tool_label}: request failed: {type(e).__name__}: {e}", "exit_code": 1}

    if resp.status_code == 401:
        return {"error": f"{tool_label}: agent rejected the token (unauthorized)", "exit_code": 1}
    if resp.status_code == 400:
        return {"error": f"{tool_label}: agent rejected the request ({resp.text})", "exit_code": 1}
    if resp.status_code != 200:
        return {"error": f"{tool_label}: agent returned unexpected status {resp.status_code}: {resp.text}", "exit_code": 1}

    data = resp.json()
    if not data.get("ok"):
        return {"error": f"{tool_label}: failed: {data.get('error', 'unknown error')}", "exit_code": 1}
    return {"_data": data, "exit_code": 0}


def _parse_service_and_lines(content: str) -> tuple[str, int]:
    raw = content.strip()
    service = raw
    lines = 50
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                service = str(parsed.get("service") or "").strip()
                lines = int(parsed.get("lines") or 50)
        except (json.JSONDecodeError, TypeError, ValueError):
            service = ""
    if not service:
        service = raw.split("\n")[0].strip()
    return service, lines


class ReadSystemdLogsTool:
    """Tail real, recent logs for any real, existing systemd service on
    the host, via the real, separate, host-level systemctl_agent.py.
    Broad by DK's own, explicit, real design choice -- not limited to
    a fixed allowlist the way RestartServiceTool is. Fails closed: no
    token configured, or any real request error, returns a clear
    error -- never a silent no-op."""

    async def execute(self, content: str, ctx: dict) -> dict:
        service, lines = _parse_service_and_lines(content)
        if not service:
            return {"error": "read_systemd_logs: no service name provided", "exit_code": 1}

        result = await _call_agent(
            SYSTEMCTL_AGENT_LOGS_URL, {"service": service, "lines": lines},
            "read_systemd_logs",
        )
        if result["exit_code"] != 0:
            return result

        logs = result["_data"].get("logs", "").strip()
        if not logs:
            return {"output": f"No recent log entries found for {service}.", "exit_code": 0}
        return {"output": logs, "exit_code": 0}


class ServiceStatusTool:
    """Read real, current systemd state (active/failed/running, PID,
    memory, restart count, last start time) for any real, existing
    systemd service on the host, via the same real, separate,
    host-level systemctl_agent.py. Broad scope, same as
    ReadSystemdLogsTool -- pairs with it and restart_service to form a
    real diagnose-then-act loop. Read-only, never modifies anything."""

    async def execute(self, content: str, ctx: dict) -> dict:
        service, _ = _parse_service_and_lines(content)
        if not service:
            return {"error": "service_status: no service name provided", "exit_code": 1}

        result = await _call_agent(
            SYSTEMCTL_AGENT_STATUS_URL, {"service": service},
            "service_status",
        )
        if result["exit_code"] != 0:
            return result

        status = result["_data"].get("status", {})
        lines = [f"{k}: {v}" for k, v in status.items() if v not in ("", "[not set]")]
        return {"output": f"Status for {service}:\n" + "\n".join(lines), "exit_code": 0}


class CheckServiceDependenciesTool:
    """Show the real, raw systemd dependency tree (Requires/Wants/
    After/Before, direct and indirect) for any real, existing service
    on the host, via the same real, separate, host-level
    systemctl_agent.py. Same broad scope as the other 2 read-only
    tools -- pairs with service_status/read_systemd_logs/
    restart_service to complete the real system_diagnostics loop:
    status -> logs -> dependencies -> restart. Deliberately returns
    the raw, real `systemctl list-dependencies` output unparsed --
    the model interprets it directly, matching the same simple,
    reliable design as read_systemd_logs. Read-only, never modifies
    anything."""

    async def execute(self, content: str, ctx: dict) -> dict:
        service, _ = _parse_service_and_lines(content)
        if not service:
            return {"error": "check_service_dependencies: no service name provided", "exit_code": 1}

        result = await _call_agent(
            SYSTEMCTL_AGENT_DEPS_URL, {"service": service},
            "check_service_dependencies",
        )
        if result["exit_code"] != 0:
            return result

        deps = result["_data"].get("dependencies", "").strip()
        if not deps:
            return {"output": f"No dependency information found for {service}.", "exit_code": 0}
        return {"output": deps, "exit_code": 0}
