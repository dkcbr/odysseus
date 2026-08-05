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
let _timelineRenderToken = 0;
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

function _formatDuration(seconds) {
  if (seconds == null || seconds < 0) return '';
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

function _agentLinkHtml(agent) {
  // Task_agent_link: clickable agent name -> opens the real, existing
  // Agent Registry panel (tool-registry-btn), not a new panel of our own.
  return `<span class="task-history-agent-link" data-agent="${esc(agent)}" style="text-decoration:underline;cursor:pointer;" title="View in Agent Registry">${esc(agent)}</span>`;
}

function _wireAgentLinks(container) {
  container.querySelectorAll('.task-history-agent-link').forEach(span => {
    span.addEventListener('click', (ev) => {
      ev.stopPropagation(); // don't also trigger the parent row's task-select click
      document.getElementById('tool-registry-btn')?.click();
    });
  });
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

  container.innerHTML = filtered.map(t => {
    const durationSec = (t.updated_at != null && t.created_at != null) ? (t.updated_at - t.created_at) : null;
    return `
    <div class="task-history-row" data-task-id="${esc(t.id)}" style="padding:6px 4px;border-bottom:1px solid var(--border);cursor:pointer;${t.id === _selectedTaskId ? 'background:color-mix(in srgb, var(--fg) 6%, transparent);' : ''}">
      <div style="display:flex;justify-content:space-between;">
        <span style="font-weight:600;">${t.name ? esc(t.name) : esc(t.id)}</span>
        <span style="display:flex;gap:8px;align-items:center;">
          ${durationSec != null ? `<span style="font-size:10px;opacity:0.6;">${_formatDuration(durationSec)}</span>` : ''}
          <span style="color:${_statusColor(t.status)};font-size:11px;font-weight:600;">${esc((t.status || '').toUpperCase())}</span>
        </span>
      </div>
      ${t.name ? `<div style="font-size:10px;opacity:0.5;">${esc(t.id)}</div>` : ''}
      <div style="font-size:11px;opacity:0.7;">${_agentLinkHtml(t.agent)} \u2192 ${esc(t.server)}.${esc(t.tool)}</div>
    </div>
  `;
  }).join('');

  container.querySelectorAll('.task-history-row').forEach(row => {
    row.addEventListener('click', () => {
      _selectedTaskId = row.dataset.taskId;
      _renderTaskList();
      _renderTimeline();
    });
  });
  _wireAgentLinks(container);

  container.scrollTop = _scrollTop;
}

function _taskEventBadge() {
  return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:color-mix(in srgb, #3498db 20%, transparent);color:#3498db;font-weight:600;">TASK</span>`;
}

function _memoryEventBadge() {
  return `<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:color-mix(in srgb, var(--color-success, #2ecc71) 20%, transparent);color:var(--color-success, #2ecc71);font-weight:600;">MEMORY</span>`;
}

function _copyButtonHtml(targetId) {
  return `<button class="task-history-copy-btn" data-copy-target="${esc(targetId)}" title="Copy to clipboard" style="font-size:9px;padding:1px 6px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--fg);opacity:0.6;cursor:pointer;">Copy</button>`;
}

function _wireCopyButtons(container) {
  container.querySelectorAll('.task-history-copy-btn').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      const targetEl = document.getElementById(btn.dataset.copyTarget);
      if (!targetEl) return;
      try {
        await navigator.clipboard.writeText(targetEl.textContent);
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = original; }, 1200);
      } catch (e) {
        // Clipboard API can fail on non-HTTPS/non-localhost origins -- fail silently, button just won't confirm.
      }
    });
  });
}

let _copyIdCounter = 0;

