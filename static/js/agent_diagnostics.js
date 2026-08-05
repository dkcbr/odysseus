// static/js/agent_diagnostics.js — Agent Diagnostics panel (ES6)
// Real, object-keyed data from /api/agent-tasks/health and
// /api/agent-tasks/registry (verified directly against live responses --
// NOT arrays, NOT the invented agent_id/last_heartbeat/uptime_seconds/
// capabilities fields from an earlier, incorrect proposal). No uptime
// shown -- the backend has no field for it; heartbeat freshness
// (seconds_since_heartbeat) is the real, honest substitute.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { notify } from './notifications.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;
let _lastServerStates = {}; // real diff cache -- server name -> status, from the last /api/mcp/servers fetch
let _serverAlertCooldown = {}; // server name -> ms timestamp of last alert -- guards against flapping spam
let _lastHeartbeatStates = {}; // real diff cache -- agent name -> "fresh" | "stale"
let _heartbeatAlertCooldown = {}; // agent name -> ms timestamp of last alert

async function _fetchJson(url) {
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`${url} failed: ${res.status}`);
  return res.json();
}

const SERVER_ALERT_COOLDOWN_MS = 5000; // per-server, not global -- one flapping server shouldn't suppress alerts for others

function _checkServerAlerts(currentStatusMap) {
  const now = Date.now();
  const disconnected = [];
  const reconnected = [];

  Object.keys(currentStatusMap).forEach(server => {
    const prev = _lastServerStates[server];
    const curr = currentStatusMap[server];
    if (prev === undefined) return; // first load -- nothing to diff against yet
    if (prev === curr) return;

    const lastAlert = _serverAlertCooldown[server] || 0;
    if (now - lastAlert < SERVER_ALERT_COOLDOWN_MS) return; // throttled -- excluded from the group too

    _serverAlertCooldown[server] = now;
    if (prev === 'connected' && curr !== 'connected') {
      disconnected.push(server);
    } else if (prev !== 'connected' && curr === 'connected') {
      reconnected.push(server);
    }
  });
  _lastServerStates = currentStatusMap;

  if (!disconnected.length && !reconnected.length) return;

  const parts = [];
  if (disconnected.length) parts.push(`Disconnected: ${disconnected.join(', ')}`);
  if (reconnected.length) parts.push(`Reconnected: ${reconnected.join(', ')}`);
  const level = disconnected.length && !reconnected.length ? 'error' : (reconnected.length && !disconnected.length ? 'info' : 'warning');
  notify({ level, message: parts.join(' \u2022 '), servers: [...disconnected, ...reconnected] });
}

const HEARTBEAT_STALE_THRESHOLD_S = 20; // real workers heartbeat every ~1-2s all session; this only fires on a genuine hang
const HEARTBEAT_ALERT_COOLDOWN_MS = 5000;

function _checkHeartbeatAlerts(healthMap) {
  const now = Date.now();
  const stale = [];
  const recovered = [];
  const staleAgentNames = [];
  const recoveredAgentNames = [];

  Object.keys(healthMap).forEach(agent => {
    const hb = healthMap[agent]?.seconds_since_heartbeat;
    if (hb == null) return;
    const state = hb > HEARTBEAT_STALE_THRESHOLD_S ? 'stale' : 'fresh';
    const prev = _lastHeartbeatStates[agent];
    _lastHeartbeatStates[agent] = state;
    if (prev === undefined || prev === state) return;

    const lastAlert = _heartbeatAlertCooldown[agent] || 0;
    if (now - lastAlert < HEARTBEAT_ALERT_COOLDOWN_MS) return;
    _heartbeatAlertCooldown[agent] = now;

    if (state === 'stale') { stale.push(`${agent} (${hb.toFixed(0)}s)`); staleAgentNames.push(agent); }
    else { recovered.push(agent); recoveredAgentNames.push(agent); }
  });

  if (!stale.length && !recovered.length) return;

  const parts = [];
  if (stale.length) parts.push(`Stale heartbeat: ${stale.join(', ')}`);
  if (recovered.length) parts.push(`Heartbeat recovered: ${recovered.join(', ')}`);
  const level = stale.length ? 'warning' : 'info'; // any stale entry -> warning, even if mixed
  notify({ level, message: parts.join(' \u2022 '), agents: staleAgentNames.concat(recoveredAgentNames) });
}

