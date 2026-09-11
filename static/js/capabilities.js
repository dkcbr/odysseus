// static/js/capabilities.js — Capability Inspector panel (ES6)
// Reverse view of the agent registry: tool -> agents, server -> agents.
// Reuses the same single /api/agent-tasks/dashboard fetch as registry.js
// (already contains full registry + capability data), same static-shell
// modal pattern as registry.js -- no new backend endpoint needed.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;

async function _fetchDashboard() {
  const res = await fetch('/api/agent-tasks/dashboard', { credentials: 'same-origin' });
  if (res.status === 401 || res.status === 403) throw new Error('Access denied');
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _buildReverseMaps(registry) {
  const toolToAgents = {};
  const serverToAgents = {};

  for (const [agentName, reg] of Object.entries(registry)) {
    for (const tool of (reg.allowed_tools || [])) {
      if (!toolToAgents[tool]) toolToAgents[tool] = [];
      toolToAgents[tool].push(agentName);
    }
    for (const server of (reg.servers || [])) {
      if (!serverToAgents[server]) serverToAgents[server] = [];
      serverToAgents[server].push(agentName);
    }
  }

  return { toolToAgents, serverToAgents };
}

function _renderMapTable(title, map) {
  const keys = Object.keys(map).sort();
  if (keys.length === 0) {
    return `<h4 style="font-size:12px;opacity:0.6;margin:12px 0 6px;">${esc(title)}</h4><div class="admin-empty">None.</div>`;
  }
  const rows = keys.map(key => `
    <tr>
      <td>${esc(key)}</td>
      <td>${esc(map[key].join(', '))}</td>
    </tr>`).join('');

  return `
    <h4 style="font-size:12px;opacity:0.6;margin:12px 0 6px;">${esc(title)}</h4>
    <table class="capabilities-table" style="width:100%;border-collapse:collapse;">
      <tbody>${rows}</tbody>
    </table>`;
}

async function _render() {
  const container = el('capabilities-content');
  if (!container) return;
  container.innerHTML = '<div class="admin-empty">Loading...</div>';
  try {
    const snapshot = await _fetchDashboard();
    const registry = snapshot.registry || {};
    const { toolToAgents, serverToAgents } = _buildReverseMaps(registry);
    container.innerHTML =
      _renderMapTable('Tool \u2192 Agents', toolToAgents) +
      _renderMapTable('Server \u2192 Agents', serverToAgents);
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load capabilities: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('capabilities-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
}

function _closePanel() {
  const modal = el('capabilities-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
}

function _ensureRegistered() {
  if (Modals.isRegistered('capabilities-modal')) return;
  Modals.register('capabilities-modal', {
    railBtnId: 'rail-capabilities',
    sidebarBtnId: 'tool-capabilities-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

function _enableDrag() {
  const modal = el('capabilities-modal');
  if (!modal) return;
  const content = modal.querySelector('.capabilities-modal-content');
  const header = modal.querySelector('.modal-header');
  if (!content || !header) return;
  makeWindowDraggable(modal, { content, header });
}

export function init() {
  _ensureRegistered();
  _enableDrag();

  const toolBtn = el('tool-capabilities-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-capabilities-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
