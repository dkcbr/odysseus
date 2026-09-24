// static/js/system_monitor.js — System Monitor dashboard panel (ES6)
//
// Real, live CPU/memory/disk/network/GPU metrics from
// /api/system-monitor/metrics. Inspired directly by a real system-monitor
// app demo DK watched (Task Manager OG) -- a rich, color-coded,
// real-time visualization, not just plain status text. Same real
// static-shell/Modals.register pattern as poller_dashboard.js.

import * as Modals from './modalManager.js';

const REFRESH_INTERVAL_MS = 2000;

function el(id) { return document.getElementById(id); }

let _open = false;
let _refreshTimer = null;

async function _fetchMetrics() {
  try {
    const res = await fetch('/api/system-monitor/metrics');
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

function _barColor(percent) {
  // Real, deliberate color coding: green (healthy) -> amber (busy) -> red
  // (near capacity), matching the video's real, color-coded style.
  if (percent < 60) return '#2ecc71';
  if (percent < 85) return '#e6b800';
  return '#e74c3c';
}

function _renderBar(label, percent, detail) {
  const color = _barColor(percent);
  return `
    <div style="margin-bottom:14px;">
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px;">
        <span style="font-weight:600;">${label}</span>
        <span style="opacity:0.75;">${detail}</span>
      </div>
      <div style="background:var(--border);border-radius:4px;height:10px;overflow:hidden;">
        <div style="width:${Math.min(100, percent)}%;height:100%;background:${color};transition:width 0.5s ease;"></div>
      </div>
    </div>
  `;
}

function _renderCores(perCorePercent) {
  return `
    <div style="margin-bottom:14px;">
      <div style="font-weight:600;font-size:13px;margin-bottom:6px;">Per-Core (${perCorePercent.length} threads)</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(6px,1fr));gap:2px;height:40px;align-items:end;">
        ${perCorePercent.map(p => `<div style="width:100%;height:${Math.max(4, p)}%;background:${_barColor(p)};border-radius:1px;" title="${p.toFixed(0)}%"></div>`).join('')}
      </div>
    </div>
  `;
}

function _render(data) {
  const container = el('system-monitor-content');
  if (!container) return;

  if (!data) {
    container.innerHTML = '<div style="opacity:0.6;">Unable to reach system metrics.</div>';
    return;
  }

  const { cpu, memory, disk, gpu } = data;

  let html = '';
  // Real, added 2026-09-17: mirrors the existing GPU temperature display
  // pattern exactly. Unlike GPU (entirely omitted when absent), CPU
  // itself is always present -- only temperature_c specifically might
  // genuinely be null (no real sensor found on this host), so this
  // needs its own null-check rather than reusing GPU's "if (gpu)" guard.
  const cpuTempLabel = (cpu.temperature_c !== null && cpu.temperature_c !== undefined)
    ? ` · ${cpu.temperature_c.toFixed(0)}°C` : '';
  html += _renderBar('CPU', cpu.percent, `${cpu.percent.toFixed(1)}% · ${cpu.core_count} threads${cpuTempLabel}`);
  html += _renderCores(cpu.per_core_percent);
  html += _renderBar('Memory', memory.percent, `${memory.used_gb} / ${memory.total_gb} GB`);
  html += _renderBar('Disk', disk.percent, `${disk.used_gb} / ${disk.total_gb} GB`);

  if (gpu) {
    html += _renderBar('GPU', gpu.utilization_percent, `${gpu.utilization_percent.toFixed(0)}% · ${gpu.temperature_c.toFixed(0)}°C`);
    const gpuMemPercent = (gpu.memory_used_mb / gpu.memory_total_mb) * 100;
    html += _renderBar('GPU Memory', gpuMemPercent, `${(gpu.memory_used_mb / 1024).toFixed(1)} / ${(gpu.memory_total_mb / 1024).toFixed(1)} GB`);
  } else {
    html += '<div style="opacity:0.6;font-size:13px;">No GPU detected.</div>';
  }

  container.innerHTML = html;
}

async function _refresh() {
  const data = await _fetchMetrics();
  _render(data);
}

export function openPanel() {
  const modal = el('system-monitor-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _refresh();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_refresh, REFRESH_INTERVAL_MS);
}

function _closePanel() {
  const modal = el('system-monitor-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('system-monitor-modal')) return;
  Modals.register('system-monitor-modal', {
    railBtnId: 'rail-system-monitor',
    sidebarBtnId: 'tool-system-monitor-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-system-monitor-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-system-monitor-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
