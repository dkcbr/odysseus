"""
src/agents/odysseus_auth.py

Pure authentication logic for talking to Odysseus's own API -- login,
cookie persistence, and session validity checking. No dashboard-specific
code here; see odysseus_dashboard.py for that.

Uses http.cookiejar.MozillaCookieJar for persistence (same file format as
curl's -c/-b flags) rather than requests.Session().cookies.save(), which
doesn't exist on the default RequestsCookieJar.
"""

from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

DEFAULT_BASE_URL = "http://localhost:7000"
DEFAULT_COOKIE_FILE = str(Path.home() / ".odysseus_client_cookies")


def login(username: str = "admin", password: str = "admin",
          cookie_file: str = DEFAULT_COOKIE_FILE,
          base_url: str = DEFAULT_BASE_URL) -> requests.Session:
    """Log in fresh and persist the real odysseus_session cookie to disk."""
    jar = MozillaCookieJar(cookie_file)
    session = requests.Session()
    session.cookies = jar

    resp = session.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Login failed: {data}")

    jar.save(ignore_discard=True, ignore_expires=True)
    return session


def is_session_valid(session: requests.Session, base_url: str = DEFAULT_BASE_URL) -> bool:
    """Check whether a session's cookie is still accepted by Odysseus."""
    try:
        check = session.get(f"{base_url}/api/auth/status", timeout=10)
        return check.ok and bool(check.json().get("username"))
    except requests.RequestException:
        return False


def get_session(cookie_file: str = DEFAULT_COOKIE_FILE,
                 base_url: str = DEFAULT_BASE_URL,
                 username: str = "admin", password: str = "admin") -> requests.Session:
    """Load a previously-saved session cookie, falling back to a fresh login
    if none exists yet (first run) or the saved session has expired.

    This is the single entry point agents should use -- it handles both the
    happy path (reuse) and the fallback path (re-auth) transparently.
    """
    if Path(cookie_file).exists():
        jar = MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        session = requests.Session()
        session.cookies = jar
        if is_session_valid(session, base_url=base_url):
            return session

    return login(username=username, password=password,
                  cookie_file=cookie_file, base_url=base_url)
