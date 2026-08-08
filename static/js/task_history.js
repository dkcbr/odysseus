// static/js/task_history.js — Task History panel (ES6)
// Real, DB-backed history from /api/agent-tasks/history-db -- reads the
// actual tasks + task_events tables (Phase 2 dual-write), matching the
// exact schema that's really running: updated_at + result (no fabricated
// claimed_at/completed_at/error columns), real agent/server values
// (browser_agent/filesystem_agent, jarvis_browser/filesystem/tradingview),
// no "machine" filter (doesn't exist in the schema).

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;
let _selectedTaskId = null;
let _lastData = { tasks: [], events: [] };

async function _fetchHistory() {
  const res = await fetch('/api/agent-tasks/history-db?limit=200', { credentials: 'same-origin' });
  if (res.status === 401 || res.status === 403) throw new Error('Access denied');
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _statusColor(status) {
  if (status === 'success') return 'var(--color-success, #2ecc71)';
  if (status === 'failed') return 'var(--red, #e74c3c)';
  if (status === 'running') return '#e6b800';
  return 'var(--fg)'; // pending
}

function _matchesFilters(task) {
  const status = el('task-history-filter-status')?.value || '';
  const agent = el('task-history-filter-agent')?.value || '';
  const server = (el('task-history-filter-server')?.value || '').toLowerCase();
  const tool = (el('task-history-filter-tool')?.value || '').toLowerCase();

  if (status && task.status !== status) return false;
  if (agent && task.agent !== agent) return false;
  if (server && !(task.server || '').toLowerCase().includes(server)) return false;
  if (tool && !(task.tool || '').toLowerCase().includes(tool)) return false;
  return true;
}

function _renderTaskList() {
  const container = el('task-history-list');
  if (!container) return;
  const _scrollTop = container.scrollTop; // preserve across the periodic re-render

  const filtered = _lastData.tasks.filter(_matchesFilters);
  if (!filtered.length) {
    container.innerHTML = '<div class="admin-empty">No tasks match the current filters.</div>';
    return;
  }

  container.innerHTML = filtered.map(t => `
    <div class="task-history-row" data-task-id="${esc(t.id)}" style="padding:6px 4px;border-bottom:1px solid var(--border);cursor:pointer;${t.id === _selectedTaskId ? 'background:color-mix(in srgb, var(--fg) 6%, transparent);' : ''}">
      <div style="display:flex;justify-content:space-between;">
        <span style="font-weight:600;">${esc(t.id)}</span>
        <span style="color:${_statusColor(t.status)};font-size:11px;font-weight:600;">${esc((t.status || '').toUpperCase())}</span>
      </div>
      <div style="font-size:11px;opacity:0.7;">${esc(t.agent)} \u2192 ${esc(t.server)}.${esc(t.tool)}</div>
    </div>
  `).join('');

  container.querySelectorAll('.task-history-row').forEach(row => {
    row.addEventListener('click', () => {
      _selectedTaskId = row.dataset.taskId;
      _renderTaskList();
      _renderTimeline();
    });
  });

  container.scrollTop = _scrollTop;
}

function _renderTimeline() {
  const container = el('task-history-timeline');
  if (!container) return;
  const _scrollTop = container.scrollTop;

  if (!_selectedTaskId) {
    container.innerHTML = 'Select a task to see its timeline.';
    return;
  }

  const events = _lastData.events
    .filter(e => e.task_id === _selectedTaskId)
    .sort((a, b) => a.ts - b.ts);

  if (!events.length) {
    container.innerHTML = '<div class="admin-empty">No events found for this task.</div>';
    return;
  }

  container.innerHTML = events.map(e => `
    <div style="padding:8px 0;border-bottom:1px solid var(--border);">
      <div style="display:flex;justify-content:space-between;">
        <span style="font-weight:600;">${esc(e.event_type)}</span>
        <span style="color:${_statusColor(e.status)};font-size:11px;font-weight:600;">${esc((e.status || '').toUpperCase())}</span>
      </div>
      <div style="font-size:11px;opacity:0.7;">${new Date(e.ts * 1000).toLocaleTimeString()} | retry ${e.retry_count ?? 0} | ${esc(e.agent)} \u2192 ${esc(e.server)}.${esc(e.tool)}</div>
      ${e.arguments && Object.keys(e.arguments).length ? `<pre style="font-size:10px;margin-top:4px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;">args: ${esc(JSON.stringify(e.arguments))}</pre>` : ''}
      ${e.result ? `<pre style="font-size:10px;margin-top:4px;background:color-mix(in srgb, var(--fg) 5%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;">${esc(JSON.stringify(e.result, null, 2))}</pre>` : ''}
    </div>
  `).join('');

  container.scrollTop = _scrollTop;
}

async function _render() {
  try {
    _lastData = await _fetchHistory();
    _renderTaskList();
    _renderTimeline();
  } catch (e) {
    const container = el('task-history-list');
    if (container) container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('task-history-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 5000);
}

function _closePanel() {
  const modal = el('task-history-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('task-history-modal')) return;
  Modals.register('task-history-modal', {
    railBtnId: 'rail-task-history',
    sidebarBtnId: 'tool-task-history-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-task-history-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-task-history-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }

  ['task-history-filter-status', 'task-history-filter-agent', 'task-history-filter-server', 'task-history-filter-tool'].forEach(id => {
    const filterEl = el(id);
    if (filterEl) {
      filterEl.addEventListener('input', _renderTaskList);
      filterEl.addEventListener('change', _renderTaskList);
    }
  });
}

export default { init, openPanel };
