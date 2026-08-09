#!/usr/bin/env python3
"""
Public.com MCP Server
======================
Real, direct integration against Public.com's official REST API
(https://public.com/api/docs), built from scratch after confirming
tonight that their hosted MCP server (mcp.public.com) cannot be
reached from this codebase's MCP SDK version: their auth server uses
Client ID Metadata Documents (CIMD) for client registration, not
classic Dynamic Client Registration (DCR), which is all the installed
`mcp` SDK (1.29.0) implements. Confirmed via their own real, live
metadata: prod-api.154310543964.hellopublic.com's OpenID configuration
has no registration_endpoint at all, but does advertise
"client_id_metadata_document_supported": true.

Auth model here is deliberately NOT OAuth -- it's Public.com's own,
simpler, real mechanism: a long-lived secret key (from
https://public.com/settings/security/api) exchanged for a short-lived
access token via POST /userapiauthservice/personal/access-tokens.

Real, deliberate scope decision: this first build exposes READ-ONLY
tools only (accounts, portfolio, quotes). The real secret key's actual
JWT scope includes trading.write (confirmed by decoding a real token
during setup) -- meaning real order placement is technically reachable
with this credential, but is deliberately NOT exposed as a tool here.
Adding order placement/cancellation is a real, separate, explicit
decision for later, not something to include by default given real
money is involved.

Registration (same direct API pattern used for jarvis_browser and
filesystem, since the "Add MCP Server" UI form has a submission bug):

    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'public_com',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/public_com/public_com_mcp.py"]',
        env: '{"PUBLIC_COM_SECRET": "<the real secret key>"}'
      })
    }).then(r => r.json()).then(console.log)
"""

import os
import time
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="Public.com",
    instructions=(
        "Real, read-only access to a Public.com brokerage account: "
        "accounts, portfolio positions, and market quotes. Does not "
        "place, modify, or cancel orders -- deliberately not exposed "
        "in this build, even though the underlying credential has "
        "trading.write scope."
    ),
)

_SECRET = os.environ.get("PUBLIC_COM_SECRET", "")
_TOKEN_EXCHANGE_URL = "https://api.public.com/userapiauthservice/personal/access-tokens"
_API_BASE = "https://api.public.com"

# Real, simple in-process token cache -- avoids re-exchanging the
# secret on every single tool call. Refreshed with a real safety
# margin before actual expiry.
_cached_token: Optional[str] = None
_cached_token_expiry: float = 0.0


# Real, single, persistent httpx client -- created lazily on first use
# and reused across all tool calls, matching the same real pattern
# jarvis_browser.py uses for its persistent browser instance. Real,
# confirmed root cause found tonight: creating a fresh
# `async with httpx.AsyncClient(...)` context manager on every single
# call caused a genuine anyio cancel-scope error
# ("Attempted to exit a cancel scope that isn't the current task's
# current cancel scope") specifically under the app's persistent,
# ASGI-managed stdio session -- reproducible via the app's real
# /api/mcp/call path, even though the exact same logic worked
# correctly in an isolated one-shot asyncio.run() test. A single,
# long-lived client avoids repeatedly entering/exiting that scope.
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20)
    return _client


async def _get_access_token() -> str:
    global _cached_token, _cached_token_expiry
    if not _SECRET:
        raise RuntimeError(
            "PUBLIC_COM_SECRET env var not set -- pass it in the server's "
            "env config at registration time."
        )
    now = time.monotonic()
    if _cached_token and now < _cached_token_expiry:
        return _cached_token

    client = _get_client()
    resp = await client.post(
        _TOKEN_EXCHANGE_URL,
        json={"validityInMinutes": 60, "secret": _SECRET},
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()

    _cached_token = data["accessToken"]
    # Real safety margin: treat the token as expiring 5 minutes early
    # so an in-flight tool call never hits a freshly-expired token.
    _cached_token_expiry = now + (60 - 5) * 60
    return _cached_token


async def _api_get(path: str, params: Optional[dict] = None) -> dict:
    token = await _get_access_token()
    client = _get_client()
    resp = await client.get(
        f"{_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params=params or {},
    )
    resp.raise_for_status()
    return resp.json()


async def _api_post(path: str, body: dict) -> dict:
    token = await _get_access_token()
    client = _get_client()
    resp = await client.post(
        f"{_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
async def get_accounts() -> dict:
    """List the real brokerage accounts linked to this Public.com credential."""
    return await _api_get("/userapigateway/trading/account")


@mcp.tool()
async def get_portfolio(account_id: str) -> dict:
    """Get real portfolio positions for a specific account.

    Args:
        account_id: The real account ID, from get_accounts().
    """
    return await _api_get(f"/userapigateway/trading/{account_id}/portfolio/v2")


@mcp.tool()
async def get_quotes(account_id: str, symbols: list[str]) -> dict:
    """Get real, current market quotes for one or more ticker symbols.

    Args:
        account_id: The real account ID, from get_accounts() -- this
            endpoint requires it in the path even though quotes aren't
            account-specific data.
        symbols: List of real, equity ticker symbols, e.g. ["AAPL", "TSLA"].
    """
    instruments = [{"symbol": s, "type": "EQUITY"} for s in symbols]
    return await _api_post(f"/userapigateway/marketdata/{account_id}/quotes", {"instruments": instruments})


if __name__ == "__main__":
    mcp.run()
