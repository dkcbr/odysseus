#!/usr/bin/env python3
"""
J.A.R.V.I.S — Browser MCP Server
==================================
Wraps jarvis_browser.py's JarvisBrowser class as a proper MCP stdio server,
so the browser instance stays alive across multiple tool calls instead of
opening/closing on every invocation.

Exposes: open, search, click, type, run_js, close

Registration (same direct API pattern used for filesystem/tradingview,
since the "Add MCP Server" UI form has a submission bug):

    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'jarvis_browser',
        transport: 'stdio',
        command: 'python3',
        args: '["/home/dk/odysseus-vault-sync/Jarvis/jarvis_browser_mcp.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import sys
import os

# Ensure jarvis_browser.py (in the same directory) is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from jarvis_browser import JarvisBrowser

mcp = FastMCP(
    name="JARVIS Browser",
    instructions=(
        "Browser automation tools backed by a persistent Brave browser session. "
        "The browser instance is created once and reused across all calls in this "
        "server's lifetime — call open() first, then use search/click/type/run_js "
        "against the same live page. Call close() when done to release resources."
    ),
)

# Lazy singleton — the actual browser only launches on first real tool call,
# not at server startup, so registering the server doesn't immediately pop
# open a visible Brave window.
_browser: JarvisBrowser | None = None


def _get_browser() -> JarvisBrowser:
    global _browser
    if _browser is None:
        _browser = JarvisBrowser()
    return _browser


@mcp.tool()
async def open(url: str) -> str:
    """Open a URL in the persistent browser session.

    Args:
        url: Full URL to navigate to, e.g. https://example.com
    """
    return await _get_browser().open(url)


@mcp.tool()
async def search(query: str) -> str:
    """Perform a Google search in the persistent browser session.

    Args:
        query: Search query text
    """
    return await _get_browser().search(query)


@mcp.tool()
async def click(selector: str) -> str:
    """Click an element on the current page.

    Args:
        selector: CSS selector for the element to click, e.g. "button#submit"
    """
    return await _get_browser().click(selector)


@mcp.tool()
async def type(selector: str, text: str) -> str:
    """Type text into an input field on the current page.

    Args:
        selector: CSS selector for the input field, e.g. "input[name='q']"
        text: Text to type into the field
    """
    return await _get_browser().type(selector, text)


@mcp.tool()
async def run_js(script: str) -> str:
    """Execute JavaScript in the current page context and return the result.

    Args:
        script: JavaScript expression to evaluate, e.g. "document.title"
    """
    return await _get_browser().run_js(script)


@mcp.tool()
async def get_dom() -> dict:
    """Return the current page's full HTML, title, and URL."""
    return await _get_browser().get_dom()


@mcp.tool()
async def query_selector(selector: str) -> dict:
    """Find a single element and return its text, attributes, and bounding box.

    Args:
        selector: CSS selector, e.g. "h1" or "input[name='q']"
    """
    return await _get_browser().query_selector(selector)


@mcp.tool()
async def query_selector_all(selector: str) -> dict:
    """Find all matching elements and return their text/attributes/bounding boxes.

    Args:
        selector: CSS selector, e.g. "p" or ".article-title"
    """
    return await _get_browser().query_selector_all(selector)


@mcp.tool()
async def wait_for_selector(selector: str, timeout_ms: int = 5000) -> dict:
    """Block until an element appears on the page (or timeout).

    Args:
        selector: CSS selector to wait for
        timeout_ms: Max time to wait in milliseconds (default 5000)
    """
    return await _get_browser().wait_for_selector(selector, timeout_ms)


@mcp.tool()
async def screenshot() -> dict:
    """Capture a PNG screenshot of the current page, base64-encoded."""
    return await _get_browser().screenshot()


@mcp.tool()
async def get_cookies() -> dict:
    """Return all cookies for the current browser context."""
    return await _get_browser().get_cookies()


@mcp.tool()
async def set_cookie(name: str, value: str, domain: str = None, path: str = "/") -> dict:
    """Set a cookie in the current browser context.

    Args:
        name: Cookie name
        value: Cookie value
        domain: Cookie domain (defaults to the current page's URL if omitted)
        path: Cookie path (default "/")
    """
    return await _get_browser().set_cookie(name, value, domain, path)


@mcp.tool()
async def clear_cookies() -> dict:
    """Clear all cookies in the current browser context. Returns the count removed."""
    return await _get_browser().clear_cookies()


@mcp.tool()
async def get_viewport() -> dict:
    """Return the current viewport size ({width, height})."""
    return await _get_browser().get_viewport()


@mcp.tool()
async def set_viewport(width: int, height: int) -> dict:
    """Set the browser viewport size.

    Args:
        width: Viewport width in pixels
        height: Viewport height in pixels
    """
    return await _get_browser().set_viewport(width, height)


@mcp.tool()
async def scroll(x: int = None, y: int = None, selector: str = None) -> dict:
    """Scroll to specific coordinates, or scroll an element into view.

    Args:
        x: Target horizontal scroll position (ignored if selector given)
        y: Target vertical scroll position (ignored if selector given)
        selector: If given, scrolls this element into view instead of using x/y
    """
    return await _get_browser().scroll(x, y, selector)


@mcp.tool()
async def get_url() -> dict:
    """Return the current page's URL."""
    return await _get_browser().get_url()


@mcp.tool()
async def find(text: str) -> dict:
    """Find an element on the current page by its visible text content.

    Args:
        text: Visible text to search for, e.g. "Sign in"
    """
    return await _get_browser().find(text)


@mcp.tool()
async def back() -> dict:
    """Navigate back in browser history."""
    return await _get_browser().back()


@mcp.tool()
async def forward() -> dict:
    """Navigate forward in browser history."""
    return await _get_browser().forward()


@mcp.tool()
async def reload() -> dict:
    """Reload the current page."""
    return await _get_browser().reload()


@mcp.tool()
async def close() -> str:
    """Close the persistent browser session and release resources.

    Call this when done with browser automation to clean up the Brave
    process and Playwright context. The next tool call will launch a
    fresh browser instance.
    """
    global _browser
    if _browser is not None:
        try:
            await _browser.close()
        except Exception as e:
            return f"Closed with warning: {e}"
        finally:
            _browser = None
        return "Browser closed."
    return "No browser session was open."


@mcp.tool()
async def list_pages() -> dict:
    """Real list of all pages currently open in this browser context,
    including popups opened via window.open() -- Playwright tracks
    these natively via BrowserContext.pages. Index 0 is the original
    primary page this session started with."""
    return await _get_browser().list_pages()


@mcp.tool()
async def switch_page(index: int) -> dict:
    """Switch which page all other tools (get_dom, click, run_js, etc.)
    operate on. Get the real index from list_pages() first.

    Args:
        index: Real page index from list_pages()
    """
    return await _get_browser().switch_page(index)


if __name__ == "__main__":
    mcp.run()