function _renderTaskEventItem(e, sincePrevSec) {
  const argsId = e.arguments && Object.keys(e.arguments).length ? `copy-target-${++_copyIdCounter}` : null;
  const resultId = e.result ? `copy-target-${++_copyIdCounter}` : null;
  return `
    <div style="padding:8px 0;border-bottom:1px solid var(--border);">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="display:flex;gap:6px;align-items:center;">${_taskEventBadge()}<span style="font-weight:600;">${esc(e.event_type)}</span></span>
        <span style="color:${_statusColor(e.status)};font-size:11px;font-weight:600;">${esc((e.status || '').toUpperCase())}</span>
      </div>
      <div style="font-size:11px;opacity:0.7;">${new Date(e.ts * 1000).toLocaleTimeString()}${sincePrevSec != null ? ` (+${_formatDuration(sincePrevSec)})` : ''} | retry ${e.retry_count ?? 0} | ${_agentLinkHtml(e.agent)} \u2192 ${esc(e.server)}.${esc(e.tool)}</div>
      ${argsId ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;"><span style="font-size:9px;opacity:0.5;">args</span>${_copyButtonHtml(argsId)}</div><pre id="${argsId}" style="font-size:10px;margin-top:2px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;">${esc(JSON.stringify(e.arguments))}</pre>` : ''}
      ${resultId ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;"><span style="font-size:9px;opacity:0.5;">result</span>${_copyButtonHtml(resultId)}</div><pre id="${resultId}" style="font-size:10px;margin-top:2px;background:color-mix(in srgb, var(--fg) 5%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;">${esc(JSON.stringify(e.result, null, 2))}</pre>` : ''}
    </div>
  `;
}

function _renderMemoryEventItem(m, sincePrevSec) {
  const text = m.text || '';
  const truncated = text.length > 100 ? text.slice(0, 100) + '...' : text;
  const cat = m.category || 'fact';
  // Memory_category_badges: reuses the real, existing .memory-cat-* CSS
  // classes from the Memory panel itself (style.css) for pixel-perfect
  // consistency, rather than inventing new colors -- covers all 7 real
  // categories from memory.js's own dropdown (fact/event fall back to the
  // base .memory-cat-badge gray, the other 5 get their real distinct color).
  return `
    <div class="task-history-memory-link" data-memory-id="${esc(m.id)}" style="padding:8px 0;border-bottom:1px solid var(--border);cursor:pointer;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <span style="display:flex;gap:6px;align-items:center;">${_memoryEventBadge()}<span class="memory-cat-badge memory-cat-${esc(cat)}">${esc(cat)}</span></span>
      </div>
      <div style="font-size:11px;opacity:0.7;">${new Date((m.timestamp || 0) * 1000).toLocaleTimeString()}${sincePrevSec != null ? ` (+${_formatDuration(sincePrevSec)})` : ''}</div>
      <div style="font-size:11px;text-decoration:underline;margin-top:2px;">"${esc(truncated)}"</div>
    </div>
  `;
}

async function _renderTimeline() {
  const container = el('task-history-timeline');
  if (!container) return;
  const _scrollTop = container.scrollTop;

  if (!_selectedTaskId) {
    container.innerHTML = 'Select a task to see its timeline.';
    return;
  }

  // Guard against overlapping calls (e.g. a click-triggered render racing
  // the 5s auto-refresh timer, or rapid task switching): only the LAST
  // call started is allowed to actually write to the DOM.
  const _taskAtCallTime = _selectedTaskId;
  const _renderToken = ++_timelineRenderToken;

  const taskEvents = _lastData.events
    .filter(e => e.task_id === _taskAtCallTime)
    .map(e => ({ type: 'task_event', ts: e.ts, data: e }));

  let memoryEvents = [];
  let memoryFetchError = null;
  try {
    const res = await fetch(`/api/memory/by-task/${encodeURIComponent(_taskAtCallTime)}`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      memoryEvents = (data.memories || []).map(m => ({ type: 'memory_event', ts: m.timestamp, data: m }));
    } else {
      memoryFetchError = `HTTP ${res.status}: ${await res.text().catch(() => '(no body)')}`;
    }
  } catch (e) {
    memoryFetchError = e.message;
  }

  // If a newer _renderTimeline() call (or a task switch) happened while
  // this fetch was in flight, abandon this one -- don't overwrite newer
  // content with a stale response.
  if (_renderToken !== _timelineRenderToken || _taskAtCallTime !== _selectedTaskId) return;

  // Durable_queue_history_bridge: genuinely interleave both streams by
  // real timestamp into one chronological list, rather than two stacked
  // sections. Both timestamps are real Unix seconds (task_events.ts,
  // memory entries' timestamp field), directly comparable.
  const merged = [...taskEvents, ...memoryEvents].sort((a, b) => a.ts - b.ts);

  if (!merged.length) {
    container.innerHTML = '<div class="admin-empty">No events found for this task.</div>';
    return;
  }

  container.innerHTML = merged.map((item, idx) => {
    const sincePrevSec = idx > 0 ? (item.ts - merged[idx - 1].ts) : null;
    return item.type === 'task_event'
      ? _renderTaskEventItem(item.data, sincePrevSec)
      : _renderMemoryEventItem(item.data, sincePrevSec);
  }).join('') + (memoryFetchError ? `<div style="color:var(--red, #e74c3c);font-size:10px;padding:6px 0;">Memory fetch error (debug): ${esc(memoryFetchError)}</div>` : '');

  _wireAgentLinks(container);
  _wireCopyButtons(container);
  container.querySelectorAll('.task-history-memory-link').forEach(el2 => {
    el2.addEventListener('click', () => {
      const memoryId = el2.dataset.memoryId;
      document.getElementById('tool-memory-btn')?.click();
      setTimeout(() => { window.memoryModule?.scrollToMemory?.(memoryId); }, 300);
    });
  });

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

async function _exportTaskTimeline() {
  // Task_history_export: real client-side export, no backend endpoint.
  // Re-fetches memory entries at click-time (rather than relying on any
  // cached state from the last _renderTimeline() call) so the export
  // always reflects current data, not something possibly stale.
  if (!_selectedTaskId) {
    uiModule.showToast ? uiModule.showToast('Select a task first') : alert('Select a task first');
    return;
  }

  const task = _lastData.tasks.find(t => t.id === _selectedTaskId);
  const taskEvents = _lastData.events
    .filter(e => e.task_id === _selectedTaskId)
    .map(e => ({
      type: 'task_event',
      event_type: e.event_type,
      timestamp: new Date(e.ts * 1000).toISOString(),
      status: e.status,
      retry_count: e.retry_count,
      agent: e.agent,
      server: e.server,
      tool: e.tool,
      arguments: e.arguments,
      result: e.result,
    }));

  let memoryEvents = [];
  try {
    const res = await fetch(`/api/memory/by-task/${encodeURIComponent(_selectedTaskId)}`, { credentials: 'same-origin' });
    if (res.ok) {
      const data = await res.json();
      memoryEvents = (data.memories || []).map(m => ({
        type: 'memory_event',
        timestamp: new Date((m.timestamp || 0) * 1000).toISOString(),
        id: m.id,
        category: m.category,
        text: m.text,
      }));
    }
  } catch (e) {
    // Non-fatal -- export still includes task events even if this fetch fails.
  }

  const merged = [...taskEvents, ...memoryEvents].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

  const exportObj = {
    task_id: _selectedTaskId,
    name: task?.name ?? null,
    agent: task?.agent ?? null,
    server: task?.server ?? null,
    tool: task?.tool ?? null,
    status: task?.status ?? null,
    timeline: merged,
  };

  const blob = new Blob([JSON.stringify(exportObj, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `task_${_selectedTaskId}_timeline.json`;
  a.click();
  URL.revokeObjectURL(url);
}

export function openPanel(taskId) {
  const modal = el('task-history-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  if (taskId) _selectedTaskId = taskId;
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

  const exportBtn = el('task-history-export-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', _exportTaskTimeline);
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
