"""Parser for RiskEvent entities in the knowledge graph.

Real structure, verified directly against all 22 live entities (NOT
assumed uniform -- it isn't). Three genuine shapes exist:

1. One legacy entity (riskevent:2026-07-22:factor1_dominance):
   uses "timestamp: 2026-07-22T00:00:00Z" (full ISO) and
   "description: ..." (not "detail:") for commentary, plus a raw "json:"
   observation.

2. Modern FactorDominance / CorrelationBreakdown entities (18 of them):
   "date: YYYY-MM-DD", "category: FactorDominance|CorrelationBreakdown",
   "severity: warning" (always exactly this value when present),
   "detail: <commentary>". Two of these also carry ad hoc one-off
   analyst fields not part of any consistent schema
   (live_verification, dust_vs_real_assessment).

3. Modern CorrelationRegime entities (3 of them, all dated 2026-08-03):
   "date: YYYY-MM-DD", "category: CorrelationRegime", "pair: X/Y",
   "rho_recent: <float> (commentary)", "rho_baseline: <float> (commentary)",
   "delta: <float>", "classification: breakdown", "source: <text>".
   NO severity field and NO detail field at all in this shape.

Design principle (matching src/regime/'s precedent): one envelope shape
with stable fields present on every entity, plus a category-specific
`detail` payload. Never invent a unified schema across the three real
shapes; never drop the original observations.
"""

import re
from typing import List, Dict, Any, Optional

# riskevent:YYYY-MM-DD:<label> -- label may itself contain colons
# (e.g. "correlationregime:mp_schd"), so capture everything after the date.
NAME_PATTERN = re.compile(r"^riskevent:(\d{4}-\d{2}-\d{2}):(.+)$")

# Leading signed float at the start of a value string, e.g. extracting
# -0.7197 from "-0.7197 (window: 20d, real, matches ...)".
LEADING_FLOAT = re.compile(r"^(-?\d+(?:\.\d+)?)")

# Fields handled explicitly -- anything else becomes part of "extra".
_KNOWN_KEYS = {
    "date", "timestamp", "category", "severity", "detail", "description",
    "canonicalId", "pair", "rho_recent", "rho_baseline", "delta",
    "classification", "source",
}
# Note: 'json' is deliberately NOT in this set -- it's an ad hoc field seen
# on only the one legacy entity, same category as live_verification/
# dust_vs_real_assessment, and belongs in 'extra' like those do.


class RiskEventParseError(ValueError):
    """Raised when an entity's name doesn't match riskevent:YYYY-MM-DD:label."""


def _find_observation(observations: List[str], key: str) -> Optional[str]:
    prefix = f"{key}:"
    for obs in observations:
        if obs.startswith(prefix):
            return obs[len(prefix):].strip()
    return None


def _leading_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    m = LEADING_FLOAT.match(value.strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _extract_key(obs: str) -> Optional[str]:
    """Get the 'key' part of an 'key: value' observation string, or None
    if it doesn't look like one (e.g. the legacy entity's
    '@2026-07-22T18:23:00Z initialized: true' style line, if seen)."""
    if ":" not in obs:
        return None
    return obs.split(":", 1)[0].strip()


def parse_risk_event(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Parse one real RiskEvent entity into the envelope-plus-payload shape:

        {
          "name": "riskevent:2026-08-03:correlationregime:mp_schd",
          "date": "2026-08-03",
          "category": "CorrelationRegime",
          "severity": None,          # "warning" (or its raw legacy variant) when present
          "detail": {...},           # category-specific, see below
          "raw_observations": [...]  # always the full original list, unmodified
        }

    detail shape by category:
      - "FactorDominance" / "CorrelationBreakdown" (and the one legacy
        entity, which is also category FactorDominance):
            {"text": <commentary string, from "detail:" or legacy
                      "description:">, "extra": {key: value, ...}}
        extra collects any ad hoc fields beyond the known core set
        (e.g. live_verification, dust_vs_real_assessment, json).
      - "CorrelationRegime":
            {"pair": "MP/SCHD",
             "rho_recent": -0.708,        # leading float, commentary stripped
             "rho_recent_raw": "-0.708 (window: 20d, ...)",  # full text kept
             "rho_baseline": 0.088,
             "rho_baseline_raw": "0.088 (window: 90d ..., NOT the original...)",
             "delta": -0.7959,
             "classification": "breakdown",
             "source": "real daily OHLCV via yfinance, ..."}

    date is always parsed from `name` (colon-delimited date segment),
    never from the timestamp/date observation field, since name is the
    one place the date is guaranteed to be present and consistently
    formatted across all three shapes.
    """
    m = NAME_PATTERN.match(entity["name"])
    if not m:
        raise RiskEventParseError(
            f"'{entity['name']}' does not match riskevent:YYYY-MM-DD:label"
        )
    date = m.group(1)

    observations = entity.get("observations", [])
    category = _find_observation(observations, "category")
    severity = _find_observation(observations, "severity")  # None if absent -- never invented

    if category == "CorrelationRegime":
        rho_recent_raw = _find_observation(observations, "rho_recent")
        rho_baseline_raw = _find_observation(observations, "rho_baseline")
        detail = {
            "pair": _find_observation(observations, "pair"),
            "rho_recent": _leading_float(rho_recent_raw),
            "rho_recent_raw": rho_recent_raw,
            "rho_baseline": _leading_float(rho_baseline_raw),
            "rho_baseline_raw": rho_baseline_raw,
            "delta": _leading_float(_find_observation(observations, "delta")),
            "classification": _find_observation(observations, "classification"),
            "source": _find_observation(observations, "source"),
        }
    else:
        # FactorDominance / CorrelationBreakdown / legacy: commentary is
        # "detail:" in modern entities, "description:" in the one legacy
        # entity -- try both.
        text = _find_observation(observations, "detail")
        if text is None:
            text = _find_observation(observations, "description")

        extra: Dict[str, str] = {}
        for obs in observations:
            key = _extract_key(obs)
            if key is not None and key not in _KNOWN_KEYS:
                extra[key] = obs[len(key) + 1:].strip()

        detail = {"text": text, "extra": extra}

    return {
        "name": entity["name"],
        "date": date,
        "category": category,
        "severity": severity,
        "detail": detail,
        "raw_observations": list(observations),
    }


def filter_by_category(
    parsed_events: List[Dict[str, Any]], category: str
) -> List[Dict[str, Any]]:
    return [e for e in parsed_events if e["category"] == category]


def sorted_by_date(
    parsed_events: List[Dict[str, Any]], newest_first: bool = True
) -> List[Dict[str, Any]]:
    return sorted(parsed_events, key=lambda e: e["date"], reverse=newest_first)
