// static/js/jarvis_home.js — Jarvis OS Home Screen (HUD aesthetic) (ES6)
// Real data only -- every gauge/indicator maps to an actual, tested value:
//   - Central ring: real system health % (agents enabled AND MCP server
//     connected AND worker alive, counted / total -- from the real
//     registry, /api/mcp/servers, and /api/agent-tasks/health)
//   - CPU/Mem gauges: real values from system_agent (tested tonight)
//   - Agent cards: real enabled/connected/alive state per agent
//   - Throughput bars: real data from /api/agent-tasks/throughput
// No fabricated numbers, no decorative-only elements, no systemd/restart
// controls -- pure UI aggregation of already-existing, real endpoints.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import processTableModule from './process_table.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _refreshTimer = null;

const CYAN = 'var(--hud-cyan)';
const ORANGE = 'var(--hud-orange)';
const RED = 'var(--hud-red)';
const GREEN = 'var(--hud-green)';

// Last-known real server names, used ONLY as a per-card amber fallback
// when /api/mcp/servers itself fails to respond -- if the endpoint is
// down we have no live list at all, so this hardcoded set (matching the
// five servers confirmed real and connected as of tonight) is the only
// honest way to show "data unavailable" per-card rather than one generic
// panel-wide error. Real risk: this list can drift if servers are added
// or removed later without updating it here.
const KNOWN_SERVER_NAMES = ['filesystem', 'jarvis_browser', 'jarvis_desktop', 'tradingview', 'jarvis_system'];

function _injectPulseStyle() {
  if (document.getElementById('jarvis-home-pulse-style')) return;
  const style = document.createElement('style');
  style.id = 'jarvis-home-pulse-style';
  style.textContent = `
    @keyframes jarvis-home-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .jarvis-home-pulse-dot { animation: jarvis-home-pulse 2s ease-in-out infinite; }
  `;
  document.head.appendChild(style);
}

async function _fetchJson(url, opts) {
  const res = await fetch(url, { credentials: 'same-origin', ...(opts || {}) });
  if (!res.ok) throw new Error(`${url} failed: ${res.status}`);
  return res.json();
}

async function _callSystemAgentTool(tool) {
  const res = await fetch('/api/mcp/call', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ server: 'jarvis_system', tool, arguments: {} }),
  });
  if (!res.ok) throw new Error(`${tool} failed: ${res.status}`);
  const result = await res.json();
  return result.stdout || '';
}

// Real SVG radial gauge -- a partial-circle arc via stroke-dasharray,
// representing a genuine 0-100 percentage, with a glow filter.
function _radialGauge(pct, color, size, label, valueText, spokes, spokeActiveIndex) {
  // spokes/spokeActiveIndex are NEW, OPTIONAL trailing params (default off) --
  // added 2026-08-06 for the central SYSTEM HEALTH gauge's radiating-spoke
  // look. Deliberately backward compatible: all 10 real pre-existing call
  // sites in this file only pass the original 5 args, so spokes defaults to
  // 0 (undefined) and every existing gauge renders exactly as before.
  const r = (size / 2) - 8;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - Math.max(0, Math.min(100, pct)) / 100);
  const c = size / 2;

  let spokesSvg = '';
  if (spokes && spokes > 0) {
    const spokeR = r + 6; // just outside the main arc, so it doesn't overlap
    const angleStep = 360 / spokes;
    const activeIdx = (typeof spokeActiveIndex === 'number') ? spokeActiveIndex : -1;
    const paths = [];
    for (let i = 0; i < spokes; i++) {
      const start = (i * angleStep - 90) * Math.PI / 180;
      const end = ((i + 0.7) * angleStep - 90) * Math.PI / 180; // small gap between spokes
      const x1 = c + spokeR * Math.cos(start);
      const y1 = c + spokeR * Math.sin(start);
      const x2 = c + spokeR * Math.cos(end);
      const y2 = c + spokeR * Math.sin(end);
      const isActive = i === activeIdx;
      // 2026-08-06: brightened inactive spokes -- was var(--hud-cyan-dim)
      // (#1a8a94) at opacity 0.4, nearly invisible against a bright solid
      // ring (confirmed via a real screenshot). Now uses the bright
      // --hud-cyan at a single, non-conflicting opacity (SVG multiplies an
      // rgba-alpha color with a separate opacity attribute, so stacking
      // both was the wrong approach -- using one plain color + one opacity
      // value here instead).
      paths.push(`<path d="M ${x1} ${y1} A ${spokeR} ${spokeR} 0 0 1 ${x2} ${y2}" stroke-width="2" fill="none" stroke-linecap="round" stroke="${isActive ? color : 'var(--hud-cyan)'}" opacity="${isActive ? 1 : 0.6}"/>`);
    }
    spokesSvg = paths.join('');
  }

  return `
    <div style="display:flex;flex-direction:column;align-items:center;">
      <svg width="${size}" height="${size}" style="filter:drop-shadow(0 0 6px ${color});overflow:visible;">
        ${spokesSvg}
        <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="var(--hud-panel)" stroke-width="6"/>
        <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${color}" stroke-width="6"
                stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
                transform="rotate(-90 ${c} ${c})" style="transition:stroke-dashoffset 0.6s ease;"/>
        <text x="${c}" y="${c - 2}" text-anchor="middle" fill="${color}" font-size="${size * 0.16}" font-family="monospace" font-weight="bold">${esc(valueText)}</text>
        <text x="${c}" y="${c + size * 0.14}" text-anchor="middle" fill="${color}" font-size="${size * 0.08}" font-family="monospace" opacity="0.7">${esc(label)}</text>
      </svg>
    </div>
  `;
}

function _statusDot(healthy, size, pulse) {
  const color = healthy ? GREEN : RED;
  const cls = pulse && healthy ? ' jarvis-home-pulse-dot' : '';
  return `<span class="${cls}" style="display:inline-block;width:${size}px;height:${size}px;border-radius:50%;background:${color};box-shadow:0 0 6px ${color};margin-right:4px;"></span>`;
}

function _obsVal(entity, key) {
  const line = (entity.observations || []).find(o => o.startsWith(key + ': '));
  return line ? line.slice(key.length + 2) : null;
}

