"""Factor routes -- exposes real PCA factor scores/loadings computed
by the standalone OHLCV/PCA pipeline (~/jarvis-scripts/ohlcv/) as
JSON for the HUD. Reads real, pre-computed factor files; does not
run PCA itself (that stays a separate, on-demand analysis step).
"""
import json
import logging
from typing import Dict, Any
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

PCA_FILES = {
    "crypto": "/home/dk/jarvis-scripts/ohlcv/pca_factors_180d.json",
    # Real, portfolio-specific PCA (DK's actual 14 real holdings), not the
    # earlier generic SPY/AAPL/MSFT basket -- this is the one with the
    # real software/commodities factors relevant to the actual portfolio.
    "stock": "/home/dk/jarvis-scripts/ohlcv/portfolio_pca_factors.json",
}


def _load_pca(domain: str) -> Dict[str, Any]:
    path = Path(PCA_FILES.get(domain, ""))
    if not path.exists():
        raise HTTPException(404, f"No PCA factor file found for domain '{domain}' -- run the PCA build script first.")
    with open(path) as f:
        return json.load(f)


def setup_factor_routes() -> APIRouter:
    router = APIRouter(tags=["factors"])

    @router.get("/api/factors/latest")
    async def get_latest_factors(domain: str = "stock") -> Dict[str, Any]:
        """Latest real factor scores (PC1/PC2/PC3...) for the given domain
        ("stock" or "crypto"), plus the real assets/loadings/explained
        variance from the last PCA run."""
        data = _load_pca(domain)
        scores = data.get("scores", [])
        latest = scores[-1] if scores else None
        return {
            "domain": domain,
            "assets": data.get("assets"),
            "explained_variance_ratio": data.get("explained_variance_ratio"),
            "loadings": data.get("loadings"),
            "window_start": data.get("window_start"),
            "window_end": data.get("window_end"),
            "latest": latest,
        }

    @router.get("/api/factors/trend")
    async def get_factor_trend(domain: str = "stock", lookback: int = 30) -> Dict[str, Any]:
        """Real delta between the latest factor score and the score
        `lookback` real data points earlier in the same series -- not a
        fixed 30 calendar days, since stock data is daily and crypto data
        is hourly; `lookback` is in units of the underlying series."""
        data = _load_pca(domain)
        scores = data.get("scores", [])
        if len(scores) < lookback + 1:
            raise HTTPException(400, f"Not enough real data points ({len(scores)}) for a {lookback}-point lookback.")
        latest = scores[-1]
        earlier = scores[-1 - lookback]
        deltas = {}
        history = {}
        pc_keys = [k for k in latest if k != "timestamp"]
        recent_window = scores[-lookback:]
        for key in pc_keys:
            deltas[f"{key}_delta"] = latest[key] - earlier[key]
            history[f"{key}_history"] = [s[key] for s in recent_window]
        return {
            "domain": domain,
            "lookback": lookback,
            "latest_timestamp": latest.get("timestamp"),
            "earlier_timestamp": earlier.get("timestamp"),
            "deltas": deltas,
            "history": history,
        }

    @router.get("/api/regime/correlation-breakdown")
    async def get_correlation_breakdown_regime() -> Dict[str, Any]:
        """Real, current correlation-breakdown regime status -- computes
        the real correlation values fresh each call using the same logic
        as detect_correlation_regime.py, WITHOUT writing to the graph
        (this is a read-only HUD endpoint; the detector script itself is
        the separate, explicit graph-writing step, run on-demand)."""
        import numpy as np
        pair_defs = [("ENPH", "SCHD"), ("ICLN", "SCHD"), ("MP", "SCHD")]
        recent_w, baseline_w, mag_threshold = 20, 90, 0.6

        def load_closes(sym):
            p = Path(f"/home/dk/jarvis-scripts/ohlcv/{sym}_1d.jsonl")
            out = {}
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        c = json.loads(line)
                        out[c["timestamp"]] = c["close"]
            return out

        pairs = []
        for a, b in pair_defs:
            ca, cb = load_closes(a), load_closes(b)
            common = sorted(set(ca.keys()) & set(cb.keys()))
            ra = np.diff(np.log([ca[t] for t in common]))
            rb = np.diff(np.log([cb[t] for t in common]))
            rho_recent = float(np.corrcoef(ra[-recent_w:], rb[-recent_w:])[0, 1])
            rho_baseline = float(np.corrcoef(ra[-recent_w - baseline_w:-recent_w], rb[-recent_w - baseline_w:-recent_w])[0, 1])
            flipped = (rho_recent * rho_baseline) < 0
            classification = "breakdown" if (flipped and abs(rho_recent - rho_baseline) > mag_threshold) else "stable"
            pairs.append({"pair": f"{a}/{b}", "rho_recent": round(rho_recent, 4), "rho_baseline": round(rho_baseline, 4), "classification": classification})

        active_breakdowns = [p for p in pairs if p["classification"] == "breakdown"]
        return {
            "primary_regime": "correlation_breakdown" if active_breakdowns else "normal",
            "pairs": pairs,
            "active_breakdown_count": len(active_breakdowns),
            "note": "Deepest-drawdown regime per real back-test evidence (avg DD -21% to -36%, worst -47.18%) -- NOT necessarily negative average returns; see graph node regime:correlation_breakdown for full context.",
        }

    @router.get("/api/suggestions/active")
    async def get_active_suggestions() -> Dict[str, Any]:
        """Real, current active suggestions from the knowledge graph
        (written by suggestions_engine.py), read fresh each call.
        Uses the real, correct in-process MCP call pattern (import app
        as _app; _app.mcp_manager.call_tool(...)) -- NOT a self-referential
        HTTP call back to this same server, which caused a real deadlock/
        timeout (confirmed via server logs: ReadTimeout on localhost:7000
        from within this server's own process)."""
        import app as _app
        KG_SERVER_ID = "1751838b"  # real, confirmed knowledge-graph-memory server id

        result = await _app.mcp_manager.call_tool(
            f"mcp__{KG_SERVER_ID}__search_nodes", {"query": "Suggestion"})
        # Real call_tool() shape confirmed against diagnostics_routes.py's
        # own usage: a dict with a "stdout" key holding the JSON string --
        # NOT a raw string or pre-parsed dict as an earlier version assumed.
        data = json.loads(result.get("stdout", "{}") or "{}")

        def obs_val(entity, key):
            for line in entity.get("observations", []):
                if line.startswith(key + ": "):
                    return line[len(key) + 2:]
            return None

        suggestions = []
        for e in data.get("entities", []):
            if e.get("entityType") != "Suggestion":
                continue
            suggestions.append({
                "name": e["name"],
                "type": obs_val(e, "type"),
                "confidence": obs_val(e, "confidence"),
                "status": obs_val(e, "status"),
                "text": obs_val(e, "text"),
                "reason": obs_val(e, "reason"),
            })
        return {"suggestions": suggestions, "count": len(suggestions)}

    return router
