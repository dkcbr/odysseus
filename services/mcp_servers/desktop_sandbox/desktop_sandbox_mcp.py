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
import struct
import time
import uuid

from mcp.server.fastmcp import FastMCP

from desktop_container_lifecycle import (
    create_task_container, wait_for_vnc_ready, destroy_task_container,
    ContainerLifecycleError,
)
from desktop_sandbox_sessions import save_registry, reconcile_on_startup

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
#
# Real, added 2026-08-24 (desktop_sandbox_sessions.py): this starts
# empty and gets populated by reconcile_on_startup() in _run() below,
# not initialized directly here -- see that module's own docstring for
# why a plain in-memory dict alone was a real, silent resource-leak
# risk (a crash/restart forgets every container it was tracking, while
# the real containers themselves keep running).
_sessions: dict[str, dict] = {}


def _persist_sessions() -> None:
    """Real, write-through: every real mutation of _sessions below calls
    this immediately afterward, so the persisted registry never drifts
    out of sync with in-memory state -- not a periodic/batched save."""
    save_registry(_sessions)


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
    _persist_sessions()
    return session


# Real, added 2026-08-26: robustness fix, directly justified by a real,
# live incident tonight -- go_back's original implementation used an
# invalid vncdo key name (capitalized 'Left' instead of the correct,
# lowercase 'left'), and confirmed directly that this doesn't fail
# cleanly, it hangs vncdo indefinitely. Before this fix, _run_vncdo had
# no timeout at all, meaning any future bad key name, subprocess bug,
# or genuinely stuck VNC connection would hang this shared function --
# used by every real tool in this file -- forever, with no recovery.
VNCDO_TIMEOUT_SECONDS = 15


async def _run_vncdo(session_id: str, *args: str) -> str:
    """Real, direct subprocess call to vncdo against this session's own,
    dedicated container -- see this module's own docstring for why this
    is a subprocess, not a Python API import.

    Real, added 2026-08-26: wrapped in a real, explicit timeout (see
    VNCDO_TIMEOUT_SECONDS's own comment for the exact, real incident
    that justified this). 15s comfortably covers every real vncdo
    sequence used by this file's own tools today (the longest,
    open_firefox, totals ~4s of real pause time) while still catching
    a genuine hang quickly rather than tying up this session
    indefinitely."""
    session = await _get_or_create_session(session_id)
    proc = await asyncio.create_subprocess_exec(
        "vncdo", "-s", f"{session['name']}::5901", "-p", session["vnc_password"], *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=VNCDO_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        # Real, deliberate: kill the real, stuck subprocess rather than
        # leave it running in the background after we give up on it --
        # confirmed directly tonight that a hung vncdo process doesn't
        # exit on its own.
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"vncdo timed out after {VNCDO_TIMEOUT_SECONDS}s "
            f"(args: {args!r}) -- likely an invalid key/argument causing "
            f"a real, known hang, not a slow but working command.")
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
            if idle:
                for sid in idle:
                    session = _sessions.pop(sid, None)
                    if session:
                        await destroy_task_container(session["name"])
                _persist_sessions()
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


# Real, added 2026-08-26: the real, expected resolution every fixed
# pixel coordinate in this file (open_firefox, navigate_and_screenshot)
# was confirmed against, live, on 2026-08-25. Not a guess -- read
# directly from a real screenshot's own PNG header during that
# session's development.
EXPECTED_WIDTH = 1360
EXPECTED_HEIGHT = 768


def _png_dimensions(path: str) -> tuple[int, int]:
    """Real, direct read of a PNG's own width/height from its IHDR
    chunk (bytes 16-24), rather than pulling in an imaging library for
    a single, simple check."""
    with open(path, "rb") as f:
        header = f.read(24)
    width, height = struct.unpack(">II", header[16:24])
    return width, height


