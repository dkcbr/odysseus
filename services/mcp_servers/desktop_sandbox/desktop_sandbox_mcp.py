#!/usr/bin/env python3
"""
J.A.R.V.I.S — Desktop Sandbox MCP Server
==========================================
Controls real, per-session, disposable desktop containers (a full
Ubuntu/XFCE desktop with Firefox, not just a browser tab -- see
docker-compose.yml's desktop-sandbox service and
desktop_container_lifecycle.py for the real container image and the
real, restricted Docker-socket-proxy-based create/destroy mechanism)
via vncdotool's own CLI (vncdo), spawned as a real, separate subprocess
for each call.

Real, added 2026-08-23/24: closing the Bytebot-inspired
desktop-isolation gap. Unlike jarvis_browser (a single Chromium tab) or
ydotool/uinput (controls the same, single, real host desktop directly),
this controls a genuinely separate, isolated, containerized desktop --
actions here never touch the real host session.

Exposes: screenshot, move_mouse, click, key_press, type_text,
close_session

Real, important design decision, made directly with DK (not assumed):
session-scoped, not task-scoped, disposability. A single real desktop
workflow is rarely one isolated action -- moving the mouse, then
clicking where it landed, then confirming the result with a screenshot
are naturally a *sequence*, and a fresh, empty container per individual
tool call would break that continuity entirely (the click would land
on a mouse position from a container that no longer exists). Every
tool below therefore takes a real session_id: the first call for a
given session_id lazily creates a real, dedicated container; every
subsequent call with the same session_id reuses it; the container is
torn down either explicitly (close_session) or automatically once idle
past a real timeout -- see _reap_idle_sessions_loop below for why an
automatic safety net exists alongside the explicit tool, not instead
of it.

Real, honest, important implementation note: this deliberately does NOT
import vncdotool's own Python API into this process. Confirmed directly
during development that doing so hangs the whole MCP server completely
(even for tool calls unrelated to any VNC operation) -- vncdotool's API
manages its own internal Twisted reactor thread, and mixing Twisted and
asyncio in one process is a known, real category of incompatibility.
Spawning vncdo as a genuinely separate subprocess for each call sidesteps
this entirely, since Twisted's reactor then lives in its own, separate
process, never touching this server's own asyncio event loop.

Registration (same direct API pattern used elsewhere in this codebase):

    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'desktop_sandbox',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/desktop_sandbox/desktop_sandbox_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import asyncio
import base64
import hashlib
import os
import time
import uuid

from mcp.server.fastmcp import FastMCP

from desktop_container_lifecycle import (
    create_task_container, wait_for_vnc_ready, destroy_task_container,
    ContainerLifecycleError,
)

mcp = FastMCP("desktop_sandbox")

# Real, session idle timeout: if no tool call for a session arrives
# within this window, the reaper loop below tears it down automatically.
# Deliberately separate from, not a replacement for, the explicit
# close_session tool -- a well-behaved caller should still close its own
# session when genuinely done, since this timeout exists as a safety net
# for forgotten/crashed callers, not as the primary cleanup path (a
# real container sitting idle for the full timeout is real, wasted
# memory/CPU in the meantime).
SESSION_IDLE_TIMEOUT_SECONDS = 600
REAPER_INTERVAL_SECONDS = 60

# Real, module-level session state: session_id -> {name, vnc_password,
# last_used}. A plain dict is safe here without a lock -- every real
# access happens on this same, single asyncio event loop; there's no
# actual concurrent-thread mutation to guard against.
_sessions: dict[str, dict] = {}


def _safe_session_key(session_id: str) -> str:
    """Real, deliberate: never build a container name directly from a
    caller-supplied session_id. Confirmed directly during the lifecycle
    module's own development that vncdotool rejects hostnames containing
    an underscore, and a caller-supplied session_id could contain
    genuinely any character. Hashing to a short, fixed-format,
    hyphen-safe hex string sidesteps that class of bug entirely, for any
    future session_id shape, not just the ones tested today."""
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return digest


async def _get_or_create_session(session_id: str) -> dict:
    """Real, lazy session lookup/creation. Returns the session's real
    container name and VNC password, creating a fresh, real container on
    first use for this session_id."""
    session = _sessions.get(session_id)
    if session is not None:
        session["last_used"] = time.monotonic()
        return session

    key = _safe_session_key(session_id)
    info = await create_task_container(key)
    await wait_for_vnc_ready(info["name"])
    session = {
        "name": info["name"],
        "vnc_password": info["vnc_password"],
        "last_used": time.monotonic(),
    }
    _sessions[session_id] = session
    return session


async def _run_vncdo(session_id: str, *args: str) -> str:
    """Real, direct subprocess call to vncdo against this session's own,
    dedicated container -- see this module's own docstring for why this
    is a subprocess, not a Python API import."""
    session = await _get_or_create_session(session_id)
    proc = await asyncio.create_subprocess_exec(
        "vncdo", "-s", f"{session['name']}::5901", "-p", session["vnc_password"], *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"vncdo failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', errors='replace')}")
    return stdout.decode("utf-8", errors="replace")


async def _reap_idle_sessions_loop() -> None:
    """Real, periodic safety-net cleanup -- see this module's own
    docstring for why this exists alongside, not instead of,
    close_session. Never lets an exception here kill the whole server;
    a real cleanup-loop failure should degrade to 'sessions leak until
    the next successful pass', not take down every other tool."""
    while True:
        try:
            await asyncio.sleep(REAPER_INTERVAL_SECONDS)
            now = time.monotonic()
            idle = [sid for sid, s in _sessions.items()
                    if now - s["last_used"] > SESSION_IDLE_TIMEOUT_SECONDS]
            for sid in idle:
                session = _sessions.pop(sid, None)
                if session:
                    await destroy_task_container(session["name"])
        except Exception:
            continue


