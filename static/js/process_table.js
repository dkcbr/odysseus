// static/js/process_table.js — Process Table panel (ES6)
// Real, unified "OS process manager" view -- pure composition of three
// already-existing, real endpoints (/api/agent-tasks/history-db,
// /api/mcp/servers, /api/agent-tasks/health), no new backend capability.
//
// Deliberately does NOT show per-worker-instance state (e.g. "worker
// market-1 busy") -- each agent has exactly one systemd-supervised worker
// process, not a pool, so there's nothing to distinguish between.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;
let _lastData = null;
let _registryCache = null;

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

async function _fetchAll() {
  const [historyRes, serversRes, healthRes] = await Promise.all([
    fetch('/api/agent-tasks/history-db?limit=100', { credentials: 'same-origin' }),
    fetch('/api/mcp/servers', { credentials: 'same-origin' }),
    fetch('/api/agent-tasks/health', { credentials: 'same-origin' }),
  ]);
  if (!historyRes.ok || !serversRes.ok || !healthRes.ok) {
    throw new Error('One or more real endpoints failed to respond');
  }
  const history = await historyRes.json();
  const servers = await serversRes.json();
  const health = await healthRes.json();
  return { tasks: history.tasks || [], events: history.events || [], servers, health };
}

function _renderTable(data) {
  const { tasks, events, servers, health } = data;

  const statusFilter = el('process-table-filter-status')?.value || '';
  const agentFilter = el('process-table-filter-agent')?.value || '';
  const filteredTasks = tasks.filter(t =>
    (!statusFilter || t.status === statusFilter) &&
    (!agentFilter || t.agent === agentFilter)
  );

  // Real server connection lookup, by name (matches task.server).
  const serverByName = {};
  servers.forEach(s => { serverByName[s.name] = s; });

  // Real last-event-per-task lookup.
  const lastEventByTask = {};
  events.forEach(e => {
    if (!lastEventByTask[e.task_id] || e.ts > lastEventByTask[e.task_id].ts) {
      lastEventByTask[e.task_id] = e;
    }
  });

  if (!filteredTasks.length) {
    return '<div class="admin-empty">No tasks match the current filter.</div>';
  }

  // Real per-agent summary, computed from the exact same real fields
  // already used for individual rows (created_at/updated_at for
  // duration, t.result.error/.stderr for error text -- not invented
  // t.durationMs/t.error fields, confirmed neither exists on real task
  // objects).
  // Real health score (0-100), same idea as proposed but corrected for
  // the real unit: updated_at/created_at are raw epoch seconds, so their
  // difference is already in seconds (confirmed against _formatDuration's
  // own parameter naming) -- not milliseconds, which the source proposal
  // assumed and would have made the duration component nearly always
  // near-maximum regardless of real task length.
  function computeAgentHealth(agentTasks, avgSec) {
    if (!agentTasks.length) return 100;
    const total = agentTasks.length;
    const successes = agentTasks.filter(t => t.status === 'success').length;

    const nowSec = Date.now() / 1000;
    const recentErrors = agentTasks.filter(t =>
      t.status === 'failed' && t.updated_at != null && (nowSec - t.updated_at) < 86400
    ).length;
    const runningCount = agentTasks.filter(t => t.status === 'running').length;

    const successScore = (successes / total) * 60;
    // avgSec is already real seconds -- scale directly, no /1000.
    const durationScore = avgSec != null ? Math.max(0, 20 - Math.min(20, avgSec)) : 20;
    const errorPenalty = recentErrors * 10;
    const stalePenalty = runningCount * 5;

    const raw = successScore + durationScore - errorPenalty - stalePenalty;
    return Math.max(0, Math.min(100, Math.round(raw)));
  }

  function computeAgentSummary(agentTasks) {
    const total = agentTasks.length;
    const successes = agentTasks.filter(t => t.status === 'success').length;
    const failures = agentTasks.filter(t => t.status === 'failed').length;

    const durationsSec = agentTasks
      .filter(t => t.updated_at != null && t.created_at != null)
      .map(t => t.updated_at - t.created_at);
    const avgSec = durationsSec.length
      ? Math.round(durationsSec.reduce((a, b) => a + b, 0) / durationsSec.length)
      : null;
    const longestSec = durationsSec.length ? Math.max(...durationsSec) : null;

    const lastFailed = agentTasks.find(t => t.status === 'failed' && t.result);
    const lastError = lastFailed
      ? (lastFailed.result.error || lastFailed.result.stderr || JSON.stringify(lastFailed.result).slice(0, 60))
      : null;

    const healthScore = computeAgentHealth(agentTasks, avgSec);

    return { total, successes, failures, avgSec, longestSec, lastError, healthScore };
  }

  function renderAgentSummary(s) {
    const metric = (label, value, color) => `
      <span style="background:rgba(255,140,58,0.12);border:1px solid ${color || 'var(--color-warning, #f0ad4e)'};padding:3px 8px;border-radius:4px;font-size:10px;margin-right:6px;display:inline-block;">${esc(label)}: ${esc(value)}</span>
    `;
    const healthColor = s.healthScore >= 80 ? 'var(--color-success, #4caf50)' : (s.healthScore >= 40 ? 'var(--color-warning, #f0ad4e)' : 'var(--color-error, #ff4444)');
    return `
      <div style="padding:4px 6px 8px;">
        ${metric('Health', String(s.healthScore), healthColor)}
        ${metric('Success', String(s.successes))}
        ${metric('Failed', String(s.failures))}
        ${metric('Avg', s.avgSec != null ? _formatDuration(s.avgSec) : 'n/a')}
        ${metric('Longest', s.longestSec != null ? _formatDuration(s.longestSec) : 'n/a')}
        ${s.lastError ? metric('Last error', s.lastError) : ''}
      </div>
    `;
  }

  function renderRow(t) {
    const durationSec = (t.updated_at != null && t.created_at != null) ? (t.updated_at - t.created_at) : null;
    const lastEvent = lastEventByTask[t.id];
    const serverInfo = serverByName[t.server];
    const serverConnected = serverInfo ? serverInfo.status === 'connected' : null;
    const workerAlive = health[t.agent] ? health[t.agent].status === 'alive' : null;

    let errorText = '';
    if (t.status === 'failed' && t.result) {
      errorText = t.result.error || t.result.stderr || JSON.stringify(t.result).slice(0, 80);
    }

    return `
      <tr data-task-id="${esc(t.id)}" style="border-bottom:1px solid var(--border);cursor:pointer;" title="Click for dependency chain">
        <td style="padding:4px 6px;font-size:10px;opacity:0.7;">${esc(t.id)}${t.name ? `<br><span style="opacity:0.8;font-weight:600;">${esc(t.name)}</span>` : ''}</td>
        <td style="padding:4px 6px;font-size:11px;">${esc(t.agent)}${workerAlive === false ? ' <span style="color:var(--red, #e74c3c);" title="Worker not heartbeating">\u26a0</span>' : ''}</td>
        <td style="padding:4px 6px;font-size:11px;">${esc(t.server)}.${esc(t.tool)}${serverConnected === false ? ' <span style="color:var(--red, #e74c3c);" title="MCP server disconnected">\u26a0</span>' : ''}</td>
        <td style="padding:4px 6px;font-size:11px;color:${_statusColor(t.status)};font-weight:600;">${esc(t.status.toUpperCase())}</td>
        <td style="padding:4px 6px;font-size:11px;">${durationSec != null ? _formatDuration(durationSec) : ''}</td>
        <td style="padding:4px 6px;font-size:11px;">${t.retry_count}/${t.max_retries}</td>
        <td style="padding:4px 6px;font-size:10px;opacity:0.7;">${lastEvent ? esc(lastEvent.event_type) : ''}</td>
        <td style="padding:4px 6px;font-size:10px;color:var(--red, #e74c3c);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(errorText)}">${esc(errorText)}</td>
      </tr>
    `;
  }

  // Real, table-valid grouping: when no agent filter is active (multiple
  // agents visible), group tasks into one <tbody> per agent with a
  // collapsible header row -- can't use arbitrary <div> wrappers inside
  // a <table>, so grouping happens at the <tbody> level instead, which
  // is where real HTML actually supports it. When an agent filter IS
  // active, there's only one agent, so the collapsible toggle adds
  // nothing -- but the summary row (including health score) is still
  // genuinely useful here, so it's kept; only the collapse behavior is
  // skipped. Corrected after DK's real usage showed this filtered view
  // (matching the Jarvis Home drill-down click) is the common path, and
  // originally missed showing the summary entirely there.
  let bodyHtml;
  if (agentFilter) {
    const summary = computeAgentSummary(filteredTasks);
    bodyHtml = `
      <tbody>
        <tr><td colspan="8" style="padding:0;">${renderAgentSummary(summary)}</td></tr>
      </tbody>
      <tbody>${filteredTasks.map(renderRow).join('')}</tbody>
    `;
  } else {
    const byAgent = {};
    filteredTasks.forEach(t => { (byAgent[t.agent] = byAgent[t.agent] || []).push(t); });
    bodyHtml = Object.entries(byAgent).map(([agentName, agentTasks]) => {
      const summary = computeAgentSummary(agentTasks);
      return `
      <tbody>
        <tr class="process-table-group-header" data-group="${esc(agentName)}" style="cursor:pointer;user-select:none;background:var(--panel);">
          <td colspan="7" style="padding:4px 6px;font-size:11px;font-weight:600;">
            <span id="process-table-group-arrow-${esc(agentName)}">\u25BC</span> ${esc(agentName)} (${agentTasks.length})
          </td>
          <td style="padding:4px 6px;">
            <button class="process-table-restart-btn" data-agent="${esc(agentName)}" title="Records a restart request -- an actual restart is carried out separately by the host-level agent supervisor" style="font-size:9px;padding:2px 6px;background:transparent;border:1px solid var(--border);color:var(--color-muted, #888);border-radius:3px;cursor:pointer;" onclick="event.stopPropagation();">Restart</button>
          </td>
        </tr>
        <tr>
          <td colspan="8" style="padding:0;">${renderAgentSummary(summary)}</td>
        </tr>
      </tbody>
      <tbody id="process-table-group-body-${esc(agentName)}">
        ${agentTasks.map(renderRow).join('')}
      </tbody>
    `;
    }).join('');
  }

  return `
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:2px solid var(--border);text-align:left;">
          <th style="padding:4px 6px;">Task</th>
          <th style="padding:4px 6px;">Agent</th>
          <th style="padding:4px 6px;">Server.Tool</th>
          <th style="padding:4px 6px;">Status</th>
          <th style="padding:4px 6px;">Runtime</th>
          <th style="padding:4px 6px;">Retries</th>
          <th style="padding:4px 6px;">Last Event</th>
          <th style="padding:4px 6px;">Error</th>
        </tr>
      </thead>
      ${bodyHtml}
    </table>
  `;
}

