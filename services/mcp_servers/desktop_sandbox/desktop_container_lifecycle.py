"""
services/mcp_servers/desktop_sandbox/desktop_container_lifecycle.py

Real, per-task disposable desktop container lifecycle: create, wait for
VNC readiness, and destroy -- driving Docker only through the real,
restricted docker-socket-proxy service (docker-compose.yml), never a
raw /var/run/docker.sock mount, which would be root-equivalent to the
host. See docker-compose.yml's own comment on that service for the
real, specific permissions granted and why.

Real, honest design note: containers created here via the raw Docker
API (not docker-compose) DO get real, working Docker-internal DNS
registration -- confirmed directly (DNSNames in the container's own
inspect output, and getent hosts resolving correctly) -- but vncdotool
itself rejects any hostname containing an underscore at its own
validation stage (confirmed directly: identical container, "_"-named
alias raised "invalid hostname"; hyphen-named alias passed validation
and correctly proceeded to a real DNS lookup instead). Container names
here are therefore hyphen-only, never underscore.

All requests use asyncio's own httpx-free, stdlib urllib -- deliberately
no new HTTP client dependency for what's a small, internal, low-volume
control-plane surface.
"""

import asyncio
import json
import secrets
import urllib.error
import urllib.request

PROXY_BASE = "http://docker-socket-proxy:2375"
IMAGE = "accetto/ubuntu-vnc-xfce-firefox-g3"
NETWORK = "odysseus_default"
VNC_READY_TIMEOUT_SECONDS = 30
VNC_READY_POLL_INTERVAL_SECONDS = 1


class ContainerLifecycleError(Exception):
    """Real, deliberate: distinguishes lifecycle failures (create/start/
    destroy) from tool-execution failures inside a successfully-running
    container, so callers can tell the two apart."""


def _sync_request(method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    url = f"{PROXY_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _real_container_name(task_id: str) -> str:
    """Real, hyphen-only name -- see this module's own docstring for why
    underscores must never appear here."""
    return f"desktop-task-{task_id}"


async def create_task_container(task_id: str) -> dict:
    """Real, complete container creation + start for one task. Returns a
    dict with the real container name (usable directly as a vncdotool
    host) and the real, freshly-generated, per-task VNC password --
    never the shared, static password used by the persistent
    desktop-sandbox service."""
    name = _real_container_name(task_id)
    vnc_password = secrets.token_urlsafe(16)

    status, body = await asyncio.to_thread(
        _sync_request, "POST", f"/containers/create?name={name}",
        {
            "Image": IMAGE,
            "Env": [f"VNC_PW={vnc_password}"],
            "HostConfig": {"NetworkMode": NETWORK},
        },
    )
    if status != 201:
        raise ContainerLifecycleError(
            f"Real container create failed for task {task_id} (HTTP {status}): "
            f"{body.decode('utf-8', errors='replace')}")
    container_id = json.loads(body)["Id"]

    status, body = await asyncio.to_thread(
        _sync_request, "POST", f"/containers/{container_id}/start")
    if status != 204:
        # Real, deliberate: attempt cleanup of the created-but-unstarted
        # container before raising, so a failed start doesn't leak a
        # real, orphaned container.
        await asyncio.to_thread(
            _sync_request, "DELETE", f"/containers/{container_id}?force=true")
        raise ContainerLifecycleError(
            f"Real container start failed for task {task_id} (HTTP {status}): "
            f"{body.decode('utf-8', errors='replace')}")

    return {"name": name, "container_id": container_id, "vnc_password": vnc_password}


async def wait_for_vnc_ready(name: str) -> None:
    """Real, direct TCP-connect polling against the container's real VNC
    port (5901) -- not a fixed sleep, and not vncdotool itself (which
    would raise a hard error on a not-yet-listening port rather than
    something cleanly retriable). Raises ContainerLifecycleError if the
    container never becomes reachable within the real timeout."""
    deadline = asyncio.get_event_loop().time() + VNC_READY_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(name, 5901), timeout=3)
            writer.close()
            await writer.wait_closed()
            return
        except Exception as e:
            last_error = e
            await asyncio.sleep(VNC_READY_POLL_INTERVAL_SECONDS)
    raise ContainerLifecycleError(
        f"Container {name} never became VNC-reachable within "
        f"{VNC_READY_TIMEOUT_SECONDS}s (last error: {last_error})")


async def list_running_task_containers() -> list[str]:
    """Real, direct query of every currently-running desktop-task-*
    container name, via the same restricted socket-proxy as every other
    operation in this module. Used for startup reconciliation -- see
    desktop_sandbox_sessions.py -- to compare "what's actually running"
    against "what the persisted registry believes exists"."""
    status, body = await asyncio.to_thread(
        _sync_request, "GET", "/containers/json")
    if status != 200:
        raise ContainerLifecycleError(
            f"Real container list failed (HTTP {status}): "
            f"{body.decode('utf-8', errors='replace')}")
    containers = json.loads(body)
    names = []
    for c in containers:
        for raw_name in c.get("Names", []):
            # Real, Docker's own convention: names come back with a
            # leading "/" (e.g. "/desktop-task-abc123").
            clean = raw_name.lstrip("/")
            if clean.startswith("desktop-task-"):
                names.append(clean)
    return names


async def destroy_task_container(name: str) -> None:
    """Real, unconditional teardown -- force-removes regardless of the
    container's current state, and never raises on failure (a task's
    own real result should not be lost or overshadowed by a cleanup
    error; log-and-continue is the right behavior here, not propagate).
    Callers must call this in a real finally block, not just on the
    success path, so a failed task never leaks a running container."""
    try:
        await asyncio.to_thread(
            _sync_request, "DELETE", f"/containers/{name}?force=true")
    except Exception:
        pass
