// static/js/worker_log.js — Worker Log panel (ES6)
// Real, structured worker logs (tool_start/tool_end with duration_ms),
// read from the real /api/agent-tasks/worker-logs/<agent> endpoint, which
// itself reads the real JSON-lines files agent_worker.py writes.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

function _formatDuration(ms) {
  if (ms == null) return '';
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function _outcomeColor(outcome) {
  if (outcome === 'success') return 'var(--color-success, #2ecc71)';
  if (outcome === 'failed' || outcome === 'exception') return 'var(--red, #e74c3c)';
  if (outcome === 'blocked') return '#e6b800';
  return 'var(--fg)'; // tool_start has no outcome yet
}

let _copyIdCounter = 0;

function _copyButtonHtml(targetId) {
  return `<button class="worker-log-copy-btn" data-copy-target="${esc(targetId)}" title="Copy to clipboard" style="font-size:9px;padding:1px 6px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--fg);opacity:0.6;cursor:pointer;">Copy</button>`;
}

function _wireCopyButtons(container) {
  container.querySelectorAll('.worker-log-copy-btn').forEach(btn => {
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

async function _fetchLogs() {
  const agent = el('worker-log-filter-agent')?.value || 'filesystem_agent';
  const phase = el('worker-log-filter-phase')?.value || '';
  const outcome = el('worker-log-filter-outcome')?.value || '';

  const params = new URLSearchParams();
  if (phase) params.set('phase', phase);
  if (outcome) params.set('outcome', outcome);

  const res = await fetch(`/api/agent-tasks/worker-logs/${encodeURIComponent(agent)}?${params}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _renderSlowestCalls(logs) {
  const ended = logs.filter(l => l.phase === 'tool_end' && l.duration_ms != null);
  if (!ended.length) return '';
  const slowest = [...ended].sort((a, b) => b.duration_ms - a.duration_ms).slice(0, 5);
  return `
    <div style="margin-bottom:12px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:4px;">Slowest Recent Tool Calls</div>
      ${slowest.map(l => `
        <div style="display:flex;justify-content:space-between;font-size:11px;padding:3px 0;border-bottom:1px solid var(--border);">
          <span>${esc(l.task_id)}</span>
          <span style="color:${_outcomeColor(l.outcome)};font-weight:600;">${_formatDuration(l.duration_ms)}</span>
        </div>
      `).join('')}
    </div>
  `;
}

function _renderLogList(logs) {
  if (!logs.length) {
    return '<div class="admin-empty">No log entries yet for this agent/filter.</div>';
  }
  return logs.map(l => {
    const argsId = l.arguments ? `worker-log-copy-target-${++_copyIdCounter}` : null;
    return `
    <div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:11px;">
      <div style="display:flex;justify-content:space-between;">
        <span style="font-weight:600;">${esc(l.phase)}</span>
        <span style="color:${_outcomeColor(l.outcome)};font-weight:600;">${l.outcome ? esc(l.outcome.toUpperCase()) : ''}${l.duration_ms != null ? ' \u00b7 ' + _formatDuration(l.duration_ms) : ''}</span>
      </div>
      <div style="opacity:0.7;">${new Date(l.ts * 1000).toLocaleTimeString()} | task ${esc(l.task_id)}${l.tool ? ` | ${esc(l.server)}.${esc(l.tool)}` : ''}</div>
      ${argsId ? `<div style="display:flex;justify-content:space-between;align-items:center;margin-top:3px;"><span style="font-size:9px;opacity:0.5;">args</span>${_copyButtonHtml(argsId)}</div><pre id="${argsId}" style="font-size:10px;margin-top:2px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:3px;border-radius:3px;white-space:pre-wrap;">${esc(JSON.stringify(l.arguments))}</pre>` : ''}
      ${l.error ? `<div style="color:var(--red, #e74c3c);margin-top:2px;">${esc(l.error)}</div>` : ''}
      ${l.stderr ? `<pre style="font-size:10px;margin-top:3px;background:color-mix(in srgb, var(--red, #e74c3c) 8%, transparent);padding:3px;border-radius:3px;white-space:pre-wrap;">${esc(l.stderr)}</pre>` : ''}
    </div>
  `;
  }).join('');
}

async function _render() {
  const container = el('worker-log-content');
  if (!container) return;
  try {
    const data = await _fetchLogs();
    container.innerHTML = _renderSlowestCalls(data.logs) +
      `<div style="font-weight:600;font-size:11px;margin-bottom:4px;">Recent Activity (${data.count})</div>` +
      _renderLogList(data.logs);
    _wireCopyButtons(container);
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('worker-log-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 5000);
}

function _closePanel() {
  const modal = el('worker-log-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('worker-log-modal')) return;
  Modals.register('worker-log-modal', {
    railBtnId: 'rail-worker-log',
    sidebarBtnId: 'tool-worker-log-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-worker-log-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-worker-log-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  ['worker-log-filter-agent', 'worker-log-filter-phase', 'worker-log-filter-outcome'].forEach(id => {
    const filterEl = el(id);
    if (filterEl) filterEl.addEventListener('change', _render);
  });
}

export default { init, openPanel };
