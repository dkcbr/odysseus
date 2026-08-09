"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research."""

import logging
import os
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Form, Request

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from core.constants import DEFAULT_HOST, DATA_DIR
from core.middleware import require_admin

logger = logging.getLogger(__name__)


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
    memory_vector=None,
) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/diagnostics/services")
    async def get_service_health(request: Request) -> Dict[str, Any]:
        """Consolidated degraded-state report for ChromaDB, SearXNG, email,
        ntfy, and provider endpoints. Non-intrusive probes — safe to poll."""
        require_admin(request)
        from src.service_health import collect_service_health
        return await collect_service_health(rag_manager, memory_vector)

    @router.get("/api/diagnostics/logs")
    async def get_diagnostics_logs(request: Request, limit: int = 200) -> Dict[str, Any]:
        require_admin(request)
        limit = max(1, min(limit, 1000))
        try:
            log_file = os.path.join(DATA_DIR, "logs", "app.log")
            if not os.path.exists(log_file):
                return {"status": "success", "logs": []}

            # Safe tail read of the log file (max 5MB via rotation)
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            tail_lines = lines[-limit:] if len(lines) > limit else lines
            tail_lines = [line.rstrip('\r\n') for line in tail_lines]

            return {
                "status": "success",
                "logs": tail_lines
            }
        except Exception as e:
            logger.error(f"Diagnostics logs retrieval error: {e}")
            raise HTTPException(500, f"Failed to retrieve logs: {str(e)}")

    @router.get("/api/db/stats")
    async def get_database_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        try:
            from core.database import get_detailed_stats
            return get_detailed_stats()
        except Exception as e:
            logger.error(f"DB stats error: {e}")
            raise HTTPException(500, "Failed to retrieve database statistics")

    @router.get("/api/rag/stats")
    async def get_rag_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        if rag_available and rag_manager:
            return rag_manager.get_stats()
        return {"error": "RAG system not available"}

    @router.get("/api/test/youtube")
    async def test_youtube(request: Request, url: str) -> Dict[str, Any]:
        require_admin(request)
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return {"error": "Invalid YouTube URL"}

            data = await extract_transcript_async(url, video_id)
            return {
                "video_id": video_id,
                "transcript_success": data.get("success", False),
                "transcript_length": len(data.get("transcript", "")) if data.get("success") else 0,
                "transcript_preview": (data.get("transcript", "")[:500] + "...")
                    if data.get("success") and len(data.get("transcript", "")) > 500
                    else data.get("transcript", ""),
                "error": data.get("error") if not data.get("success") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/test-research")
    async def test_research(request: Request, query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        require_admin(request)
        try:
            endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            return {
                "status": "success",
                "query": query,
                "result_preview": result[:200] + "..." if len(result) > 200 else result,
                "result_length": len(result),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "query": query}

    @router.post("/api/risk/refresh")
    async def refresh_risk_engine(request: Request) -> Dict[str, Any]:
        """Real, callable risk engine pipeline -- ports the exact,
        already-verified standalone scripts (/home/dk/jarvis/risk_engine/)
        into an in-process route. Fetches real price history for the real
        portfolio universe, computes real PCA, classifies regime relative
        to the portfolio's own historical vol range, detects real risk
        events (vol spike, factor dominance, correlation breakdown),
        generates descriptive suggestions, writes to the real graph with
        canonicalId via delete-then-recreate. Manual-first: no scheduling,
        called explicitly."""
        require_admin(request)
        import re
        import numpy as np
        import app as _app

        KG_SERVER_ID = "1751838b"
        ACCOUNT_ID = "5OS47729"
        ROLLING_WINDOW = 10
        RECENT_WINDOW = 20
        from datetime import datetime, timezone
        TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        def normalize(t):
            t = t.strip().lower()
            t = re.sub(r"[\s\-]+", "_", t)
            t = re.sub(r"_+", "_", t)
            return t.strip("_")

        async def public_call(tool, arguments):
            result = await _app.mcp_manager.call_tool(f"mcp__74167655__{tool}", arguments)
            if result.get("stderr"):
                raise RuntimeError(f"{tool} error: {result['stderr'][:300]}")
            return _json.loads(result["stdout"])

        async def kg_call(tool, arguments):
            result = await _app.mcp_manager.call_tool(f"mcp__{KG_SERVER_ID}__{tool}", arguments)
            if result.get("stderr"):
                raise RuntimeError(f"{tool} error: {result['stderr'][:300]}")
            stdout = result.get("stdout", "")
            try:
                return _json.loads(stdout) if stdout else None
            except _json.JSONDecodeError:
                return stdout

        import json as _json

        # Step 1: real portfolio universe + price data
        portfolio = await public_call("get_portfolio", {"account_id": ACCOUNT_ID})
        positions = portfolio.get("positions", [])
        universe = {p["instrument"]["symbol"]: p["instrument"]["type"] for p in positions}
        weight_by_symbol = {p["instrument"]["symbol"]: float(p["percentOfPortfolio"]) / 100.0
                            for p in positions}

        price_series = {}
        for symbol, itype in universe.items():
            try:
                data = await public_call("get_price_history",
                    {"symbol": symbol, "period": "YEAR", "account_id": ACCOUNT_ID, "instrument_type": itype})
                bars = data.get("regularMarket", {}).get("bars", [])
                price_series[symbol] = {b["timestamp"][:10]: float(b["close"]) for b in bars}
            except Exception:
                continue

        common_dates = None
        for series in price_series.values():
            dates = set(series.keys())
            common_dates = dates if common_dates is None else (common_dates & dates)
        common_dates = sorted(common_dates)
        symbols = sorted(price_series.keys())
        price_matrix = np.array([[price_series[s][d] for d in common_dates] for s in symbols])
        returns = np.diff(np.log(price_matrix), axis=1)

        weights = np.array([weight_by_symbol.get(s, 0.0) for s in symbols])
        weights = weights / weights.sum()

        # Step 2: real PCA
        corr = np.corrcoef(returns)
        eigvals, eigvecs = np.linalg.eigh(corr)
        order = np.argsort(eigvals)[::-1]
        eigvals, eigvecs = eigvals[order], eigvecs[:, order]
        total_var = eigvals.sum()
        cum_var = np.cumsum(eigvals) / total_var
        n_factors = int(np.searchsorted(cum_var, 0.80) + 1)

        factors_out = []
        for i in range(n_factors):
            loadings = eigvecs[:, i]
            idx_sorted = np.argsort(-np.abs(loadings))
            top = [(symbols[j], round(float(loadings[j]), 3)) for j in idx_sorted[:8]]
            factors_out.append({"index": i + 1, "variance_explained": round(float(eigvals[i] / total_var), 4), "top_loadings": top})

        # Step 3: real regime classification, relative to own history
        portfolio_returns = weights @ returns
        rolling_vols = []
        for i in range(len(portfolio_returns) - ROLLING_WINDOW + 1):
            window = portfolio_returns[i:i + ROLLING_WINDOW]
            rolling_vols.append(np.std(window, ddof=1) * np.sqrt(252))
        rolling_vols = np.array(rolling_vols)
        current_vol = float(rolling_vols[-1])
        percentile = float((rolling_vols < current_vol).mean() * 100)
        p33, p67 = np.percentile(rolling_vols, [33, 67])
        level = "LOW" if current_vol <= p33 else ("NORMAL" if current_vol <= p67 else "HIGH")

        # Step 4: real risk events
        risk_events = []
        hist_max = float(rolling_vols.max())
        if current_vol >= hist_max * 0.95:
            risk_events.append({"category": "VolSpike", "severity": "warning",
                "detail": f"Current annualized vol {current_vol*100:.1f}% is within 5% of this portfolio's own historical max ({hist_max*100:.1f}%)."})
        fair_share = 1.0 / n_factors
        for f in factors_out:
            if f["variance_explained"] > fair_share * 2:
                top_syms = ", ".join(s for s, _ in f["top_loadings"][:5])
                risk_events.append({"category": "FactorDominance", "severity": "warning",
                    "detail": f"Factor {f['index']} explains {f['variance_explained']*100:.1f}% of variance, over 2x the {fair_share*100:.1f}% fair share for {n_factors} factors. Top loadings: {top_syms}."})
        if returns.shape[1] >= RECENT_WINDOW + 10:
            baseline_corr = np.corrcoef(returns)
            recent_corr = np.corrcoef(returns[:, -RECENT_WINDOW:])
            breakdowns = []
            for i in range(len(symbols)):
                for j in range(i + 1, len(symbols)):
                    base, recent = baseline_corr[i, j], recent_corr[i, j]
                    if abs(base) < 0.3 and abs(recent) > 0.7:
                        breakdowns.append((symbols[i], symbols[j], round(float(base), 2), round(float(recent), 2)))
            if breakdowns:
                detail_lines = "; ".join(f"{a}/{b}: {bc}->{rc}" for a, b, bc, rc in breakdowns[:5])
                risk_events.append({"category": "CorrelationBreakdown", "severity": "warning",
                    "detail": f"{len(breakdowns)} pair(s) moved from low baseline correlation to high recent correlation (last {RECENT_WINDOW} days): {detail_lines}."})

        # Step 5: real, descriptive suggestions
        suggestions = [{"source": "RiskEngine (automated pipeline)",
            "summary": f"{e['detail']} Surfaced as a diagnostic finding for review; no specific action recommended."}
            for e in risk_events]

        # Step 6: real graph write, canonicalId, delete-then-recreate
        entities, relations = [], []
        factor_names = []
        for f in factors_out:
            name = f"factor:{TODAY}:{f['index']}"
            top = ", ".join(f"{s} ({l})" for s, l in f["top_loadings"])
            entities.append({"name": name, "entityType": "Factor", "observations": [
                f"date: {TODAY}", f"variance_explained: {f['variance_explained']:.4f}",
                f"top_loadings: {top}", f"canonicalId: {normalize(name)}"]})
            factor_names.append(name)

        regime_name = f"regime:{TODAY}"
        entities.append({"name": regime_name, "entityType": "Regime", "observations": [
            f"date: {TODAY}", f"vol_level: {level}", f"current_vol_annualized: {current_vol:.4f}",
            f"percentile_in_own_history: {percentile:.0f}",
            f"method: relative to this portfolio's own historical rolling-vol range ({len(rolling_vols)} observations, {ROLLING_WINDOW}-day window)",
            f"canonicalId: {normalize(regime_name)}"]})
        for fn in factor_names:
            relations.append({"from": regime_name, "to": fn, "relationType": "has_factor"})

        snapshot_name = f"snapshot:{TODAY}"
        entities.append({"name": snapshot_name, "entityType": "PortfolioSnapshot", "observations": [
            f"date: {TODAY}", f"n_factors: {n_factors}", f"cumulative_variance: {cum_var[n_factors-1]:.4f}",
            f"canonicalId: {normalize(snapshot_name)}"]})
        relations.append({"from": snapshot_name, "to": regime_name, "relationType": "has_regime"})

        event_names = []
        for e in risk_events:
            name = f"riskevent:{TODAY}:{e['category'].lower()}"
            entities.append({"name": name, "entityType": "RiskEvent", "observations": [
                f"date: {TODAY}", f"category: {e['category']}", f"severity: {e['severity']}",
                f"detail: {e['detail']}", f"canonicalId: {normalize(name)}"]})
            relations.append({"from": name, "to": snapshot_name, "relationType": "detected_in_snapshot"})
            event_names.append(name)

        for i, (s, ev_name) in enumerate(zip(suggestions, event_names)):
            name = f"suggestion:{TODAY}:{i+1}"
            entities.append({"name": name, "entityType": "Suggestion", "observations": [
                f"date: {TODAY}", f"source: {s['source']}", f"summary: {s['summary']}",
                f"canonicalId: {normalize(name)}"]})
            relations.append({"from": name, "to": ev_name, "relationType": "responds_to_event"})

        pca_name = f"pca:{TODAY}"
        entities.append({"name": pca_name, "entityType": "PCA_Metadata", "observations": [
            f"date: {TODAY}", f"method: eigendecomposition of correlation matrix, {len(rolling_vols)}-observation window",
            f"factors_selected: {n_factors} factors for {cum_var[n_factors-1]*100:.1f}% variance (80% target)",
            f"canonicalId: {normalize(pca_name)}"]})

        all_names = [e["name"] for e in entities]
        await kg_call("delete_entities", {"entityNames": all_names})
        await kg_call("create_entities", {"entities": entities})
        await kg_call("create_relations", {"relations": relations})

        # Real, purely additive event-writing for the Unified Timeline --
        # wrapped so a failure here can NEVER break the already-working
        # refresh logic above (everything above this point is unchanged).
        # New, separate timeline_events table (real, existing task_events
        # table is reused as-is for the agent-tasks half, no changes there).
        try:
            import sqlite3
            import time as _time
            conn = sqlite3.connect("/app/data/agent_tasks.db")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS timeline_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    entity_id TEXT,
                    entity_type TEXT
                )
            """)
            now = _time.time()
            conn.execute(
                "INSERT INTO timeline_events (ts, source, event_type, summary, entity_id, entity_type) VALUES (?, ?, ?, ?, ?, ?)",
                (now, "risk", "risk.regime_refreshed",
                 f"Regime {level}, {len(risk_events)} risk event(s), {len(suggestions)} suggestion(s)",
                 regime_name, "regime"),
            )
            for e in risk_events:
                conn.execute(
                    "INSERT INTO timeline_events (ts, source, event_type, summary, entity_id, entity_type) VALUES (?, ?, ?, ?, ?, ?)",
                    (now, "risk", "risk.event_detected", e["detail"][:200],
                     f"riskevent:{TODAY}:{e['category'].lower()}", "riskevent"),
                )
            conn.commit()
            conn.close()
        except Exception as _e:
            logger.warning(f"risk refresh: timeline event write failed (non-fatal): {_e}")

        return {
            "date": TODAY, "symbols_used": len(symbols), "symbols_failed": len(universe) - len(symbols),
            "n_factors": n_factors, "regime_level": level, "current_vol_annualized": current_vol,
            "risk_events": risk_events, "suggestions": suggestions,
            "entities_written": len(entities), "relations_written": len(relations),
        }



    return router
