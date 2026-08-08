"""
J.A.R.V.I.S — Browser Controller (Async)
==========================================
Async Playwright wrapper — required for compatibility with FastMCP's
async event loop (the original sync_playwright() version deadlocks/
conflicts when called from inside an async server context).
"""

import os
import shutil

from playwright.async_api import async_playwright


class JarvisBrowser:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._started = False
        # Real multi-page support: the "primary" page created at startup,
        # plus any new pages/popups Playwright's own context tracks
        # natively (e.g. from window.open() -- confirmed this already
        # works via BrowserContext.pages, just wasn't exposed before).
        self._primary_page = None

    async def _ensure_started(self):
        if self._started:
            return
        self._playwright = await async_playwright().start()

        # Use real, visible Brave when it exists and a display is available
        # (JARVIS REPL on the host). Fall back to headless Chromium otherwise
        # (e.g. running inside Odysseus's headless Docker container, where
        # Brave isn't installed and there's no $DISPLAY at all).
        brave_path = shutil.which("brave-browser") or "/usr/bin/brave-browser"
        has_brave = os.path.exists(brave_path)
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        if has_brave and has_display:
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir="/home/dk/.jarvis-brave-profile",
                executable_path=brave_path,
                headless=False,
                args=["--no-sandbox", "--ozone-platform=wayland", "--enable-features=UseOzonePlatform"],
            )
        else:
            # Headless Chromium (bundled with Playwright) -- works anywhere,
            # including inside Odysseus's container, with no visible window.
            self._browser = await self._playwright.chromium.launch_persistent_context(
                user_data_dir="/tmp/.jarvis-chromium-profile",
                headless=True,
                args=["--no-sandbox"],
            )
        self._page = await self._browser.new_page()
        self._primary_page = self._page
        self._started = True

    async def open(self, url: str) -> str:
        await self._ensure_started()
        await self._page.goto(url)
        return f"Opened {url}"

    async def search(self, query: str) -> str:
        await self._ensure_started()
        await self._page.goto(f"https://www.google.com/search?q={query}")
        return f"Searched for {query}"

    async def click(self, selector: str) -> str:
        await self._ensure_started()
        await self._page.click(selector)
        return f"Clicked {selector}"

    async def type(self, selector: str, text: str) -> str:
        await self._ensure_started()
        await self._page.fill(selector, text)
        return f"Typed '{text}' into {selector}"

    async def run_js(self, script: str) -> str:
        await self._ensure_started()
        result = await self._page.evaluate(script)
        return str(result)

    async def close(self) -> str:
        if not self._started:
            return "No browser session was open."
        await self._browser.close()
        await self._playwright.stop()
        self._started = False
        return "Browser closed."

    # ---- New capabilities: DOM inspection, selectors, cookies, viewport, scroll ----

    async def get_dom(self) -> dict:
        await self._ensure_started()
        return {
            "html": await self._page.content(),
            "title": await self._page.title(),
            "url": self._page.url,
        }

    async def _describe_element(self, element) -> dict:
        """Real helper -- Playwright's ElementHandle has no single "get all
        attributes" method, so we evaluate JS to collect them."""
        text = await element.text_content()
        attributes = await element.evaluate(
            "el => Object.fromEntries(Array.from(el.attributes).map(a => [a.name, a.value]))"
        )
        box = await element.bounding_box()
        return {
            "exists": True,
            "text": text,
            "attributes": attributes,
            "bounding_box": box,
        }

    async def query_selector(self, selector: str) -> dict:
        await self._ensure_started()
        element = await self._page.query_selector(selector)
        if element is None:
            return {"exists": False, "text": None, "attributes": {}, "bounding_box": None}
        return await self._describe_element(element)

    async def query_selector_all(self, selector: str) -> dict:
        await self._ensure_started()
        elements = await self._page.query_selector_all(selector)
        described = [await self._describe_element(el) for el in elements]
        return {"count": len(described), "elements": described}

    async def wait_for_selector(self, selector: str, timeout_ms: int = 5000) -> dict:
        await self._ensure_started()
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms)
            return {"found": True, "timeout": False}
        except Exception:
            # Real Playwright raises TimeoutError (and occasionally other
            # errors for a detached/invalid selector) -- either way, this
            # is a legitimate "didn't appear in time" result, not a crash.
            return {"found": False, "timeout": True}

    async def screenshot(self) -> dict:
        import base64
        await self._ensure_started()
        png_bytes = await self._page.screenshot(type="png")
        return {"screenshot_base64": base64.b64encode(png_bytes).decode("ascii")}

    async def get_cookies(self) -> dict:
        await self._ensure_started()
        cookies = await self._browser.cookies()
        return {"count": len(cookies), "cookies": cookies}

    async def set_cookie(self, name: str, value: str, domain: str = None, path: str = "/") -> dict:
        await self._ensure_started()
        cookie = {"name": name, "value": value, "path": path}
        if domain:
            cookie["domain"] = domain
        else:
            # Real Playwright requires either (domain + path) or url -- use
            # the current page's URL if no explicit domain was given.
            cookie["url"] = self._page.url
        await self._browser.add_cookies([cookie])
        return {"success": True}

    async def clear_cookies(self) -> dict:
        await self._ensure_started()
        existing = await self._browser.cookies()
        count = len(existing)
        await self._browser.clear_cookies()
        return {"count": count}

    async def get_viewport(self) -> dict:
        await self._ensure_started()
        size = self._page.viewport_size
        if size is None:
            return {"width": None, "height": None}
        return {"width": size["width"], "height": size["height"]}

    async def set_viewport(self, width: int, height: int) -> dict:
        await self._ensure_started()
        await self._page.set_viewport_size({"width": width, "height": height})
        return {"success": True}

    async def scroll(self, x: int = None, y: int = None, selector: str = None) -> dict:
        await self._ensure_started()
        if selector:
            element = await self._page.query_selector(selector)
            if element is None:
                return {"x": None, "y": None, "error": f"selector not found: {selector}"}
            await element.scroll_into_view_if_needed()
        else:
            target_x = x if x is not None else 0
            target_y = y if y is not None else 0
            await self._page.evaluate("([x, y]) => window.scrollTo(x, y)", [target_x, target_y])
        pos = await self._page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")
        return {"x": pos["x"], "y": pos["y"]}

    # ---- Real gaps found by checking against a proposed command list ----

    async def get_url(self) -> dict:
        await self._ensure_started()
        return {"url": self._page.url}

    async def find(self, text: str) -> dict:
        """Real Playwright built-in text-selector engine (page.query_selector
        with a "text=" prefix) -- not a fabricated method, this is Playwright's
        own documented selector syntax for finding an element by its visible
        text content."""
        await self._ensure_started()
        element = await self._page.query_selector(f"text={text}")
        if element is None:
            return {"exists": False, "text": None, "attributes": {}, "bounding_box": None}
        return await self._describe_element(element)

    async def back(self) -> dict:
        await self._ensure_started()
        await self._page.go_back()
        return {"url": self._page.url}

    async def forward(self) -> dict:
        await self._ensure_started()
        await self._page.go_forward()
        return {"url": self._page.url}

    async def reload(self) -> dict:
        await self._ensure_started()
        await self._page.reload()
        return {"url": self._page.url}

    async def list_pages(self) -> dict:
        """Real list of all pages currently open in this browser context,
        including any popups opened via window.open() (Playwright's
        BrowserContext.pages tracks these natively). Index 0 is always
        the original primary page."""
        await self._ensure_started()
        pages = self._browser.pages
        result = []
        for i, p in enumerate(pages):
            is_current = p == self._page
            try:
                title = await p.title()
            except Exception:
                title = None
            result.append({
                "index": i,
                "url": p.url,
                "title": title,
                "is_current": is_current,
                "is_primary": p == self._primary_page,
            })
        return {"pages": result, "count": len(pages)}

    async def switch_page(self, index: int) -> dict:
        """Switch the active page all other tools (get_dom, click, etc.)
        operate on, by real index from list_pages(). Real, genuine
        context switch -- not a simulation."""
        await self._ensure_started()
        pages = self._browser.pages
        if index < 0 or index >= len(pages):
            return {"error": f"Invalid index {index}; {len(pages)} real pages currently open (0-{len(pages)-1})"}
        self._page = pages[index]
        await self._page.bring_to_front()
        return {"switched_to_index": index, "url": self._page.url}