async def _verify_expected_resolution(session_id: str) -> None:
    """Real, added 2026-08-26: robustness check, directly motivated by
    an honest limitation flagged in open_firefox/navigate_and_screenshot's
    own docstrings since they were first written -- both use fixed pixel
    coordinates that silently click the wrong thing if the container's
    real screen resolution ever changes from what was confirmed on
    2026-08-25. This raises a real, clear, early error instead of a
    silent, wrong click when that assumption no longer holds."""
    path = f"/tmp/desktop_sandbox_rescheck_{uuid.uuid4().hex}.png"
    await _run_vncdo(session_id, "capture", path)
    try:
        width, height = _png_dimensions(path)
    finally:
        os.remove(path)
    if (width, height) != (EXPECTED_WIDTH, EXPECTED_HEIGHT):
        raise RuntimeError(
            f"Real resolution mismatch: this session's desktop is "
            f"{width}x{height}, but open_firefox/navigate_and_screenshot's "
            f"fixed pixel coordinates were confirmed against "
            f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}. Proceeding would likely "
            f"click the wrong thing -- refusing rather than doing that "
            f"silently.")


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
async def double_click(session_id: str, x: int, y: int, button: int = 1) -> str:
    """Real, deliberate double-click: moves the mouse to (x, y), then
    sends two clicks separated by a real, short pause. Chained into one
    real vncdo invocation (move -> click -> pause -> click), matching the
    existing click tool's own single-call pattern -- vncdo's own real
    'pause SECONDS' command handles the gap between clicks, rather than
    two separate subprocess calls from this side. 0.15s genuinely sits
    within the real, standard OS double-click interval (typically
    ~0.2-0.5s), confirmed against vncdo's own real --help output."""
    await _run_vncdo(
        session_id, "move", str(x), str(y),
        "click", str(button), "pause", "0.15", "click", str(button),
    )
    return f"Double-clicked button {button} at ({x}, {y})."


@mcp.tool()
async def scroll(session_id: str, direction: str) -> str:
    """Real scroll, implemented the same way the actual VNC protocol
    represents it -- there is no dedicated scroll command; scroll wheel
    events are sent as clicks on virtual mouse buttons 4 (up) and 5
    (down), the same convention vncdo/vncdotool follow. Only up/down are
    genuinely, reliably supported this way -- horizontal scroll
    (buttons 6/7) is a much less consistently implemented X11
    convention and is deliberately not exposed here."""
    button_map = {"up": "4", "down": "5"}
    if direction not in button_map:
        raise ValueError(
            f"Unsupported scroll direction: {direction!r}. Use 'up' or 'down'.")
    await _run_vncdo(session_id, "click", button_map[direction])
    return f"Scrolled {direction}."


@mcp.tool()
async def new_tab(session_id: str) -> str:
    """Real, deliberately simple macro: opens a new browser tab via
    Firefox's own real, standard keyboard shortcut (Ctrl+T), confirmed
    directly against vncdo's own real, documented 'key' command syntax
    (not assumed) -- no fixed screen coordinates involved at all, unlike
    open_firefox/navigate_and_screenshot, so this one isn't tied to this
    container's current resolution or menu layout the same way. Assumes
    Firefox is already the focused/active window."""
    await _run_vncdo(session_id, "key", "ctrl-t", "pause", "0.5")
    return "Opened a new tab (Ctrl+T)."


@mcp.tool()
async def close_tab(session_id: str) -> str:
    """Real, deliberately simple macro: closes the current browser tab
    via Firefox's own real, standard keyboard shortcut (Ctrl+W). Same
    real, coordinate-free approach as new_tab."""
    await _run_vncdo(session_id, "key", "ctrl-w", "pause", "0.5")
    return "Closed the current tab (Ctrl+W)."