function _toolList(tools, cssClass) {
  if (!tools || !tools.length) return '<div style="font-size:10px;color:#7a9aab;">none</div>';
  return `<div class="${cssClass}" style="display:none;margin-top:4px;padding-left:10px;">
    ${tools.map(t => `<div style="font-size:10px;padding:1px 0;">${esc(t)}</div>`).join('')}
  </div>`;
}

function _serverList(servers, cssClass, serverStatusMap) {
  if (!servers || !servers.length) return '<div style="font-size:10px;color:#7a9aab;">none</div>';
  return `<div class="${cssClass}" style="display:none;margin-top:4px;padding-left:10px;">
    ${servers.map(s => {
      const state = serverStatusMap[s] ?? 'unknown';
      const color = state === 'connected' ? '#4ade80' : (state === 'disconnected' ? '#ff4a5e' : '#7a9aab');
      return `<div style="font-size:10px;padding:1px 0;color:${color};">${esc(s)} (${esc(state)})</div>`;
    }).join('')}
  </div>`;
}

// Real, confirmed event-type colors -- matches the vocabulary already
// verified for Task Timeline: created, claimed, success, failed,
// requeued, rejected_tool, rejected_disabled. No fabricated types.
const EVENT_COLORS = {
  created: '#7a9aab', claimed: '#4ad4e8', success: '#4ade80', failed: '#ff4a5e',
  requeued: '#ff8c3a', rejected_tool: '#c0392b', rejected_disabled: '#8e44ad',
};

// Real error-streak logic: computed per TASK (final outcome), not per raw
// event -- a single task can have multiple requeued/claimed events before
// its terminal outcome, so counting each retry as a separate "failure"
// would inflate the streak. Strict mode: failed/rejected_tool/
// rejected_disabled count as failures; requeued/claimed/created don't.
const FAILURE_TYPES = new Set(['failed', 'rejected_tool', 'rejected_disabled']);

function _computeErrorStreak(events, agentName) {
  const agentEvents = events.filter(e => e.agent === agentName);
  // Group by task_id, take each task's terminal event (success or a
  // failure type) -- the latest such event per task.
  const taskOutcomes = {}; // task_id -> { ts, event_type, tool, server }
  agentEvents.forEach(e => {
    if (e.event_type !== 'success' && !FAILURE_TYPES.has(e.event_type)) return;
    const existing = taskOutcomes[e.task_id];
    if (!existing || e.ts > existing.ts) {
      taskOutcomes[e.task_id] = { ts: e.ts, event_type: e.event_type, tool: e.tool, server: e.server };
    }
  });

  const outcomes = Object.values(taskOutcomes).sort((a, b) => b.ts - a.ts);
  if (!outcomes.length) return null;

  let consecutiveFailures = 0;
  for (const o of outcomes) {
    if (FAILURE_TYPES.has(o.event_type)) consecutiveFailures++;
    else break; // stop at the first success, counting backward from newest
  }

  const lastSuccess = outcomes.find(o => o.event_type === 'success');
  const lastFailure = outcomes.find(o => FAILURE_TYPES.has(o.event_type));

  return {
    consecutiveFailures,
    lastSuccessTs: lastSuccess ? lastSuccess.ts : null,
    lastSuccessTool: lastSuccess && lastSuccess.tool ? `${lastSuccess.server || '?'}.${lastSuccess.tool}` : null,
    lastFailureTool: lastFailure && lastFailure.tool ? `${lastFailure.server || '?'}.${lastFailure.tool}` : null,
  };
}

