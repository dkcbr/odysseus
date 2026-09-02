#!/usr/bin/env python3
"""
J.A.R.V.I.S -- Risk Surface MCP Server
========================================
Real, added 2026-08-17: rebuild of a real, previously-orphaned server
(the same registry entry name existed pointing at a deleted script --
confirmed via .pyc cache artifacts, real source gone). This is a fresh,
from-scratch implementation, not a recovery of the old file (which
could not be found anywhere on disk).

**Backend switched 2026-08-23** after a real, live audit found two
separate, parallel risk-engine pipelines existed: the original
standalone scripts (~/jarvis/risk_engine/, writing plain JSON files
this server originally read) and a separate, in-process
/api/risk/refresh route in Odysseus (routes/diagnostics_routes.py),
writing to the real knowledge-graph store instead. Confirmed directly:
the JSON files had gone stale (~1 month old) while the systemd timer
driving /api/risk/refresh had run successfully every day, silently
refreshing only the graph copy. DK confirmed the knowledge-graph
pipeline is the real, intended source of truth going forward -- this
server now reads from there instead, keeping its external tool
interface (names, output shape) unchanged.

Exposes the real Risk Engine's already-computed output (now via the
knowledge graph, not the old JSON files) as MCP tools -- a read-only
wrapper, does NOT re-run any real computation itself (the pipeline
runs separately via /api/risk/refresh, on a daily systemd timer).

Real, honest schema note: regime here is a VOLATILITY classifier
(LOW/NORMAL/HIGH bands based on this portfolio's own historical vol
percentile), not a directional bull/bear/sideways classifier -- the
real Risk Engine has no directional-regime module.

Exposes: get_risk_surface() -- single snapshot combining all 4 real
             sources below.
         get_volatility_regime() -- most recent Regime entity.
         get_risk_events() -- most recent date's RiskEvent entities.
         get_risk_factors() -- most recent PortfolioSnapshot entity.
         get_risk_suggestions() -- most recent date's Suggestion entities.

All tools fail visibly (real error message, not silent empty data) if
the graph file is missing, unreadable, or has no entity of the
requested type, and all responses include a real "stale" flag if the
most recent entity's own date is older than 24 hours, so a caller can
tell freshness at a glance rather than trusting silently-old numbers.

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
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="JARVIS Risk Surface",
    instructions=(
        "Read-only access to the real Risk Engine's current output: "
        "volatility regime, risk events, PCA factors, and diagnostic "
        "suggestions. Does not compute anything itself -- reads the "
        "real, already-computed knowledge-graph entities the Risk "
        "Engine pipeline (/api/risk/refresh) writes separately, on a "
        "daily systemd timer. Note: regime here means VOLATILITY "
        "regime (LOW/NORMAL/HIGH), not a directional bull/bear/"
        "sideways call -- the real Risk Engine has no directional "
        "classifier."
    ),
)

# Real, container-internal path for the knowledge-graph store this
# server now reads from, matching the same path the /api/risk/refresh
# route (routes/diagnostics_routes.py) and the knowledge-graph-memory
# MCP server both write to. Falls back to a real, host-side path so
# this stays portable, matching the same pattern the old JSON-based
# version used.
_CONTAINER_PATH = Path("/app/data/knowledge-graph.jsonl")
_HOST_PATH = Path("/home/dk/jarvis/projects/odysseus/data/knowledge-graph.jsonl")
GRAPH_PATH = _CONTAINER_PATH if _CONTAINER_PATH.exists() else _HOST_PATH
STALE_THRESHOLD_SECONDS = 24 * 60 * 60


def _load_graph_entities() -> dict:
    """Real, direct read of the whole knowledge-graph file, returning
    only entity-type records keyed by name. Raises a clear, real error
    (not silent fallback data) if the file is missing."""
    if not GRAPH_PATH.exists():
        raise FileNotFoundError(
            f"Knowledge graph file not found: {GRAPH_PATH}. The risk "
            f"refresh pipeline (/api/risk/refresh) may not have run yet."
        )
    entities = {}
    with open(GRAPH_PATH) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "entity":
                entities[record["name"]] = record
    return entities


def _observation_date(entity: dict) -> str:
    """Real, extracts the 'date: YYYY-MM-DD' observation from an
    entity's own observations list. Returns empty string if absent."""
    for obs in entity.get("observations", []):
        if obs.startswith("date: "):
            return obs[len("date: "):]
    return ""


def _entity_age_seconds(entity: dict) -> float:
    """Real, computes staleness from the entity's own 'date:'
    observation (a calendar date, not a timestamp) -- treats the start
    of that date, UTC, as the reference point."""
    date_str = _observation_date(entity)
    if not date_str:
        return float("inf")
    try:
        entity_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return (datetime.now(timezone.utc) - entity_dt).total_seconds()


def _most_recent_of_type(entities: dict, entity_type: str) -> dict:
    """Real, finds the entity of a given entityType with the most
    recent 'date:' observation. Raises a clear, real error if none
    exist at all."""
    candidates = [e for e in entities.values() if e.get("entityType") == entity_type]
    if not candidates:
        raise FileNotFoundError(
            f"No {entity_type} entity found in the knowledge graph. "
            f"The risk refresh pipeline (/api/risk/refresh) may not "
            f"have run yet, or written no data of this type."
        )
    candidates.sort(key=lambda e: _observation_date(e), reverse=True)
    return candidates[0]


def _entities_for_date(entities: dict, entity_type: str, date_str: str) -> list:
    """Real, all entities of a given type matching a specific date --
    used for RiskEvent/Suggestion, which can have multiple real
    entries per day, unlike Regime/PortfolioSnapshot (one per day)."""
    return [
        e for e in entities.values()
        if e.get("entityType") == entity_type and _observation_date(e) == date_str
    ]