@mcp.tool()
async def go_back(session_id: str) -> str:
    """Real, deliberately simple macro: navigates back in browser
    history via Firefox's own real, standard keyboard shortcut
    (Alt+Left). Same real, coordinate-free approach as new_tab.

    Real, honest bug caught and fixed live, 2026-08-25: vncdotool's own
    KEYMAP dict maps this key as lowercase 'left' (confirmed directly
    from the real, live import), not the capitalized 'Left' an X11
    keysym name would suggest -- using the capitalized form doesn't
    just fail cleanly, it causes vncdo to hang indefinitely (confirmed
    directly: a real, stuck process had to be killed). Lowercase is
    required here."""
    await _run_vncdo(session_id, "key", "alt-left", "pause", "1")
    return "Navigated back (Alt+Left)."


@mcp.tool()
async def go_forward(session_id: str) -> str:
    """Real, deliberately simple macro: navigates forward in browser
    history via Firefox's own real, standard keyboard shortcut
    (Alt+Right). Same real, coordinate-free approach as new_tab. Same
    real, lowercase-key requirement as go_back -- see that tool's own
    docstring for the full, real reasoning."""
    await _run_vncdo(session_id, "key", "alt-right", "pause", "1")
    return "Navigated forward (Alt+Right)."


@mcp.tool()
async def open_firefox(session_id: str) -> str:
    """Real, verified sequence to launch Firefox via the desktop's real
    Applications menu: open menu (top-left corner) -> hover "Web
    Browser" -> click -> settle. The coordinates here (15,15 for the
    menu button, 15,140 for "Web Browser" in the menu) were confirmed
    live, directly, against the real, running desktop-sandbox container
    on 2026-08-25 -- not estimated or assumed. Honest limitation: these
    are pixel coordinates for this container's own current screen
    resolution and this specific XFCE menu's own current layout/theme.
    If either changes, this will silently click the wrong thing --
    there is no resolution-independent menu-item lookup here. The 0.5s
    pauses between move/click steps are real and necessary: a same-tick
    click was directly confirmed to not register against this menu.

    Real, added 2026-08-26: now verifies the real, current screen
    resolution matches what these coordinates were confirmed against,
    before attempting the fixed-coordinate clicks -- see
    _verify_expected_resolution's own docstring for why."""
    await _verify_expected_resolution(session_id)
    await _run_vncdo(
        session_id,
        "move", "15", "15", "click", "1", "pause", "0.5",
        "move", "15", "140", "pause", "0.5", "click", "1", "pause", "3",
    )
    return "Opened Firefox via Applications menu."


@mcp.tool()
async def navigate_and_screenshot(session_id: str, url: str) -> dict:
    """Real, verified browser navigation macro: assumes Firefox is
    already open (call open_firefox first if not) and the address bar
    is empty/selectable, focuses the address bar, types the given URL,
    presses Enter, waits for the page to load, and returns a screenshot.

    The address bar coordinate here (400,82) was confirmed live,
    directly, against the real, running desktop-sandbox container on
    2026-08-25 -- the mouse cursor's own shape changing from an arrow to
    an I-beam was used as the real, direct confirmation signal, not
    assumed. Same honest limitation as open_firefox: this is a fixed
    pixel coordinate for this container's own current resolution and
    Firefox's own current window layout -- it will silently click the
    wrong thing if either changes. The move+click here are deliberately
    chained into the same vncdo invocation -- a real, direct bug was
    caught and fixed during this tool's own development: a click sent
    as a separate invocation from its preceding move fires at (0,0)
    instead, due to how vncdo/the VNC protocol encode click events,
    which in this container's case hits the Applications menu button
    instead of the address bar.

    Real, added 2026-08-26: now verifies the real, current screen
    resolution before attempting the fixed-coordinate click -- same
    real reasoning as open_firefox's own equivalent check."""
    await _verify_expected_resolution(session_id)
    await _run_vncdo(
        session_id,
        "move", "400", "82", "click", "1", "pause", "0.3",
        "type", url, "pause", "0.3", "key", "enter", "pause", "2",
    )
    return await screenshot(session_id)


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


