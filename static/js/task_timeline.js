// static/js/task_timeline.js — Task Timeline Panel (v1 + zoom/pan) (ES6)
// Real, chronological visualization of real task events -- uses the
// confirmed, real event vocabulary only: created, claimed, success,
// failed, requeued, rejected_tool, rejected_disabled (from history-db).
// No fabricated event types, no fabricated fields -- verified directly
// against the real API response before building this.
//
// Zoom/pan model: zoom changes timeScale (px per ms); SVG width grows
// with it, wrapped in a horizontally-scrolling container -- pan is just
// native scroll, no drag logic needed. Re-renders use the cached event
// set (no re-fetch on zoom/pan).

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;
let _lastEvents = null;
let _timeScale = null; // px per ms -- null until first render computes a fit-to-viewport default

const EVENT_COLORS = {
  created: '#7a9aab',
  claimed: '#4ad4e8',
  success: '#4ade80',
  failed: '#ff4a5e',
  requeued: '#ff8c3a',
  rejected_tool: '#c0392b',
  rejected_disabled: '#8e44ad',
};

const VIEWPORT_WIDTH = 900;
const LEFT_MARGIN = 120;
const ROW_HEIGHT = 50;

async function _fetchEvents() {
  const res = await fetch('/api/agent-tasks/history-db?limit=200', { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  const data = await res.json();
  return data.events || [];
}

function _renderTimeline(events) {
  if (!events.length) {
    return '<div class="admin-empty">No events yet.</div>';
  }

  const agents = [...new Set(events.map(e => e.agent))].sort();
  const timestamps = events.map(e => e.ts * 1000); // work in ms for timeScale
  const minTs = Math.min(...timestamps);
  const maxTs = Math.max(...timestamps);
  const durationMs = Math.max(maxTs - minTs, 1000);

  // Default timeScale: fit the whole range into the viewport, computed
  // once on first real render (not re-derived on every zoom step).
  if (_timeScale === null) {
    _timeScale = (VIEWPORT_WIDTH - LEFT_MARGIN - 20) / durationMs;
  }

  const svgWidth = Math.max(LEFT_MARGIN + durationMs * _timeScale + 20, VIEWPORT_WIDTH);
  const height = agents.length * ROW_HEIGHT + 40;

  const xFor = (tsMs) => LEFT_MARGIN + (tsMs - minTs) * _timeScale;
  const yFor = (agent) => 20 + agents.indexOf(agent) * ROW_HEIGHT + ROW_HEIGHT / 2;

  const rows = agents.map(agent => {
    const y = yFor(agent);
    return `
      <text x="4" y="${y + 4}" fill="#7a9aab" font-size="11" font-family="monospace">${esc(agent)}</text>
      <line x1="${LEFT_MARGIN}" y1="${y}" x2="${svgWidth - 20}" y2="${y}" stroke="#1a3a4a" stroke-width="1"/>
    `;
  }).join('');

  const markers = events.map(e => {
    const x = xFor(e.ts * 1000);
    const y = yFor(e.agent);
    const color = EVENT_COLORS[e.event_type] || '#7a9aab';
    const tooltip = `${e.event_type} \u00b7 ${e.agent}.${e.server || ''}.${e.tool || ''} \u00b7 task ${e.task_id} \u00b7 ${new Date(e.ts * 1000).toLocaleTimeString()}${e.name ? ' \u00b7 ' + e.name : ''}`;
    return `<circle class="task-timeline-marker" data-task-id="${esc(e.task_id)}" cx="${x}" cy="${y}" r="5" fill="${color}" stroke="#050810" stroke-width="1" style="cursor:pointer;"><title>${esc(tooltip)}</title></circle>`;
  }).join('');

  const startLabel = new Date(minTs).toLocaleString();
  const endLabel = new Date(maxTs).toLocaleString();

  return `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <div style="font-size:10px;color:#7a9aab;">${esc(startLabel)} \u2192 ${esc(endLabel)}</div>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="task-timeline-preset" data-window="5m" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">5m</button>
        <button class="task-timeline-preset" data-window="30m" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">30m</button>
        <button class="task-timeline-preset" data-window="2h" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">2h</button>
        <button class="task-timeline-preset" data-window="24h" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">24h</button>
        <button class="task-timeline-preset" data-window="all" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">All</button>
        <span style="width:1px;height:14px;background:#1a3a4a;margin:0 2px;"></span>
        <button id="task-timeline-zoom-out" title="Zoom out" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid #4ad4e8;color:#4ad4e8;border-radius:3px;cursor:pointer;">\u2212</button>
        <button id="task-timeline-zoom-in" title="Zoom in" style="font-size:11px;padding:2px 8px;background:transparent;border:1px solid #4ad4e8;color:#4ad4e8;border-radius:3px;cursor:pointer;">+</button>
      </div>
    </div>
    <div id="task-timeline-scroll-container" style="overflow-x:auto;white-space:nowrap;border:1px solid #1a3a4a;border-radius:4px;background:#08111c;">
      <svg width="${svgWidth}" height="${height}" style="display:block;">
        ${rows}
        ${markers}
      </svg>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;font-size:9px;">
      ${Object.entries(EVENT_COLORS).map(([type, color]) => `
        <span style="color:${color};"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:3px;"></span>${esc(type)}</span>
      `).join('')}
    </div>
  `;
}


const TASK_TIMELINE_TERMINAL_TYPES = new Set(['success', 'failed', 'rejected_tool', 'rejected_disabled']);

function _openDrawer(taskId) {
  const drawer = el('task-timeline-drawer');
  const title = el('task-timeline-drawer-title');
  const content = el('task-timeline-drawer-content');
  if (!drawer || !title || !content || !_lastEvents) return;

  const taskEvents = _lastEvents.filter(e => e.task_id === taskId).sort((a, b) => a.ts - b.ts);
  if (!taskEvents.length) return;

  const claimed = taskEvents.filter(e => e.event_type === 'claimed').pop(); // real: latest claim if retried
  const terminal = taskEvents.filter(e => TASK_TIMELINE_TERMINAL_TYPES.has(e.event_type)).pop();
  const first = taskEvents[0];

  title.textContent = `Task ${taskId}`;

  const rows = [];
  rows.push(`<div style="margin-bottom:2px;"><strong>Agent:</strong> ${esc(first.agent || '\u2014')}</div>`);
  if (first.tool) rows.push(`<div style="margin-bottom:2px;"><strong>Tool:</strong> ${esc(first.server || '?')}.${esc(first.tool)}</div>`);
  if (claimed) rows.push(`<div style="margin-bottom:2px;"><strong>Claimed:</strong> ${esc(new Date(claimed.ts * 1000).toLocaleTimeString())}</div>`);
  if (terminal) {
    rows.push(`<div style="margin-bottom:2px;"><strong>Outcome:</strong> <span style="color:${EVENT_COLORS[terminal.event_type] || '#7a9aab'};">${esc(terminal.event_type)}</span></div>`);
    rows.push(`<div style="margin-bottom:2px;"><strong>Completed:</strong> ${esc(new Date(terminal.ts * 1000).toLocaleTimeString())}</div>`);
    if (claimed) {
      const durationS = terminal.ts - claimed.ts;
      if (durationS >= 0) rows.push(`<div style="margin-bottom:2px;"><strong>Duration (claim\u2192outcome):</strong> ${durationS < 1 ? Math.round(durationS * 1000) + 'ms' : durationS.toFixed(2) + 's'}</div>`);
    }
  } else {
    rows.push(`<div style="margin-bottom:2px;color:#7a9aab;">No terminal outcome yet in the fetched window.</div>`);
  }

  // Full real event chain for this task, in case of retries/requeues.
  rows.push(`<div style="margin-top:8px;font-weight:600;">Full event chain:</div>`);
  taskEvents.forEach(e => {
    const color = EVENT_COLORS[e.event_type] || '#7a9aab';
    rows.push(`<div style="font-size:10px;padding:1px 0;"><span style="color:${color};">${esc(e.event_type)}</span> <span style="color:#7a9aab;">${esc(new Date(e.ts * 1000).toLocaleTimeString())}</span></div>`);
  });

  content.innerHTML = rows.join('');
  drawer.style.display = 'block';
}

function _closeDrawer() {
  const drawer = el('task-timeline-drawer');
  if (drawer) drawer.style.display = 'none';
}

function _wireDrawerMarkers() {
  document.querySelectorAll('.task-timeline-marker').forEach(marker => {
    marker.addEventListener('click', () => _openDrawer(marker.dataset.taskId));
  });
  const closeBtn = el('task-timeline-drawer-close');
  if (closeBtn && !closeBtn.dataset.wired) {
    closeBtn.dataset.wired = '1';
    closeBtn.addEventListener('click', _closeDrawer);
  }
}

function _rerenderFromCache() {
  const container = el('task-timeline-content');
  if (!container || !_lastEvents) return;
  container.innerHTML = _renderTimeline(_lastEvents);
  _wireZoomButtons();
  _wireDrawerMarkers();
}

function _wireZoomButtons() {
  const zoomInBtn = el('task-timeline-zoom-in');
  const zoomOutBtn = el('task-timeline-zoom-out');
  if (zoomInBtn) zoomInBtn.addEventListener('click', () => { _timeScale *= 1.5; _rerenderFromCache(); });
  if (zoomOutBtn) zoomOutBtn.addEventListener('click', () => { _timeScale /= 1.5; _rerenderFromCache(); });
}

const PRESET_WINDOWS_S = { '5m': 5 * 60, '30m': 30 * 60, '2h': 2 * 60 * 60, '24h': 24 * 60 * 60 };
let _allFetchedEvents = null; // the real, unfiltered fetch -- presets filter from this, not from an already-filtered view

function _wirePresetButtons() {
  document.querySelectorAll('.task-timeline-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      if (!_allFetchedEvents) return;
      const w = btn.dataset.window;
      let filtered;
      if (w === 'all') {
        filtered = _allFetchedEvents;
      } else {
        const nowS = Date.now() / 1000; // real field e.ts is Unix seconds, not ms
        const cutoffS = nowS - PRESET_WINDOWS_S[w];
        filtered = _allFetchedEvents.filter(e => e.ts >= cutoffS);
      }
      _lastEvents = filtered;
      _timeScale = null; // re-fit to the new, possibly narrower real range
      const container = el('task-timeline-content');
      if (container) {
        container.innerHTML = _renderTimeline(filtered);
        _wireZoomButtons();
        _wirePresetButtons();
        _wireDrawerMarkers();
      }
    });
  });
}

async function _render() {
  const container = el('task-timeline-content');
  if (!container) return;
  try {
    const events = await _fetchEvents();
    _allFetchedEvents = events;
    _lastEvents = events;
    container.innerHTML = _renderTimeline(events);
    _wireZoomButtons();
    _wirePresetButtons();
    _wireDrawerMarkers();
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('task-timeline-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 10000);
}

function _closePanel() {
  const modal = el('task-timeline-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('task-timeline-modal')) return;
  Modals.register('task-timeline-modal', {
    railBtnId: 'rail-task-timeline',
    sidebarBtnId: 'tool-task-timeline-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-task-timeline-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-task-timeline-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);
}

export default { init, openPanel };
