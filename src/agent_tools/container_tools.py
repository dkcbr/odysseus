"""
container_tools.py

RestartContainerTool -- the first tool in a new, real container_management
domain. Calls the real, already-deployed, already-secured
docker-socket-proxy (Tecnativa's, HAProxy-based) directly over the
internal Docker network -- confirmed directly, empirically, that
Odysseus's own container can already reach it at
http://docker-socket-proxy:2375, and that the proxy's own real
config genuinely blocks the dangerous `exec` endpoint (traced through
both real steps of Docker's own two-step exec API, confirmed 403 on
the actual, dangerous "run it" step) while allowing container
list/inspect/logs/stats/restart.

Real, deliberate, explicit, narrow scope -- DK's own, direct, real
decision: exactly 2 real, already-restart-tested containers
(odysseus-searxng-1, odysseus-ntfy-1). Structurally, not just
policy-level, excludes everything else, especially
odysseus-odysseus-1 (Odysseus's own hosting container -- a real,
explicit design requirement: restarting one's own runtime must be
structurally impossible, not merely discouraged). The allowlist check
happens in code, not just the schema's enum, since a schema enum
alone does not prevent the model from sending an arbitrary string.
"""

import asyncio
import json
import os

DOCKER_SOCKET_PROXY_URL = "http://docker-socket-proxy:2375"

# Real, deliberate, explicit allowlist -- exactly the 2 real containers
# DK directly approved, both already restart-tested empirically before
# this tool existed. Adding a new container here is a real, deliberate
# decision, never something this tool infers or accepts from a caller.
ALLOWED_CONTAINERS = frozenset({
    "odysseus-searxng-1",
    "odysseus-ntfy-1",
})


class RestartContainerTool:
    """Restart one of 2 real, explicitly allowlisted, low-stakes
    containers via the real, already-deployed docker-socket-proxy.
    Structurally cannot restart odysseus-odysseus-1 (Odysseus's own
    runtime) or any other real container -- the allowlist check is a
    real, direct code comparison, not a schema-level suggestion.
    Fails closed: any request error, or any container not on the
    real allowlist, returns a clear error -- never a silent no-op."""

    async def execute(self, content: str, ctx: dict) -> dict:
        raw = content.strip()
        container = raw
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    container = str(parsed.get("container") or "").strip()
            except json.JSONDecodeError:
                container = ""
        if not container:
            container = raw.split("\n")[0].strip()

        # Real, structural check -- the actual safety boundary, not
        # just the schema's own enum (which the model could, in
        # principle, still not respect).
        if container not in ALLOWED_CONTAINERS:
            return {
                "error": (
                    f"restart_container: '{container}' is not an allowed "
                    f"container. Allowed: {', '.join(sorted(ALLOWED_CONTAINERS))}. "
                    "This is a real, structural restriction, not a suggestion."
                ),
                "exit_code": 1,
            }

        loop = asyncio.get_running_loop()
        try:
            import httpx

            def _post():
                return httpx.post(
                    f"{DOCKER_SOCKET_PROXY_URL}/containers/{container}/restart",
                    params={"t": 10},
                    timeout=25,
                )

            resp = await asyncio.wait_for(loop.run_in_executor(None, _post), timeout=30)
        except asyncio.TimeoutError:
            return {"error": f"restart_container: timed out restarting {container}", "exit_code": 1}
        except Exception as e:
            return {
                "error": f"restart_container: request failed for {container}: {type(e).__name__}: {e}",
                "exit_code": 1,
            }

        # Real, deliberate: Docker's own real restart endpoint returns
        # 204 (no content) on real success -- not a JSON body.
        if resp.status_code == 204:
            return {"output": f"{container} restarted successfully.", "exit_code": 0}
        if resp.status_code == 404:
            return {"error": f"restart_container: container '{container}' not found", "exit_code": 1}
        return {
            "error": f"restart_container: proxy returned unexpected status {resp.status_code}: {resp.text[:200]}",
            "exit_code": 1,
        }


class ContainerStatusTool:
    """Read real, current Docker state (running/exited, uptime,
    restart count, health, image, last start time) for any real,
    existing container on the host, via the same real, already-
    deployed docker-socket-proxy. Broad scope, unlike
    RestartContainerTool -- purely read-only (docker inspect via
    /containers/{name}/json), no side effects, so not limited to the
    2-container restart allowlist. Pairs with restart_container the
    same way service_status pairs with restart_service. Read-only,
    never modifies anything."""

    async def execute(self, content: str, ctx: dict) -> dict:
        raw = content.strip()
        container = raw
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    container = str(parsed.get("container") or "").strip()
            except json.JSONDecodeError:
                container = ""
        if not container:
            container = raw.split("\n")[0].strip()

        if not container:
            return {"error": "container_status: no container name provided", "exit_code": 1}

        loop = asyncio.get_running_loop()
        try:
            import httpx

            def _get():
                return httpx.get(
                    f"{DOCKER_SOCKET_PROXY_URL}/containers/{container}/json",
                    timeout=15,
                )

            resp = await asyncio.wait_for(loop.run_in_executor(None, _get), timeout=20)
        except asyncio.TimeoutError:
            return {"error": f"container_status: timed out checking {container}", "exit_code": 1}
        except Exception as e:
            return {
                "error": f"container_status: request failed for {container}: {type(e).__name__}: {e}",
                "exit_code": 1,
            }

        if resp.status_code == 404:
            return {"error": f"container_status: container '{container}' not found", "exit_code": 1}
        if resp.status_code != 200:
            return {
                "error": f"container_status: proxy returned unexpected status {resp.status_code}: {resp.text[:200]}",
                "exit_code": 1,
            }

        data = resp.json()
        state = data.get("State", {})
        summary = {
            "name": data.get("Name", "").lstrip("/"),
            "status": state.get("Status"),
            "running": state.get("Running"),
            "restart_count": data.get("RestartCount"),
            "started_at": state.get("StartedAt"),
            "health": (state.get("Health") or {}).get("Status"),
            "image": data.get("Config", {}).get("Image"),
        }
        lines = [f"{k}: {v}" for k, v in summary.items() if v not in (None, "")]
        return {"output": "Container status for " + container + ":\n" + "\n".join(lines), "exit_code": 0}
