// static/js/registry.js — Agent Registry panel (ES6)
// Admin-gated view over the real backend at /api/agent-tasks/* --
// registry (enabled/disabled + capabilities), merged agent health, and
// queue counts. Uses the single /dashboard endpoint (already merges
// registry + health + queue server-side), so this module makes exactly
// one fetch per render instead of the two originally assumed.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;

async function _fetchDashboard() {
  const res = await fetch('/api/agent-tasks/dashboard', { credentials: 'same-origin' });
  if (res.status === 401 || res.status === 403) {
    throw new Error('Access denied');
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

async function _postJSON(path) {
  const res = await fetch(path, { method: 'POST', credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _statusBadge(status) {
  const cls = status === 'alive' ? 'status-alive'
    : status === 'stale' ? 'status-stale'
    : 'status-disabled';
  const label = (status || 'never seen').toUpperCase();
  return `<span class="registry-status-badge ${cls}">${esc(label)}</span>`;
}

function _renderTable(snapshot) {
  const registry = snapshot.registry || {};
  const health = snapshot.agents || {};
  const agentNames = Object.keys(registry);

  if (agentNames.length === 0) {
    return '<div class="admin-empty">No agents registered.</div>';
  }

  const rows = agentNames.map(name => {
    const reg = registry[name] || {};
    const h = health[name] || {};
    const status = !reg.enabled ? 'disabled' : (h.status || 'never seen');
    const lastSeen = h.seconds_since_heartbeat !== undefined ? `${h.seconds_since_heartbeat}s ago` : '—';
    const servers = (reg.servers || []).join(', ');
    const tools = (reg.allowed_tools || []).join(', ');

    return `
      <tr data-agent="${esc(name)}">
        <td>${esc(name)}</td>
        <td>
          <label class="admin-switch" style="transform:scale(0.85);">
            <input type="checkbox" class="registry-toggle" data-agent="${esc(name)}" ${reg.enabled ? 'checked' : ''}>
            <span class="admin-slider"></span>
          </label>
        </td>
        <td>${_statusBadge(status)}</td>
        <td>${esc(lastSeen)}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(servers)}">${esc(servers)}</td>
        <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(tools)}">${esc(tools)}</td>
        <td><button class="admin-btn-sm registry-restart-btn" data-agent="${esc(name)}">Restart</button></td>
      </tr>`;
  }).join('');

  const total = snapshot.total || 0;
  const pending = (snapshot.pending || []).length;
  const running = (snapshot.running || []).length;
  const failed = (snapshot.failed || []).length;
  const success = (snapshot.success || []).length;

  return `
    <div class="registry-summary" style="display:flex;gap:16px;margin-bottom:12px;font-size:12px;opacity:0.7;">
      <span>Total: ${total}</span>
      <span>Pending: ${pending}</span>
      <span>Running: ${running}</span>
      <span>Failed: ${failed}</span>
      <span>Success: ${success}</span>
    </div>
    <table class="registry-table" style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="text-align:left;font-size:11px;text-transform:uppercase;opacity:0.5;">
          <th>Agent</th><th>Enabled</th><th>Status</th><th>Last Seen</th><th>Servers</th><th>Allowed Tools</th><th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function _render() {
  const container = el('registry-content');
  if (!container) return;
  container.innerHTML = '<div class="admin-empty">Loading...</div>';
  try {
    const snapshot = await _fetchDashboard();
    container.innerHTML = _renderTable(snapshot);
    _attachRowListeners();
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load registry: ${esc(e.message)}</div>`;
  }
}

function _attachRowListeners() {
  document.querySelectorAll('.registry-toggle').forEach(input => {
    input.addEventListener('change', async () => {
      const agent = input.dataset.agent;
      const action = input.checked ? 'enable' : 'disable';
      try {
        await _postJSON(`/api/agent-tasks/registry/${encodeURIComponent(agent)}/${action}`);
        uiModule.showToast(`${agent} ${action}d`);
      } catch (e) {
        uiModule.showError(`Failed to ${action} ${agent}: ${e.message}`);
        input.checked = !input.checked; // revert on failure
      }
      _render();
    });
  });

  document.querySelectorAll('.registry-restart-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const agent = btn.dataset.agent;
      try {
        await _postJSON(`/api/agent-tasks/restart/${encodeURIComponent(agent)}`);
        uiModule.showToast(`Restart requested for ${agent}`);
      } catch (e) {
        uiModule.showError(`Failed to request restart for ${agent}: ${e.message}`);
      }
    });
  });
}

export function openPanel() {
  const modal = el('registry-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
}

function _closePanel() {
  const modal = el('registry-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
}

function _ensureRegistered() {
  if (Modals.isRegistered('registry-modal')) return;
  Modals.register('registry-modal', {
    railBtnId: 'rail-registry',
    sidebarBtnId: 'tool-registry-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

function _enableDrag() {
  const modal = el('registry-modal');
  if (!modal) return;
  const content = modal.querySelector('.registry-modal-content');
  const header = modal.querySelector('.modal-header');
  if (!content || !header) return;
  makeWindowDraggable(modal, { content, header });
}

export function init() {
  _ensureRegistered();
  _enableDrag();

  const toolBtn = el('tool-registry-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-registry-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
