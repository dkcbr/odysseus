"""
systemctl_tools.py

RestartServiceTool -- calls the real, separate, host-level
systemctl_agent.py (a narrow, allowlisted restart capability for 4
real, user-level Whisper/Piper services), exactly matching the same
real pattern TickerLookupTool (finance_tools.py) already established:
a plain Python class with an execute() method, a real HTTP call via
httpx, returning {"output"/"error", "exit_code"}.

Real, deliberate scope: this tool can restart exactly the 4 real
services the host-side agent itself allowlists -- the agent enforces
this independently (this tool's own list below is a real, additional,
defense-in-depth check, not the sole enforcement point). Exists
specifically because Odysseus has no direct systemd access from
inside its own container, and should never be handed sudo, container
privilege escalation, or arbitrary shell access to work around that.
"""

import asyncio
import json
import os
from typing import Any, Dict

SYSTEMCTL_AGENT_URL = "http://100.93.206.89:9001/restart"

# Real, deliberate: matches the host-side agent's own real allowlist
# exactly (systemctl_agent.py, ALLOWED_SERVICES). Kept here too as a
# real, additional, defense-in-depth check -- not because the agent
# doesn't already enforce this itself.
ALLOWED_SERVICES = frozenset({
    "jarvis-piper-server.service",
    "jarvis-whisper-bridge.service",
    "jarvis-whisper-server-container.service",
    "jarvis-whisper-server.service",
})


class RestartServiceTool:
    """Restart one of 4 real, allowlisted, user-level Whisper/Piper
    services via the real, separate, host-level systemctl_agent.py.
    Fails closed: no token configured, disallowed service name, or any
    request error returns a clear error -- never a silent no-op."""

    async def execute(self, content: str, ctx: dict) -> dict:
        raw = content.strip()
        service = raw
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    service = str(parsed.get("service") or "").strip()
            except json.JSONDecodeError:
                service = ""
        if not service:
            service = raw.split("\n")[0].strip()

        if service not in ALLOWED_SERVICES:
            return {
                "error": (
                    f"restart_service: '{service}' is not an allowed "
                    f"service. Allowed: {', '.join(sorted(ALLOWED_SERVICES))}"
                ),
                "exit_code": 1,
            }

        token = os.environ.get("SYSTEMCTL_AGENT_TOKEN")
        if not token:
            return {
                "error": (
                    "restart_service: SYSTEMCTL_AGENT_TOKEN is not "
                    "configured. Do not attempt this again this turn -- "
                    "tell the user the restart agent isn't reachable "
                    "right now."
                ),
                "exit_code": 1,
            }

        loop = asyncio.get_running_loop()
        try:
            import httpx

            def _post():
                resp = httpx.post(
                    SYSTEMCTL_AGENT_URL,
                    json={"service": service},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=35,
                )
                return resp

            resp = await asyncio.wait_for(loop.run_in_executor(None, _post), timeout=40)
        except asyncio.TimeoutError:
            return {"error": f"restart_service: timed out restarting {service}", "exit_code": 1}
        except Exception as e:
            return {
                "error": f"restart_service: request failed for {service}: {type(e).__name__}: {e}",
                "exit_code": 1,
            }

        if resp.status_code == 401:
            return {"error": "restart_service: agent rejected the token (unauthorized)", "exit_code": 1}
        if resp.status_code == 400:
            return {"error": f"restart_service: agent rejected '{service}' ({resp.text})", "exit_code": 1}
        if resp.status_code != 200:
            return {
                "error": f"restart_service: agent returned unexpected status {resp.status_code}: {resp.text}",
                "exit_code": 1,
            }

        data = resp.json()
        if not data.get("ok"):
            return {
                "error": f"restart_service: restart of {service} failed: {data.get('error', 'unknown error')}",
                "exit_code": 1,
            }

        return {
            "output": f"{service} restarted successfully at {data.get('timestamp')}",
            "exit_code": 0,
        }
