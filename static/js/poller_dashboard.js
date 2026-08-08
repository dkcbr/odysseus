// static/js/poller_dashboard.js — Multi-Poller Dashboard panel (ES6)
// Aggregates three real health endpoints: TradingView poller (Oracle,
// DB-backed), NVDA + crypto zone monitors (Pop!_OS, timestamp-file-backed
// since they have no database). Same real static-shell/Modals.register
// pattern as poller_status.js.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

const ENDPOINTS = [
  { label: 'TradingView Poller (Oracle)', url: 'http://100.116.88.44:7010/poller/health' },
  { label: 'NVDA Zone Monitor (Pop!_OS)', url: 'http://100.93.206.89:7020/nvda/health' },
  { label: 'Crypto Zone Monitor (Pop!_OS)', url: 'http://100.93.206.89:7020/crypto/health' },
];
const REFRESH_INTERVAL_MS = 10000;

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

async function _fetchHealth(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return { status: 'dead', reason: `http_${res.status}` };
    return await res.json();
  } catch (e) {
    return { status: 'dead', reason: 'fetch_failed' };
  }
}

function _statusColor(status) {
  if (status === 'healthy') return 'var(--color-success, #2ecc71)';
  if (status === 'stale') return '#e6b800';
  return 'var(--red, #e74c3c)';
}

function _renderBlock(label, data) {
  const color = _statusColor(data.status);
  return `
    <div style="padding:10px 0;border-bottom:1px solid var(--border);">
      <div style="font-weight:600;margin-bottom:4px;">${esc(label)}</div>
      <div style="color:${color};font-weight:600;">${esc((data.status || 'unknown').toUpperCase())}</div>
      ${data.reason ? `<div style="opacity:0.7;font-size:12px;">${esc(data.reason)}</div>` : ''}
      ${data.last_update_timestamp ? `<div style="font-size:12px;">Last update: ${esc(data.last_update_timestamp)}</div>` : ''}
      ${data.seconds_since_update !== undefined ? `<div style="font-size:12px;">${data.seconds_since_update.toFixed(1)}s ago</div>` : ''}
    </div>
  `;
}

async function _render() {
  const container = el('poller-dashboard-content');
  if (!container) return;

  const results = await Promise.all(ENDPOINTS.map(e => _fetchHealth(e.url)));
  container.innerHTML = ENDPOINTS.map((e, i) => _renderBlock(e.label, results[i])).join('');
}

export function openPanel() {
  const modal = el('poller-dashboard-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, REFRESH_INTERVAL_MS);
}

function _closePanel() {
  const modal = el('poller-dashboard-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('poller-dashboard-modal')) return;
  Modals.register('poller-dashboard-modal', {
    railBtnId: 'rail-poller-dashboard',
    sidebarBtnId: 'tool-poller-dashboard-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-poller-dashboard-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-poller-dashboard-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
