// static/js/notifications_drawer.js — Notifications Drawer (ES6)
// Real, plain drawer overlay reading from the real notifications_store.js.
// Field names match the actual store exactly: id, ts (seconds, not
// timestamp), level (not type), message, agents (array), servers
// (array). No diff/worker fields -- those don't exist in the real
// store, so they're not rendered.

import { getNotifications, getNotificationsFiltered, clearNotifications, getAllAgents, getAllServers, getLevelCounts, getAgentCounts, getServerCounts } from './notifications_store.js';

const LEVEL_COLORS = { info: '#4ad4e8', warning: '#ff8c3a', error: '#ff4a5e' };

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _expandedId = null;
let _levelFilter = 'all'; // real values: 'all' | 'info' | 'warning' | 'error' -- matches the actual LEVEL_COLORS keys in notifications.js
let _agentFilter = null;
let _serverFilter = null;

function _renderFilterBar() {
  const levels = ['all', 'info', 'warning', 'error'];
  const levelCounts = getLevelCounts();
  const agents = getAllAgents();
  const servers = getAllServers();
  const agentCounts = getAgentCounts();
  const serverCounts = getServerCounts();
  return `
    <div style="padding:8px;border-bottom:1px solid #1a3a4a;">
      <div style="display:flex;gap:4px;margin-bottom:6px;">
        ${levels.map(lvl => `
          <button class="notif-level-filter-btn" data-level="${lvl}" style="font-size:10px;padding:3px 7px;background:${_levelFilter === lvl ? '#1a3a4a' : 'transparent'};border:1px solid #1a3a4a;color:${_levelFilter === lvl ? '#e6f7ff' : '#7a9aab'};border-radius:3px;cursor:pointer;">${lvl.toUpperCase()} <span style="opacity:0.7;">${levelCounts[lvl]}</span></button>
        `).join('')}
      </div>
      <div style="display:flex;gap:6px;">
        <select id="notif-agent-filter" style="font-size:10px;flex:1;background:#08111c;color:#7a9aab;border:1px solid #1a3a4a;border-radius:3px;">
          <option value="">All agents</option>
          ${agents.map(a => `<option value="${esc(a)}" ${_agentFilter === a ? 'selected' : ''}>${esc(a)} (${agentCounts.get(a) || 0})</option>`).join('')}
        </select>
        <select id="notif-server-filter" style="font-size:10px;flex:1;background:#08111c;color:#7a9aab;border:1px solid #1a3a4a;border-radius:3px;">
          <option value="">All servers</option>
          ${servers.map(s => `<option value="${esc(s)}" ${_serverFilter === s ? 'selected' : ''}>${esc(s)} (${serverCounts.get(s) || 0})</option>`).join('')}
        </select>
      </div>
    </div>
  `;
}

function _renderList() {
  const list = el('notifications-drawer-list');
  if (!list) return;
  const source = getNotificationsFiltered({
    level: _levelFilter === 'all' ? undefined : _levelFilter,
    agent: _agentFilter || undefined,
    server: _serverFilter || undefined,
  });
  const alerts = source.slice().reverse(); // newest first
  if (!alerts.length) {
    const emptyMsg = getNotifications().length === 0 ? 'No notifications yet this session.' : 'No notifications match this filter.';
    list.innerHTML = `<div style="font-size:11px;color:#7a9aab;padding:8px;">${emptyMsg}</div>`;
    return;
  }
  list.innerHTML = alerts.map(a => {
    const color = LEVEL_COLORS[a.level] || '#7a9aab';
    const time = new Date(a.ts * 1000).toLocaleTimeString();
    return `
      <div class="notif-drawer-item" data-id="${a.id}" style="border:1px solid #1a3a4a;border-radius:4px;padding:8px;margin-bottom:6px;cursor:pointer;">
        <div style="display:flex;align-items:center;gap:6px;">
          <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${color};"></span>
          <span style="font-size:10px;color:#7a9aab;">${esc(time)}</span>
        </div>
        <div style="font-size:11px;margin-top:3px;">${esc(a.message)}</div>
      </div>
    `;
  }).join('');

  list.querySelectorAll('.notif-drawer-item').forEach(item => {
    item.addEventListener('click', () => {
      const id = Number(item.dataset.id);
      _expandedId = _expandedId === id ? null : id;
      _renderExpanded();
    });
  });
}

function _renderExpanded() {
  const box = el('notifications-drawer-expanded');
  if (!box) return;
  if (_expandedId == null) { box.style.display = 'none'; return; }
  const alert = getNotifications().find(a => a.id === _expandedId);
  if (!alert) { box.style.display = 'none'; return; }

  const color = LEVEL_COLORS[alert.level] || '#7a9aab';
  box.style.display = 'block';
  box.innerHTML = `
    <div style="color:${color};font-weight:600;text-transform:uppercase;font-size:10px;">${esc(alert.level)}</div>
    <div style="margin-top:4px;">${esc(alert.message)}</div>
    ${alert.agents.length ? `<div style="margin-top:4px;color:#7a9aab;">Agents: ${esc(alert.agents.join(', '))}</div>` : ''}
    ${alert.servers.length ? `<div style="margin-top:2px;color:#7a9aab;">Servers: ${esc(alert.servers.join(', '))}</div>` : ''}
  `;
}

function _wireFilterBar() {
  document.querySelectorAll('.notif-level-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _levelFilter = btn.dataset.level;
      _renderFilterBarInPlace();
      _renderList();
    });
  });
  const agentSel = el('notif-agent-filter');
  if (agentSel) agentSel.addEventListener('change', () => { _agentFilter = agentSel.value || null; _renderList(); });
  const serverSel = el('notif-server-filter');
  if (serverSel) serverSel.addEventListener('change', () => { _serverFilter = serverSel.value || null; _renderList(); });
}

function _renderFilterBarInPlace() {
  const holder = el('notifications-drawer-filterbar');
  if (holder) holder.innerHTML = _renderFilterBar();
  _wireFilterBar();
}

export function openDrawer() {
  const drawer = el('notifications-drawer');
  if (!drawer) return;
  drawer.style.display = 'flex';
  _renderFilterBarInPlace();
  _renderList();
  _renderExpanded();
}

function _closeDrawer() {
  const drawer = el('notifications-drawer');
  if (drawer) drawer.style.display = 'none';
}

export function init() {
  const btn = el('tool-notifications-drawer-btn');
  if (btn) btn.addEventListener('click', openDrawer);

  const closeBtn = el('notifications-drawer-close');
  if (closeBtn) closeBtn.addEventListener('click', _closeDrawer);

  const clearBtn = el('notifications-drawer-clear');
  if (clearBtn) clearBtn.addEventListener('click', () => {
    clearNotifications();
    _expandedId = null;
    _renderList();
    _renderExpanded();
  });

  const exportBtn = el('notifications-drawer-export');
  if (exportBtn) exportBtn.addEventListener('click', () => {
    const data = getNotifications();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `jarvis-notifications-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });
}

export default { init, openDrawer };