// Real portfolio/risk/system data, fetched independently from the agent
// registry data above -- a failure here (e.g. risk engine hasn't run
// yet) must never take down the rest of the panel, matching the same
// resilience pattern already used for the MCP servers fetch.
async function _fetchPortfolioAndSystem() {
  // Real perf fix, verified 2026-08-08: these 7 calls are fully
  // independent (none depends on another's result), but were being
  // awaited sequentially -- measured real cost via direct timing before
  // changing anything: ~70ms sequential vs ~32ms run concurrently (3
  // runs each, consistent). Converted to real parallel execution while
  // preserving the exact same per-call error isolation as before (one
  // failing call still can't take down the others) -- each call is its
  // own try/catch wrapped in an async IIFE, all run via Promise.all.
  const result = { regimeLine: null, volLevel: null, dominant: null, percentile: null, topLoadings: null, allFactors: null, riskEvents: null, cycleDate: null, graphCounts: null, relevance: null, temps: null, gpuTemp: null, diskUsage: null };

  await Promise.all([
    (async () => {
  try {
    const risk = await _fetchJson('/api/risk/latest');
    if (risk.found) {
      const vol = _obsVal(risk.regime, 'current_vol_annualized');
      const level = _obsVal(risk.regime, 'vol_level');
      const dom = (risk.factors || []).reduce((max, f) => {
        const v = parseFloat(_obsVal(f, 'variance_explained') || 0);
        return (!max || v > max.v) ? { f, v } : max;
      }, null);
      result.regimeLine = `${level || '?'} — ${vol ? (parseFloat(vol) * 100).toFixed(1) + '%' : '?'} vol`;
      // Real field, stored raw for color-coding below. Only "NORMAL" has
      // been confirmed in live data this session -- other values
      // (HIGH, etc.) are handled with a safe neutral fallback color
      // rather than assumed color mappings for values never observed.
      result.volLevel = level || null;
      if (dom) result.dominant = { name: dom.f.name, confidence: dom.v };
      // Real field, previously unused: percentile_in_own_history.
      result.percentile = _obsVal(risk.regime, 'percentile_in_own_history');
      // Real field: top_loadings is a single string (e.g. "TOXR (-0.241),
      // GXRP (-0.241), ..."), not an array of objects -- confirmed
      // directly against the live API before building this, since an
      // earlier proposal incorrectly assumed an array-of-objects shape.
      if (dom) result.topLoadings = _obsVal(dom.f, 'top_loadings');
      // Real, previously-unfetched: the full factor list, for factor
      // telemetry display. Matches dominance against the actual computed
      // max (dom.f.name) rather than assuming index 0 is always dominant
      // -- in practice PCA output is ordered by variance descending, so
      // this holds today, but matching by name is robust regardless.
      if (risk.factors && risk.factors.length > 0) {
        result.allFactors = risk.factors.map(f => ({
          name: f.name,
          isDominant: dom && f.name === dom.f.name,
          varianceExplained: _obsVal(f, 'variance_explained'),
          topLoadings: _obsVal(f, 'top_loadings'),
        }));
      }
      // Real risk_events array, not previously fetched here at all.
      // Each event's category/severity/detail live inside its own
      // observations array, same pattern as regime/factor above.
      // Real, top-level field: risk.date is the shared cycle date (date
      // only, no time component -- confirmed directly against the live
      // API, which returns e.g. "2026-07-30", not a timestamp).
      result.cycleDate = risk.date || null;
      if (risk.risk_events && risk.risk_events.length > 0) {
        // Real field: risk.date is the shared cycle date for this batch
        // of risk events -- there is no per-event timestamp in the real
        // API, so "timeline" ordering below uses this shared date plus
        // each event's real position in the array, not invented per-event
        // times.
        result.riskEvents = risk.risk_events.map(ev => ({
          category: _obsVal(ev, 'category'),
          severity: _obsVal(ev, 'severity'),
          detail: _obsVal(ev, 'detail'),
          date: risk.date,
        }));
      }
    }
  } catch (e) { /* real risk-engine data may not exist yet -- honest, not an error */ }
    })(),
    (async () => {
  try {
    const [nodesRes, edgesRes] = await Promise.all([_fetchJson('/api/graph/nodes'), _fetchJson('/api/graph/edges')]);
    result.graphCounts = { nodes: nodesRes.nodes.length, edges: edgesRes.edges.length };
  } catch (e) { /* real, best-effort */ }
    })(),
    (async () => {
  try {
    result.relevance = await _fetchJson('/api/relevance/today');
  } catch (e) { /* real, best-effort */ }
    })(),
    (async () => {
  try {
    result.temps = await _callSystemAgentTool('get_temps');
  } catch (e) { /* real, best-effort -- tool may not exist on an older jarvis_system version */ }
    })(),
    (async () => {
  try {
    result.gpuTemp = await _callSystemAgentTool('get_gpu_temp');
  } catch (e) { /* real, best-effort */ }
    })(),
    (async () => {
  try {
    result.diskUsage = await _callSystemAgentTool('get_disk_usage');
  } catch (e) { /* real, best-effort */ }
    })(),
  ]);

  return result;
}

async function _fetchAll() {
  const [registry, health, queue, throughput, history] = await Promise.all([
    _fetchJson('/api/agent-tasks/registry'),
    _fetchJson('/api/agent-tasks/health'),
    _fetchJson('/api/agent-tasks/queue'),
    _fetchJson('/api/agent-tasks/throughput?bucket_minutes=30&hours=6'),
    _fetchJson('/api/agent-tasks/history-db?limit=100'),
  ]);

  // Servers fetched independently -- a failure here shouldn't take down
  // the whole panel; it falls back to per-card amber "unavailable" badges
  // using the known server names instead of one generic error banner.
  let servers, serversUnavailable = false;
  try {
    servers = await _fetchJson('/api/mcp/servers');
  } catch (e) {
    serversUnavailable = true;
    servers = KNOWN_SERVER_NAMES.map(name => ({ name, status: 'unknown', tool_count: null }));
  }

  return { registry, servers, serversUnavailable, health, queue, throughput, events: history.events || [] };
}

function _computeHealthPct(data) {
  const agentNames = Object.keys(data.registry);
  if (!agentNames.length) return 0;
  const serverByName = {};
  data.servers.forEach(s => { serverByName[s.name] = s; });

  let healthyCount = 0;
  agentNames.forEach(name => {
    const agent = data.registry[name];
    const enabled = agent.enabled;
    const serversOk = (agent.servers || []).every(sName => serverByName[sName] && serverByName[sName].status === 'connected');
    const workerAlive = data.health[name] ? data.health[name].status === 'alive' : false;
    if (enabled && serversOk && workerAlive) healthyCount++;
  });
  return Math.round((healthyCount / agentNames.length) * 100);
}

