#!/usr/bin/env python3
"""
J.A.R.V.I.S — Desktop Sandbox MCP Server
==========================================
Wraps vncdotool's synchronous VNC client as a proper MCP stdio server,
controlling the real, isolated desktop-sandbox container (docker-compose.yml,
accetto/ubuntu-vnc-xfce-firefox-g3 -- a full Ubuntu/XFCE desktop with
Firefox, not just a browser tab).

Real, added 2026-08-23: first agent-control piece of closing the
Bytebot-inspired desktop-isolation gap. Unlike jarvis_browser (a single
Chromium tab) or ydotool/uinput (controls the same, single, real host
desktop directly), this controls a genuinely separate, isolated,
containerized desktop -- actions here never touch the real host session.

Exposes: screenshot, move_mouse, click, key_press, type_text

Real, honest, deliberate scope for this first version: a single,
persistent connection to a single, persistent container (matching
desktop-sandbox's current docker-compose definition) -- not yet
per-task-disposable the way Bytebot's own real architecture is. That's
real, separate, future work if this proves useful.

Registration (same direct API pattern used elsewhere in this codebase):

    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'desktop_sandbox',
        transport: 'stdio',
        command: 'python3',
        args: '["/home/dk/jarvis/projects/odysseus/services/mcp_servers/desktop_sandbox/desktop_sandbox_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import asyncio
import base64
import os

from mcp.server.fastmcp import FastMCP
from vncdotool import api as vnc_api

mcp = FastMCP("desktop_sandbox")

# Real, matches docker-compose.yml's desktop-sandbox service: the raw
# VNC port (5901), not noVNC's websocket port (6901) -- vncdotool speaks
# the real RFB protocol directly. Password read from the same real env
# var the compose file's own VNC_PW is set from, so this stays in sync
# with whatever DESKTOP_SANDBOX_VNC_PW is actually configured, rather
# than a second, separately-hardcoded copy that could drift.
VNC_HOST = os.environ.get("DESKTOP_SANDBOX_VNC_HOST", "127.0.0.1::5901")
VNC_PASSWORD = os.environ.get("DESKTOP_SANDBOX_VNC_PW", "changeme")

_client = None


def _get_client():
    """Real, lazy, persistent connection -- reused across calls, matching
    the same reasoning as jarvis_browser's persistent Chromium session:
    reconnecting on every single tool call would be genuinely wasteful
    and slower than necessary."""
    global _client
    if _client is None:
        _client = vnc_api.connect(VNC_HOST, password=VNC_PASSWORD)
    return _client


def _sync_screenshot() -> dict:
    client = _get_client()
    path = "/tmp/desktop_sandbox_screenshot.png"
    client.captureScreen(path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return {"image_base64": data, "mime_type": "image/png"}


def _sync_move_mouse(x: int, y: int) -> str:
    client = _get_client()
    client.mouseMove(x, y)
    return f"Mouse moved to ({x}, {y})."


def _sync_click(x: int, y: int, button: int) -> str:
    client = _get_client()
    client.mouseMove(x, y)
    client.mousePress(button)
    return f"Clicked button {button} at ({x}, {y})."


def _sync_key_press(key: str) -> str:
    client = _get_client()
    client.keyPress(key)
    return f"Pressed key: {key}"


def _sync_type_text(text: str) -> str:
    client = _get_client()
    for ch in text:
        client.keyPress(ch)
    return f"Typed {len(text)} character(s)."


# Real, deliberate: every tool below wraps its real, blocking vncdotool
# call in asyncio.to_thread(). Confirmed directly, via a real, live hang
# during initial testing, that calling vncdotool's synchronous API
# directly inside an `async def` tool blocks the whole asyncio event
# loop -- including the MCP server's own stdio read/write -- since
# asyncio doesn't preemptively interrupt a blocking synchronous call.
# Matches the same real pattern already used in this codebase's
# service_health.py (_run_subsystem's own asyncio.to_thread wrapping).

@mcp.tool()
async def screenshot() -> dict:
    """Capture a real, current screenshot of the desktop-sandbox container
    and return it as base64-encoded PNG data."""
    return await asyncio.to_thread(_sync_screenshot)


@mcp.tool()
async def move_mouse(x: int, y: int) -> str:
    """Move the mouse cursor to real, absolute (x, y) coordinates on the
    desktop-sandbox container's screen (1360x768)."""
    return await asyncio.to_thread(_sync_move_mouse, x, y)


@mcp.tool()
async def click(x: int, y: int, button: int = 1) -> str:
    """Real, deliberate two-step click: moves the mouse to (x, y) first,
    then clicks -- vncdotool's own docs note that a click without a
    preceding move fires at (0, 0) due to how VNC encodes click events,
    so this tool always does both, matching the documented, correct
    usage pattern rather than leaving that footgun for the caller."""
    return await asyncio.to_thread(_sync_click, x, y, button)


@mcp.tool()
async def key_press(key: str) -> str:
    """Press a single, named key (e.g. 'enter', 'tab', 'ctrl-c',
    'shift-a') on the desktop-sandbox container."""
    return await asyncio.to_thread(_sync_key_press, key)


@mcp.tool()
async def type_text(text: str) -> str:
    """Type a real, literal string of text on the desktop-sandbox
    container, one character at a time. Does not support special keys
    (use key_press for those, e.g. 'enter')."""
    return await asyncio.to_thread(_sync_type_text, text)


if __name__ == "__main__":
    mcp.run()