function _bindProcessTableGroups(container) {
  container.querySelectorAll('.process-table-group-header').forEach(header => {
    header.addEventListener('click', () => {
      const groupName = header.dataset.group;
      const body = el(`process-table-group-body-${groupName}`);
      const arrow = el(`process-table-group-arrow-${groupName}`);
      if (!body) return;
      const isHidden = body.style.display === 'none';
      body.style.display = isHidden ? '' : 'none';
      if (arrow) arrow.textContent = isHidden ? '\u25BC' : '\u25B6';
    });
  });

  // Real restart-agent action, wired to the actual /restart/{agent} endpoint
  // (which itself only records the request -- an actual restart is carried
  // out separately by the host-level agent supervisor, per its own real
  // docstring; not fabricated as an instant action).
  container.querySelectorAll('.process-table-restart-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const agentName = btn.dataset.agent;
      const original = btn.textContent;
      btn.textContent = '...';
      btn.disabled = true;
      try {
        const res = await fetch(`/api/agent-tasks/restart/${encodeURIComponent(agentName)}`, {
          method: 'POST', credentials: 'same-origin',
        });
        if (res.ok) {
          btn.textContent = 'Requested';
          setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
        } else {
          btn.textContent = 'Failed';
          setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
        }
      } catch (e) {
        btn.textContent = 'Failed';
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
      }
    });
  });
}