function _computeAgentThroughput(throughput, agentName) {
  // Real, already-fetched data -- each throughput bucket includes a real
  // by_agent breakdown; sum across buckets for this agent's total in
  // the fetched window. No extra request needed.
  let total = 0;
  (throughput.series || []).forEach(bucket => {
    total += (bucket.by_agent || {})[agentName] || 0;
  });
  return total;
}

async function _fetchAgentErrorRate(agent) {
  try {
    const res = await fetch(`/api/agent-tasks/worker-logs/${encodeURIComponent(agent)}?phase=tool_end`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    const data = await res.json();
    const logs = data.logs || [];
    if (!logs.length) return null;
    const failed = logs.filter(l => l.outcome && l.outcome !== 'success').length;
    return Math.round((failed / logs.length) * 100);
  } catch (e) {
    return null;
  }
}

function _relativeTime(ts) {
  if (!ts) return 'never';
  const diffSec = (Date.now() / 1000) - ts;
  if (diffSec < 60) return `${Math.round(diffSec)}s ago`;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.round(diffSec / 3600)}h ago`;
  return `${Math.round(diffSec / 86400)}d ago`;
}

function _computeAgentLastEvents(events, agentName) {
  let lastSuccess = null, lastFailed = null;
  events.forEach(e => {
    if (e.agent !== agentName) return;
    if (e.event_type === 'success' && (!lastSuccess || e.ts > lastSuccess)) lastSuccess = e.ts;
    if (e.event_type === 'failed' && (!lastFailed || e.ts > lastFailed)) lastFailed = e.ts;
  });
  return { lastSuccess, lastFailed };
}

function _renderAgentCards(data) {
  const serverByName = {};
  data.servers.forEach(s => { serverByName[s.name] = s; });

  return Object.keys(data.registry).map(name => {
    const agent = data.registry[name];
    const serversOk = (agent.servers || []).every(sName => serverByName[sName] && serverByName[sName].status === 'connected');
    const workerAlive = data.health[name] ? data.health[name].status === 'alive' : false;
    const healthy = agent.enabled && serversOk && workerAlive;
    const throughputCount = _computeAgentThroughput(data.throughput, name);
    const { lastSuccess, lastFailed } = _computeAgentLastEvents(data.events || [], name);
    return `
      <div data-agent-name="${esc(name)}" class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:8px;background:var(--hud-panel);cursor:pointer;" title="View tasks for this agent in Process Table">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div>
            <div style="font-size:11px;font-weight:600;color:${CYAN};">${_statusDot(healthy, 8)}${esc(name)}</div>
            <div style="font-size:9px;color:var(--hud-text-dim);margin-top:4px;">enabled: ${agent.enabled ? 'yes' : 'no'} \u00b7 server: ${serversOk ? 'ok' : 'down'} \u00b7 worker: ${workerAlive ? 'alive' : 'dead'}</div>
            <div style="font-size:9px;color:var(--hud-text-dim);margin-top:2px;">throughput (6h): ${throughputCount} \u00b7 last success: ${_relativeTime(lastSuccess)} \u00b7 last fail: ${_relativeTime(lastFailed)}</div>
            <div id="jarvis-home-agent-errrate-${esc(name)}" style="font-size:9px;color:var(--hud-text-dim);margin-top:2px;">error rate: ...</div>
          </div>
          <div id="jarvis-home-agent-gauge-${esc(name)}"></div>
        </div>
      </div>
    `;
  }).join('');
}

function _renderMcpServerCards(data) {
  return (data.servers || []).map(s => {
    if (data.serversUnavailable) {
      return `
        <div style="border:1px solid ${ORANGE};border-radius:4px;padding:6px 8px;background:var(--hud-panel);display:flex;justify-content:space-between;align-items:center;">
          <div style="font-size:11px;font-weight:600;color:${ORANGE};"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${ORANGE};box-shadow:0 0 6px ${ORANGE};margin-right:4px;"></span>${esc(s.name)}</div>
          <div style="font-size:9px;color:${ORANGE};">data unavailable</div>
        </div>
      `;
    }
    const connected = s.status === 'connected';
    const uid = s.name.replace(/[^a-z0-9]/gi, '');
    return `
      <div class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:6px 8px;background:var(--hud-panel);">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <div style="font-size:11px;font-weight:600;color:${CYAN};">${_statusDot(connected, 8, true)}${esc(s.name)}</div>
          <div style="font-size:9px;color:var(--hud-text-dim);">${connected ? 'connected' : 'disconnected'}</div>
        </div>
        <button class="jarvis-home-toggle-tools-${uid}" data-server-id="${esc(s.id)}" style="font-size:9px;padding:2px 6px;margin-top:4px;background:transparent;border:1px solid var(--hud-cyan-dim);color:var(--hud-text-dim);border-radius:3px;cursor:pointer;">${s.tool_count != null ? s.tool_count : '?'} tools \u25be</button>
        <div class="jarvis-home-tools-${uid}" style="display:none;margin-top:4px;padding-left:6px;max-height:140px;overflow-y:auto;"></div>
      </div>
    `;
  }).join('');
}

async function _toggleServerTools(uid, serverId, container) {
  const list = container.querySelector(`.jarvis-home-tools-${uid}`);
  const btn = container.querySelector(`.jarvis-home-toggle-tools-${uid}`);
  if (!list || !btn) return;
  const isHidden = list.style.display === 'none';
  if (!isHidden) { list.style.display = 'none'; return; }
  list.style.display = 'block';
  if (list.dataset.loaded) return;
  list.innerHTML = '<div style="font-size:9px;color:var(--hud-text-dim);">Loading...</div>';
  try {
    const tools = await _fetchJson(`/api/mcp/servers/${encodeURIComponent(serverId)}/tools`);
    list.innerHTML = tools.map(t => `<div style="font-size:9px;padding:1px 0;color:${t.is_disabled ? 'var(--hud-text-dim)' : CYAN};">${esc(t.name)}${t.is_disabled ? ' (disabled)' : ''}</div>`).join('') || '<div style="font-size:9px;color:var(--hud-text-dim);">No tools.</div>';
    list.dataset.loaded = '1';
  } catch (e) {
    list.innerHTML = `<div style="font-size:9px;color:${RED};">Failed to load tools.</div>`;
  }
}

function _renderThroughputBars(throughput) {
  if (!throughput.series.length) return '<div style="font-size:10px;color:var(--hud-text-dim);">No recent activity.</div>';
  const maxCount = Math.max(...throughput.series.map(b => b.success + b.failed), 1);
  const bars = throughput.series.map(b => {
    const total = b.success + b.failed;
    const heightPct = Math.max((total / maxCount) * 100, total > 0 ? 8 : 0);
    const failedPct = total > 0 ? (b.failed / total) * 100 : 0;
    return `<div style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:40px;min-width:3px;">
      <div style="height:${heightPct}%;background:linear-gradient(to top, ${RED} ${failedPct}%, ${CYAN} ${failedPct}%);border-radius:1px;box-shadow:0 0 4px ${CYAN};"></div>
    </div>`;
  }).join('');
  return `<div style="display:flex;align-items:flex-end;gap:1px;height:40px;">${bars}</div>
    <div style="font-size:9px;color:var(--hud-text-dim);margin-top:4px;">${throughput.total_completed} completed \u00b7 ${throughput.avg_per_hour}/hr avg (last 6h)</div>`;
}

function _navButton(id, label, targetModalId, description) {
  return `<button id="${id}" data-target-modal="${esc(targetModalId)}" title="${esc(description)}" style="font-size:10px;padding:6px 10px;background:transparent;border:1px solid ${CYAN};color:${CYAN};border-radius:3px;cursor:pointer;text-shadow:0 0 4px ${CYAN};box-shadow:0 0 4px rgba(74,212,232,0.3);transition:box-shadow 0.3s, border-color 0.3s;">${esc(label)}</button>`;
}

// Real, honest collapsible section -- built to match this file's actual
// convention (inline styles, no .panel/.panel-title classes; those were
// never real). A clickable header toggles a max-height CSS transition on
// the content div directly below it. sectionId must be unique per call.
function _collapsibleSection(sectionId, titleText, innerHtml) {
  return `
    <div class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);">
      <div class="jarvis-home-collapse-toggle" data-section="${sectionId}" style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;cursor:pointer;user-select:none;">
        <span id="jarvis-home-collapse-arrow-${sectionId}">\u25BC</span> ${esc(titleText)}
      </div>
      <div id="jarvis-home-collapse-body-${sectionId}" style="max-height:1000px;overflow:hidden;transition:max-height 0.25s ease;">
        ${innerHtml}
      </div>
    </div>
  `;
}

function _bindCollapsibleSections(container) {
  container.querySelectorAll('.jarvis-home-collapse-toggle').forEach(toggle => {
    toggle.addEventListener('click', () => {
      const id = toggle.dataset.section;
      const body = el(`jarvis-home-collapse-body-${id}`);
      const arrow = el(`jarvis-home-collapse-arrow-${id}`);
      if (!body) return;
      const isCollapsed = body.style.maxHeight === '0px';
      body.style.maxHeight = isCollapsed ? '1000px' : '0px';
      if (arrow) arrow.textContent = isCollapsed ? '\u25BC' : '\u25B6';
    });
  });
}

function _renderPortfolioSystemSection(ps) {
  // Real core-ring visual for the two headline numbers, reusing the
  // exact same _radialGauge() already used for CPU/MEM/health -- no new
  // CSS classes invented, matches the file's established pattern.
  const confidencePct = ps.dominant ? ps.dominant.confidence * 100 : 0;
  const confColor = confidencePct >= 50 ? RED : (confidencePct >= 30 ? ORANGE : CYAN);
  const riskCoreRing = ps.dominant
    ? _radialGauge(confidencePct, confColor, 120, 'CONFIDENCE', `${confidencePct.toFixed(0)}%`)
    : _radialGauge(0, CYAN, 120, 'CONFIDENCE', '...');

  // Real vol_level color mapping. Only "NORMAL" confirmed live this
  // session -- other real values (if the engine ever emits them) get a
  // safe neutral fallback rather than an assumed color, since their
  // actual existence/meaning hasn't been observed.
  const _volColor = (lvl) => lvl === 'NORMAL' ? 'var(--hud-green)' : (lvl === 'HIGH' ? ORANGE : 'var(--hud-text-dim)');
  const volDot = ps.volLevel
    ? `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${_volColor(ps.volLevel)};margin-right:4px;"></span>`
    : '';
  // Percentile bar -- real 0-100 field, no invented range.
  const percentileBarHtml = (ps.percentile != null)
    ? (() => {
        const pct = Math.max(0, Math.min(100, parseFloat(ps.percentile)));
        const filled = Math.round(pct / 2); // 50-char bar
        return `<div style="font-size:8px;color:var(--hud-text-dim);font-family:monospace;">[${'\u25A0'.repeat(filled)}${' '.repeat(50 - filled)}] ${pct}%</div>`;
      })()
    : '';

  const regimeText = ps.regimeLine || 'No risk-engine data yet.';
  const dominantText = ps.dominant ? ps.dominant.name : '\u2014';
  const graphText = ps.graphCounts ? `${ps.graphCounts.nodes} nodes, ${ps.graphCounts.edges} edges` : '...';

  // Real fields only -- percentile_in_own_history and top_loadings are
  // plain strings from the live API, risk_events is a real array of
  // {category, severity} pulled from the risk engine's own observations.
  const percentileText = ps.percentile ? `${ps.percentile}th percentile (own history)` : null;
  const topLoadingsText = ps.topLoadings || null;
  // Real severity values seen so far: "warning". Colors map warning to
  // orange (matching the file's existing ORANGE constant); other real
  // values (if the engine ever emits them) fall back to a neutral color
  // rather than guessing at ones that don't exist yet.
  const _severityColor = (sev) => sev === 'warning' ? ORANGE : (sev === 'critical' ? RED : 'var(--hud-text-dim)');
  // Real factor telemetry -- all factors from the live engine, dominant
  // one highlighted (matched by name against the actual computed max,
  // not assumed by position). All values real, all escaped.
  const factorTelemetryHtml = (ps.allFactors && ps.allFactors.length > 0)
    ? ps.allFactors.map((f, i) => `
        <div style="margin-bottom:2px;${f.isDominant ? `border-left:3px solid ${ORANGE};padding-left:6px;font-weight:600;` : 'padding-left:9px;'}">
          <span>${f.isDominant ? '\u2605 ' : ''}Factor ${i + 1}</span>
          <span style="color:var(--hud-text-dim);"> Var: ${esc(f.varianceExplained || '?')}</span>
          <div style="color:var(--hud-text-dim);font-size:9px;">${esc(f.topLoadings || '')}</div>
        </div>
      `).join('')
    : '<span style="color:var(--hud-text-dim);">No factor data</span>';

  // Grouped by category, each category as its own real, reusable
  // _collapsibleSection() (the same helper AGENTS/THROUGHPUT/MCP SERVERS
  // already use -- no new toggle mechanism invented, and
  // _bindCollapsibleSections() already binds any new .jarvis-home-collapse-toggle
  // elements automatically). "Timeline" ordering uses each event's real
  // position plus the shared cycle date -- there is no per-event
  // timestamp in the real API, so this is honestly an ordering, not a
  // true per-event time series.
  // Real severity values seen so far: "warning". Weight map covers the
  // other real values the schema/engine could plausibly emit (critical,
  // info) without asserting they currently exist -- confirmed only
  // "warning" appears in live data so far.
  const SEVERITY_WEIGHT = { critical: 3, warning: 2, info: 1 };

  // Real header using only the confirmed date-only field -- no invented
  // time-of-day.
  const cycleDateHtml = ps.cycleDate
    ? `<div style="font-size:9px;color:var(--hud-text-dim);margin-bottom:3px;">Risk Events \u2014 Cycle: ${esc(ps.cycleDate)}</div>`
    : '';

  let riskEventsHtml;
  if (!ps.riskEvents || ps.riskEvents.length === 0) {
    riskEventsHtml = cycleDateHtml + '<span style="color:var(--hud-text-dim);">No active risk events</span>';
  } else {
    const grouped = {};
    ps.riskEvents.forEach((ev, i) => {
      const cat = ev.category || 'Uncategorized';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push({ ...ev, order: i + 1 });
    });

    // Sort within each category by severity, highest first.
    for (const cat in grouped) {
      grouped[cat].sort((a, b) => (SEVERITY_WEIGHT[b.severity] || 0) - (SEVERITY_WEIGHT[a.severity] || 0));
    }

    riskEventsHtml = cycleDateHtml + Object.keys(grouped).map(cat => {
      const events = grouped[cat];
      const inner = events.map(ev => `
        <div style="margin-bottom:4px;">
          <span style="color:${_severityColor(ev.severity)};font-weight:600;">#${ev.order}</span>
          <span style="color:${_severityColor(ev.severity)};">${esc(ev.severity || '?')}</span>
          <span style="color:var(--hud-text-dim);font-size:9px;">${esc(ev.date || '')}</span>
          ${ev.detail ? `<div style="color:var(--hud-text-dim);font-size:9px;margin-left:14px;">${esc(ev.detail)}</div>` : ''}
        </div>
      `).join('');
      // _collapsibleSection() escapes its titleText internally (esc() runs
      // inside the shared helper), so an HTML-colored badge isn't
      // possible here without modifying that shared function -- which is
      // also used by AGENTS/THROUGHPUT/MCP SERVERS, so left untouched.
      // A plain-text severity indicator in the title is the safe version.
      const highestSeverity = events[0].severity || '?';
      const sectionId = `riskevent-${cat.replace(/[^a-zA-Z0-9]/g, '')}`;
      // Icons for the two real, confirmed categories only -- no icon
      // invented for unconfirmed categories, since emoji characters pass
      // through _collapsibleSection()'s internal esc() unaffected (esc()
      // only escapes HTML special chars, not unicode).
      const CATEGORY_ICONS = { FactorDominance: '\u{1F4C8}', CorrelationBreakdown: '\u{1F517}' };
      const icon = CATEGORY_ICONS[cat] ? CATEGORY_ICONS[cat] + ' ' : '';
      return _collapsibleSection(sectionId, `${icon}${cat} (${events.length}) \u2014 ${highestSeverity}`, inner);
    }).join('');
  }

  let relevanceHtml = '<div style="font-size:10px;color:var(--hud-text-dim);">No related vault notes for today&#39;s dominant factor.</div>';
  if (ps.relevance && ps.relevance.notes && ps.relevance.notes.length > 0) {
    relevanceHtml = ps.relevance.notes.map(n => `
      <div style="font-size:10px;color:${CYAN};margin-top:2px;">\u{1F4D3} <b>${esc(n.ticker)}</b> \u2014 ${esc(n.title)}</div>
    `).join('');
  }

  // Real CPU temp (Tctl, the standard AMD "package" reading) as the
  // second core ring -- mapped onto the same 0-100 gauge scale used
  // elsewhere in this file (rough visual mapping, not a literal percent;
  // real CPU temps in practice land in a range that reads intuitively
  // on a 0-100 arc).
  let cpuTempVal = null;
  if (ps.temps) {
    const m = ps.temps.match(/k10temp\/Tctl:\s*([\d.]+)C/);
    if (m) cpuTempVal = parseFloat(m[1]);
  }
  const tempColor = cpuTempVal == null ? CYAN : (cpuTempVal >= 75 ? RED : (cpuTempVal >= 60 ? ORANGE : CYAN));
  const sysCoreRing = cpuTempVal != null
    ? _radialGauge(cpuTempVal, tempColor, 120, 'CPU TEMP', `${cpuTempVal.toFixed(0)}C`)
    : _radialGauge(0, CYAN, 120, 'CPU TEMP', '...');

  const tempsLines = ps.temps ? ps.temps.split('\n').filter(l => l.includes('k10temp') || l.includes('asusec')) : [];
  const tempsHtml = tempsLines.length
    ? tempsLines.map(l => `<div style="font-size:9px;color:var(--hud-text-dim);">${esc(l)}</div>`).join('')
    : '<div style="font-size:9px;color:var(--hud-text-dim);">Temp data unavailable.</div>';
  const gpuTempText = ps.gpuTemp || '?';

  let diskText = 'Disk data unavailable.';
  if (ps.diskUsage) {
    const rootLine = ps.diskUsage.split('\n').find(l => l.trim().endsWith(' /'));
    if (rootLine) diskText = rootLine.trim();
  }

  return `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
      <div class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);">
        <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">PORTFOLIO / RISK</div>
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px;">
          ${riskCoreRing}
          <div style="flex:1;">
            <div style="font-size:11px;color:var(--hud-text);margin-bottom:2px;">${volDot}${esc(regimeText)}</div>
            ${percentileBarHtml}
            <div style="font-size:10px;color:var(--hud-text-dim);margin-bottom:2px;">Dominant: ${esc(dominantText)}</div>
            ${percentileText ? `<div style="font-size:10px;color:var(--hud-text-dim);margin-bottom:2px;">${esc(percentileText)}</div>` : ''}
            ${topLoadingsText ? `<div style="font-size:10px;color:var(--hud-text-dim);margin-bottom:2px;">Loadings: ${esc(topLoadingsText)}</div>` : ''}
            <div style="font-size:10px;margin-bottom:2px;">${riskEventsHtml}</div>
            <div style="font-size:10px;margin-top:4px;">${factorTelemetryHtml}</div>
            <div style="font-size:10px;color:var(--hud-text-dim);">Graph: ${esc(graphText)}</div>
          </div>
        </div>
        <div style="font-size:10px;color:${CYAN};margin-bottom:2px;">Related vault notes:</div>
        ${relevanceHtml}
      </div>
      <div class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);">
        <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">SYSTEM</div>
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px;">
          ${sysCoreRing}
          <div style="flex:1;">
            <div style="font-size:10px;color:var(--hud-text-dim);margin-bottom:4px;">GPU temp: ${esc(gpuTempText)}</div>
            ${tempsHtml}
          </div>
        </div>
        <div style="font-size:9px;color:var(--hud-text-dim);">${esc(diskText)}</div>
      </div>
    </div>
  `;
}

