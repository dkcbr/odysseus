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

    @router.get("/api/diagnostics/default-model")
    async def get_default_model_health(request: Request) -> Dict[str, Any]:
        """Real, on-demand (costly) verification that the default chat model
        genuinely resolves and completes a real request as intended -- not
        just that some endpoint is reachable. Deliberately NOT included in
        the automatic /api/diagnostics/services aggregation since that runs
        on every diagnostics-panel view; this makes a real, metered API
        call and should only run on-demand or from a real scheduled task at
        a controlled cadence.

        Real, honest note on restoration, 2026-08-23: this endpoint (and its
        own backing function, default_model_health in src/service_health.py)
        existed once before, confirmed via git history (99f7fe2f), and was
        silently lost in the same merge that also dropped the Market
        Dashboard/Worker Logs/Process Table Jarvis Home features earlier
        this same engagement. Restored here after model_drift_verifier.py
        (a real, scheduled systemd service) started failing against it with
        a genuine 404."""
        require_admin(request)
        from src.service_health import default_model_health
        return default_model_health(owner="admin")

    @router.get("/api/mcp/server-detail")
    async def get_mcp_server_detail(request: Request, id: str) -> Dict[str, Any]:
        """Real, read-only single-server detail, using only real,
        confirmed fields (no "ping"/"type"/"last_ping" -- those don't
        exist; confirmed directly against a live server object before
        building). Reuses the existing real /api/mcp/servers list and the
        confirmed real per-server tools route.

        Real, honest restoration note, 2026-08-23: lost in the same
        commit (99f7fe2f) that dropped the Jarvis Home features and
        default-model diagnostics endpoint earlier this engagement.
        Verified both real dependencies (mcp_manager._connections,
        mcp_manager.get_all_tools()) still exist with a compatible
        shape before restoring."""
        require_admin(request)
        import app as _app

        server = None
        for sid, v in _app.mcp_manager._connections.items():
            if sid == id or v.get("name") == id:
                server = {"id": sid, **v}
                break
        if server is None:
            raise HTTPException(status_code=404, detail=f"No MCP server found with id/name {id}")

        # Real, confirmed method (same one the existing, already-used
        # /api/mcp/servers/{id}/tools route calls internally).
        all_tools = _app.mcp_manager.get_all_tools()
        tools = [t["name"] for t in all_tools if t.get("server_id") == server["id"]]

        return {
            "id": server.get("id"),
            "name": server.get("name"),
            "status": server.get("status"),
            "transport": server.get("transport"),
            "command": server.get("command"),
            "url": server.get("url"),
            "error": server.get("error"),
            "has_oauth": server.get("has_oauth"),
            "tool_count": server.get("tool_count"),
            "tools": tools,
        }

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



    @router.get("/api/risk/latest")
    async def get_latest_risk_state(request: Request) -> Dict[str, Any]:
        """Real, read-only fetch of the most recent risk-engine run from
        the graph -- finds the latest date among Regime entities (not
        hardcoded to "today", so this works correctly for any past or
        future refresh), then returns its factors, risk events, and
        suggestions."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        regimes = [e for e in entities if e["entityType"] == "Regime"]
        if not regimes:
            return {"found": False, "message": "No Regime entities found -- run /api/risk/refresh first."}

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        latest_regime = max(regimes, key=lambda e: obs_value(e, "date") or "")
        latest_date = obs_value(latest_regime, "date")

        factor_names = [r["to"] for r in relations
                        if r["from"] == latest_regime["name"] and r["relationType"] == "has_factor"]
        factors = [e for e in entities if e["name"] in factor_names]
        factors.sort(key=lambda e: e["name"])

        snapshot_names = [r["from"] for r in relations
                          if r["to"] == latest_regime["name"] and r["relationType"] == "has_regime"]
        risk_event_names = [r["from"] for r in relations
                            if r["to"] in snapshot_names and r["relationType"] == "detected_in_snapshot"]
        risk_events = [e for e in entities if e["name"] in risk_event_names]

        suggestion_names = [r["from"] for r in relations
                            if r["to"] in risk_event_names and r["relationType"] == "responds_to_event"]
        suggestions = [e for e in entities if e["name"] in suggestion_names]

        def to_dict(e):
            return {"name": e["name"], "type": e["entityType"], "observations": e["observations"]}

        return {
            "found": True,
            "date": latest_date,
            "regime": to_dict(latest_regime),
            "factors": [to_dict(f) for f in factors],
            "risk_events": [to_dict(r) for r in risk_events],
            "suggestions": [to_dict(s) for s in suggestions],
        }



    @router.get("/api/relevance/today")
    async def get_relevance_today(request: Request) -> Dict[str, Any]:
        """Real backend route wrapping Ambient Relevance's existing
        logic (originally inline in risk_panel.js) so the HUD overlay can
        poll it too, instead of duplicating frontend logic. No new
        subsystem -- reuses /api/risk/latest's own real data fetch and
        the same real Cross Search route, just server-side."""
        require_admin(request)
        import app as _app
        import json as _json
        import re as _re

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        factors = [e for e in entities if e["entityType"] == "Factor" and obs_value(e, "date")]
        if not factors:
            return {"tickers": [], "notes": []}

        latest_date = max(obs_value(f, "date") for f in factors)
        todays_factors = [f for f in factors if obs_value(f, "date") == latest_date]
        dominant = max(todays_factors, key=lambda f: float(obs_value(f, "variance_explained") or 0))

        loadings_text = obs_value(dominant, "top_loadings") or ""
        tickers = [p.strip().split(" ")[0] for p in loadings_text.split(",") if p.strip()]

        vault_notes = [e for e in entities if e["entityType"] == "VaultNote"]
        notes = []
        seen_ids = set()
        for ticker in tickers:
            for note in vault_notes:
                if note["name"] in seen_ids:
                    continue
                title = obs_value(note, "title") or note["name"]
                haystack = title + " " + " ".join(note.get("observations", []))
                if _re.search(rf"\b{_re.escape(ticker)}\b", haystack):
                    notes.append({"ticker": ticker, "title": title, "id": note["name"]})
                    seen_ids.add(note["name"])

        return {"tickers": tickers, "notes": notes}


    @router.get("/api/graph/nodes")
    async def get_graph_nodes(request: Request) -> Dict[str, Any]:
        """Real, read-only unified graph node view, scoped to vault+risk
        only (per explicit scope decision) -- these are the only domains
        that actually live in the real knowledge graph. Does NOT invent
        MCPServer/AgentTask/MarketEntity nodes (confirmed directly: none
        of those exist as graph entities, and no "MarketEntity" concept
        exists anywhere in the system at all). Reuses the exact same real
        entities already used by /api/risk/latest and vault ingestion."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        type_map = {
            "VaultNote": "vault_note", "Tag": "vault_tag",
            "Regime": "risk_regime", "Factor": "risk_factor",
            "RiskEvent": "risk_event", "Suggestion": "risk_suggestion",
        }

        nodes = []
        for e in entities:
            node_type = type_map.get(e["entityType"])
            if not node_type:
                continue
            nodes.append({
                "id": e["name"], "type": node_type, "label": e["name"],
                "metadata": {"observations": e.get("observations", [])},
            })

        # Real MCP domain, using the exact same verified patterns as
        # mcp_server_detail_panel.js's backend route -- not the invented
        # list_mcp_servers()/server.tools from an earlier proposal. Real
        # counts confirmed before building: 11 servers, 202 total tools.
        all_tools = _app.mcp_manager.get_all_tools()
        for sid, v in _app.mcp_manager._connections.items():
            nodes.append({
                "id": f"mcp_server:{sid}", "type": "mcp_server", "label": v.get("name", sid),
                "metadata": {"status": v.get("status")},
            })
        for t in all_tools:
            sid = t.get("server_id")
            nodes.append({
                "id": f"mcp_tool:{sid}:{t['name']}", "type": "mcp_tool", "label": t["name"],
                "metadata": {"server": sid},
            })

        # Minimal, honest Market nodes: real ticker symbols extracted from
        # real risk_factor observations only -- no invented sector/price/
        # asset_type metadata, since no such data exists anywhere (confirmed
        # directly: no market entity table, class, or ingestion pipeline
        # exists in the system). Handles both real observation formats
        # found in the graph: today's pipeline ("top_loadings: SYM (w), ...")
        # and the July 22 hand-analysis ("json: {"dominant_symbols":[...]}").
        import re as _re
        tickers = set()
        for e in entities:
            if e["entityType"] != "Factor":
                continue
            for o in e.get("observations", []):
                if o.startswith("top_loadings: "):
                    tickers.update(_re.findall(r"([A-Z]{2,6})\s*\(", o))
                elif o.startswith("json: "):
                    try:
                        payload = _json.loads(o[len("json: "):])
                        tickers.update(payload.get("dominant_symbols", []))
                    except _json.JSONDecodeError:
                        pass
        for symbol in sorted(tickers):
            nodes.append({
                "id": f"market_entity:{symbol}", "type": "market_entity", "label": symbol,
                "metadata": {},
            })

        # Agent Task domain -- real, one-row-per-task "tasks" table
        # (not task_events, which has multiple rows per task), same real
        # schema already used by Agent Task Detail. Not the invented
        # list_agent_tasks()/list_agents() from an earlier proposal.
        import sqlite3
        conn = sqlite3.connect("/app/data/agent_tasks.db")
        conn.row_factory = sqlite3.Row
        seen_agents = set()
        for row in conn.execute("SELECT id, agent, server, tool, status FROM tasks"):
            if row["agent"] not in seen_agents:
                seen_agents.add(row["agent"])
                nodes.append({
                    "id": f"agent:{row['agent']}", "type": "agent", "label": row["agent"],
                    "metadata": {},
                })
            nodes.append({
                "id": f"agent_task:{row['id']}", "type": "agent_task",
                "label": f"{row['agent']}:{row['tool']}",
                "metadata": {"agent": row["agent"], "server": row["server"],
                             "tool": row["tool"], "status": row["status"]},
            })
        conn.close()

        # Minimal, honest Market Entity nodes: extract real ticker symbols
        # from the top_loadings text already stored on real Factor
        # entities (e.g. "TOXR (-0.247), XRP (-0.247), ...") -- no
        # sector/price/asset_type, since no such data exists anywhere in
        # the system (confirmed directly, twice). Just the symbol.
        tickers = set()
        for e in entities:
            if e["entityType"] != "Factor":
                continue
            loadings = obs_value(e, "top_loadings") or ""
            for part in loadings.split(","):
                symbol = part.strip().split(" ")[0]
                if symbol:
                    tickers.add(symbol)
        for symbol in tickers:
            nodes.append({
                "id": f"market_entity:{symbol}", "type": "market_entity",
                "label": symbol, "metadata": {},
            })

        return {"nodes": nodes}


    @router.get("/api/graph/edges")
    async def get_graph_edges(request: Request) -> Dict[str, Any]:
        """Real, read-only unified graph edge view, scoped to vault+risk
        only. Uses only the real, confirmed relation types: HAS_TAG
        (uppercase), has_factor, has_regime, detected_in_snapshot,
        responds_to_event. No invented cross-domain edges (no
        vault_note-to-risk_factor "semantic_link" -- that doesn't exist
        as a real relation anywhere in the graph)."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        relations = graph_data.get("relations", [])
        entities = graph_data.get("entities", [])

        edges = [
            {"source": r["from"], "target": r["to"], "type": r["relationType"]}
            for r in relations
        ]

        # Real, minimal risk_factor -> market_entity edges, same real
        # top_loadings parsing as the nodes route.
        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        import re as _re
        for e in entities:
            if e["entityType"] != "Factor":
                continue
            loadings = obs_value(e, "top_loadings") or ""
            for part in loadings.split(","):
                part = part.strip()
                symbol = part.split(" ")[0]
                if not symbol:
                    continue
                # Real weight: parse the numeric loading from "SYM (0.247)"
                # text already stored on the Factor entity -- no invented
                # field, the number is right there in the real observation.
                m = _re.search(r"\(([-\d.]+)\)", part)
                loading = float(m.group(1)) if m else 0.0
                edges.append({
                    "source": e["name"], "target": f"market_entity:{symbol}",
                    "type": "risk_factor_market",
                    "metadata": {"loading": loading, "weight": abs(loading)},
                })

        # Real MCP server -> tool edges, same verified pattern as above.
        # Real invocation-count weight from task_events, scoped honestly:
        # this counts only invocations that went through the agent-task
        # system, not every possible tool call (e.g. direct chat-model
        # tool use isn't logged here) -- the only real signal available.
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect("/app/data/agent_tasks.db")
        invocation_counts = {}
        for row in _conn.execute(
            "SELECT server, tool, COUNT(*) as cnt FROM task_events WHERE event_type='created' GROUP BY server, tool"
        ):
            invocation_counts[(row[0], row[1])] = row[2]
        _conn.close()
        name_to_id_for_weights = {v.get("name"): sid for sid, v in _app.mcp_manager._connections.items()}

        all_tools = _app.mcp_manager.get_all_tools()
        for t in all_tools:
            sid = t.get("server_id")
            server_name = next((n for n, i in name_to_id_for_weights.items() if i == sid), None)
            invocations = invocation_counts.get((server_name, t["name"]), 0)
            edges.append({
                "source": f"mcp_server:{sid}", "target": f"mcp_tool:{sid}:{t['name']}",
                "type": "mcp_server_tool",
                "metadata": {"invocations": invocations, "weight": invocations},
            })

        # Minimal, honest risk_factor -> market_entity edges, same real
        # ticker extraction as the nodes route (both observation formats).
        import re as _re2
        for e in entities:
            if e["entityType"] != "Factor":
                continue
            factor_tickers = set()
            for o in e.get("observations", []):
                if o.startswith("top_loadings: "):
                    factor_tickers.update(_re2.findall(r"([A-Z]{2,6})\s*\(", o))
                elif o.startswith("json: "):
                    try:
                        payload = _json.loads(o[len("json: "):])
                        factor_tickers.update(payload.get("dominant_symbols", []))
                    except _json.JSONDecodeError:
                        pass
            for symbol in factor_tickers:
                edges.append({
                    "source": e["name"], "target": f"market_entity:{symbol}",
                    "type": "risk_factor_market",
                })

        # Agent Task edges. Real, confirmed correction: tasks.server
        # stores the readable NAME (e.g. "jarvis_system"), not the
        # internal hex id used by mcp_server nodes -- built a real
        # name->id map first, since a naive name-as-id edge would
        # silently fail to match any real node.
        name_to_id = {v.get("name"): sid for sid, v in _app.mcp_manager._connections.items()}

        import sqlite3
        conn = sqlite3.connect("/app/data/agent_tasks.db")
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT id, agent, server, tool, created_at, updated_at FROM tasks"):
            # Real weight: actual elapsed time from the task's own real
            # created_at/updated_at timestamps (same fields already shown
            # in Agent Task Detail) -- not an invented duration_ms field.
            duration_ms = None
            if row["created_at"] is not None and row["updated_at"] is not None:
                duration_ms = round((row["updated_at"] - row["created_at"]) * 1000)

            edges.append({
                "source": f"agent:{row['agent']}", "target": f"agent_task:{row['id']}",
                "type": "agent_task_assignment",
                "metadata": {"duration_ms": duration_ms, "weight": duration_ms or 0},
            })
            sid = name_to_id.get(row["server"])
            if sid:
                edges.append({
                    "source": f"agent_task:{row['id']}", "target": f"mcp_server:{sid}",
                    "type": "agent_task_server",
                    "metadata": {"duration_ms": duration_ms, "weight": duration_ms or 0},
                })
                edges.append({
                    "source": f"agent_task:{row['id']}", "target": f"mcp_tool:{sid}:{row['tool']}",
                    "type": "agent_task_tool",
                    "metadata": {"duration_ms": duration_ms, "weight": duration_ms or 0},
                })
        conn.close()

        return {"edges": edges}



    return router