async function _render() {
  const container = el('process-table-content');
  if (!container) return;
  try {
    const data = await _fetchAll();
    _lastData = data;
    container.innerHTML = _renderTable(data);
    container.querySelectorAll('tr[data-task-id]').forEach(row => {
      row.addEventListener('click', () => _openDrawer(row.dataset.taskId));
    });
    _bindProcessTableGroups(container);
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

function _wireFilters() {
  ['process-table-filter-status', 'process-table-filter-agent'].forEach(id => {
    const filterEl = el(id);
    if (filterEl && !filterEl.dataset.wired) {
      filterEl.dataset.wired = '1';
      filterEl.addEventListener('change', () => {
        const container = el('process-table-content');
        if (container && _lastData) {
          container.innerHTML = _renderTable(_lastData);
          container.querySelectorAll('tr[data-task-id]').forEach(row => {
            row.addEventListener('click', () => _openDrawer(row.dataset.taskId));
          });
        }
      });
    }
  });
}

async function _fetchRegistry() {
  if (_registryCache) return _registryCache;
  const res = await fetch('/api/agent-tasks/registry', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Registry request failed: ${res.status}`);
  _registryCache = await res.json();
  return _registryCache;
}

async function _fetchWorkerLogsForTask(agent, taskId) {
  const res = await fetch(`/api/agent-tasks/worker-logs/${encodeURIComponent(agent)}?task_id=${encodeURIComponent(taskId)}`, { credentials: 'same-origin' });
  if (!res.ok) return { logs: [] };
  return res.json();
}

async function _fetchWorkerErrorRate(agent) {
  const res = await fetch(`/api/agent-tasks/worker-logs/${encodeURIComponent(agent)}?phase=tool_end`, { credentials: 'same-origin' });
  if (!res.ok) return null;
  const data = await res.json();
  const logs = data.logs || [];
  if (!logs.length) return null;
  const failed = logs.filter(l => l.outcome && l.outcome !== 'success').length;
  return { failed, total: logs.length, pct: Math.round((failed / logs.length) * 100) };
}

function _drawerSection(title, bodyHtml) {
  return `
    <div style="margin-bottom:12px;padding:8px;border:1px solid var(--border);border-radius:4px;">
      <div style="font-weight:600;font-size:11px;margin-bottom:6px;">${esc(title)}</div>
      ${bodyHtml}
    </div>
  `;
}

async function _openDrawer(taskId) {
  const drawer = el('process-table-drawer');
  const drawerBody = el('process-table-drawer-body');
  if (!drawer || !drawerBody || !_lastData) return;

  const task = _lastData.tasks.find(t => t.id === taskId);
  if (!task) return;

  drawer.classList.remove('hidden');
  drawerBody.innerHTML = 'Loading...';

  try {
    const [registry, workerLogs, errorRate] = await Promise.all([
      _fetchRegistry(),
      _fetchWorkerLogsForTask(task.agent, taskId),
      _fetchWorkerErrorRate(task.agent),
    ]);

    const agentInfo = registry[task.agent] || {};
    const serverInfo = (_lastData.servers || []).find(s => s.name === task.server) || {};
    const workerAlive = _lastData.health[task.agent] ? _lastData.health[task.agent].status === 'alive' : null;

    // Real event vocabulary only -- created/claimed/success/failed/rejected_tool/rejected_disabled
    // from history-db, tool_start/tool_end from worker logs, kept distinct (not conflated).
    const historyEvents = (_lastData.events || [])
      .filter(e => e.task_id === taskId)
      .map(e => ({ ts: e.ts, source: 'history', label: e.event_type }));
    const logEvents = (workerLogs.logs || [])
      .map(l => ({ ts: l.ts, source: 'worker_log', label: l.phase + (l.outcome ? ` (${l.outcome})` : '') }));
    const timeline = [...historyEvents, ...logEvents].sort((a, b) => a.ts - b.ts);

    const lastLogLine = (workerLogs.logs || []).slice(-1)[0];

    drawerBody.innerHTML = `
      <div style="font-weight:600;font-size:12px;margin-bottom:10px;">${esc(task.name || task.id)}${task.name ? `<div style="font-size:10px;opacity:0.6;font-weight:400;">${esc(task.id)}</div>` : ''}</div>

      ${_drawerSection('Agent', `
        <div style="font-size:11px;">Name: ${esc(task.agent)}</div>
        <div style="font-size:11px;">Enabled: ${agentInfo.enabled ? 'yes' : 'no'}${agentInfo.enabled === false ? ' <span style="color:var(--red, #e74c3c);">\u26a0</span>' : ''}</div>
        <div style="font-size:11px;">Capabilities: ${esc((agentInfo.allowed_tools || []).join(', ') || 'unknown')}</div>
      `)}

      ${_drawerSection('MCP Server', `
        <div style="font-size:11px;">Name: ${esc(task.server)}</div>
        <div style="font-size:11px;">Status: ${esc(serverInfo.status || 'unknown')}${serverInfo.status !== 'connected' ? ' <span style="color:var(--red, #e74c3c);">\u26a0</span>' : ''}</div>
        <div style="font-size:11px;">Tools provided: ${serverInfo.tool_count != null ? serverInfo.tool_count : 'unknown'}</div>
      `)}

      ${_drawerSection('Tool Call', `
        <div style="font-size:11px;">Tool: ${esc(task.server)}.${esc(task.tool)}</div>
        <div style="font-size:11px;margin-top:4px;">Arguments:</div>
        <pre style="font-size:10px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;margin:2px 0;">${esc(JSON.stringify(task.arguments || {}))}</pre>
        ${task.result ? `<div style="font-size:11px;margin-top:4px;">Result:</div><pre style="font-size:10px;background:color-mix(in srgb, var(--fg) 4%, transparent);padding:4px;border-radius:3px;white-space:pre-wrap;margin:2px 0;max-height:120px;overflow:auto;">${esc(JSON.stringify(task.result).slice(0, 400))}</pre>` : ''}
      `)}

      ${_drawerSection('Worker', `
        <div style="font-size:11px;">Status: ${workerAlive === null ? 'no heartbeat recorded' : (workerAlive ? 'alive' : 'not heartbeating \u26a0')}</div>
        <div style="font-size:11px;">Recent error rate: ${errorRate ? `${errorRate.failed}/${errorRate.total} (${errorRate.pct}%)` : 'no data'}</div>
        <div style="font-size:11px;">Last log line for this task: ${lastLogLine ? esc(`${lastLogLine.phase}${lastLogLine.outcome ? ' (' + lastLogLine.outcome + ')' : ''}`) : 'none'}</div>
      `)}

      ${_drawerSection('Timeline', timeline.length ? timeline.map(e => `
        <div style="font-size:11px;padding:2px 0;border-bottom:1px solid var(--border);">
          <span style="opacity:0.6;">${new Date(e.ts * 1000).toLocaleTimeString()}</span>
          <span style="font-size:9px;padding:1px 4px;border-radius:2px;margin:0 4px;background:color-mix(in srgb, var(--fg) 8%, transparent);">${esc(e.source)}</span>
          ${esc(e.label)}
        </div>
      `).join('') : '<div style="font-size:11px;opacity:0.5;">No events found.</div>')}
    `;
  } catch (e) {
    drawerBody.innerHTML = `<div class="admin-empty">Failed to load dependency chain: ${esc(e.message)}</div>`;
  }
}

export function openPanel(initialFilter) {
  const modal = el('process-table-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _wireFilters();
  if (initialFilter) {
    const statusEl = el('process-table-filter-status');
    const agentEl = el('process-table-filter-agent');
    if (statusEl) statusEl.value = initialFilter.status || '';
    if (agentEl) agentEl.value = initialFilter.agent || '';
  }
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 5000);
}

function _closePanel() {
  const modal = el('process-table-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('process-table-modal')) return;
  Modals.register('process-table-modal', {
    railBtnId: 'rail-process-table',
    sidebarBtnId: 'tool-process-table-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-process-table-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-process-table-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const closeDrawerBtn = el('close-process-table-drawer');
  if (closeDrawerBtn) {
    closeDrawerBtn.addEventListener('click', () => {
      const drawer = el('process-table-drawer');
      if (drawer) drawer.classList.add('hidden');
    });
  }
}

export default { init, openPanel };
