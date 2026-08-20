#!/usr/bin/env python3
"""
J.A.R.V.I.S -- Risk Surface MCP Server
========================================
Real, added 2026-08-17: rebuild of a real, previously-orphaned server
(the same registry entry name existed pointing at a deleted script --
confirmed via .pyc cache artifacts, real source gone). This is a fresh,
from-scratch implementation, not a recovery of the old file (which
could not be found anywhere on disk).

Exposes the real Risk Engine's (~/jarvis/risk_engine/) already-computed
output as MCP tools -- a read-only wrapper, does NOT re-run any real
computation itself (the pipeline runs separately via cron/
run_pipeline.py and writes these files). This server just reads and
returns their real, current content.

Real, honest schema note: regime.json is a VOLATILITY classifier
(LOW/NORMAL/HIGH bands based on this portfolio's own historical vol
percentile), not a directional bull/bear/sideways classifier -- the
real Risk Engine has no directional-regime module. Confirmed by
reading every real file in ~/jarvis/risk_engine/ directly before
building this, not assumed.

Exposes: get_risk_surface() -- single snapshot combining all 4 real
             sources below.
         get_volatility_regime() -- regime.json only.
         get_risk_events() -- risk_events.json only.
         get_risk_factors() -- factors.json only.
         get_risk_suggestions() -- suggestions.json only.

All tools fail visibly (real error message, not silent empty data) if
a source file is missing or unreadable, and all responses include a
real "stale" flag if the underlying data is older than 24 hours (based
on the real, actual file mtime), so a caller can tell freshness at a
glance rather than trusting silently-old numbers.

Registration:
    fetch('/api/mcp/servers', {
      method: 'POST',
      credentials: 'same-origin',
      body: new URLSearchParams({
        name: 'risk_surface',
        transport: 'stdio',
        command: 'python3',
        args: '["/app/services/mcp_servers/risk_surface/risk_surface_server.py"]',
        env: '{}'
      })
    }).then(r => r.json()).then(console.log)
"""

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="JARVIS Risk Surface",
    instructions=(
        "Read-only access to the real Risk Engine's current output: "
        "volatility regime, risk events, PCA factors, and diagnostic "
        "suggestions. Does not compute anything itself -- reads the "
        "real, already-computed files the Risk Engine pipeline writes "
        "separately. Note: regime here means VOLATILITY regime "
        "(LOW/NORMAL/HIGH), not a directional bull/bear/sideways call -- "
        "the real Risk Engine has no directional classifier."
    ),
)

# Real, container-internal path -- this container never has direct access
# to the host filesystem's /home/dk/... paths. Mounted read-only in
# docker-compose.yml as /app/risk_engine_data (see the volume comment there
# for why). This script runs both inside the container (via MCP) and could
# theoretically run directly on the host too, so fall back to the real host
# path if the mounted one doesn't exist -- makes this genuinely portable
# rather than assuming one specific runtime environment.
_CONTAINER_PATH = Path("/app/risk_engine_data")
_HOST_PATH = Path("/home/dk/jarvis/risk_engine")
RISK_ENGINE_DIR = _CONTAINER_PATH if _CONTAINER_PATH.exists() else _HOST_PATH
STALE_THRESHOLD_SECONDS = 24 * 60 * 60


def _read_json(filename: str) -> dict:
    """Real, direct read of one Risk Engine output file. Raises a clear,
    real error (not silent fallback data) if missing or invalid."""
    path = RISK_ENGINE_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Risk Engine output file not found: {path}. The pipeline "
            f"(run_pipeline.py) may not have run yet, or this file was "
            f"never generated."
        )
    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Risk Engine output file {path} is not valid JSON: {e}")
    age_seconds = time.time() - path.stat().st_mtime
    return {
        "data": data,
        "_meta": {
            "source_file": str(path),
            "age_seconds": round(age_seconds),
            "stale": age_seconds > STALE_THRESHOLD_SECONDS,
        },
    }


@mcp.tool()
def get_volatility_regime() -> str:
    """Real, current volatility regime from regime.json -- a
    LOW/NORMAL/HIGH classification based on this portfolio's own
    historical volatility percentile. NOT a directional (bull/bear)
    signal -- the real Risk Engine has no directional classifier."""
    result = _read_json("regime.json")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_risk_events() -> str:
    """Real, recent risk events detected by the Risk Engine (vol spikes,
    factor dominance, correlation breakdown) from risk_events.json."""
    result = _read_json("risk_events.json")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_risk_factors() -> str:
    """Real PCA factor decomposition from factors.json -- variance
    explained per factor and top contributing positions (loadings)."""
    result = _read_json("factors.json")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_risk_suggestions() -> str:
    """Real, human-readable diagnostic suggestions from suggestions.json
    -- surfaced findings for review, not scored or ranked recommendations."""
    result = _read_json("suggestions.json")
    return json.dumps(result, indent=2)


@mcp.tool()
def get_risk_surface() -> str:
    """Real, combined snapshot: volatility regime + risk events + PCA
    factors + suggestions, in one call. Each section fails independently
    with a real error message (included inline) if its source file is
    missing, rather than the whole call failing silently."""
    sections = {}
    for key, filename in [
        ("volatility_regime", "regime.json"),
        ("risk_events", "risk_events.json"),
        ("risk_factors", "factors.json"),
        ("suggestions", "suggestions.json"),
    ]:
        try:
            sections[key] = _read_json(filename)
        except (FileNotFoundError, ValueError) as e:
            sections[key] = {"error": str(e)}
    return json.dumps(sections, indent=2)


@mcp.tool()
def get_risk_surface_health() -> str:
    """Real, lightweight per-subsystem freshness summary -- staleness
    metadata only (no data payload), for the same 4 real files
    get_risk_surface reads. Genuinely useful separately from
    get_risk_surface itself: each of the 4 individual tools
    (get_volatility_regime etc.) already includes this same _meta info
    alongside its full data, but a caller who only wants to check
    "is anything stale right now" without pulling the full payload can
    use this instead. Real, direct filesystem check (mtime), not cached
    or estimated."""
    health = {}
    for key, filename in [
        ("volatility_regime", "regime.json"),
        ("risk_events", "risk_events.json"),
        ("risk_factors", "factors.json"),
        ("suggestions", "suggestions.json"),
    ]:
        try:
            result = _read_json(filename)
            health[key] = result["_meta"]
        except (FileNotFoundError, ValueError) as e:
            health[key] = {"error": str(e)}
    any_stale = any(v.get("stale") for v in health.values() if "stale" in v)
    return json.dumps({"subsystems": health, "any_stale": any_stale}, indent=2)


if __name__ == "__main__":
    mcp.run()