function _computeLatencyStats(events, agentName) {
  const agentEvents = events.filter(e => e.agent === agentName);
  const claimedByTask = {};
  const terminalByTask = {};

  agentEvents.forEach(e => {
    if (e.event_type === 'claimed') {
      // A task can be claimed multiple times (retries) -- use the LATEST
      // claim, matching the attempt that actually led to the terminal outcome.
      if (!claimedByTask[e.task_id] || e.ts > claimedByTask[e.task_id]) claimedByTask[e.task_id] = e.ts;
    } else if (e.event_type === 'success' || FAILURE_TYPES.has(e.event_type)) {
      if (!terminalByTask[e.task_id] || e.ts > terminalByTask[e.task_id]) terminalByTask[e.task_id] = e.ts;
    }
  });

  const latenciesMs = [];
  Object.keys(terminalByTask).forEach(taskId => {
    const claimedTs = claimedByTask[taskId];
    const terminalTs = terminalByTask[taskId];
    if (claimedTs == null || terminalTs == null) return; // missing either real timestamp -- skip
    const latencyMs = (terminalTs - claimedTs) * 1000;
    if (latencyMs >= 0) latenciesMs.push(latencyMs); // guard against out-of-order/duplicate claims producing negative values
  });

  if (!latenciesMs.length) return null;
  const avg = latenciesMs.reduce((a, b) => a + b, 0) / latenciesMs.length;
  const min = Math.min(...latenciesMs);
  const max = Math.max(...latenciesMs);
  const avgS = avg / 1000;
  const color = avgS < 3 ? '#4ade80' : (avgS <= 10 ? '#ff8c3a' : '#ff4a5e');

  // Percentiles only computed (and only ever shown) once sample size is
  // real enough to be meaningful -- with 2-3 samples, p50/p95/p99 just
  // collapse to one of the raw values, adding false precision, not insight.
  const PERCENTILE_MIN_SAMPLES = 20;
  let percentiles = null;
  if (latenciesMs.length >= PERCENTILE_MIN_SAMPLES) {
    const sorted = [...latenciesMs].sort((a, b) => a - b);
    const pct = p => sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))];
    percentiles = { p50: pct(50), p95: pct(95), p99: pct(99) };
  }

  return { avg, min, max, color, sampleCount: latenciesMs.length, percentiles };
}

const TERMINAL_TYPES = new Set(['success', 'failed', 'rejected_tool', 'rejected_disabled']);

function _computeThroughput(events, agentName, windowMinutes = 10) {
  const agentEvents = events.filter(e => e.agent === agentName);
  const nowS = Date.now() / 1000; // real field e.ts is Unix seconds, not ms -- keep everything in seconds
  const windowS = windowMinutes * 60;

  const recent = agentEvents.filter(e => TERMINAL_TYPES.has(e.event_type) && e.ts >= nowS - windowS);
  const tasksCompleted = recent.length;
  const perMinute = tasksCompleted / windowMinutes;
  const color = perMinute >= 1 ? '#4ade80' : (perMinute >= 0.1 ? '#ff8c3a' : '#ff4a5e');
  return { tasksCompleted, perMinute, windowMinutes, color };
}

function _renderThroughputLine(events, agentName) {
  const t = _computeThroughput(events, agentName);
  return `
    <div style="font-size:10px;margin-top:4px;">
      <span style="color:${t.color};font-weight:600;">Throughput:</span>
      <span style="color:#7a9aab;"> ${t.perMinute.toFixed(2)} tasks/min (last ${t.windowMinutes} min, ${t.tasksCompleted} completed)</span>
    </div>
  `;
}

function _renderLatencyBlock(events, agentName) {
  const stats = _computeLatencyStats(events, agentName);
  if (!stats) return '';
  const fmt = ms => ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;

  const percentileLine = stats.percentiles
    ? `<div style="color:#7a9aab;margin-top:2px;">p50 ${fmt(stats.percentiles.p50)} \u00b7 p95 ${fmt(stats.percentiles.p95)} \u00b7 p99 ${fmt(stats.percentiles.p99)}</div>`
    : `<div style="color:#7a9aab;margin-top:2px;font-style:italic;">(percentiles appear once this agent has \u226520 completed tasks -- currently ${stats.sampleCount})</div>`;

  return `
    <div style="font-size:10px;margin-top:6px;">
      <span style="color:${stats.color};font-weight:600;">Latency (claim\u2192outcome):</span>
      <span style="color:#7a9aab;"> avg ${fmt(stats.avg)} \u00b7 fastest ${fmt(stats.min)} \u00b7 slowest ${fmt(stats.max)}</span>
      ${percentileLine}
    </div>
  `;
}

function _formatTimeSince(ts) {
  const diffS = Date.now() / 1000 - ts;
  if (diffS < 60) return `${Math.round(diffS)}s`;
  if (diffS < 3600) return `${Math.round(diffS / 60)}m`;
  return `${Math.round(diffS / 3600)}h`;
}