@mcp.tool()
async def screenshot(session_id: str) -> dict:
    """Capture a real, current screenshot of this session's desktop
    container and return it as base64-encoded PNG data. The first call
    for a new session_id creates a fresh, real container; later calls
    with the same session_id reuse it."""
    path = f"/tmp/desktop_sandbox_screenshot_{uuid.uuid4().hex}.png"
    await _run_vncdo(session_id, "capture", path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    os.remove(path)
    return {"image_base64": data, "mime_type": "image/png"}


@mcp.tool()
async def move_mouse(session_id: str, x: int, y: int) -> str:
    """Move the mouse cursor to real, absolute (x, y) coordinates on this
    session's desktop container (1360x768)."""
    await _run_vncdo(session_id, "move", str(x), str(y))
    return f"Mouse moved to ({x}, {y})."


@mcp.tool()
async def click(session_id: str, x: int, y: int, button: int = 1) -> str:
    """Real, deliberate two-step click: moves the mouse to (x, y) first,
    then clicks -- vncdotool's own docs note that a click without a
    preceding move fires at (0, 0) due to how VNC encodes click events,
    so this tool always does both, matching the documented, correct
    usage pattern rather than leaving that footgun for the caller."""
    await _run_vncdo(session_id, "move", str(x), str(y), "click", str(button))
    return f"Clicked button {button} at ({x}, {y})."


@mcp.tool()
async def key_press(session_id: str, key: str) -> str:
    """Press a single, named key (e.g. 'enter', 'tab', 'ctrl-c',
    'shift-a') on this session's desktop container."""
    await _run_vncdo(session_id, "key", key)
    return f"Pressed key: {key}"


@mcp.tool()
async def type_text(session_id: str, text: str) -> str:
    """Type a real, literal string of text on this session's desktop
    container. Does not support special keys (use key_press for those,
    e.g. 'enter')."""
    await _run_vncdo(session_id, "type", text)
    return f"Typed {len(text)} character(s)."


@mcp.tool()
async def close_session(session_id: str) -> str:
    """Real, explicit, immediate teardown of this session's desktop
    container. Callers should call this when genuinely done with a
    session rather than rely solely on the automatic idle timeout --
    see this module's own docstring for why both exist."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return f"No active session for {session_id!r} (already closed, or never created)."
    try:
        await destroy_task_container(session["name"])
    except ContainerLifecycleError as e:
        return f"Session {session_id!r} removed from tracking, but real teardown reported an error: {e}"
    return f"Session {session_id!r} closed."


if __name__ == "__main__":
    async def _run() -> None:
        asyncio.create_task(_reap_idle_sessions_loop())
        await mcp.run_stdio_async()

    asyncio.run(_run())
