// static/js/system_health_panel.js — Jarvis System Health Panel (ES6)
// Self-contained, matching command_palette.js's pattern (no modalManager
// involvement, following the same real precedent that avoided a prior
// hang incident) -- this is a simple, read-only overlay, not a complex
// interactive panel like portfolio_panel.js.
//
// Real data only: fetches the actual, live /api/diagnostics/services
// endpoint (built and verified working tonight) -- no invented state
// object, no polling into a fictional window.odysseusHealth.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;
let _refreshTimer = null;

// Real, bounded UI-side history buffer -- pushed to on every successful
// fetch (initial load and each refresh tick), not on a fabricated push
// event. Capped at 50 entries so it can't grow unbounded across a long
// open session.
const _MAX_HISTORY = 50;
const _history = [];

// Real, honest mechanism: there is no backend push/event system for
// health data (no WebSocket, no SSE, no window.odysseusHealth global) --
// the real architecture is a plain fetch of /api/diagnostics/services on
// demand. Auto-refresh here means genuinely re-fetching on an interval
// while the panel is open, not subscribing to a push event that doesn't
// exist. 15s balances staleness against re-running the real, non-trivial
// diagnostics aggregation (which itself makes several real network calls)
// too often.
const _REFRESH_INTERVAL_MS = 15000;

function _severityIcon(status) {
  if (status === 'ok') return '🟢';
  if (status === 'degraded') return '🟡';
  if (status === 'down') return '🔴';
  return '⚪';
}

function _renderRow(svc) {
  return `
    <tr style="border-bottom:1px solid #1a3a4a;">
      <td style="padding:8px 10px;font-size:12px;color:#e6f7ff;">${esc(svc.name)}</td>
      <td style="padding:8px 10px;font-size:12px;">${_severityIcon(svc.status)} ${esc(svc.status)}</td>
      <td style="padding:8px 10px;font-size:11px;color:#7a9aab;">${esc(svc.detail || '')}</td>
    </tr>
  `;
}

async function _render() {
  const body = el('system-health-panel-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;font-size:12px;color:#7a9aab;">Loading…</div>';

  let data;
  try {
    const res = await fetch('/api/diagnostics/services', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;font-size:12px;color:#e88;">Failed to load: ${esc(String(e))}</div>`;
    return;
  }

  const overall = data.overall || 'unknown';
  const services = data.services || [];
  const timestamp = data.timestamp || '';

  _history.push({ timestamp, overall });
  if (_history.length > _MAX_HISTORY) _history.shift();

  const historyHtml = _history
    .slice()
    .reverse()
    .map(entry => `
      <div style="display:flex;justify-content:space-between;padding:4px 10px;font-size:10px;border-bottom:1px solid #12222c;">
        <span style="color:#7a9aab;">${esc(entry.timestamp)}</span>
        <span>${_severityIcon(entry.overall)} ${esc(entry.overall)}</span>
      </div>
    `)
    .join('');

  body.innerHTML = `
    <div style="padding:14px 16px;border-bottom:1px solid #1a3a4a;display:flex;align-items:center;gap:10px;">
      <span style="font-size:20px;">${_severityIcon(overall)}</span>
      <div>
        <div style="font-size:14px;font-weight:600;color:#e6f7ff;text-transform:uppercase;">${esc(overall)}</div>
        <div style="font-size:10px;color:#7a9aab;">Checked ${esc(timestamp)}</div>
      </div>
    </div>
    <table style="width:100%;border-collapse:collapse;">
      ${services.map(_renderRow).join('')}
    </table>
    <div style="padding:10px 16px;font-size:10px;color:#7a9aab;border-top:1px solid #1a3a4a;">
      🟢 ok — healthy &nbsp; 🟡 degraded — partial issue &nbsp; 🔴 down — unreachable/failing
    </div>
    <div style="border-top:1px solid #1a3a4a;">
      <div style="padding:8px 10px 4px;font-size:11px;font-weight:600;color:#4ad4e8;">History (this session, last ${_history.length})</div>
      <div style="max-height:180px;overflow-y:auto;">
        ${historyHtml}
      </div>
    </div>
  `;
}

export async function openPanel() {
  const modal = el('system-health-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();

  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(() => {
    if (_open) _render();
  }, _REFRESH_INTERVAL_MS);
}

function _closePanel() {
  const modal = el('system-health-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;

  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

export function init() {
  const toolBtn = el('tool-system-health-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-system-health-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const modal = el('system-health-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
