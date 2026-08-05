"""Parser for Regime and RiskRegime entities in the knowledge graph.

Real structure, verified directly against a live read_graph() dump (NOT
assumed to be structurally consistent -- it isn't):

Legacy Regime entity (undated name, one seen: regime:1_crypto-style):
    {
      "name": "regime:2026-07-22:crypto_highvol",
      "entityType": "Regime",
      "observations": [
        "name: Crypto-Dominant High-Vol Regime",
        "start_date: 2026-07-22",
        "vol_state: high (annualized portfolio vol ~31-51% depending on "
        "method, both elevated for a diversified portfolio)",
        "description: ...", "dominant_factor: factor:1_crypto",
        "cluster: crypto", "json: {...}",
        "@2026-07-22T18:23:00Z initialized: true",
        "canonicalId: regime:2026_07_22:crypto_highvol",
      ],
    }

Modern daily Regime entity (9 seen, 2026-07-26 through 2026-08-03):
    {
      "name": "regime:2026-07-26",
      "entityType": "Regime",
      "observations": [
        "date: 2026-07-26", "vol_level: NORMAL",
        "current_vol_annualized: 0.3714", "percentile_in_own_history: 62",
        "method: relative to this portfolio's own historical rolling-vol "
        "range (53 observations, 10-day window)",
        "canonicalId: regime:2026_07_26",
      ],
    }

Key real quirks, all confirmed -- do not assume otherwise:
  - Legacy entities use observation key "vol_state" with a free-text value
    (e.g. "high (annualized ... elevated for a diversified portfolio)").
    Modern entities use a DIFFERENT key, "vol_level", with a clean
    uppercase value (NORMAL, LOW -- HIGH not observed in the real data
    seen so far, but should not be assumed impossible).
  - There is no unified three-tier low/mid/high vocabulary across eras --
    legacy uses lowercase free-text ("high"), modern uses uppercase
    single-word values (NORMAL, LOW).
  - `name` uses colons (regime:2026-07-26); `canonicalId` uses underscores
    (regime:2026_07_26) -- same inconsistency as Factor entities. Always
    parse from `name`.
  - RiskRegime is NOT a parallel, uniform structure. Two real entities
    seen, structurally unrelated to each other:
      1. A daily composite snapshot (riskregime:2026-08-03) with
         primary_label, tags, crypto_factor_share, and an explicit note
         that its thresholds are "unvalidated first-pass... not
         back-tested."
      2. A static reference/definition entity (name: "regime:
         correlation_breakdown", but entityType "RiskRegime" -- the name
         prefix does NOT match the entity type here) documenting backtest
         evidence, with no date and no vol_level/vol_state field at all.
    This module treats RiskRegime parsing as best-effort/partial --
    unlike Regime, there isn't enough structural consistency yet to
    guarantee full extraction, and callers should not assume every
    RiskRegime entity yields a comparable result.
"""

import re
from typing import List, Dict, Any, Optional

# Matches modern daily entity names: regime:YYYY-MM-DD (no trailing label)
MODERN_NAME_PATTERN = re.compile(r"^regime:(\d{4}-\d{2}-\d{2})$")

# Matches legacy entity names: regime:YYYY-MM-DD:label
LEGACY_NAME_PATTERN = re.compile(r"^regime:(\d{4}-\d{2}-\d{2}):(.+)$")


class RegimeParseError(ValueError):
    """Raised when an entity's name matches neither the modern daily
    format nor the legacy dated-label format (e.g. a name with no date
    at all, like the RiskRegime reference entity 'regime:
    correlation_breakdown')."""


def _find_observation(observations: List[str], key: str) -> Optional[str]:
    prefix = f"{key}:"
    for obs in observations:
        if obs.startswith(prefix):
            return obs[len(prefix):].strip()
    return None


def _parse_name(name: str) -> Dict[str, Any]:
    m = MODERN_NAME_PATTERN.match(name)
    if m:
        return {"date": m.group(1), "era": "modern", "label": None}
    m = LEGACY_NAME_PATTERN.match(name)
    if m:
        return {"date": m.group(1), "era": "legacy", "label": m.group(2)}
    raise RegimeParseError(
        f"'{name}' matches neither modern (regime:YYYY-MM-DD) nor legacy "
        f"(regime:YYYY-MM-DD:label) format"
    )


def parse_regime_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Parse one real Regime entity into structured form:

        {
          "date": "2026-07-26",
          "era": "modern",                # or "legacy"
          "vol_label": "NORMAL",           # raw value, whatever vocabulary its era uses
          "vol_annualized": 0.3714,        # None if not present (legacy entities lack this)
          "percentile": 62,                # None if not present
        }

    Deliberately does NOT normalize vol_label into a fake unified
    vocabulary (e.g. mapping "high"/"NORMAL"/"LOW" onto invented
    low/mid/high tiers) -- the real eras don't share a vocabulary, and
    inventing one here would repeat the exact mistake already made and
    corrected earlier this session. Callers needing a unified scale must
    make that mapping decision explicitly and separately, with real
    justification, not have it silently baked into the parser.
    """
    name_info = _parse_name(entity["name"])
    observations = entity.get("observations", [])

    # vol_level (modern) or vol_state (legacy) -- try both, whichever exists.
    vol_label = _find_observation(observations, "vol_level")
    if vol_label is None:
        vol_label = _find_observation(observations, "vol_state")

    vol_annualized_raw = _find_observation(observations, "current_vol_annualized")
    vol_annualized = None
    if vol_annualized_raw is not None:
        try:
            vol_annualized = float(vol_annualized_raw)
        except ValueError:
            vol_annualized = None

    percentile_raw = _find_observation(observations, "percentile_in_own_history")
    percentile = None
    if percentile_raw is not None:
        try:
            percentile = int(percentile_raw)
        except ValueError:
            percentile = None

    return {
        "date": name_info["date"],
        "era": name_info["era"],
        "vol_label": vol_label,
        "vol_annualized": vol_annualized,
        "percentile": percentile,
    }


def current_regime(parsed_regimes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the most recent regime by date (modern entities only --
    legacy entities are excluded from 'current' lookup since there's
    only ever been one and it predates the daily series)."""
    modern = [r for r in parsed_regimes if r["era"] == "modern"]
    if not modern:
        return None
    return max(modern, key=lambda r: r["date"])


def regime_history(
    parsed_regimes: List[Dict[str, Any]], n: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Return modern regimes sorted oldest->newest, optionally limited to
    the last n."""
    modern = sorted(
        (r for r in parsed_regimes if r["era"] == "modern"),
        key=lambda r: r["date"],
    )
    return modern[-n:] if n is not None else modern


def parse_riskregime_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort parse of a RiskRegime entity. Unlike parse_regime_entity,
    this does NOT guarantee a consistent shape -- the two real RiskRegime
    entities observed are structurally unrelated (one a dated composite
    snapshot, one an undated reference/definition document). Returns
    whatever fields are actually present; callers must check for None
    rather than assume completeness.
    """
    observations = entity.get("observations", [])
    date = _find_observation(observations, "date")
    primary_label = _find_observation(observations, "primary_label")
    vol_level_raw = _find_observation(observations, "vol_level")

    return {
        "name": entity["name"],
        "date": date,  # None for undated reference entities
        "primary_label": primary_label,  # None if absent
        "vol_level_raw": vol_level_raw,  # kept raw -- may include trailing "(percentile N)" text
        "is_dated_snapshot": date is not None,
    }