function _renderErrorStreak(events, agentName) {
  const streak = _computeErrorStreak(events, agentName);
  if (!streak) return '<div style="font-size:10px;color:#7a9aab;margin-top:6px;">Error Streak: no completed tasks yet</div>';

  const n = streak.consecutiveFailures;
  const color = n === 0 ? '#4ade80' : (n <= 2 ? '#ff8c3a' : '#ff4a5e');
  const timeSince = streak.lastSuccessTs != null ? _formatTimeSince(streak.lastSuccessTs) + ' ago' : 'No successful tasks yet';

  return `
    <div style="font-size:10px;margin-top:6px;padding-top:6px;border-top:1px solid var(--border);">
      <span style="color:${color};font-weight:600;">Error Streak: ${n} consecutive</span>
      <div style="color:#7a9aab;margin-top:2px;">Time since last success: ${esc(timeSince)}</div>
      ${streak.lastSuccessTool ? `<div style="color:#7a9aab;">Last success: ${esc(streak.lastSuccessTool)}</div>` : ''}
      ${streak.lastFailureTool ? `<div style="color:#7a9aab;">Last failure: ${esc(streak.lastFailureTool)}</div>` : ''}
    </div>
  `;
}

function _recentEventsList(events, agentName, cssClass) {
  const agentEvents = events.filter(e => e.agent === agentName).sort((a, b) => b.ts - a.ts).slice(0, 10);
  if (!agentEvents.length) return `<div class="${cssClass}" style="display:none;margin-top:4px;padding-left:10px;font-size:10px;color:#7a9aab;">No recent events.</div>`;
  return `<div class="${cssClass}" style="display:none;margin-top:4px;padding-left:10px;">
    ${agentEvents.map(e => {
      const color = EVENT_COLORS[e.event_type] || '#7a9aab';
      const time = new Date(e.ts * 1000).toLocaleTimeString();
      const toolStr = e.tool ? ` \u00b7 ${esc(e.server || '?')}.${esc(e.tool)}` : '';
      return `<div style="font-size:10px;padding:1px 0;"><span style="color:${color};font-weight:600;">${esc(e.event_type)}</span> <span style="opacity:0.7;">${esc(time)}${toolStr}</span></div>`;
    }).join('')}
  </div>`;
}

function _renderAgentRow(agentName, health, registry, serverStatusMap, events, supervisorInfo) {
  const status = health?.status ?? 'unknown';
  const healthy = status === 'alive';
  const heartbeatAge = health?.seconds_since_heartbeat;
  const lastSeen = health?.last_seen ? new Date(health.last_seen * 1000).toLocaleTimeString() : '\u2014';
  const restartCount = supervisorInfo?.restart_count;
  const lastRestart = supervisorInfo?.last_restart
    ? new Date(supervisorInfo.last_restart * 1000).toLocaleTimeString() : null;
  // Severity coloring matching this file's real existing palette (see
  // EVENT_COLORS above), not an invented one: green=#4ade80 (success),
  // orange=#ff8c3a (requeued -- used here for "recovered, watch it"),
  // red=#ff4a5e (failed -- same red already used for the unhealthy status
  // dot above, so a red restart count means the same severity level).
  let restartColor = '#7a9aab'; // default muted, matches the row's own base text color
  if (restartCount >= 3) restartColor = '#ff4a5e';
  else if (restartCount === 1 || restartCount === 2) restartColor = '#ff8c3a';
  const allowed = registry?.allowed_tools ?? [];
  const forbidden = registry?.forbidden_tools ?? [];
  const servers = registry?.servers ?? [];

  const uid = agentName.replace(/[^a-z0-9]/gi, '');

  return `
    <div style="border:1px solid var(--border);border-radius:4px;padding:10px;margin-bottom:8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div style="font-weight:600;font-size:13px;">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${healthy ? '#4ade80' : '#ff4a5e'};margin-right:6px;"></span>
          ${esc(agentName)}
        </div>
        <div style="font-size:10px;opacity:0.7;">${esc(status)}</div>
      </div>
      <div style="font-size:11px;margin-top:6px;color:#7a9aab;">
        Last seen: ${esc(lastSeen)} \u00b7 Heartbeat age: ${heartbeatAge != null ? heartbeatAge.toFixed(1) + 's' : '\u2014'} \u00b7 Enabled: ${registry?.enabled ? 'yes' : 'no'}${restartCount != null ? ` \u00b7 <span style="color:${restartColor}">Restarts: ${restartCount}${lastRestart ? ' (last ' + esc(lastRestart) + ')' : ''}</span>` : ''}
      </div>
      ${registry?.description ? `<div style="font-size:11px;margin-top:4px;">${esc(registry.description)}</div>` : ''}
      ${_renderErrorStreak(events, agentName)}
      ${_renderLatencyBlock(events, agentName)}
      ${_renderThroughputLine(events, agentName)}

      <div style="margin-top:8px;font-size:11px;">
        <button class="ad-toggle-allowed-${uid}" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid var(--border);border-radius:3px;cursor:pointer;">Allowed tools: ${allowed.length} \u25be</button>
        <button class="ad-toggle-forbidden-${uid}" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid var(--border);border-radius:3px;cursor:pointer;margin-left:6px;">Forbidden tools: ${forbidden.length} \u25be</button>
        <button class="ad-toggle-servers-${uid}" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid var(--border);border-radius:3px;cursor:pointer;margin-left:6px;">Servers: ${servers.length} \u25be</button>
        <button class="ad-toggle-events-${uid}" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid var(--border);border-radius:3px;cursor:pointer;margin-left:6px;">Recent events \u25be</button>
      </div>
      ${_toolList(allowed, `ad-allowed-${uid}`)}
      ${_toolList(forbidden, `ad-forbidden-${uid}`)}
      ${_serverList(servers, `ad-servers-${uid}`, serverStatusMap)}
      ${_recentEventsList(events, agentName, `ad-events-${uid}`)}
    </div>
  `;
}