def _wrap(entity_or_entities) -> dict:
    """Real, wraps one entity or a list of entities with the same
    _meta staleness shape the old JSON-file version returned, computed
    from the newest real entity's own date."""
    items = entity_or_entities if isinstance(entity_or_entities, list) else [entity_or_entities]
    if not items:
        return {"data": [], "_meta": {"source": "knowledge_graph", "age_seconds": None, "stale": True}}
    age_seconds = min(_entity_age_seconds(e) for e in items)
    return {
        "data": entity_or_entities,
        "_meta": {
            "source": "knowledge_graph",
            "age_seconds": round(age_seconds) if age_seconds != float("inf") else None,
            "stale": age_seconds > STALE_THRESHOLD_SECONDS,
        },
    }


@mcp.tool()
def get_volatility_regime() -> str:
    """Real, current volatility regime from the most recent Regime
    entity in the knowledge graph -- a LOW/NORMAL/HIGH classification
    based on this portfolio's own historical volatility percentile.
    NOT a directional (bull/bear) signal -- the real Risk Engine has
    no directional classifier."""
    entities = _load_graph_entities()
    regime = _most_recent_of_type(entities, "Regime")
    return json.dumps(_wrap(regime), indent=2)


@mcp.tool()
def get_risk_events() -> str:
    """Real, recent risk events detected by the Risk Engine (vol
    spikes, factor dominance, correlation breakdown) -- all RiskEvent
    entities from the most recent date present in the graph."""
    entities = _load_graph_entities()
    most_recent_regime = _most_recent_of_type(entities, "Regime")
    date_str = _observation_date(most_recent_regime)
    events = _entities_for_date(entities, "RiskEvent", date_str)
    return json.dumps(_wrap(events), indent=2)


@mcp.tool()
def get_risk_factors() -> str:
    """Real PCA factor decomposition from the most recent
    PortfolioSnapshot entity -- variance explained per factor."""
    entities = _load_graph_entities()
    snapshot = _most_recent_of_type(entities, "PortfolioSnapshot")
    return json.dumps(_wrap(snapshot), indent=2)


@mcp.tool()
def get_risk_suggestions() -> str:
    """Real, human-readable diagnostic suggestions -- all Suggestion
    entities from the most recent date present in the graph. Surfaced
    findings for review, not scored or ranked recommendations."""
    entities = _load_graph_entities()
    most_recent_regime = _most_recent_of_type(entities, "Regime")
    date_str = _observation_date(most_recent_regime)
    suggestions = _entities_for_date(entities, "Suggestion", date_str)
    return json.dumps(_wrap(suggestions), indent=2)


@mcp.tool()
def get_risk_surface() -> str:
    """Real, combined snapshot: volatility regime + risk events + PCA
    factors + suggestions, in one call. Each section fails
    independently with a real error message (included inline) if its
    data is missing, rather than the whole call failing silently."""
    sections = {}
    try:
        entities = _load_graph_entities()
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, indent=2)

    try:
        sections["volatility_regime"] = _wrap(_most_recent_of_type(entities, "Regime"))
    except FileNotFoundError as e:
        sections["volatility_regime"] = {"error": str(e)}

    try:
        most_recent_regime = _most_recent_of_type(entities, "Regime")
        date_str = _observation_date(most_recent_regime)
        sections["risk_events"] = _wrap(_entities_for_date(entities, "RiskEvent", date_str))
    except FileNotFoundError as e:
        sections["risk_events"] = {"error": str(e)}

    try:
        sections["risk_factors"] = _wrap(_most_recent_of_type(entities, "PortfolioSnapshot"))
    except FileNotFoundError as e:
        sections["risk_factors"] = {"error": str(e)}

    try:
        most_recent_regime = _most_recent_of_type(entities, "Regime")
        date_str = _observation_date(most_recent_regime)
        sections["suggestions"] = _wrap(_entities_for_date(entities, "Suggestion", date_str))
    except FileNotFoundError as e:
        sections["suggestions"] = {"error": str(e)}

    return json.dumps(sections, indent=2)


@mcp.tool()
def get_risk_surface_health() -> str:
    """Real, lightweight per-subsystem freshness summary -- staleness
    metadata only (no data payload), for the same 4 real entity types
    get_risk_surface reads. Genuinely useful separately from
    get_risk_surface itself: a caller who only wants to check "is
    anything stale right now" without pulling the full payload can use
    this instead. Real, direct check against each entity's own 'date:'
    observation, not cached or estimated."""
    try:
        entities = _load_graph_entities()
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)}, indent=2)

    health = {}
    for key, entity_type in [
        ("volatility_regime", "Regime"),
        ("risk_factors", "PortfolioSnapshot"),
    ]:
        try:
            entity = _most_recent_of_type(entities, entity_type)
            health[key] = _wrap(entity)["_meta"]
        except FileNotFoundError as e:
            health[key] = {"error": str(e)}

    try:
        most_recent_regime = _most_recent_of_type(entities, "Regime")
        date_str = _observation_date(most_recent_regime)
        for key, entity_type in [("risk_events", "RiskEvent"), ("suggestions", "Suggestion")]:
            health[key] = _wrap(_entities_for_date(entities, entity_type, date_str))["_meta"]
    except FileNotFoundError as e:
        health["risk_events"] = {"error": str(e)}
        health["suggestions"] = {"error": str(e)}

    any_stale = any(v.get("stale") for v in health.values() if "stale" in v)
    return json.dumps({"subsystems": health, "any_stale": any_stale}, indent=2)


if __name__ == "__main__":
    mcp.run()
