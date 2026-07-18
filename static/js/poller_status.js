// static/js/poller_status.js — Poller Status panel (ES6)
// Fetches TradingView poller health from a real, dedicated HTTP server on
// Oracle (100.116.88.44:7010) -- Odysseus itself, running in a container on
// Pop!_OS, has no filesystem access to Oracle at all, so this can't be a
// local Odysseus MCP tool; it has to be a real cross-machine HTTP fetch.
// Real, verified table/column names on the Oracle side: market_snapshots
// (plural), ts (not timestamp).

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

const POLLER_HEALTH_URL = 'http://100.116.88.44:7010/poller/health';
const REFRESH_INTERVAL_MS = 10000;

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

async function _fetchHealth() {
  const res = await fetch(POLLER_HEALTH_URL);
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _statusColor(status) {
  if (status === 'healthy') return 'var(--color-success, #2ecc71)';
  if (status === 'stale') return '#e6b800';
  return 'var(--red, #e74c3c)';
}

async function _render() {
  const container = el('poller-status-content');
  if (!container) return;
  try {
    const data = await _fetchHealth();
    const color = _statusColor(data.status);
    container.innerHTML = `
      <div style="font-size:18px;font-weight:600;color:${color};">${esc((data.status || 'unknown').toUpperCase())}</div>
      ${data.reason ? `<div style="opacity:0.7;margin-top:4px;">${esc(data.reason)}</div>` : ''}
      ${data.last_update_timestamp ? `<div style="margin-top:10px;"><strong>Last Update:</strong> ${esc(data.last_update_timestamp)}</div>` : ''}
      ${data.seconds_since_update !== undefined ? `<div><strong>Seconds Since Update:</strong> ${data.seconds_since_update.toFixed(1)}</div>` : ''}
      <pre style="margin-top:12px;background:color-mix(in srgb, var(--fg) 5%, transparent);padding:8px;border-radius:4px;font-size:11px;white-space:pre-wrap;">${esc(JSON.stringify(data, null, 2))}</pre>
    `;
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to reach poller health server: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('poller-status-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, REFRESH_INTERVAL_MS);
}

function _closePanel() {
  const modal = el('poller-status-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('poller-status-modal')) return;
  Modals.register('poller-status-modal', {
    railBtnId: 'rail-poller-status',
    sidebarBtnId: 'tool-poller-status-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-poller-status-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-poller-status-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