function _wireToggle(container, uid, kind) {
  const btn = container.querySelector(`.ad-toggle-${kind}-${uid}`);
  const list = container.querySelector(`.ad-${kind}-${uid}`);
  if (!btn || !list) return;
  btn.addEventListener('click', () => {
    const isHidden = list.style.display === 'none';
    list.style.display = isHidden ? 'block' : 'none';
  });
}

async function _render() {
  const container = el('agent-diagnostics-list');
  if (!container) return;
  try {
    const [health, registry, servers, historyData, supervisorStatus] = await Promise.all([
      _fetchJson('/api/agent-tasks/health'),
      _fetchJson('/api/agent-tasks/registry'),
      _fetchJson('/api/mcp/servers'),
      _fetchJson('/api/agent-tasks/history-db?limit=200'),
      _fetchJson('/api/agents/status').catch(() => ({})),
    ]);
    const events = historyData.events || [];

    // Real shape: servers is an array of {id, name, status, ...} objects,
    // not a dict keyed by name -- build the real lookup ourselves.
    const serverStatusMap = {};
    (servers || []).forEach(s => { serverStatusMap[s.name] = s.status; });
    _checkServerAlerts(serverStatusMap);
    _checkHeartbeatAlerts(health);

    const agentNames = [...new Set([...Object.keys(health), ...Object.keys(registry)])].sort();
    if (!agentNames.length) {
      container.innerHTML = '<div class="admin-empty">No agents registered.</div>';
      return;
    }

    container.innerHTML = agentNames.map(name => _renderAgentRow(name, health[name], registry[name], serverStatusMap, events, supervisorStatus[name])).join('');

    agentNames.forEach(name => {
      const uid = name.replace(/[^a-z0-9]/gi, '');
      _wireToggle(container, uid, 'allowed');
      _wireToggle(container, uid, 'forbidden');
      _wireToggle(container, uid, 'servers');
      _wireToggle(container, uid, 'events');
    });
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('agent-diagnostics-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 15000);
}

function _closePanel() {
  const modal = el('agent-diagnostics-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('agent-diagnostics-modal')) return;
  Modals.register('agent-diagnostics-modal', {
    railBtnId: 'rail-agent-diagnostics',
    sidebarBtnId: 'tool-agent-diagnostics-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-agent-diagnostics-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-agent-diagnostics-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const refreshBtn = el('agent-diagnostics-refresh');
  if (refreshBtn) refreshBtn.addEventListener('click', _render);
}

export default { init, openPanel };