# Real, flow definitions as data: each step is (tool_name, extra_kwargs).
# session_id is deliberately not included per-step -- run_browser_flow
# injects the caller's own session_id into every step, since every real
# tool in this module requires it and a flow should only ever operate
# within one real session/container.
_FLOWS: dict[str, list[tuple[str, dict]]] = {
    # Real, deliberately built from only already ground-truthed steps.
    # An earlier draft of this flow included a click on Google's own
    # search box, guessed at the same coordinate already proven for the
    # address bar -- caught directly before testing: that coordinate was
    # never actually verified against Google's real page layout, and
    # would have just re-clicked the address bar instead. Left out
    # entirely rather than guess a second, unverified coordinate.
    "open_and_capture": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://example.com"}),
    ],
    # Real, the search-box click coordinate (512,376) was confirmed live,
    # directly, on 2026-08-26 -- DK confirmed the cursor landed exactly
    # centered in Google's real search field at that position, then
    # confirmed the full click+type+search sequence actually worked.
    # An earlier draft flow reused the address-bar coordinate here
    # instead, which would have silently typed into the URL bar --
    # caught and fixed before this was ever built.
    "google_search": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://www.google.com"}),
        ("click", {"x": 512, "y": 376}),
        ("type_text", {"text": "jarvis ai"}),
        # Real, fixed: "Return" (capitalized) is not a valid vncdo key
        # name and causes a real, direct hang -- confirmed live, "enter"
        # (lowercase) is the correct name, matching every other real,
        # working key_press call already used tonight.
        ("key_press", {"key": "enter"}),
        ("screenshot", {}),
    ],
    "scroll_and_capture": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://example.com"}),
        ("scroll", {"direction": "down"}),
        ("screenshot", {}),
    ],
    # Real, GitHub login field coordinates confirmed live, directly,
    # against the real, running desktop-sandbox container on 2026-08-26
    # -- DK confirmed each position, and that a real "xxx"/"yyy" test
    # string landed in the correct real field, not the address bar or
    # the wrong field. The submit button's own position was confirmed
    # visually (cursor centered on it) but deliberately never actually
    # clicked during ground-truthing, to avoid triggering a real login
    # attempt against GitHub's own servers. {username}/{password} are
    # real placeholders substituted at call time via run_browser_flow's
    # own params argument -- never hardcoded here.
    "login_sequence": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://github.com/login"}),
        ("click", {"x": 680, "y": 320}),
        ("type_text", {"text": "{username}"}),
        ("click", {"x": 680, "y": 360}),
        ("type_text", {"text": "{password}"}),
        ("click", {"x": 680, "y": 455}),
        ("screenshot", {}),
    ],
    # Real, fixed: the earlier message's own version omitted x/y from the
    # click step entirely, which would genuinely fail -- click's real
    # signature requires both, confirmed against every real, working use
    # of it tonight. {x}/{y} are real, string-formatted placeholders here
    # (run_browser_flow's own substitution only touches string values);
    # the underlying click() call still passes them through str() to
    # vncdo either way, same as every other coordinate tonight.
    "click_target": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "{url}"}),
        ("click", {"x": "{x}", "y": "{y}"}),
        ("screenshot", {}),
    ],
    # Real, parameterized version of google_search -- reuses the exact
    # same search-box coordinate (512,376) already ground-truthed live
    # with DK earlier tonight, not re-verified here since it's the same
    # page element already proven. An earlier proposal for this flow
    # called ("click", {}) with no x/y at all, which would fail
    # immediately -- fixed to match click_target's own proven pattern.
    "google_search_dynamic": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://www.google.com"}),
        ("click", {"x": 512, "y": 376}),
        ("type_text", {"text": "{query}"}),
        ("key_press", {"key": "enter"}),
        ("screenshot", {}),
    ],
    "navigate_and_capture_dynamic": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "{url}"}),
        ("screenshot", {}),
    ],
    "scroll_dynamic": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "{url}"}),
        ("scroll", {"direction": "{scroll_direction}"}),
        ("screenshot", {}),
    ],
    "multi_page_dynamic": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "{page1}"}),
        ("navigate_and_screenshot", {"url": "{page2}"}),
        ("navigate_and_screenshot", {"url": "{page3}"}),
        ("screenshot", {}),
    ],
    "multi_search_dynamic": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://www.google.com"}),
        ("click", {"x": 512, "y": 376}),
        ("type_text", {"text": "{query1}"}),
        ("key_press", {"key": "enter"}),
        ("navigate_and_screenshot", {"url": "https://www.google.com"}),
        ("click", {"x": 512, "y": 376}),
        ("type_text", {"text": "{query2}"}),
        ("key_press", {"key": "enter"}),
        ("screenshot", {}),
    ],
    # Real, Wikipedia account-creation field coordinates confirmed live,
    # directly, with DK on 2026-08-26 -- each field verified via a real
    # "xxx"/"yyy"/"zzz"/test-email string landing in the correct field.
    # {username}/{password}/{email} are real placeholders substituted at
    # call time via run_browser_flow's own params -- never hardcoded.
    # Deliberately, permanently excludes any submit step: the "Create
    # account" button's position (176,680) was confirmed only visually,
    # never actually clicked, since clicking it would genuinely create
    # a real Wikipedia account. If DK ever wants that last step, it
    # needs his explicit, separate go-ahead, not something this flow
    # does on its own.
    "wikipedia_signup_fields": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://en.wikipedia.org/w/index.php?title=Special:CreateAccount"}),
        ("click", {"x": 176, "y": 335}),
        ("type_text", {"text": "{username}"}),
        ("click", {"x": 176, "y": 460}),
        ("type_text", {"text": "{password}"}),
        ("click", {"x": 176, "y": 527}),
        ("type_text", {"text": "{password}"}),
        ("click", {"x": 176, "y": 632}),
        ("type_text", {"text": "{email}"}),
        ("screenshot", {}),
    ],
    # Real, YouTube search field and search-button coordinates
    # ground-truthed live, directly, with DK on 2026-08-26. Worth
    # noting directly: the search field (y=141) sits close enough
    # below the browser's own address bar (y=82, ground-truthed
    # earlier) that an initial attempt typed into the address bar by
    # mistake and triggered an unrelated Google redirect -- confirmed
    # and fixed live before this coordinate was accepted.
    "youtube_search": [
        ("open_firefox", {}),
        ("navigate_and_screenshot", {"url": "https://www.youtube.com"}),
        ("click", {"x": 512, "y": 141}),
        ("type_text", {"text": "{query}"}),
        ("click", {"x": 920, "y": 141}),
        ("screenshot", {}),
    ],
}


