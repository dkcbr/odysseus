// static/js/risk_panel.js — Jarvis Risk Panel (ES6)
// Self-contained, matching system_health_panel.js / vault_graph_panel.js's
// pattern. Combines Regime + Factors + Risk Events (with Suggestions) in
// one panel, per real user choice -- not three separate panels.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

function _regimeColor(level) {
  if (level === 'LOW') return 'var(--color-success)';
  if (level === 'NORMAL') return 'var(--color-subheader)';
  if (level === 'HIGH') return 'var(--color-error)';
  return 'var(--color-muted)';
}

function _renderRegime(regime) {
  if (!regime) return '<div style="padding:12px;color:var(--color-muted);font-size:12px;">No regime data.</div>';
  const obs = {};
  regime.observations.forEach(o => {
    const idx = o.indexOf(': ');
    if (idx > -1) obs[o.slice(0, idx)] = o.slice(idx + 2);
  });
  const level = obs.vol_level || 'unknown';
  return `
    <div style="padding:14px 16px;border-bottom:1px solid var(--border);">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
        <span style="width:12px;height:12px;border-radius:50%;background:${_regimeColor(level)};display:inline-block;"></span>
        <span style="font-size:16px;font-weight:600;color:var(--fg);">${esc(level)}</span>
        <span style="font-size:10px;color:var(--color-muted);">as of ${esc(obs.date || '')}</span>
      </div>
      <div style="font-size:11px;color:var(--color-muted);">
        Vol: ${esc(obs.current_vol_annualized ? (parseFloat(obs.current_vol_annualized) * 100).toFixed(1) + '%' : '?')} annualized
        (percentile ${esc(obs.percentile_in_own_history || '?')} of its own history)
      </div>
      <div style="font-size:10px;color:#5a7a8a;margin-top:4px;">${esc(obs.method || '')}</div>
    </div>
  `;
}

function _renderFactors(factors) {
  if (!factors.length) return '<div style="padding:12px;color:var(--color-muted);font-size:12px;">No factor data.</div>';
  const rows = factors.map(f => {
    const obs = {};
    f.observations.forEach(o => {
      const idx = o.indexOf(': ');
      if (idx > -1) obs[o.slice(0, idx)] = o.slice(idx + 2);
    });
    const pct = obs.variance_explained ? (parseFloat(obs.variance_explained) * 100).toFixed(1) : '?';
    return `
      <div style="padding:6px 16px;border-bottom:1px solid var(--border);">
        <div style="font-size:12px;color:var(--fg);">${esc(f.name)} — ${pct}% of variance</div>
        <div style="font-size:10px;color:var(--color-muted);">${esc(obs.top_loadings || '')}</div>
      </div>
    `;
  }).join('');
  return rows;
}

function _renderRiskEvents(riskEvents) {
  if (!riskEvents.length) return '<div style="padding:12px;color:var(--color-muted);font-size:12px;">No active risk events.</div>';
  const rows = riskEvents.map(e => {
    const obs = {};
    e.observations.forEach(o => {
      const idx = o.indexOf(': ');
      if (idx > -1) obs[o.slice(0, idx)] = o.slice(idx + 2);
    });
    return `
      <div style="padding:8px 16px;border-bottom:1px solid var(--border);">
        <div style="font-size:12px;color:var(--color-warning);">⚠ ${esc(obs.category || '')}</div>
        <div style="font-size:11px;color:var(--fg);margin-top:2px;">${esc(obs.detail || '')}</div>
      </div>
    `;
  }).join('');
  return rows;
}

function _renderSuggestions(suggestions) {
  if (!suggestions.length) return '<div style="padding:12px;color:var(--color-muted);font-size:12px;">No suggestions.</div>';
  return suggestions.map(s => {
    const obs = {};
    s.observations.forEach(o => {
      const idx = o.indexOf(': ');
      if (idx > -1) obs[o.slice(0, idx)] = o.slice(idx + 2);
    });
    return `
      <div style="padding:8px 16px;border-bottom:1px solid var(--border);">
        <div style="font-size:11px;color:var(--fg);">${esc(obs.summary || '')}</div>
      </div>
    `;
  }).join('');
}

