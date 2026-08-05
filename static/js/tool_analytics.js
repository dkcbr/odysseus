// static/js/tool_analytics.js — MCP Tool Usage Analytics panel (ES6)
// Real, server-side aggregated data from /api/agent-tasks/tool-analytics,
// which reads the same real JSON-lines worker log files Worker Logs uses,
// correlating tool_start/tool_end by task_id to get real tool names (a
// bug caught and fixed before this ever shipped -- tool_end entries don't
// carry server/tool fields on their own).

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

async function _fetchAnalytics() {
  const res = await fetch('/api/agent-tasks/tool-analytics', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _renderTable(data) {
  const tools = data.tools || [];
  if (!tools.length) {
    return '<div class="admin-empty">No completed tool calls logged yet.</div>';
  }

  const byMostUsed = [...tools].sort((a, b) => b.calls - a.calls);
  const bySlowest = [...tools].sort((a, b) => b.avg_duration_ms - a.avg_duration_ms).slice(0, 5);
  const byFailing = [...tools].filter(t => t.failures > 0).sort((a, b) => b.failure_rate_pct - a.failure_rate_pct);

  const agentRows = Object.entries(data.by_agent || {}).map(([agent, s]) => `
    <tr>
      <td style="padding:4px 8px;">${esc(agent)}</td>
      <td style="padding:4px 8px;">${s.total_calls}</td>
      <td style="padding:4px 8px;">${s.failed_calls}</td>
    </tr>
  `).join('');

  const mainRows = byMostUsed.map(t => `
    <tr class="ta-tool-row" data-tool="${esc(t.tool)}" style="cursor:pointer;" title="Click for recent call details">
      <td style="padding:4px 8px;">${esc(t.tool)}</td>
      <td style="padding:4px 8px;">${t.calls}</td>
      <td style="padding:4px 8px;">${t.min_duration_ms != null ? t.min_duration_ms + 'ms' : '\u2014'} / ${t.avg_duration_ms}ms / ${t.max_duration_ms}ms</td>
      <td style="padding:4px 8px;color:${t.failure_rate_pct > 0 ? 'var(--red, #e74c3c)' : 'inherit'};">${t.failure_rate_pct}%</td>
      <td style="padding:4px 8px;font-size:10px;opacity:0.7;">${esc(t.agents.join(', '))}</td>
    </tr>
  `).join('');

  return `
    <div style="margin-bottom:14px;">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;">All Tools (by usage)</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead><tr style="border-bottom:2px solid var(--border);text-align:left;">
          <th style="padding:4px 8px;">Tool</th><th style="padding:4px 8px;">Calls</th>
          <th style="padding:4px 8px;">Min / Avg / Max</th>
          <th style="padding:4px 8px;">Fail %</th><th style="padding:4px 8px;">Agents</th>
        </tr></thead>
        <tbody>${mainRows}</tbody>
      </table>
    </div>

    <div style="margin-bottom:14px;">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;">Slowest (top 5 by avg duration)</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <tbody>${bySlowest.map(t => `<tr><td style="padding:4px 8px;">${esc(t.tool)}</td><td style="padding:4px 8px;">${t.avg_duration_ms}ms avg</td></tr>`).join('')}</tbody>
      </table>
    </div>

    ${byFailing.length ? `
    <div style="margin-bottom:14px;">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;color:var(--red, #e74c3c);">Tools With Failures</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <tbody>${byFailing.map(t => `<tr><td style="padding:4px 8px;">${esc(t.tool)}</td><td style="padding:4px 8px;">${t.failures}/${t.calls} failed (${t.failure_rate_pct}%)</td></tr>`).join('')}</tbody>
      </table>
    </div>
    ` : ''}

    <div>
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;">Per-Agent Totals</div>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead><tr style="border-bottom:2px solid var(--border);text-align:left;">
          <th style="padding:4px 8px;">Agent</th><th style="padding:4px 8px;">Total Calls</th><th style="padding:4px 8px;">Failed</th>
        </tr></thead>
        <tbody>${agentRows}</tbody>
      </table>
    </div>

    <div id="ta-drawer" style="display:none;margin-top:14px;border-top:1px solid var(--border);padding-top:10px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
        <div id="ta-drawer-title" style="font-weight:600;font-size:12px;"></div>
        <button id="ta-drawer-close" style="font-size:10px;padding:2px 6px;">Close</button>
      </div>
      <div id="ta-drawer-content" style="font-size:11px;max-height:240px;overflow-y:auto;"></div>
    </div>
  `;
}

async function _openDrawer(toolKey) {
  const drawer = el('ta-drawer');
  const title = el('ta-drawer-title');
  const content = el('ta-drawer-content');
  if (!drawer || !title || !content) return;

  drawer.style.display = 'block';
  title.textContent = `Recent calls: ${toolKey}`;
  content.innerHTML = 'Loading...';

  try {
    const res = await fetch('/api/agent-tasks/history-db?limit=200', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    const data = await res.json();
    const events = data.events || [];

    // Real correlation, matching the backend's approach: find task_ids whose
    // tool_start server.tool matches, then pull their corresponding
    // success/failed event for the real outcome and result.
    const matchingTaskIds = new Set();
    events.forEach(e => {
      if (e.event_type === 'created' && e.tool && `${e.server || '?'}.${e.tool}` === toolKey) {
        matchingTaskIds.add(e.task_id);
      }
    });

    const rows = events
      .filter(e => matchingTaskIds.has(e.task_id) && (e.event_type === 'success' || e.event_type === 'failed'))
      .sort((a, b) => b.ts - a.ts)
      .slice(0, 15)
      .map(e => {
        const time = new Date(e.ts * 1000).toLocaleTimeString();
        const statusColor = e.event_type === 'success' ? '#4ade80' : '#ff4a5e';
        const argsStr = e.arguments ? JSON.stringify(e.arguments) : '';
        return `
          <div style="border-bottom:1px solid var(--border);padding:4px 0;">
            <span style="color:${statusColor};font-weight:600;">${esc(e.event_type)}</span>
            <span style="opacity:0.7;"> \u00b7 ${esc(time)} \u00b7 ${esc(e.agent || '')} \u00b7 task ${esc(e.task_id)}</span>
            ${argsStr ? `<div style="font-size:10px;opacity:0.6;margin-top:2px;">args: ${esc(argsStr.slice(0, 200))}</div>` : ''}
          </div>
        `;
      }).join('');

    content.innerHTML = rows || '<div style="opacity:0.6;">No completed calls found in the last 200 real events.</div>';
  } catch (e) {
    content.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

function _closeDrawer() {
  const drawer = el('ta-drawer');
  if (drawer) drawer.style.display = 'none';
}

async function _render() {
  const container = el('tool-analytics-content');
  if (!container) return;
  try {
    const data = await _fetchAnalytics();
    container.innerHTML = _renderTable(data);
    container.querySelectorAll('.ta-tool-row').forEach(row => {
      row.addEventListener('click', () => _openDrawer(row.dataset.tool));
    });
    const closeBtn = el('ta-drawer-close');
    if (closeBtn) closeBtn.addEventListener('click', _closeDrawer);
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('tool-analytics-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 15000);
}

function _closePanel() {
  const modal = el('tool-analytics-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('tool-analytics-modal')) return;
  Modals.register('tool-analytics-modal', {
    railBtnId: 'rail-tool-analytics',
    sidebarBtnId: 'tool-tool-analytics-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-tool-analytics-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-tool-analytics-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);
}

export default { init, openPanel };