@mcp.tool()
async def read_screenshot(filename: str) -> dict:
    """Real, step-one-of-two tool: reads a PNG from DK's own real
    screenshot folder, mounted read-only into this container at
    /app/screenshots_data (docker-compose.yml, added 2026-08-26 --
    the container genuinely had no access to /home/dk/Pictures/Screenshots
    at all before this mount was added; confirmed missing live, first,
    before adding it, rather than assumed present) and returns it as
    base64.

    Real, honest limitation, deliberately not oversold: this alone
    does NOT give any part of Jarvis "vision" -- it only moves bytes.
    agent_worker.py is a plain Python script with no image
    understanding of its own. A real second piece (a call from
    agent_worker.py to a genuine vision-capable model, e.g. a real
    Anthropic API call with this base64 data as image content) would be
    needed before anything in Jarvis could actually "see" what's in the
    returned bytes. This tool is that first, real, working piece --
    not a complete solution on its own."""
    path = os.path.join("/app/screenshots_data", filename)
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    with open(path, "rb") as f:
        data = f.read()
    return {
        "filename": filename,
        "base64": base64.b64encode(data).decode("utf-8"),
    }

# Real, maps each real flow step name to its actual, real function --
# used instead of any generic "call_mcp_tool" dispatcher, since every
# real tool this could call already lives in this same module as a
# real, direct Python function, not behind any external MCP-call layer.
_FLOW_TOOL_FUNCS = {
    "open_firefox": open_firefox,
    "navigate_and_screenshot": navigate_and_screenshot,
    "click": click,
    "double_click": double_click,
    "move_mouse": move_mouse,
    "type_text": type_text,
    "key_press": key_press,
    "scroll": scroll,
    "screenshot": screenshot,
}


