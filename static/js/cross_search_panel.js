// static/js/cross_search_panel.js — Jarvis Vault + Risk Cross Search (ES6)
// Self-contained, matching risk_panel.js / vault_graph_panel.js's pattern.
// Real, read-only search across VaultNote/Tag/Regime/Factor/RiskEvent/
// Suggestion, using the real /api/search/cross route. Clicking a result
// routes to the correct existing panel and jumps to the right section.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

function obsValue(observations, key) {
  const line = observations.find(o => o.startsWith(`${key}: `));
  return line ? line.split(`${key}: `, 1)[1] || line.slice(key.length + 2) : null;
}

function _renderResults(data) {
  const body = el('cross-search-body');
  if (!body) return;

  const vaultNotes = data.vault.notes;
  const tags = data.vault.tags;
  const regimes = data.risk.regimes;
  const factors = data.risk.factors;
  const events = data.risk.events;
  const suggestions = data.risk.suggestions;

  const total = vaultNotes.length + tags.length + regimes.length + factors.length + events.length + suggestions.length;
  if (total === 0) {
    body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">No results.</div>';
    return;
  }

  const noteRows = vaultNotes.map(n => {
    const title = obsValue(n.observations, 'title') || n.id;
    return `<div class="cs-result" data-route="vault-note" data-id="${esc(n.id)}" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--fg);">● ${esc(title)} <span style="color:var(--color-muted);font-size:10px;">— ${n.tags.map(esc).join(', ')}</span></div>`;
  }).join('');

  const tagRows = tags.map(t => {
    const name = obsValue(t.observations, 'name') || t.id;
    return `<div class="cs-result" data-route="vault-tag" data-id="${esc(t.id)}" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--fg);">🏷 ${esc(name)}</div>`;
  }).join('');

  const regimeRows = regimes.map(r => {
    const level = obsValue(r.observations, 'vol_level') || r.id;
    return `<div class="cs-result" data-route="risk-regime" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--fg);">📊 Regime: ${esc(level)} (${esc(r.id)})</div>`;
  }).join('');

  const factorRows = factors.map(f => {
    const loadings = obsValue(f.observations, 'top_loadings') || '';
    return `<div class="cs-result" data-route="risk-factors" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--fg);">📈 ${esc(f.id)} <span style="color:var(--color-muted);font-size:10px;">— ${esc(loadings)}</span></div>`;
  }).join('');

  const eventRows = events.map(e => {
    const detail = obsValue(e.observations, 'detail') || '';
    return `<div class="cs-result" data-route="risk-events" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--color-warning);">⚠ ${esc(detail.slice(0, 100))}</div>`;
  }).join('');

  const suggestionRows = suggestions.map(s => {
    const summary = obsValue(s.observations, 'summary') || '';
    return `<div class="cs-result" data-route="risk-suggestions" style="padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border);font-size:12px;color:var(--fg);">${esc(summary.slice(0, 100))}</div>`;
  }).join('');

  body.innerHTML = `
    ${vaultNotes.length || tags.length ? `<div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);">Vault</div>${noteRows}${tagRows}` : ''}
    ${regimes.length || factors.length || events.length || suggestions.length ? `<div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Risk</div>${regimeRows}${factorRows}${eventRows}${suggestionRows}` : ''}
  `;

  body.querySelectorAll('.cs-result').forEach(item => {
    item.addEventListener('click', async () => {
      const route = item.dataset.route;
      if (route === 'vault-note' || route === 'vault-tag') {
        const m = await import('./vault_graph_panel.js');
        m.default.openPanel(false, route === 'vault-note' ? item.dataset.id : null);
      } else {
        const m = await import('./risk_panel.js');
        const section = route.replace('risk-', '');
        m.default.openPanel(section);
      }
    });
  });
}

async function _runSearch(query) {
  const body = el('cross-search-body');
  if (!query) {
    if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Type to search.</div>';
    return;
  }
  if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Searching…</div>';
  try {
    const res = await fetch(`/api/search/cross?q=${encodeURIComponent(query)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _renderResults(data);
  } catch (e) {
    if (body) body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Search failed: ${esc(String(e))}</div>`;
  }
}

export async function openPanel() {
  const modal = el('cross-search-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  const input = el('cross-search-input');
  if (input) { input.value = ''; input.focus(); }
  const body = el('cross-search-body');
  if (body) body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Type to search.</div>';
}

function _closePanel() {
  const modal = el('cross-search-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-cross-search-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-cross-search-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const input = el('cross-search-input');
  if (input) {
    let debounceTimer;
    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => _runSearch(input.value.trim()), 250);
    });
  }

  const modal = el('cross-search-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