async function _render() {
  const body = el('risk-panel-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--color-muted);">Loading…</div>';

  try {
    const res = await fetch('/api/risk/latest', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (!data.found) {
      body.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--color-muted);">${esc(data.message)}</div>`;
      return;
    }

    // Real, ambient relevance: extract top ticker symbols from the
    // dominant factor's top_loadings text (same real parsing approach
    // used in Graph Explorer v2's market_entity extraction), then reuse
    // the exact same, already-verified /api/search/cross route to find
    // any real vault notes mentioning them -- surfaced automatically,
    // not requiring the user to search.
    let relevanceHtml = '';
    try {
      // Real, correct parsing: factors are {name, type, observations}
      // (raw text lines), not flat objects -- confirmed directly against
      // the live /api/risk/latest response before fixing this.
      function obsVal(entity, key) {
        const line = (entity.observations || []).find(o => o.startsWith(key + ': '));
        return line ? line.slice(key.length + 2) : null;
      }
      const dominant = (data.factors || []).reduce((max, f) => {
        const v = parseFloat(obsVal(f, 'variance_explained') || 0);
        return (!max || v > max.v) ? { f, v } : max;
      }, null);
      if (dominant) {
        const loadingsText = obsVal(dominant.f, 'top_loadings') || '';
        // Real fix: use ALL tickers shown for the dominant factor, not an
        // arbitrary top-3 cutoff -- confirmed live that a tiny loading
        // difference (0.004) can push a genuinely relevant ticker (XRP)
        // just outside a rigid position cutoff on any given day.
        const tickers = loadingsText.split(',').map(s => s.trim().split(' ')[0]).filter(Boolean);
        const seenNotes = new Map();
        for (const ticker of tickers) {
          const csRes = await fetch(`/api/search/cross?q=${encodeURIComponent(ticker)}`, { credentials: 'same-origin' });
          if (!csRes.ok) continue;
          const csData = await csRes.json();
          (csData.vault.notes || []).forEach(n => seenNotes.set(n.id, n));
        }
        if (seenNotes.size > 0) {
          const rows = [...seenNotes.values()].map(n => {
            const title = n.observations.find(o => o.startsWith('title: '));
            return `<div style="padding:4px 16px;font-size:11px;color:var(--fg);border-bottom:1px solid var(--border);">📓 ${esc(title ? title.slice(7) : n.id)}</div>`;
          }).join('');
          relevanceHtml = `
            <div id="risk-section-relevance">
              <div style="padding:8px 16px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">
                Related Vault Notes (matching ${esc(tickers.join(', '))})
              </div>
              ${rows}
            </div>
          `;
        }
      }
    } catch (_e) { /* ambient, best-effort -- never break the rest of the panel */ }

    let timelineHtml = '';
    try {
      const tlRes = await fetch('/api/risk/timeline', { credentials: 'same-origin' });
      if (tlRes.ok) {
        const tl = await tlRes.json();
        const rows = tl.timeline.map(day => `
          <div style="padding:4px 16px;font-size:11px;color:var(--fg);border-bottom:1px solid var(--border);">
            ${esc(day.date)} — ${esc(day.regime)}
            <span style="color:var(--color-muted);font-size:10px;">
              (${day.current_vol_annualized ? (parseFloat(day.current_vol_annualized) * 100).toFixed(1) + '%' : '?'} vol,
              ${day.event_count} event(s)${day.dominant_factor ? ', dominant ' + esc(day.dominant_factor.id) : ''})
            </span>
          </div>
        `).join('');
        timelineHtml = `
          <div id="risk-section-timeline">
            <div style="padding:8px 16px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">
              Regime Timeline (${tl.days} real day${tl.days === 1 ? '' : 's'} — still thin, more useful as it accumulates)
            </div>
            ${rows || '<div style="padding:8px 16px;color:var(--color-muted);font-size:11px;">No data yet.</div>'}
          </div>
        `;
      }
    } catch (_e) { /* timeline is a real, best-effort addition -- don't break the rest of the panel if it fails */ }

    body.innerHTML = `
      <div id="risk-section-regime">${_renderRegime(data.regime)}</div>
      <div id="risk-section-factors">
        <div style="padding:8px 16px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Factors</div>
        ${_renderFactors(data.factors)}
      </div>
      <div id="risk-section-events">
        <div style="padding:8px 16px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Risk Events</div>
        ${_renderRiskEvents(data.risk_events)}
      </div>
      <div id="risk-section-suggestions">
        <div style="padding:8px 16px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Suggestions</div>
        ${_renderSuggestions(data.suggestions)}
      </div>
      ${timelineHtml}
      ${relevanceHtml}
    `;
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--color-error);">Failed to load: ${esc(String(e))}</div>`;
  }
}

function _scrollToSection(id) {
  const target = el(id);
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.style.outline = '2px solid var(--color-subheader)';
    setTimeout(() => { target.style.outline = 'none'; }, 1200);
  }
}

export async function openPanel(section) {
  const modal = el('risk-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();
  if (section) _scrollToSection(`risk-section-${section}`);
}

function _closePanel() {
  const modal = el('risk-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

async function _refreshNow() {
  const btn = el('risk-refresh-btn');
  if (btn) btn.textContent = 'Refreshing…';
  try {
    const res = await fetch('/api/risk/refresh', { method: 'POST', credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await _render();
  } catch (e) {
    const body = el('risk-panel-body');
    if (body) body.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--color-error);">Refresh failed: ${esc(String(e))}</div>`;
  }
  if (btn) btn.textContent = 'Refresh Now';
}

export function init() {
  const toolBtn = el('tool-risk-panel-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-risk-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const refreshBtn = el('risk-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', _refreshNow);

  const modal = el('risk-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