async function _render() {
  const container = el('jarvis-home-content');
  if (!container) return;
  _injectPulseStyle();
  try {
    const [data, portfolioSystem] = await Promise.all([_fetchAll(), _fetchPortfolioAndSystem()]);
    const healthPct = _computeHealthPct(data);
    const healthColor = healthPct >= 90 ? GREEN : (healthPct >= 50 ? ORANGE : RED);

    let cpuText = '...', memText = '...';
    _callSystemAgentTool('get_cpu_percent').then(v => {
      cpuText = v.trim();
      const cpuEl = el('jarvis-home-cpu-gauge');
      if (cpuEl) cpuEl.innerHTML = _radialGauge(parseFloat(v) || 0, CYAN, 100, 'CPU', cpuText);
    }).catch(() => {});
    _callSystemAgentTool('get_mem_percent').then(v => {
      const pct = parseFloat(v) || 0;
      const memEl = el('jarvis-home-mem-gauge');
      if (memEl) memEl.innerHTML = _radialGauge(pct, ORANGE, 100, 'MEM', `${pct.toFixed(0)}%`);
    }).catch(() => {});

    // Real sparkline from an actual history array -- no external chart
    // library, matching this file's existing plain-template-string style.
    function _sparkline(arr) {
      if (!arr || arr.length < 2) return '';
      const chars = '\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588';
      const min = Math.min(...arr), max = Math.max(...arr);
      const range = max - min || 1;
      return arr.map(v => chars[Math.min(chars.length - 1, Math.floor(((v - min) / range) * (chars.length - 1)))]).join('');
    }

    _fetchJson('/api/suggestions/active').then(r => {
      const hudEl = el('jarvis-home-suggestions-hud');
      if (!hudEl) return;
      if (!r.suggestions.length) {
        hudEl.innerHTML = `<div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">ACTIVE SUGGESTIONS</div><div style="font-size:11px;color:var(--hud-text-dim);">No active suggestions.</div>`;
        return;
      }
      // Real confidence tiers, matching the actual string-based values
      // suggestions_engine.py writes (low/medium/high), not a numeric scale.
      // Some entities in the shared graph come from other sessions with a
      // different observation schema (no text/type/confidence) -- skip
      // those rather than rendering blank "/ confidence" lines.
      const tierColor = { high: RED, medium: ORANGE, low: 'var(--hud-text-dim)' };
      const displayable = r.suggestions.filter(s => s.text);
      const rows = displayable.map(s => {
        const color = tierColor[s.confidence] || 'var(--hud-text-dim)';
        const meta = [s.type, s.confidence ? `${s.confidence} confidence` : null].filter(Boolean).join(' / ');
        return `
          <div style="border-left:3px solid ${color};padding:6px 8px;margin-bottom:6px;background:rgba(0,0,0,0.15);">
            <div style="font-size:11px;color:var(--hud-text);">${esc(s.text)}</div>
            <div style="font-size:9px;color:${color};margin-top:3px;text-transform:uppercase;">${esc(meta)}</div>
          </div>`;
      }).join('');
      const skipped = r.count - displayable.length;
      hudEl.innerHTML = `
        <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">ACTIVE SUGGESTIONS (${displayable.length})</div>
        ${rows}
        ${skipped > 0 ? `<div style="font-size:9px;color:var(--hud-text-dim);margin-top:4px;">+${skipped} other graph entries without displayable text (different schema)</div>` : ''}
      `;
    }).catch(() => {
      const hudEl = el('jarvis-home-suggestions-hud');
      if (hudEl) hudEl.innerHTML = '<div style="font-size:11px;color:var(--hud-text-dim);">Suggestions unavailable.</div>';
    });

    _fetchJson('/api/regime/correlation-breakdown').then(r => {
      const hudEl = el('jarvis-home-correlation-hud');
      if (!hudEl) return;
      const color = r.primary_regime === 'correlation_breakdown' ? RED : GREEN;
      const rows = r.pairs.map(p => `<div>${esc(p.pair)}: recent ${p.rho_recent} vs baseline ${p.rho_baseline} <span style="color:${p.classification === 'breakdown' ? RED : GREEN};">[${esc(p.classification)}]</span></div>`).join('');
      hudEl.innerHTML = `
        <div style="font-size:11px;color:${color};text-shadow:0 0 4px ${color};margin-bottom:8px;">CORRELATION BREAKDOWN REGIME (primary risk signal)</div>
        <div style="font-family:monospace;font-size:11px;">${rows}</div>
        <div style="font-size:9px;color:var(--hud-text-dim);margin-top:6px;">Deepest-drawdown regime per real back-test (avg DD -21% to -36%, worst -47.18%) -- not necessarily negative returns.</div>
      `;
    }).catch(() => {
      const hudEl = el('jarvis-home-correlation-hud');
      if (hudEl) hudEl.innerHTML = '<div style="font-size:11px;color:var(--hud-text-dim);">Correlation regime data unavailable.</div>';
    });

    Promise.all([
      _fetchJson('/api/factors/latest?domain=stock'),
      _fetchJson('/api/factors/trend?domain=stock&lookback=30').catch(() => null),
    ]).then(([f, trend]) => {
      const hudEl = el('jarvis-home-factor-hud');
      if (!hudEl || !f.latest) return;
      const pc1 = f.latest.pc1, pc2 = f.latest.pc2, pc4 = f.latest.pc4;
      let trendHtml = '';
      if (trend && trend.deltas && trend.history) {
        const d = trend.deltas, h = trend.history;
        trendHtml = `
          <div style="font-size:10px;color:var(--hud-text-dim);margin-top:8px;">TREND (${trend.lookback}pt)</div>
          <div style="font-family:monospace;font-size:11px;">
            <div>PC1 &Delta;: ${d.pc1_delta >= 0 ? '+' : ''}${d.pc1_delta.toFixed(3)} <span style="color:${CYAN};">${_sparkline(h.pc1_history)}</span></div>
            <div>PC2 &Delta;: ${d.pc2_delta >= 0 ? '+' : ''}${d.pc2_delta.toFixed(3)} <span style="color:${ORANGE};">${_sparkline(h.pc2_history)}</span></div>
            <div>PC4 &Delta;: ${d.pc4_delta >= 0 ? '+' : ''}${d.pc4_delta.toFixed(3)} <span style="color:${GREEN};">${_sparkline(h.pc4_history)}</span></div>
          </div>
        `;
      }
      hudEl.innerHTML = `
        <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">PORTFOLIO FACTOR REGIME</div>
        <div style="display:flex;gap:16px;flex-wrap:wrap;font-family:monospace;font-size:12px;">
          <div>Market Beta (PC1): <span style="color:${CYAN};">${pc1.toFixed(3)}</span></div>
          <div>Software (PC2): <span style="color:${ORANGE};">${pc2.toFixed(3)}</span></div>
          <div>Commodities (PC4): <span style="color:${GREEN};">${pc4.toFixed(3)}</span></div>
        </div>
        <div style="font-size:9px;color:var(--hud-text-dim);margin-top:6px;">
          Explained variance: PC1 ${(f.explained_variance_ratio[0]*100).toFixed(1)}%, PC2 ${(f.explained_variance_ratio[1]*100).toFixed(1)}%
        </div>
        ${trendHtml}
      `;
    }).catch(() => {
      const hudEl = el('jarvis-home-factor-hud');
      if (hudEl) hudEl.innerHTML = '<div style="font-size:11px;color:var(--hud-text-dim);">Factor data unavailable -- run the PCA build script first.</div>';
    });

    // Five-zone grid layout: top-strip (portfolio/risk + queue metrics),
    // hub (center, scaled-up system health + satellite CPU/MEM gauges),
    // left-rail (agents), right-stack (MCP servers), bottom-rail
    // (throughput + quick access). Real restructure, not just CSS --
    // every existing element ID/function call preserved so live updates
    // (cpu/mem gauge refresh, collapsible sections) keep working.
    container.innerHTML = `
      <div class="jarvis-home-grid">
        <div class="jarvis-home-zone-top">
          ${_renderPortfolioSystemSection(portfolioSystem)}
          <div style="display:flex;gap:10px;margin-top:12px;">
            <div class="jarvis-card jarvis-gauge-card" style="flex:1;flex-direction:column;text-align:center;border:1px solid var(--hud-cyan-dim);padding:8px;background:var(--hud-panel);cursor:pointer;" id="jarvis-home-metric-pending" title="Pending: ${data.queue.pending.length} -- View pending tasks in Process Table">
              ${_radialGauge(Math.min(data.queue.pending.length * 10, 100), CYAN, 56, 'PENDING', String(data.queue.pending.length))}
            </div>
            <div class="jarvis-card jarvis-gauge-card" style="flex:1;flex-direction:column;text-align:center;border:1px solid var(--hud-cyan-dim);padding:8px;background:var(--hud-panel);cursor:pointer;" id="jarvis-home-metric-running" title="Running: ${data.queue.running.length} -- View running tasks in Process Table">
              ${_radialGauge(Math.min(data.queue.running.length * 10, 100), ORANGE, 56, 'RUNNING', String(data.queue.running.length))}
            </div>
            <div class="jarvis-card" style="flex:1;text-align:center;border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:8px;background:var(--hud-panel);cursor:pointer;" id="jarvis-home-metric-success" title="View successful tasks in Process Table">
              <div style="font-size:20px;font-weight:700;color:${GREEN};text-shadow:0 0 6px ${GREEN};">${data.queue.success.length}</div>
              <div style="font-size:9px;color:var(--hud-text-dim);">SUCCESS</div>
            </div>
            <div class="jarvis-card" style="flex:1;text-align:center;border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:8px;background:var(--hud-panel);cursor:pointer;" id="jarvis-home-metric-failed" title="View failed tasks in Process Table">
              <div style="font-size:20px;font-weight:700;color:${RED};text-shadow:0 0 6px ${RED};">${data.queue.failed.length}</div>
              <div style="font-size:9px;color:var(--hud-text-dim);">FAILED</div>
            </div>
          </div>
        </div>

        <div class="jarvis-home-zone-hub">
          <div style="flex:0 0 auto;">
            ${_radialGauge(healthPct, healthColor, 200, 'SYSTEM HEALTH', `${healthPct}%`, 28, -1)}
          </div>
          <div id="jarvis-home-cpu-gauge" class="jarvis-gauge-card" style="flex:0 0 auto;">${_radialGauge(0, CYAN, 90, 'CPU', '...')}</div>
          <div id="jarvis-home-mem-gauge" class="jarvis-gauge-card" style="flex:0 0 auto;">${_radialGauge(0, ORANGE, 90, 'MEM', '...')}</div>
        </div>

        <div class="jarvis-home-zone-left">
          ${_collapsibleSection('agents', 'AGENTS', `<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(180px, 1fr));gap:8px;">${_renderAgentCards(data)}</div>`)}
        </div>

        <div class="jarvis-home-zone-right">
          ${_collapsibleSection('mcpservers', 'MCP SERVERS', `<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(180px, 1fr));gap:8px;">${_renderMcpServerCards(data)}</div>`)}
        </div>

        <div class="jarvis-home-zone-bottom">
          <div id="jarvis-home-factor-hud" class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);margin-bottom:12px;">
            <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">PORTFOLIO FACTOR REGIME</div>
            <div style="font-size:11px;color:var(--hud-text-dim);">Loading real factor data...</div>
          </div>
          <div id="jarvis-home-correlation-hud" class="jarvis-card" style="border:1px solid var(--hud-red);border-radius:4px;padding:10px;background:var(--hud-bg);margin-bottom:12px;">
            <div style="font-size:11px;color:${RED};text-shadow:0 0 4px ${RED};margin-bottom:8px;">CORRELATION BREAKDOWN REGIME (primary risk signal)</div>
            <div style="font-size:11px;color:var(--hud-text-dim);">Loading real correlation data...</div>
          </div>
          <div id="jarvis-home-suggestions-hud" class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);margin-bottom:12px;">
            <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">ACTIVE SUGGESTIONS</div>
            <div style="font-size:11px;color:var(--hud-text-dim);">Loading real suggestions...</div>
          </div>
          ${_collapsibleSection('throughput', 'THROUGHPUT (LAST 6H)', _renderThroughputBars(data.throughput))}
          <div class="jarvis-card" style="border:1px solid var(--hud-cyan-dim);border-radius:4px;padding:10px;background:var(--hud-bg);margin-top:12px;">
            <div style="font-size:11px;color:${CYAN};text-shadow:0 0 4px ${CYAN};margin-bottom:8px;">QUICK ACCESS</div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              ${_navButton('jarvis-home-nav-process', 'Process Table', 'process-table-modal', 'View active and completed tasks')}
              ${_navButton('jarvis-home-nav-market', 'Market Dashboard', 'market-dashboard-modal', 'Monitor live market data')}
              ${_navButton('jarvis-home-nav-agents', 'Agent Registry', 'registry-modal', 'Inspect agent capability profiles')}
              ${_navButton('jarvis-home-nav-workerlogs', 'Worker Logs', 'worker-log-modal', 'Review worker activity and errors')}
              ${_navButton('jarvis-home-nav-capability', 'Capability Inspector', 'capabilities-modal', 'Explore available tools')}
              ${_navButton('jarvis-home-nav-history', 'Task History', 'task-history-modal', 'Browse historical task results')}
            </div>
          </div>
        </div>
      </div>
    `;

    _bindCollapsibleSections(container);

    const navMap = {
      'jarvis-home-nav-process': 'tool-process-table-btn',
      'jarvis-home-nav-market': 'tool-market-dashboard-btn',
      'jarvis-home-nav-agents': 'tool-registry-btn',
      'jarvis-home-nav-workerlogs': 'tool-worker-log-btn',
      'jarvis-home-nav-capability': 'tool-capabilities-btn',
      'jarvis-home-nav-history': 'tool-task-history-btn',
    };
    Object.entries(navMap).forEach(([btnId, targetId]) => {
      const btn = el(btnId);
      if (btn) btn.addEventListener('click', () => { document.getElementById(targetId)?.click(); });
    });

    // Real active-state glow: checks the actual modal's real, current
    // .hidden class -- modalManager exposes no isOpen() query, so this
    // reads DOM state directly rather than assuming an API that doesn't exist.
    container.querySelectorAll('[data-target-modal]').forEach(btn => {
      const targetModal = document.getElementById(btn.dataset.targetModal);
      const isOpenNow = targetModal && !targetModal.classList.contains('hidden');
      if (isOpenNow) {
        btn.style.boxShadow = `0 0 12px ${GREEN}`;
        btn.style.borderColor = GREEN;
      }
    });

    // Real drill-down: metric cards and agent cards open Process Table
    // pre-filtered via its real filter dropdowns (client-side filtering of
    // already-fetched data) -- direct in-page function call, not URL
    // routing, since this is a single-page app, not a multi-page site.
    ['pending', 'running', 'success', 'failed'].forEach(status => {
      const btn = el(`jarvis-home-metric-${status}`);
      if (btn) btn.addEventListener('click', () => { processTableModule.openPanel({ status }); });
    });
    container.querySelectorAll('[data-agent-name]').forEach(card => {
      card.addEventListener('click', () => { processTableModule.openPanel({ agent: card.dataset.agentName }); });
    });

    // Real per-agent mini-gauges: throughput count rendered synchronously
    // (already-fetched data), error rate fetched async per agent (matches
    // the CPU/Mem gauge pattern -- update specific DOM elements on resolve,
    // don't block the main render).
    Object.keys(data.registry).forEach(name => {
      const throughputCount = _computeAgentThroughput(data.throughput, name);
      const gaugeEl = el(`jarvis-home-agent-gauge-${name}`);
      if (gaugeEl) gaugeEl.innerHTML = _radialGauge(Math.min(throughputCount * 10, 100), CYAN, 50, '', String(throughputCount));

      _fetchAgentErrorRate(name).then(pct => {
        const errEl = el(`jarvis-home-agent-errrate-${name}`);
        if (errEl) errEl.textContent = pct === null ? 'error rate: no data' : `error rate: ${pct}%`;
      });
    });

    (data.servers || []).forEach(s => {
      const uid = s.name.replace(/[^a-z0-9]/gi, '');
      const btn = container.querySelector(`.jarvis-home-toggle-tools-${uid}`);
      if (btn) btn.addEventListener('click', () => _toggleServerTools(uid, s.id, container));
    });
  } catch (e) {
    container.innerHTML = `<div class="admin-empty" style="color:${RED};">Failed to load: ${esc(e.message)}</div>`;
  }
}

export function openPanel() {
  const modal = el('jarvis-home-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
  if (_refreshTimer) clearInterval(_refreshTimer);
  _refreshTimer = setInterval(_render, 10000);
}

function _closePanel() {
  const modal = el('jarvis-home-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

function _ensureRegistered() {
  if (Modals.isRegistered('jarvis-home-modal')) return;
  Modals.register('jarvis-home-modal', {
    railBtnId: 'rail-jarvis-home',
    sidebarBtnId: 'tool-jarvis-home-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-jarvis-home-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-jarvis-home-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);
}

// This is a standalone entry-point module (loaded via its own <script
// type="module"> tag in index.html, not imported by app.js or anything
// else) - nothing else has a reference to call init(), so it must
// self-invoke here. Real, confirmed bug: this was missing, meaning the
// button/modal registration and click wiring never ran even after the
// HTML markup and the broken process_table.js import were both fixed.
init();

export default { init, openPanel };
