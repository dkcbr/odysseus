// static/js/task_queue_inspector.js — Task Queue Inspector panel (ES6)
// Real, live view of the task queue itself: pending/running/failed/success
// counts and lists, per-agent breakdown, and pending-task aging. Reuses
// the existing, already-tested /api/agent-tasks/queue endpoint (built in
// Phase 3) -- no new backend, pure UI addition.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

function _formatDuration(seconds) {
  if (seconds == null || seconds < 0) return '';
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}m ${secs}s`;
}

function _statusColor(status) {
  if (status === 'success') return 'var(--color-success, #2ecc71)';
  if (status === 'failed') return 'var(--red, #e74c3c)';
  if (status === 'running') return '#e6b800';
  return 'var(--fg)'; // pending
}

async function _fetchQueue() {
  const res = await fetch('/api/agent-tasks/queue', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

async function _fetchThroughput() {
  const res = await fetch('/api/agent-tasks/throughput?bucket_minutes=15&hours=24', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

async function _fetchRecentActivity() {
  const res = await fetch('/api/agent-tasks/history-db?limit=10', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

async function _callSystemAgentTool(tool) {
  // Real, direct MCP call -- NOT routed through the task queue. These are
  // fast, read-only diagnostic checks; queueing them would add claim/poll
  // delay for something that should be instant, unlike real automation tasks.
  const res = await fetch('/api/mcp/call', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ server: 'jarvis_system', tool, arguments: {} }),
  });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _renderRecentActivity(historyData) {
  const tasks = (historyData.tasks || []).slice(0, 10);
  if (!tasks.length) {
    return `<div style="margin-bottom:12px;"><div style="font-weight:600;font-size:11px;margin-bottom:4px;">Recent Activity</div><div style="font-size:11px;opacity:0.5;">No tasks yet.</div></div>`;
  }
  const rows = tasks.map(t => {
    const durationSec = (t.updated_at != null && t.created_at != null) ? (t.updated_at - t.created_at) : null;
    return `
      <div style="display:flex;justify-content:space-between;padding:3px 0;font-size:11px;border-bottom:1px solid var(--border);">
        <span>${esc(t.agent)}.${esc(t.tool)}</span>
        <span style="color:${_statusColor(t.status)};">${esc(t.status)}${durationSec != null ? ' \u00b7 ' + _formatDuration(durationSec) : ''}</span>
      </div>
    `;
  }).join('');
  return `
    <div style="margin-bottom:12px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:4px;">Recent Activity (last 10, all agents)</div>
      ${rows}
    </div>
  `;
}

function _renderSummaryBar(data, tp, uptimeText) {
  const loadMatch = uptimeText ? uptimeText.match(/load average:\s*([\d.]+)/) : null;
  const loadAvg = loadMatch ? loadMatch[1] : null;
  return `
    <div style="font-size:11px;opacity:0.8;margin-bottom:8px;padding:4px 0;border-bottom:1px solid var(--border);">
      Summary: Pending ${data.pending.length} \u2022 Running ${data.running.length} \u2022 Completed (24h) ${tp.total_completed} \u2022 Throughput ${(tp.avg_per_hour / 60).toFixed(2)}/min${loadAvg ? ` \u2022 Load avg: ${loadAvg}` : ''}
    </div>
  `;
}

function _renderSystemHealth(uptimeText) {
  return `
    <div style="margin-bottom:12px;padding:8px;border:1px solid var(--border);border-radius:4px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:4px;">System Health</div>
      <div style="font-size:11px;font-family:monospace;white-space:pre-wrap;">${esc(uptimeText || 'Loading...')}</div>
    </div>
  `;
}

function _renderDiagnosticsButtons() {
  return `
    <div style="margin-bottom:12px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:6px;">Diagnostics</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;">
        <button class="diag-btn" data-tool="get_system_uptime" style="font-size:11px;padding:4px 8px;">System Uptime</button>
        <button class="diag-btn" data-tool="check_worker_status" style="font-size:11px;padding:4px 8px;">Worker Status</button>
        <button class="diag-btn" data-tool="get_disk_usage" style="font-size:11px;padding:4px 8px;">Disk Usage</button>
        <button class="diag-btn" data-tool="get_process_list" style="font-size:11px;padding:4px 8px;">Process List</button>
      </div>
      <pre id="diag-output" style="font-size:10px;margin-top:6px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:6px;border-radius:3px;white-space:pre-wrap;min-height:1em;"></pre>
    </div>
  `;
}

function _wireDiagnosticsButtons(container) {
  container.querySelectorAll('.diag-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const output = el('diag-output');
      if (output) output.textContent = 'Running...';
      try {
        const result = await _callSystemAgentTool(btn.dataset.tool);
        if (output) output.textContent = result.stdout || result.error || JSON.stringify(result);
      } catch (e) {
        if (output) output.textContent = `Error: ${e.message}`;
      }
    });
  });
}

function _renderThroughput(tp) {
  if (!tp.series.length) {
    return `<div style="margin-bottom:12px;"><div style="font-weight:600;font-size:11px;margin-bottom:4px;">Throughput (last 24h)</div><div style="font-size:11px;opacity:0.5;">No completed tasks in this window yet.</div></div>`;
  }
  const maxCount = Math.max(...tp.series.map(b => b.success + b.failed), 1);
  const bars = tp.series.map(b => {
    const total = b.success + b.failed;
    const heightPct = Math.max((total / maxCount) * 100, total > 0 ? 8 : 0);
    const failedPct = total > 0 ? (b.failed / total) * 100 : 0;
    const label = new Date(b.bucket_start * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return `
      <div title="${label}: ${b.success} success, ${b.failed} failed" style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:60px;min-width:4px;">
        <div style="height:${heightPct}%;background:linear-gradient(to top, var(--red, #e74c3c) ${failedPct}%, var(--color-success, #2ecc71) ${failedPct}%);border-radius:2px 2px 0 0;"></div>
      </div>
    `;
  }).join('');

  return `
    <div style="margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-weight:600;font-size:11px;">Throughput (last 24h, 15min buckets)</span>
        <span style="font-size:11px;opacity:0.7;">${tp.total_completed} completed \u00b7 ${tp.avg_per_hour}/hr avg</span>
      </div>
      <div style="display:flex;align-items:flex-end;gap:1px;height:60px;border:1px solid var(--border);border-radius:4px;padding:4px;">
        ${bars}
      </div>
    </div>
  `;
}

function _renderCountsBar(data, uptimeText) {
  const counts = [
    { label: 'PENDING', n: data.pending.length, color: _statusColor('pending') },
    { label: 'RUNNING', n: data.running.length, color: _statusColor('running') },
    { label: 'FAILED', n: data.failed.length, color: _statusColor('failed') },
    { label: 'SUCCESS', n: data.success.length, color: _statusColor('success') },
  ];
  return `
    <div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">
      ${counts.map(c => `
        <div style="flex:1;min-width:80px;padding:8px;border:1px solid var(--border);border-radius:4px;text-align:center;">
          <div style="font-size:20px;font-weight:700;color:${c.color};">${c.n}</div>
          <div style="font-size:10px;opacity:0.6;">${c.label}</div>
        </div>
      `).join('')}
      <div style="flex:1;min-width:80px;padding:8px;border:1px solid var(--border);border-radius:4px;text-align:center;">
        <div style="font-size:20px;font-weight:700;">${data.total}</div>
        <div style="font-size:10px;opacity:0.6;">TOTAL (ALL-TIME)</div>
      </div>
    </div>
  `;
}

function _renderAgentBreakdown(data) {
  const agents = Object.keys(data.registry || {});
  if (!agents.length) return '';

  const rows = agents.map(agent => {
    const reg = data.registry[agent];
    const health = (data.agents || {})[agent];
    const pendingCount = data.pending.filter(t => t.agent === agent).length;
    const runningCount = data.running.filter(t => t.agent === agent).length;
    const healthStatus = health ? health.status : 'no heartbeat yet';
    const healthColor = health && health.status === 'alive' ? 'var(--color-success, #2ecc71)' : 'var(--red, #e74c3c)';
    const heartbeatDetail = health ? ` (${health.seconds_since_heartbeat.toFixed(1)}s ago)` : '';

    return `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);">
        <div>
          <span style="font-weight:600;">${esc(agent)}</span>
          <span style="font-size:10px;opacity:0.6;margin-left:6px;">${reg.enabled ? 'enabled' : 'disabled'}</span>
        </div>
        <div style="display:flex;gap:10px;align-items:center;font-size:11px;">
          <span>pending: ${pendingCount}</span>
          <span>running: ${runningCount}</span>
          <span style="color:${healthColor};">${esc(healthStatus)}${heartbeatDetail}</span>
        </div>
      </div>
    `;
  }).join('');

  return `
    <div style="margin-bottom:12px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:6px;">Per-Agent Load</div>
      ${rows}
    </div>
  `;
}

function _renderTaskListSection(title, tasks, showAging) {
  if (!tasks.length) {
    return `<div style="margin-bottom:12px;"><div style="font-weight:600;font-size:11px;margin-bottom:4px;">${esc(title)}</div><div style="font-size:11px;opacity:0.5;">None</div></div>`;
  }
  const now = Date.now() / 1000;
  const rows = tasks.map(t => {
    const age = showAging && t.created_at ? now - t.created_at : null;
    return `
      <div style="display:flex;justify-content:space-between;padding:4px 0;font-size:11px;border-bottom:1px solid var(--border);">
        <span>${esc(t.name || t.id)} <span style="opacity:0.5;">${esc(t.agent)} \u2192 ${esc(t.server)}.${esc(t.tool)}</span></span>
        ${age != null ? `<span style="opacity:0.7;">waiting ${_formatDuration(age)}</span>` : ''}
      </div>
    `;
  }).join('');
  return `
    <div style="margin-bottom:12px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:4px;">${esc(title)} (${tasks.length})</div>
      ${rows}
    </div>
  `;
}

let _lastUptimeText = null;
let _lastUptimeFetchTime = 0;
const UPTIME_REFRESH_INTERVAL_MS = 30000; // uptime/load genuinely changes -- refresh periodically, but not on every 5s panel tick

async function _render() {
  const container = el('task-queue-inspector-content');
  if (!container) return;
  try {
    const [data, throughput, recentActivity] = await Promise.all([_fetchQueue(), _fetchThroughput(), _fetchRecentActivity()]);

    // Uptime fetched separately/non-blocking -- a slow/failed diagnostic
    // call shouldn't prevent the rest of the panel from rendering. Refreshed
    // periodically (not every 5s tick) since it's real, changing data, not
    // static config.
    const now = Date.now();
    if (_lastUptimeText === null || (now - _lastUptimeFetchTime) > UPTIME_REFRESH_INTERVAL_MS) {
      _lastUptimeFetchTime = now;
      _callSystemAgentTool('get_system_uptime').then(r => {
        _lastUptimeText = r.stdout || r.error || 'Unavailable';
        _render();
      }).catch(() => { _lastUptimeText = 'Unavailable'; });
    }

    container.innerHTML =
      _renderSummaryBar(data, throughput, _lastUptimeText) +
      _renderSystemHealth(_lastUptimeText) +
      _renderCountsBar(data) +
      _renderThroughput(throughput) +
      _renderRecentActivity(recentActivity) +
      _renderAgentBreakdown(data) +
      _renderDiagnosticsButtons() +
      _renderTaskListSection('Pending (queue depth + aging)', data.pending, true) +
      _renderTaskListSection('Running', data.running, false);
    _wireDiagnosticsButtons(container);
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('task-queue-inspector-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 5000);
}

function _closePanel() {
  const modal = el('task-queue-inspector-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('task-queue-inspector-modal')) return;
  Modals.register('task-queue-inspector-modal', {
    railBtnId: 'rail-task-queue-inspector',
    sidebarBtnId: 'tool-task-queue-inspector-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-task-queue-inspector-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-task-queue-inspector-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
