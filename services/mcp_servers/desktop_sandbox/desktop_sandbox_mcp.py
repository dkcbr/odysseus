#!/usr/bin/env python3
"""
J.A.R.V.I.S — Desktop Sandbox MCP Server
==========================================
Controls the real, isolated desktop-sandbox container (docker-compose.yml,
accetto/ubuntu-vnc-xfce-firefox-g3 -- a full Ubuntu/XFCE desktop with
Firefox, not just a browser tab) via vncdotool's own CLI (vncdo), spawned
as a real, separate subprocess for each call.

Real, added 2026-08-23: first agent-control piece of closing the
Bytebot-inspired desktop-isolation gap. Unlike jarvis_browser (a single
Chromium tab) or ydotool/uinput (controls the same, single, real host
desktop directly), this controls a genuinely separate, isolated,
containerized desktop -- actions here never touch the real host session.

Exposes: screenshot, move_mouse, click, key_press, type_text

Real, honest, important implementation note: this deliberately does NOT
import vncdotool's own Python API into this process. Confirmed directly
during development that doing so hangs the whole MCP server completely
(even for tool calls unrelated to any VNC operation) -- vncdotool's API
manages its own internal Twisted reactor thread, and mixing Twisted and
asyncio in one process is a known, real category of incompatibility.
Spawning vncdo as a genuinely separate subprocess for each call sidesteps
this entirely, since Twisted's reactor then lives in its own, separate
process, never touching this server's own asyncio event loop.

Real, honest, deliberate scope for this first version: no per-task
disposability yet (matches desktop-sandbox's current, single, persistent
container definition) -- that's real, separate, future work.

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
import os
import uuid

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("desktop_sandbox")

# Real, matches docker-compose.yml's desktop-sandbox service name -- this
# server runs inside the Odysseus container (per this file's own real
# registration args, /app/...), so it must reach desktop-sandbox via
# Docker Compose's own internal service-name DNS, not "127.0.0.1" (which
# would refer to this container itself, not the separate desktop-sandbox
# one). Confirmed directly: jarvis_shell/jarvis_browser's own real,
# configured args use the same /app/... container-internal path pattern.
VNC_HOST = os.environ.get("DESKTOP_SANDBOX_VNC_HOST", "desktop-sandbox::5901")
VNC_PASSWORD = os.environ.get("DESKTOP_SANDBOX_VNC_PW", "changeme")


async def _run_vncdo(*args: str) -> str:
    """Real, direct subprocess call to vncdo -- see this module's own
    docstring for why this is a subprocess, not a Python API import."""
    proc = await asyncio.create_subprocess_exec(
        "vncdo", "-s", VNC_HOST, "-p", VNC_PASSWORD, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"vncdo failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', errors='replace')}")
    return stdout.decode("utf-8", errors="replace")


@mcp.tool()
async def screenshot() -> dict:
    """Capture a real, current screenshot of the desktop-sandbox container
    and return it as base64-encoded PNG data."""
    path = f"/tmp/desktop_sandbox_screenshot_{uuid.uuid4().hex}.png"
    await _run_vncdo("capture", path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    os.remove(path)
    return {"image_base64": data, "mime_type": "image/png"}


@mcp.tool()
async def move_mouse(x: int, y: int) -> str:
    """Move the mouse cursor to real, absolute (x, y) coordinates on the
    desktop-sandbox container's screen (1360x768)."""
    await _run_vncdo("move", str(x), str(y))
    return f"Mouse moved to ({x}, {y})."


@mcp.tool()
async def click(x: int, y: int, button: int = 1) -> str:
    """Real, deliberate two-step click: moves the mouse to (x, y) first,
    then clicks -- vncdotool's own docs note that a click without a
    preceding move fires at (0, 0) due to how VNC encodes click events,
    so this tool always does both, matching the documented, correct
    usage pattern rather than leaving that footgun for the caller."""
    await _run_vncdo("move", str(x), str(y), "click", str(button))
    return f"Clicked button {button} at ({x}, {y})."


@mcp.tool()
async def key_press(key: str) -> str:
    """Press a single, named key (e.g. 'enter', 'tab', 'ctrl-c',
    'shift-a') on the desktop-sandbox container."""
    await _run_vncdo("key", key)
    return f"Pressed key: {key}"


@mcp.tool()
async def type_text(text: str) -> str:
    """Type a real, literal string of text on the desktop-sandbox
    container. Does not support special keys (use key_press for those,
    e.g. 'enter')."""
    await _run_vncdo("type", text)
    return f"Typed {len(text)} character(s)."


if __name__ == "__main__":
    mcp.run()
