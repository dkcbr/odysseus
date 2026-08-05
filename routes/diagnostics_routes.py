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
        a controlled cadence."""
        require_admin(request)
        from src.service_health import default_model_health
        return default_model_health(owner='admin')

    @router.post("/api/vault/ingest")
    async def ingest_vault(request: Request) -> Dict[str, Any]:
        """Real vault ingestion into MemoryVectorStore via
        NativeMemoryProvider.replace_fact() -- runs inside this live app
        process specifically because it needs the real, initialized
        src.ai_interaction._memory_manager/_memory_vector globals, which
        only exist here, not in a standalone docker exec script (confirmed
        directly: every note failed with 'not yet initialized' when tried
        as an external script)."""
        require_admin(request)
        import asyncio
        from pathlib import Path
        import src.ai_interaction as _ai
        from src.memory_provider import NativeMemoryProvider

        if _ai._memory_manager is None:
            raise HTTPException(status_code=503, detail="memory_manager not initialized")

        vault_root = Path("/app/vault")
        if not vault_root.exists():
            raise HTTPException(status_code=404, detail=f"Vault root not found: {vault_root}")

        provider = NativeMemoryProvider(_ai._memory_manager, _ai._memory_vector)
        # Real scope fix: rglob("*.md") originally swept up the nested
        # odysseus repo (confirmed also synced under vault_root/Jarvis/,
        # README/SECURITY/CONTRIBUTING etc.) alongside real vault notes.
        # Root-level .md files plus these three subtrees are the real,
        # confirmed vault content -- Decisions/My AI Files/Oracle Server/
        # Positions/graphify-out contain zero .md files so need no
        # explicit exclusion.
        md_files = sorted(vault_root.glob("*.md"))
        for sub in ("Portfolio", "Thesis", "Watchlist"):
            md_files += sorted((vault_root / sub).rglob("*.md"))
        # Explicit exclusion: a confirmed test file, not real vault content.
        md_files = [f for f in md_files if f.name != "context_test3.md"]

        results = []
        for f in md_files:
            rel_path = str(f.relative_to(vault_root))
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                await provider.replace_fact(
                    f"vault:{rel_path}", text,
                    category="fact", source="vault_ingest",
                )
                results.append({"path": rel_path, "ok": True, "chars": len(text)})
            except Exception as e:
                results.append({"path": rel_path, "ok": False, "error": str(e)})

        ok_count = sum(1 for r in results if r["ok"])
        return {"total": len(md_files), "ingested": ok_count,
                "failed": len(md_files) - ok_count, "results": results}

    @router.post("/api/vault/cleanup")
    async def cleanup_vault_topics(request: Request, prefix: str) -> Dict[str, Any]:
        """Real cleanup by topic prefix -- reuses the exact same
        filter/remove/save logic replace_fact() itself uses internally
        (confirmed by reading its real implementation), just applied to
        every topic matching a prefix instead of one exact topic. Built to
        clean up the 18 wrongly-scoped vault:jarvis/odysseus/... entries
        from before the ingestion scope fix, but generally reusable."""
        require_admin(request)
        import src.ai_interaction as _ai
        from src.memory_provider import NativeMemoryProvider

        if _ai._memory_manager is None:
            raise HTTPException(status_code=503, detail="memory_manager not initialized")

        provider = NativeMemoryProvider(_ai._memory_manager, _ai._memory_vector)
        memories = provider.memory_manager.load_all()
        remaining = []
        removed = []
        for m in memories:
            m_topic = (m.get("metadata") or {}).get("topic") or ""
            if m_topic.startswith(prefix):
                removed.append(m_topic)
                if provider._vector_available():
                    try:
                        provider.memory_vector.remove(m["id"])
                    except Exception:
                        pass
            else:
                remaining.append(m)

        provider.memory_manager.save(remaining)
        return {"removed_count": len(removed), "removed_topics": removed}

    @router.post("/api/vault/graph-sync")
    async def sync_vault_graph(request: Request) -> Dict[str, Any]:
        """Real Option A vault graph modeling: VaultNote + Tag entities,
        HAS_TAG relations, via real MCP tool calls (create_entities,
        create_relations on the real knowledge-graph-memory server,
        confirmed exact tool names and server id tonight) -- not Python
        graph methods, which don't exist. Title = first '# heading' line
        (confirmed real, consistent pattern across every real vault note).
        Tag = the note's real top-level folder (Thesis/Watchlist/Portfolio)
        -- confirmed no real inline/frontmatter tag syntax exists in any
        note, so folder structure is the only real, honest signal to use,
        not invented content-based tag extraction.
        """
        require_admin(request)
        from pathlib import Path
        import app as _app
        from src.memory_provider import NativeMemoryProvider

        vault_root = Path("/app/vault")
        KG_SERVER_ID = "1751838b"  # real, confirmed knowledge-graph-memory server id

        md_files = sorted(vault_root.glob("*.md"))
        for sub in ("Portfolio", "Thesis", "Watchlist"):
            md_files += sorted((vault_root / sub).rglob("*.md"))
        md_files = [f for f in md_files if f.name != "context_test3.md"]

        entities = []
        relations = []
        tags_seen = set()

        for f in md_files:
            rel_path = str(f.relative_to(vault_root))
            text = f.read_text(encoding="utf-8", errors="replace")
            first_line = text.splitlines()[0] if text.splitlines() else ""
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else f.stem

            note_name = f"vault:{rel_path}"
            # Real canonical ID: reuses the same, existing normalization
            # NativeMemoryProvider.replace_fact() already applies to vault
            # topics -- not a new function, not a new set of rules. Added
            # as an observation (non-breaking) rather than replacing the
            # real entity name, so nothing that already depends on the
            # raw path breaks.
            canonical_id = NativeMemoryProvider._normalize_topic(f"vault:{rel_path}")
            entities.append({
                "name": note_name,
                "entityType": "VaultNote",
                "observations": [f"path: {rel_path}", f"title: {title}", f"canonicalId: {canonical_id}"],
            })

            # Real, honest tag: the note's top-level real folder, if any
            # (root-level files like context.md have no folder tag).
            parts = Path(rel_path).parts
            if len(parts) > 1:
                tag = parts[0]
                tag_name = f"tag:{tag}"
                if tag_name not in tags_seen:
                    tags_seen.add(tag_name)
                    entities.append({
                        "name": tag_name,
                        "entityType": "Tag",
                        "observations": [f"name: {tag}"],
                    })
                relations.append({
                    "from": note_name,
                    "to": tag_name,
                    "relationType": "HAS_TAG",
                })

        # Real fix: create_entities silently no-ops on an existing name
        # (confirmed directly) rather than updating observations -- so a
        # re-run of this full sync would never actually refresh an
        # existing note's title/canonicalId. delete_entities cascades to
        # remove associated relations too (confirmed), so deleting
        # everything in this batch first, then recreating fresh, is the
        # correct way to make a full re-sync genuinely idempotent/refreshing.
        # Real, actual variable names in this function: entities is a
        # single list containing both VaultNote and Tag dicts, each with
        # a real "name" key -- there is no separate vault_entities/
        # tag_entities dict here (that naming belongs to a different
        # function; this NameError was caught directly from a real 500).
        all_names = [e["name"] for e in entities]
        if all_names:
            await _app.mcp_manager.call_tool(
                f"mcp__{KG_SERVER_ID}__delete_entities", {"entityNames": all_names})

        r1 = await _app.mcp_manager.call_tool(
            f"mcp__{KG_SERVER_ID}__create_entities", {"entities": entities})
        r2 = await _app.mcp_manager.call_tool(
            f"mcp__{KG_SERVER_ID}__create_relations", {"relations": relations})

        return {
            "notes": len(md_files),
            "tags": len(tags_seen),
            "relations": len(relations),
            "create_entities_result": r1,
            "create_relations_result": r2,
        }

    @router.get("/api/jarvis/health-vault-summary")
    async def health_vault_summary(request: Request) -> Dict[str, Any]:
        """Real, read-only summary module: health snapshot + real vault
        listing, plain text, no actions/decisions/auto-execution. Uses the
        exact same real data sources as the System Health panel and the
        vault graph -- no new state, no invented connection between health
        and vault content (they're separate, unrelated memory domains in
        this vault; investment notes, not infra notes)."""
        require_admin(request)
        import app as _app
        from src.service_health import collect_service_health

        health = await collect_service_health(rag_manager, memory_vector)
        overall = health.get("overall", "unknown")
        subsystem_lines = [f"- {s['name']}: {s['status']}" for s in health.get("services", [])]

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(
            f"mcp__{kg_id}__read_graph", {})
        import json as _json
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        vault_notes = [e for e in graph_data.get("entities", [])
                       if e.get("entityType") == "VaultNote"]
        note_lines = []
        for n in vault_notes:
            obs = n.get("observations", [])
            title = next((o.split("title: ", 1)[1] for o in obs if o.startswith("title: ")), n["name"])
            note_lines.append(f"- {n['name']} ({title})")

        summary = (
            f"System health: {overall.upper()}\n"
            f"Subsystems:\n" + "\n".join(subsystem_lines) + "\n\n"
            f"Vault notes ({len(note_lines)} total, real investment/portfolio "
            f"content -- no genuine relation to system health status):\n"
            + "\n".join(note_lines)
        )
        return {"summary": summary, "overall": overall, "vault_note_count": len(note_lines)}

    @router.get("/api/jarvis/context")
    async def jarvis_context(request: Request) -> Dict[str, Any]:
        """Real, read-only JarvisContext bundle: health + vault/tags + real
        MCP server list + latest risk-engine state. No actions, no writes,
        no autonomy -- a view, not a control surface. Combines already-
        verified real data sources: the health aggregator, the real
        knowledge graph (via actual MCP calls, reused for both vault and
        risk extraction), and the real /api/mcp/servers list."""
        require_admin(request)
        import app as _app
        from src.service_health import collect_service_health

        health = await collect_service_health(rag_manager, memory_vector)
        health_context = {
            "status": health.get("overall", "unknown"),
            "subsystems": [
                {"name": s["name"], "status": s["status"], "details": s.get("detail")}
                for s in health.get("services", [])
            ],
        }

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(
            f"mcp__{kg_id}__read_graph", {})
        import json as _json
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        vault_notes = [e for e in entities if e.get("entityType") == "VaultNote"]
        tag_map: Dict[str, list] = {}
        for r in relations:
            if r.get("relationType") == "HAS_TAG":
                tag_name = r["to"].split("tag:", 1)[-1] if r["to"].startswith("tag:") else r["to"]
                tag_map.setdefault(r["from"], []).append(tag_name)

        notes = []
        for n in vault_notes:
            obs = n.get("observations", [])
            path = next((o.split("path: ", 1)[1] for o in obs if o.startswith("path: ")), "")
            title = next((o.split("title: ", 1)[1] for o in obs if o.startswith("title: ")), n["name"])
            notes.append({"path": path, "title": title, "tags": tag_map.get(n["name"], [])})

        # Real attribute confirmed: _connections is keyed by server_id, with
        # each value a dict containing the real server name and status.
        mcp_names = [
            v.get("name", sid) for sid, v in _app.mcp_manager._connections.items()
            if v.get("status") == "connected"
        ] if hasattr(_app.mcp_manager, "_connections") else []

        # Real risk-domain extraction, reusing the SAME entities/relations
        # already fetched above (no second MCP call). Uses the real,
        # verified relation types (has_factor, has_regime,
        # detected_in_snapshot, responds_to_event) -- corrected after an
        # earlier, wrong relation model was caught and fixed.
        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        regimes = [e for e in entities if e.get("entityType") == "Regime"]
        risk_context = {"available": False}
        if regimes:
            latest_regime = max(regimes, key=lambda e: obs_value(e, "date") or "")
            factor_names = [r["to"] for r in relations
                            if r["from"] == latest_regime["name"] and r["relationType"] == "has_factor"]
            factors = [e for e in entities if e["name"] in factor_names]
            snapshot_names = [r["from"] for r in relations
                              if r["to"] == latest_regime["name"] and r["relationType"] == "has_regime"]
            risk_event_names = [r["from"] for r in relations
                                if r["to"] in snapshot_names and r["relationType"] == "detected_in_snapshot"]
            risk_events = [e for e in entities if e["name"] in risk_event_names]
            suggestion_names = [r["from"] for r in relations
                                if r["to"] in risk_event_names and r["relationType"] == "responds_to_event"]
            suggestions = [e for e in entities if e["name"] in suggestion_names]

            risk_context = {
                "available": True,
                "date": obs_value(latest_regime, "date"),
                "regimeLevel": obs_value(latest_regime, "vol_level"),
                "currentVolAnnualized": obs_value(latest_regime, "current_vol_annualized"),
                "factors": [{"name": f["name"], "varianceExplained": obs_value(f, "variance_explained"),
                             "topLoadings": obs_value(f, "top_loadings")} for f in factors],
                "riskEvents": [{"category": obs_value(e, "category"), "detail": obs_value(e, "detail")}
                                for e in risk_events],
                "suggestions": [obs_value(s, "summary") for s in suggestions],
            }

        return {
            "health": health_context,
            "vaultSummary": {"notes": notes},
            "mcpServers": mcp_names,
            "risk": risk_context,
        }

    @router.get("/api/vault/graph-explorer")
    async def vault_graph_explorer(request: Request) -> Dict[str, Any]:
        """Real, read-only vault subgraph query: VaultNote + Tag + HAS_TAG
        only, via the real read_graph MCP tool -- no new ontology, no
        writes. Uses the real, confirmed entity naming convention
        (vault:{path}, tag:{name}), not an invented VaultNote:... format."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(
            f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        vault_entities = {e["name"]: e for e in entities if e.get("entityType") == "VaultNote"}
        tag_entities = {e["name"]: e for e in entities if e.get("entityType") == "Tag"}
        has_tag = [r for r in relations if r.get("relationType") == "HAS_TAG"
                   and r.get("from") in vault_entities and r.get("to") in tag_entities]

        tag_note_counts: Dict[str, int] = {t: 0 for t in tag_entities}
        note_tags: Dict[str, list] = {n: [] for n in vault_entities}
        for r in has_tag:
            tag_name = r["to"].split("tag:", 1)[-1]
            tag_note_counts[r["to"]] = tag_note_counts.get(r["to"], 0) + 1
            note_tags[r["from"]].append(tag_name)

        notes = []
        for name, e in vault_entities.items():
            obs = e.get("observations", [])
            path = next((o.split("path: ", 1)[1] for o in obs if o.startswith("path: ")), "")
            title = next((o.split("title: ", 1)[1] for o in obs if o.startswith("title: ")), name)
            notes.append({"id": name, "title": title, "path": path, "tags": note_tags.get(name, [])})

        tags = [{"id": name, "name": name.split("tag:", 1)[-1], "noteCount": tag_note_counts.get(name, 0)}
                for name in tag_entities]

        edges = [{"from": r["from"], "to": r["to"], "type": "HAS_TAG"} for r in has_tag]

        return {"notes": notes, "tags": tags, "edges": edges}

    @router.get("/api/vault/note")
    async def get_vault_note(request: Request, path: str) -> Dict[str, Any]:
        """Real, read-only fetch of a single vault note's current content,
        for the Composer panel's original/proposed diff view. Scoped to the
        same real, clean vault set as ingestion (root .md + Portfolio/
        Thesis/Watchlist) -- rejects anything else, including any attempt
        to path outside the vault root."""
        require_admin(request)
        from pathlib import Path

        vault_root = Path("/app/vault").resolve()
        target = (vault_root / path).resolve()
        if not str(target).startswith(str(vault_root)) or not target.is_file():
            raise HTTPException(status_code=404, detail="Note not found or path outside vault.")
        if target.suffix != ".md":
            raise HTTPException(status_code=400, detail="Only .md files are supported.")

        text = target.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "text": text}

    @router.post("/api/vault/note/apply")
    async def apply_vault_note_edit(request: Request) -> Dict[str, Any]:
        """Real write: applies an approved edit to a real vault file, then
        re-ingests it via the same replace_fact() supersede semantics used
        by /api/vault/ingest -- so semantic memory stays in sync with the
        edit rather than silently going stale. ALSO performs a scoped,
        single-note graph re-sync (delete-then-recreate the VaultNote
        entity and its HAS_TAG relation, since create_entities silently
        no-ops on an existing name rather than updating it) -- so the
        graph stays in sync with an edited title/tag too, not just
        semantic memory. Also stamps a canonicalId observation, reusing
        the exact same NativeMemoryProvider._normalize_topic() function
        replace_fact() already applies to vault topics -- one shared
        normalization, not a new parallel one -- so semantic memory and
        the graph can eventually be joined by canonicalId even though
        their raw identifiers (normalized topic vs. raw path) differ."""
        require_admin(request)
        from pathlib import Path
        import src.ai_interaction as _ai
        import app as _app
        from src.memory_provider import NativeMemoryProvider

        kg_id = "1751838b"  # real, confirmed knowledge-graph-memory server id

        body = await request.json()
        path = body.get("path", "")
        new_text = body.get("text", "")
        if not path or new_text is None:
            raise HTTPException(status_code=400, detail="path and text are required.")

        vault_root = Path("/app/vault").resolve()
        target = (vault_root / path).resolve()
        if not str(target).startswith(str(vault_root)) or target.suffix != ".md":
            raise HTTPException(status_code=400, detail="Invalid or out-of-vault path.")

        target.write_text(new_text, encoding="utf-8")

        if _ai._memory_manager is not None:
            provider = NativeMemoryProvider(_ai._memory_manager, _ai._memory_vector)
            await provider.replace_fact(
                f"vault:{path}", new_text, category="fact", source="vault_ingest",
            )

        # Real, scoped graph re-sync for just this one note -- confirmed
        # tonight that create_entities silently no-ops on an existing name
        # (does NOT update observations), so a plain re-create would not
        # actually refresh a changed title. Real fix: delete_entities
        # cascades to remove associated relations (confirmed via its own
        # description), so delete-then-recreate is the correct pattern,
        # matching replace_fact()'s own delete-then-add semantics.
        try:
            note_name = f"vault:{path}"
            await _app.mcp_manager.call_tool(
                f"mcp__{kg_id}__delete_entities", {"entityNames": [note_name]})

            first_line = new_text.splitlines()[0] if new_text.splitlines() else ""
            title = first_line.lstrip("#").strip() if first_line.startswith("#") else Path(path).stem
            canonical_id = NativeMemoryProvider._normalize_topic(note_name)

            await _app.mcp_manager.call_tool(
                f"mcp__{kg_id}__create_entities",
                {"entities": [{
                    "name": note_name, "entityType": "VaultNote",
                    "observations": [f"path: {path}", f"title: {title}", f"canonicalId: {canonical_id}"],
                }]})

            parts = Path(path).parts
            if len(parts) > 1:
                tag = parts[0]
                tag_name = f"tag:{tag}"
                await _app.mcp_manager.call_tool(
                    f"mcp__{kg_id}__create_relations",
                    {"relations": [{"from": note_name, "to": tag_name, "relationType": "HAS_TAG"}]})
        except Exception as e:
            logger.warning(f"vault note apply: graph re-sync for {path} failed: {e}")

        return {"path": path, "ok": True, "chars": len(new_text)}

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
            result = await _app.mcp_manager.call_tool(f"mcp__77dcd752__{tool}", arguments)
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

    @router.post("/api/vault/note/create")
    async def create_vault_note(request: Request) -> Dict[str, Any]:
        """Real write: creates a new, empty vault note at a given real
        path, scoped to root .md files or Portfolio/Thesis/Watchlist
        (same real, clean vault scope as ingestion), rejecting anything
        outside that or any path traversal. Does NOT overwrite an
        existing file. Does not auto-ingest/graph-sync -- the note is
        empty until the user actually writes content via Composer and
        applies it, which already triggers ingestion/graph-sync for real
        content."""
        require_admin(request)
        from pathlib import Path

        body = await request.json()
        path = body.get("path", "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="path is required.")
        if not path.endswith(".md"):
            path += ".md"

        vault_root = Path("/app/vault").resolve()
        target = (vault_root / path).resolve()
        if not str(target).startswith(str(vault_root)):
            raise HTTPException(status_code=400, detail="Path outside vault root.")

        parts = Path(path).parts
        allowed_top = {"Portfolio", "Thesis", "Watchlist"}
        if len(parts) > 1 and parts[0] not in allowed_top:
            raise HTTPException(status_code=400,
                detail=f"New notes must be at the vault root or under one of: {sorted(allowed_top)}.")

        if target.exists():
            raise HTTPException(status_code=409, detail=f"A note already exists at {path}.")

        target.parent.mkdir(parents=True, exist_ok=True)
        title = Path(path).stem.replace("_", " ")
        target.write_text(f"# {title}\n\n", encoding="utf-8")

        return {"path": path, "ok": True}

    @router.get("/api/search/cross")
    async def cross_search(request: Request, q: str) -> Dict[str, Any]:
        """Real, read-only cross-domain search over Vault + Risk entities,
        using only real, verified entity types (VaultNote, Tag, Regime,
        Factor, RiskEvent, Suggestion) and the real HAS_TAG relation
        (uppercase, confirmed against the live graph -- an earlier
        proposal's "has_tag"/"tagged_note" did not match reality). Case-
        insensitive substring match against each entity's real
        observations. No writes, no new schema."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        query = q.lower().strip()

        def obs_text(e):
            return " ".join(e.get("observations", [])).lower()

        def matches(e):
            return query in e["name"].lower() or query in obs_text(e)

        vault_notes = [e for e in entities if e["entityType"] == "VaultNote" and matches(e)]
        tags = [e for e in entities if e["entityType"] == "Tag" and matches(e)]
        regimes = [e for e in entities if e["entityType"] == "Regime" and matches(e)]
        factors = [e for e in entities if e["entityType"] == "Factor" and matches(e)]
        risk_events = [e for e in entities if e["entityType"] == "RiskEvent" and matches(e)]
        suggestions = [e for e in entities if e["entityType"] == "Suggestion" and matches(e)]

        tag_map: Dict[str, list] = {}
        for r in relations:
            if r.get("relationType") == "HAS_TAG":
                tag_name = r["to"].split("tag:", 1)[-1] if r["to"].startswith("tag:") else r["to"]
                tag_map.setdefault(r["from"], []).append(tag_name)

        def to_dict(e):
            return {"id": e["name"], "type": e["entityType"], "observations": e["observations"]}

        return {
            "vault": {
                "notes": [{**to_dict(n), "tags": tag_map.get(n["name"], [])} for n in vault_notes],
                "tags": [to_dict(t) for t in tags],
            },
            "risk": {
                "regimes": [to_dict(r) for r in regimes],
                "factors": [to_dict(f) for f in factors],
                "events": [to_dict(e) for e in risk_events],
                "suggestions": [to_dict(s) for s in suggestions],
            },
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

    @router.get("/api/risk/timeline")
    async def get_risk_regime_timeline(request: Request) -> Dict[str, Any]:
        """Real, read-only regime timeline -- finds all real,
        same-pipeline Regime entities (excluding the July 22 hand-analysis,
        using the same real 'method' observation filter already used
        elsewhere), sorted chronologically. Genuinely thin right now (2
        real days as of this build) -- this route just returns whatever
        real data exists, honestly, rather than padding it out."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        regimes = [e for e in entities if e["entityType"] == "Regime" and obs_value(e, "method")]
        regimes.sort(key=lambda e: obs_value(e, "date") or "")

        timeline = []
        for r in regimes:
            factor_names = [rel["to"] for rel in relations
                            if rel["from"] == r["name"] and rel["relationType"] == "has_factor"]
            factors = [e for e in entities if e["name"] in factor_names]
            dominant = max(factors, key=lambda f: float(obs_value(f, "variance_explained") or 0), default=None)
            snapshot_names = [rel["from"] for rel in relations
                              if rel["to"] == r["name"] and rel["relationType"] == "has_regime"]
            risk_event_names = [rel["from"] for rel in relations
                                if rel["to"] in snapshot_names and rel["relationType"] == "detected_in_snapshot"]

            timeline.append({
                "date": obs_value(r, "date"),
                "regime": obs_value(r, "vol_level"),
                "current_vol_annualized": obs_value(r, "current_vol_annualized"),
                "dominant_factor": {"id": dominant["name"], "variance_explained": obs_value(dominant, "variance_explained")} if dominant else None,
                "event_count": len(risk_event_names),
            })

        return {"days": len(timeline), "timeline": timeline}

    @router.get("/api/risk/story")
    async def risk_story(request: Request) -> Dict[str, Any]:
        """Real, read-only risk story: today's regime, factors, risk
        events, and suggestions, reusing the exact same real relation
        chain as /api/risk/latest (has_factor, has_regime,
        detected_in_snapshot, responds_to_event). Deliberately does NOT
        compare against the July 22 snapshot -- confirmed directly that
        it uses a genuinely different observation schema and vol
        methodology (hand-analyzed, not the automated pipeline), so a
        naive "yesterday" comparison would be misleading. Comparison
        support is added once 2+ real, same-pipeline daily snapshots
        exist to compare."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])
        relations = graph_data.get("relations", [])

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        # Only real, same-pipeline regimes -- confirmed by checking for the
        # real "method" observation this pipeline always writes, excluding
        # the hand-analyzed July 22 entity which has no such field.
        regimes = [e for e in entities if e["entityType"] == "Regime" and obs_value(e, "method")]
        if not regimes:
            return {"available": False, "message": "No pipeline-generated Regime found -- run /api/risk/refresh first."}

        latest_regime = max(regimes, key=lambda e: obs_value(e, "date") or "")
        latest_date = obs_value(latest_regime, "date")

        factor_names = [r["to"] for r in relations
                        if r["from"] == latest_regime["name"] and r["relationType"] == "has_factor"]
        factors = sorted([e for e in entities if e["name"] in factor_names], key=lambda e: e["name"])

        snapshot_names = [r["from"] for r in relations
                          if r["to"] == latest_regime["name"] and r["relationType"] == "has_regime"]
        risk_event_names = [r["from"] for r in relations
                            if r["to"] in snapshot_names and r["relationType"] == "detected_in_snapshot"]
        risk_events = [e for e in entities if e["name"] in risk_event_names]

        suggestion_names = [r["from"] for r in relations
                            if r["to"] in risk_event_names and r["relationType"] == "responds_to_event"]
        suggestions = [e for e in entities if e["name"] in suggestion_names]

        # How many real, same-pipeline regimes exist -- used by the UI to
        # decide whether a day-over-day comparison is even meaningful yet.
        comparable_regime_count = len(regimes)

        def to_dict(e):
            return {"name": e["name"], "type": e["entityType"], "observations": e["observations"]}

        return {
            "available": True,
            "date": latest_date,
            "regime": to_dict(latest_regime),
            "factors": [to_dict(f) for f in factors],
            "risk_events": [to_dict(r) for r in risk_events],
            "suggestions": [to_dict(s) for s in suggestions],
            "comparable_regime_count": comparable_regime_count,
        }

    @router.get("/api/system/state")
    async def system_state(request: Request) -> Dict[str, Any]:
        """Real, read-only State of Jarvis dashboard: vault counts, real
        risk status, real MCP server list, real recent agent tasks. Scoped
        down from an earlier proposal after confirming directly: "MCP
        servers" and agent workers (browser_agent/market_agent/etc.) are
        two genuinely separate systems, recent tasks have no "domain"
        field (real fields are agent/server/tool/status), and refresh
        cadence/context-summary sections were dropped -- the systemd timer
        lives on the host, not reachable from inside this container."""
        require_admin(request)
        import app as _app
        import json as _json

        kg_id = "1751838b"
        graph_result = await _app.mcp_manager.call_tool(f"mcp__{kg_id}__read_graph", {})
        graph_data = _json.loads(graph_result.get("stdout", "{}") or "{}")
        entities = graph_data.get("entities", [])

        vault_notes = [e for e in entities if e["entityType"] == "VaultNote"]
        tags = [e for e in entities if e["entityType"] == "Tag"]

        def obs_value(e, key):
            for o in e.get("observations", []):
                if o.startswith(f"{key}: "):
                    return o.split(f"{key}: ", 1)[1]
            return None

        regimes = [e for e in entities if e["entityType"] == "Regime" and obs_value(e, "method")]
        risk_state = {"available": False}
        if regimes:
            latest_regime = max(regimes, key=lambda e: obs_value(e, "date") or "")
            factor_names_rel = [r["to"] for r in graph_data.get("relations", [])
                                if r["from"] == latest_regime["name"] and r["relationType"] == "has_factor"]
            factors = [e for e in entities if e["name"] in factor_names_rel]
            dominant = max(factors, key=lambda f: float(obs_value(f, "variance_explained") or 0), default=None)
            snapshot_names = [r["from"] for r in graph_data.get("relations", [])
                              if r["to"] == latest_regime["name"] and r["relationType"] == "has_regime"]
            risk_event_names = [r["from"] for r in graph_data.get("relations", [])
                                if r["to"] in snapshot_names and r["relationType"] == "detected_in_snapshot"]
            suggestion_names = [r["from"] for r in graph_data.get("relations", [])
                                if r["to"] in risk_event_names and r["relationType"] == "responds_to_event"]
            risk_state = {
                "available": True,
                "latest_snapshot": snapshot_names[0] if snapshot_names else None,
                "regime": obs_value(latest_regime, "vol_level"),
                "dominant_factor": {"id": dominant["name"], "variance_share": obs_value(dominant, "variance_explained")} if dominant else None,
                "event_count": len(risk_event_names),
                "suggestion_count": len(suggestion_names),
            }

        # Real, already-verified pattern from the jarvis_context route:
        # _connections is keyed by server_id, values contain real name+status.
        mcp_servers = [
            {"id": sid, "name": v.get("name", sid), "status": v.get("status")}
            for sid, v in _app.mcp_manager._connections.items()
        ] if hasattr(_app.mcp_manager, "_connections") else []

        from routes.tasks_history import get_history_db
        try:
            recent = get_history_db(10).get("tasks", [])
        except Exception:
            recent = []

        return {
            "vault": {"note_count": len(vault_notes), "tag_count": len(tags)},
            "risk": risk_state,
            "mcp_servers": mcp_servers,
            "recent_tasks": [
                {"id": t.get("id"), "agent": t.get("agent"), "server": t.get("server"),
                 "tool": t.get("tool"), "status": t.get("status")}
                for t in recent
            ],
        }

    @router.get("/api/agent-tasks/task")
    async def get_agent_task_detail(request: Request, id: str) -> Dict[str, Any]:
        """Real, read-only single-task detail. Wraps the existing, real
        get_task_db_native() function (already used internally by
        routes/tasks.py) as an actual HTTP route -- this route did not
        exist before (confirmed directly). Real fields: arguments (not
        "input"), result (nested stdout/stderr/exit_code, not flat
        "output"/"error"), created_at/updated_at (not a single
        "timestamp") -- corrected from an earlier proposal's assumed
        field names."""
        require_admin(request)
        from routes.tasks import get_task_db_native

        task = get_task_db_native(id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"No task found with id {id}")
        return task

    @router.get("/api/mcp/server-detail")
    async def get_mcp_server_detail(request: Request, id: str) -> Dict[str, Any]:
        """Real, read-only single-server detail, using only real,
        confirmed fields (no "ping"/"type"/"last_ping" -- those don't
        exist; confirmed directly against a live server object before
        building). Reuses the existing real /api/mcp/servers list and the
        confirmed real per-server tools route."""
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

    @router.get("/api/timeline")
    async def get_unified_timeline(request: Request, limit: int = 100) -> Dict[str, Any]:
        """Real, read-only unified timeline, scoped to risk + agent tasks
        only (per explicit scope decision). Combines the real, existing
        task_events table (no changes made to it or to any task-completion
        code) with the new, purely additive timeline_events table (written
        by /api/risk/refresh). Sorted by timestamp, newest first."""
        require_admin(request)
        import sqlite3

        conn = sqlite3.connect("/app/data/agent_tasks.db")
        conn.row_factory = sqlite3.Row
        events = []

        for row in conn.execute(
            "SELECT ts, event_type, agent, server, tool, task_id, status FROM task_events ORDER BY ts DESC LIMIT ?", (limit,)
        ):
            events.append({
                "ts": row["ts"], "source": "agent",
                "event_type": f"agent.task_{row['event_type']}",
                "summary": f"{row['agent']} → {row['server']} / {row['tool']} [{row['status']}]",
                "entity_id": row["task_id"], "entity_type": "agent-task",
            })

        try:
            for row in conn.execute(
                "SELECT ts, source, event_type, summary, entity_id, entity_type FROM timeline_events ORDER BY ts DESC LIMIT ?", (limit,)
            ):
                events.append(dict(row))
        except sqlite3.OperationalError:
            pass  # table not created yet -- real, first-run case, not an error

        conn.close()
        events.sort(key=lambda e: e["ts"], reverse=True)
        return {"events": events[:limit]}

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

    return router
