"""Parser for daily PCA factor entities in the knowledge graph.

These entities come from the knowledge-graph-memory MCP server's
read_graph() output. Real structure (verified directly against a live
dump, not assumed):

    {
      "name": "factor:2026-07-26:3",
      "entityType": "Factor",
      "observations": [
        "date: 2026-07-26",
        "variance_explained: 0.0686",
        "top_loadings: DBC (-0.491), MLPI (-0.475), INDA (0.341), ...",
        "canonicalId: factor:2026_07_26:3"
      ]
    }

Notes on real quirks, all confirmed against the live graph:
  - The entity's `name` uses colons (factor:2026-07-26:3); its
    `canonicalId` observation uses underscores (factor:2026_07_26:3).
    This module always derives date/component from `name`, never from
    canonicalId, to avoid depending on that inconsistency.
  - top_loadings is free text inside `observations`, not a structured
    field -- there is no top-level "component" or "top_loadings" key on
    the entity itself.
  - Decimal precision in loadings varies (1-3 digits seen, e.g. "-0.25"
    vs "-0.247") -- the regex must not assume a fixed digit count.
  - Symbols observed are purely alphabetic (2-6 chars); no digits appear
    in any real symbol seen in the live graph.
  - Not every daily entity necessarily has the same number of components
    or the same number of loadings per component (3-8 components/day,
    variable loadings per component) -- this parser makes no assumption
    about counts.
"""

import re
from typing import List, Dict, Any, Optional

# Matches the entity's `name` field: factor:YYYY-MM-DD:<component_index>
NAME_PATTERN = re.compile(r"^factor:(\d{4}-\d{2}-\d{2}):(\d+)$")

# Matches "SYMBOL (loading)" pairs within a top_loadings string. Symbols
# are alphabetic only (confirmed against all 72 real entities -- no
# digits ever appear); loading is a signed float with variable decimal
# precision (1-3 digits seen).
LOADING_PATTERN = re.compile(r"([A-Za-z]+)\s*\((-?\d+(?:\.\d+)?)\)")


class FactorParseError(ValueError):
    """Raised when an entity's name doesn't match the expected
    factor:YYYY-MM-DD:N format. Missing/malformed top_loadings is NOT an
    error here -- it just yields an empty loadings list, since a factor
    entity without loadings is unusual but not necessarily invalid."""


def parse_entity_name(name: str) -> Dict[str, Any]:
    """Extract (date, component_index) from a real entity name like
    'factor:2026-07-26:3'. Raises FactorParseError if the name doesn't
    match the expected daily-factor format (e.g. it's a legacy-era name
    like 'factor:1_crypto', which this parser deliberately does not
    handle -- legacy entities are a different, undated format)."""
    m = NAME_PATTERN.match(name)
    if not m:
        raise FactorParseError(
            f"'{name}' does not match expected format factor:YYYY-MM-DD:N "
            f"(if this is a legacy-era entity like 'factor:1_crypto', it is "
            f"out of scope for this parser)"
        )
    date, component_str = m.groups()
    return {"date": date, "component_index": int(component_str)}


def parse_loadings(top_loadings_text: str) -> List[Dict[str, Any]]:
    """Extract [{symbol, loading}, ...] from a raw top_loadings observation
    string (with or without the 'top_loadings: ' prefix -- both are
    accepted so callers can pass the full observation string directly)."""
    prefix = "top_loadings:"
    if top_loadings_text.strip().startswith(prefix):
        top_loadings_text = top_loadings_text.strip()[len(prefix):]

    results = []
    for symbol, loading_str in LOADING_PATTERN.findall(top_loadings_text):
        results.append({
            "symbol": symbol.upper(),
            "loading": float(loading_str),
        })
    return results


def _find_observation(observations: List[str], key: str) -> Optional[str]:
    """Find the first observation string starting with 'key:'."""
    prefix = f"{key}:"
    for obs in observations:
        if obs.startswith(prefix):
            return obs
    return None


def parse_factor_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Parse one real Factor entity (as returned by read_graph()) into
    structured form:

        {
          "date": "2026-07-26",
          "component_index": 3,
          "variance_explained": 0.0686,   # None if the observation is absent
          "loadings": [{"symbol": "DBC", "loading": -0.491}, ...]
        }

    Raises FactorParseError if entity['name'] isn't a daily-factor name.
    Does NOT raise if top_loadings is missing/empty -- returns loadings: [].
    """
    name_info = parse_entity_name(entity["name"])

    observations = entity.get("observations", [])
    top_loadings_obs = _find_observation(observations, "top_loadings")
    loadings = parse_loadings(top_loadings_obs) if top_loadings_obs else []

    variance_obs = _find_observation(observations, "variance_explained")
    variance_explained = None
    if variance_obs is not None:
        try:
            variance_explained = float(variance_obs.split(":", 1)[1].strip())
        except (IndexError, ValueError):
            variance_explained = None

    return {
        "date": name_info["date"],
        "component_index": name_info["component_index"],
        "variance_explained": variance_explained,
        "loadings": loadings,
    }


def group_by_date(parsed_entities: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Group a list of parse_factor_entity() results by date, matching the
    design spec's {"date": ..., "components": [...]} shape per date."""
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for pe in parsed_entities:
        by_date.setdefault(pe["date"], []).append({
            "component_index": pe["component_index"],
            "variance_explained": pe["variance_explained"],
            "loadings": pe["loadings"],
        })
    return {
        date: {"date": date, "components": sorted(comps, key=lambda c: c["component_index"])}
        for date, comps in by_date.items()
    }


def find_symbol_across_days(
    parsed_entities: List[Dict[str, Any]], symbol: str
) -> List[Dict[str, Any]]:
    """Look up a symbol (e.g. 'HBAR') by presence across all parsed daily
    factor entities -- the correct lookup method given that a symbol's
    component index shifts day to day (confirmed: HBAR appears on
    components 4, 7, and 8 across different days, never a fixed index)."""
    symbol = symbol.upper()
    hits = []
    for pe in parsed_entities:
        for loading in pe["loadings"]:
            if loading["symbol"] == symbol:
                hits.append({
                    "date": pe["date"],
                    "component_index": pe["component_index"],
                    "loading": loading["loading"],
                })
    return sorted(hits, key=lambda h: (h["date"], h["component_index"]))