@mcp.tool()
async def run_browser_flow(session_id: str, flow_name: str, params: dict | None = None) -> dict:
    """Real, sequential execution of a named, pre-defined flow (see
    _FLOWS above) -- each step is a real, already-verified tool in this
    same module, called directly as a real Python function, not through
    any external dispatch layer. Stops and returns immediately on the
    first step that raises a real exception, rather than continuing a
    flow after something has genuinely gone wrong. Deliberately
    atomic from the worker's own perspective: the worker's real
    retry/backoff/cooldown logic (in agent_worker.py) applies to this
    whole flow as one task, not to each individual step -- an honest
    tradeoff, since a step 3 failure means steps 1-2 already ran again
    on any retry, not something this tool tries to make idempotent.

    Real, honest limitation: _FLOWS is currently a fixed, in-code dict,
    not a database or config file -- adding a new flow means editing
    this file directly and rebuilding, the same real deployment cost as
    any other new tool tonight, not a data-only change despite flows
    being represented as data internally.

    params, if given, substitutes real {field_name}-style placeholders
    (e.g. {"username": "..."}) into any string-valued step argument via
    str.format -- added specifically so a flow like login_sequence can
    keep real credentials out of the fixed, in-code _FLOWS dict itself,
    passed instead at call time."""
    if flow_name not in _FLOWS:
        raise ValueError(f"Unknown flow: {flow_name!r}. Known flows: {sorted(_FLOWS.keys())}")

    params = params or {}
    steps = _FLOWS[flow_name]
    results = []
    for tool_name, extra_kwargs in steps:
        func = _FLOW_TOOL_FUNCS[tool_name]
        real_kwargs = {
            k: (v.format(**params) if isinstance(v, str) else v)
            for k, v in extra_kwargs.items()
        }
        try:
            step_result = await func(session_id=session_id, **real_kwargs)
        except Exception as e:
            return {
                "flow": flow_name,
                "status": "failed",
                "failed_step": tool_name,
                "error": str(e),
                "completed_steps": results,
            }
        results.append({"tool": tool_name, "result": step_result})

    return {"flow": flow_name, "status": "success", "steps": results}


@mcp.tool()
async def close_session(session_id: str) -> str:
    """Real, explicit, immediate teardown of this session's desktop
    container. Callers should call this when genuinely done with a
    session rather than rely solely on the automatic idle timeout --
    see this module's own docstring for why both exist."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return f"No active session for {session_id!r} (already closed, or never created)."
    _persist_sessions()
    try:
        await destroy_task_container(session["name"])
    except ContainerLifecycleError as e:
        return f"Session {session_id!r} removed from tracking, but real teardown reported an error: {e}"
    return f"Session {session_id!r} closed."


@mcp.tool()
async def list_sessions() -> list[dict]:
    """Real, direct observability into every currently-tracked session --
    not a black box only visible by querying Docker directly. Returns
    each session's real id, container name, and idle time in seconds."""
    now = time.monotonic()
    return [
        {
            "session_id": sid,
            "container_name": s["name"],
            "idle_seconds": round(now - s["last_used"], 1),
        }
        for sid, s in _sessions.items()
    ]


if __name__ == "__main__":
    async def _run() -> None:
        global _sessions
        # Real, added 2026-08-24: ground-truth reconciliation before
        # anything else runs -- see desktop_sandbox_sessions.py's own
        # docstring for the full, real reasoning.
        _sessions = await reconcile_on_startup()
        asyncio.create_task(_reap_idle_sessions_loop())
        await mcp.run_stdio_async()

    asyncio.run(_run())
