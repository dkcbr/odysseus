"""
src/agents/mcp_readiness_cache.py

Short-lived cache over the real /api/mcp/servers `is_ready` field, so
agent_worker.py doesn't make an HTTP call per task. Used only to gate task
dispatch -- never touches _connections/_tools directly (that would repeat
the exact "fresh process, no live state" mistake already made and fixed
once this session).
"""

import time
from typing import Optional, Dict

import requests

MCP_SERVERS_URL = "http://localhost:7000/api/mcp/servers"
# Real cookie name confirmed throughout this session -- NOT "session".
AUTH_COOKIE = {"odysseus_session": "4a075813131f0019beaaee93a533f6f4cfe7ef8cc3b302c77414a6d42497aead"}
CACHE_TTL = 1.0  # seconds

_cache = {"ts": 0.0, "by_id": {}}


def _fetch_all_servers():
    r = requests.get(MCP_SERVERS_URL, cookies=AUTH_COOKIE, timeout=3)
    r.raise_for_status()
    return r.json()


def get_server_entry(server_id: str) -> Optional[Dict]:
    """Look up a server's current entry by id OR name (task payloads use
    the name, e.g. "risk_surface"; /api/mcp/servers keys by both real id
    and name in this cache so either lookup works)."""
    now = time.time()
    if now - _cache["ts"] > CACHE_TTL:
        try:
            data = _fetch_all_servers()
            by_id = {}
            for s in data:
                by_id[s.get("id")] = s
                by_id[s.get("name")] = s
            _cache["by_id"] = by_id
            _cache["ts"] = now
        except Exception:
            pass  # keep stale cache if available; better than blocking every task on a transient fetch failure
    return _cache["by_id"].get(server_id)
