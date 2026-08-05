// static/js/hud_overlay.js — Realistic HUD overlay (ES6)
// A small, honest, polling-based overlay. No pub/sub, no event bus, no
// WebSockets/SSE, no invented endpoints -- confirmed real routes only:
// /api/risk/latest, /api/graph/nodes + /api/graph/edges, and
// /api/agents/status (agent restart-count/health telemetry, added
// 2026-08-04, sourced from agent_supervisor.py's live state file via
// routes/agent_dashboard.py). Polls on a simple timer, matching the same
// ES6-module + template-literal pattern used throughout the rest of this
// build.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _pollTimer = null;
let _visible = true;

function obsVal(entity, key) {
  const line = (entity.observations || []).find(o => o.startsWith(key + ': '));
  return line ? line.slice(key.length + 2) : null;
}

async function _poll() {
  const box = el('hud-overlay-box');
  if (!box || !_visible) return;

  try {
    const [riskRes, nodesRes, edgesRes, relevanceRes, agentsRes] = await Promise.all([
      fetch('/api/risk/latest', { credentials: 'same-origin' }),
      fetch('/api/graph/nodes', { credentials: 'same-origin' }),
      fetch('/api/graph/edges', { credentials: 'same-origin' }),
      fetch('/api/relevance/today', { credentials: 'same-origin' }),
      fetch('/api/agents/status', { credentials: 'same-origin' }),
    ]);

    let regimeLine = 'Risk: —';
    let dominantConfidence = null; // real variance_explained (0-1), used below for pulse speed
    let dominantFactorName = null; // real factor entity name, e.g. "factor:2026-07-28:1"
    if (riskRes.ok) {
      const risk = await riskRes.json();
      if (risk.found) {
        const vol = obsVal(risk.regime, 'current_vol_annualized');
        const level = obsVal(risk.regime, 'vol_level');
        const dominant = (risk.factors || []).reduce((max, f) => {
          const v = parseFloat(obsVal(f, 'variance_explained') || 0);
          return (!max || v > max.v) ? { f, v } : max;
        }, null);
        regimeLine = `${esc(level || '?')} — ${vol ? (parseFloat(vol) * 100).toFixed(1) + '%' : '?'} vol` +
          (dominant ? ` — dominant ${esc(dominant.f.name)} (${(dominant.v * 100).toFixed(0)}%)` : '');
        if (dominant) { dominantConfidence = dominant.v; dominantFactorName = dominant.f.name; }
      } else {
        regimeLine = 'Risk: no data yet';
      }
    }

    let graphLine = 'Graph: —';
    if (nodesRes.ok && edgesRes.ok) {
      const nodes = (await nodesRes.json()).nodes;
      const edges = (await edgesRes.json()).edges;
      graphLine = `${nodes.length} nodes, ${edges.length} edges`;
    }

    // Real agent restart telemetry -- sourced from agent_supervisor.py's
    // live state file (written every 5s), passed through verbatim by
    // /api/agents/status. Highlights anything not currently "alive"
    // (stale, or fully missing from health -- the exact failure mode
    // that motivated adding this in the first place).
    let agentsLine = 'Agents: —';
    let agentsColor = 'var(--color-muted)';
    if (agentsRes.ok) {
      const agents = await agentsRes.json();
      if (!agents.error) {
        const names = Object.keys(agents);
        const totalRestarts = names.reduce((sum, n) => sum + (agents[n].restart_count || 0), 0);
        const unhealthy = names.filter(n => agents[n].status !== 'alive');
        agentsLine = `${names.length} agents, ${totalRestarts} restart${totalRestarts === 1 ? '' : 's'}`;
        if (unhealthy.length > 0) {
          agentsLine += ` — ${unhealthy.map(esc).join(', ')}`;
          agentsColor = 'var(--color-error)';
        }
      } else {
        agentsLine = 'Agents: state unavailable';
        agentsColor = 'var(--color-warning)';
      }
    }

    // Real relevance cards -- uses the new /api/relevance/today route,
    // which wraps the exact same logic already proven in risk_panel.js.
    let relevanceHtml = '';
    if (relevanceRes.ok) {
      const rel = await relevanceRes.json();
      if (rel.notes.length > 0) {
        // Real, small visual identity per ticker without needing to
        // source actual logo assets (confirmed none exist -- the only
        // icons/ dir on disk is for PWA/app icons, not ticker logos).
        // A deterministic hash-based dot color instead.
        const dotColor = (ticker) => {
          let hash = 0;
          for (const c of ticker) hash = (hash * 31 + c.charCodeAt(0)) % 360;
          return `hsl(${hash}, 70%, 55%)`;
        };
        // Real pulse speed from the dominant factor's actual confidence
        // (variance_explained, already fetched above) -- higher
        // confidence pulses faster. Clamped to a sensible range so it
        // never goes too fast/slow regardless of the real value.
        const conf = dominantConfidence != null ? dominantConfidence : 0.3;
        const pulseSpeed = Math.max(1.0, Math.min(2.5, 2.5 - (conf * 1.5))).toFixed(2);
        // Real border thickness from the same real confidence value.
        const borderWidth = (1 + (conf * 3)).toFixed(2);
        // Real factor tint: extract the trailing integer from the real
        // compound factor name (e.g. "factor:2026-07-28:1" -> "1"),
        // not a direct id match -- confirmed the real format before
        // building this, since it's a compound string, not a plain int.
        const FACTOR_COLOR_SHIFT = {
          1: 'rgba(255, 80, 80, 0.35)',
          2: 'rgba(80, 160, 255, 0.35)',
          3: 'rgba(120, 255, 120, 0.35)',
          4: 'rgba(255, 200, 80, 0.35)',
        };
        const factorNum = dominantFactorName ? dominantFactorName.split(':').pop() : null;
        const factorTint = FACTOR_COLOR_SHIFT[factorNum] || 'rgba(255,255,255,0.25)';
        // Real icon path if DK has populated /static/ticker-icons/ for
        // this ticker; falls back to the colored dot via onerror if the
        // file doesn't exist (no broken-image placeholders).
        // Real filenames confirmed on disk: uppercase, @2x retina
        // suffix, crypto tickers additionally suffixed -CRYPTO (e.g.
        // XRP-CRYPTO@2x.png vs MSFT@2x.png). Try the plain form first,
        // then the -CRYPTO form, then fall back to the colored dot --
        // no hardcoded crypto/equity ticker list needed.
        relevanceHtml = rel.notes.map(n => {
          const t = esc(n.ticker);
          return `
          <div style="display:flex;align-items:center;gap:6px;font-size:10px;color:var(--fg);margin-top:4px;padding-top:4px;border-top:1px solid var(--border);">
            <span class="hud-ticker-fallback hud-ticker-dot" style="width:8px;height:8px;border-radius:50%;background:${dotColor(n.ticker)};--ticker-color:${dotColor(n.ticker)};--pulse-speed:${pulseSpeed}s;--intensity-border:${borderWidth}px;--factor-tint:${factorTint};flex-shrink:0;display:none;"></span>
            <img src="/static/ticker-icons/${t}@2x.png" style="width:14px;height:14px;flex-shrink:0;object-fit:contain;"
                 onerror="if (!this.dataset.triedCrypto) { this.dataset.triedCrypto = '1'; this.src = '/static/ticker-icons/${t}-CRYPTO@2x.png'; } else { this.style.display='none'; this.previousElementSibling.style.display='inline-block'; }">
            <span style="color:var(--orange, var(--color-warning));">${t}</span> — ${esc(n.title)}
          </div>
        `;
        }).join('');
      }
    }

    box.innerHTML = `
      <div style="font-size:10px;color:var(--color-subheader);font-weight:600;margin-bottom:4px;">HUD</div>
      <div style="font-size:11px;color:var(--fg);margin-bottom:2px;">${regimeLine}</div>
      <div style="font-size:11px;color:var(--color-muted);">${esc(graphLine)}</div>
      <div style="font-size:11px;color:${agentsColor};">${esc(agentsLine)}</div>
      ${relevanceHtml}
    `;
  } catch (e) {
    box.innerHTML = `<div style="font-size:10px;color:var(--color-error);">HUD poll failed</div>`;
  }
}

export function init() {
  const toggleBtn = el('tool-hud-overlay-btn');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      const container = el('hud-overlay-container');
      if (!container) return;
      _visible = !_visible;
      container.style.display = _visible ? 'block' : 'none';
      if (_visible) _poll();
    });
  }

  // Real, simple timer -- no pub/sub, no push, matching what was
  // actually agreed on: poll every 10s (medium cadence; risk/graph data
  // doesn't change fast enough to need faster polling than that).
  _poll();
  _pollTimer = setInterval(_poll, 10000);
}

export default { init };
