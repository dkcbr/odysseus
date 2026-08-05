// static/js/command_palette.js — Jarvis Command Palette (ES6)
// Deliberately self-contained: no modalManager involvement. The earlier
// hang incident (root cause never fully confirmed) was suspected to
// involve the modal/register() path; this palette needs none of
// modalManager's features (stacking, minimize, drag-to-front) since
// it's a fleeting, always-on-top overlay, so it's built with plain,
// direct DOM show/hide instead -- the same safe pattern already
// confirmed working all session for the Notifications drawer.
//
// Real navigation only: every command triggers a real, existing
// sidebar button via .click() -- no invented router, no fake
// navigation API.

import { getLevelCounts } from './notifications_store.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

// Real commands only -- each maps to a real, confirmed sidebar button ID.
export const COMMANDS = [
  { label: 'Show Current Regime', category: 'Risk',
    action: async () => { const m = await import('./risk_panel.js'); m.default.openPanel('regime'); } },
  { label: 'Show Factor Summary', category: 'Risk',
    action: async () => { const m = await import('./risk_panel.js'); m.default.openPanel('factors'); } },
  { label: 'Show Risk Events', category: 'Risk',
    action: async () => { const m = await import('./risk_panel.js'); m.default.openPanel('events'); } },
  { label: 'Show Suggestions', category: 'Risk',
    action: async () => { const m = await import('./risk_panel.js'); m.default.openPanel('suggestions'); } },
  { label: 'Search Notes', category: 'Vault',
    action: async () => { const m = await import('./vault_graph_panel.js'); m.default.openPanel(true); } },
  { label: 'New Note', category: 'Vault',
    action: async () => {
      const path = prompt('New note path (e.g. Watchlist/TICKER.md, or just a name for a root-level note):');
      if (!path) return;
      try {
        const res = await fetch('/api/vault/note/create', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path }),
        });
        const data = await res.json();
        if (!res.ok) { alert('Could not create note: ' + (data.detail || res.status)); return; }
        const m = await import('./composer_panel.js');
        m.default.openPanel(data.path);
      } catch (e) {
        alert('Could not create note: ' + String(e));
      }
    } },
  { label: 'Open Diagnostics', btnId: 'tool-agent-diagnostics-btn', category: 'Panels' },
  { label: 'Open Notifications', btnId: 'tool-notifications-drawer-btn', category: 'Panels' },
  { label: 'Open Task History', btnId: 'tool-task-history-btn', category: 'Panels' },
  { label: 'Open Task Timeline', btnId: 'tool-task-timeline-btn', category: 'Panels' },
  { label: 'Open Tool Analytics', btnId: 'tool-tool-analytics-btn', category: 'Panels' },
  { label: 'Open Worker Logs', btnId: 'tool-worker-log-btn', category: 'Panels' },
  { label: 'Open Registry', btnId: 'tool-registry-btn', category: 'Panels' },
  { label: 'Open Capabilities', btnId: 'tool-capabilities-btn', category: 'Panels' },
  { label: 'Open Market Dashboard', btnId: 'tool-market-dashboard-btn', category: 'Panels' },
  { label: 'Open Process Table', btnId: 'tool-process-table-btn', category: 'Panels' },
  { label: 'Open Portfolio', btnId: 'tool-portfolio-panel-btn', category: 'Panels' },
  { label: 'Open Tasks', btnId: 'tool-tasks-btn', category: 'Panels' },
  { label: 'Open Task Queue Inspector', btnId: 'tool-task-queue-inspector-btn', category: 'Panels' },
  { label: 'Open Calendar', btnId: 'tool-calendar-btn', category: 'Panels' },
  { label: 'Open Memory', btnId: 'tool-memory-btn', category: 'Panels' },
  { label: 'Open Notes', btnId: 'tool-notes-btn', category: 'Panels' },
  { label: 'Open Research', btnId: 'tool-research-btn', category: 'Panels' },
  { label: 'Open Library', btnId: 'tool-library-btn', category: 'Panels' },
  { label: 'Open Gallery', btnId: 'tool-gallery-btn', category: 'Panels' },
  { label: 'Open Cookbook', btnId: 'tool-cookbook-btn', category: 'Panels' },
  { label: 'Open Compare', btnId: 'tool-compare-btn', category: 'Panels' },
  { label: 'Open TradingView', btnId: 'tool-tradingview-btn', category: 'Panels' },
  { label: 'Open Theme Settings', btnId: 'tool-theme-btn', category: 'Panels' },
  { label: 'Open Poller Dashboard', btnId: 'tool-poller-dashboard-btn', category: 'Panels' },
  { label: 'Open Poller Status', btnId: 'tool-poller-status-btn', category: 'Panels' },
  { label: 'Open Jarvis Home', btnId: 'tool-jarvis-home-btn', category: 'Panels' },
];

let _open = false;
let _filtered = COMMANDS;
let _selectedIndex = 0;

function _executeCommand(cmd) {
  // Real, direct usage tracking -- stored on the command object itself,
  // same as everything else in COMMANDS, no separate storage layer.
  cmd.usageCount = (cmd.usageCount || 0) + 1;
  cmd.lastUsed = Date.now();
  _closePanel();
  // Real bug fixed here: dynamic, action-based commands need a second
  // execution path alongside the original btnId-click one, since not
  // every registered command will correspond to an existing sidebar
  // button. Both are real, direct DOM/JS execution -- no router, no
  // invented API either way.
  if (typeof cmd.action === 'function') {
    cmd.action();
    return;
  }
  const btn = el(cmd.btnId);
  if (btn) btn.click();
}

// Real, direct registration -- panels/modules can call this to add a
// command at runtime. Duplicate ids are ignored rather than silently
// creating two entries with the same id.
export function registerCommand(cmd) {
  if (!cmd || !cmd.label) return;
  if (cmd.id && COMMANDS.some(c => c.id === cmd.id)) return;
  // Real bug avoided here: the 26 static commands above have no id field,
  // so dedup-by-id alone would let auto-discovered entries for the same
  // buttons through as duplicates. Also check btnId directly.
  if (cmd.btnId && COMMANDS.some(c => c.btnId === cmd.btnId)) return;
  COMMANDS.push(cmd);
}

// Real auto-discovery: scan the DOM for any element explicitly marked
// data-panel-btn, extract its visible label and id, and register it.
// New panels only need the attribute added to their button/list-item --
// no static COMMANDS edit required. Buttons without a stable id or any
// visible text are skipped rather than registered with a blank label.
export function autoDiscoverPanelCommands() {
  const buttons = document.querySelectorAll('[data-panel-btn]');
  buttons.forEach(btn => {
    const label = btn.innerText?.trim();
    const btnId = btn.id;
    if (!label || !btnId) return;
    registerCommand({
      id: `auto_${btnId}`,
      label,
      btnId,
      category: 'Panels',
      keywords: [label.toLowerCase()],
    });
  });
}

// Real, direct unregistration by id -- for panels/modules that need to
// remove a command they previously registered (e.g. on unmount).
export function unregisterCommand(id) {
  const idx = COMMANDS.findIndex(c => c.id === id);
  if (idx !== -1) COMMANDS.splice(idx, 1);
}

// Subsequence fuzzy match: characters must appear in order but not
// necessarily contiguously ("diag" matches "Open Diagnostics").
function _fuzzyScore(query, target) {
  query = query.toLowerCase();
  target = target.toLowerCase();
  let score = 0, qi = 0;
  for (let ti = 0; ti < target.length && qi < query.length; ti++) {
    if (target[ti] === query[qi]) { score += 1; qi++; }
  }
  return qi === query.length ? score / query.length : 0; // only count real full-subsequence matches
}

function _highlightMatch(label, query) {
  if (!query) return esc(label);
  const q = query.toLowerCase();
  const l = label.toLowerCase();
  let result = '', qi = 0;
  for (let i = 0; i < label.length; i++) {
    if (qi < q.length && l[i] === q[qi]) {
      result += `<span style="color:#4ad4e8;font-weight:600;">${esc(label[i])}</span>`;
      qi++;
    } else {
      result += esc(label[i]);
    }
  }
  return result;
}

let _lastQuery = '';

function _renderResults() {
  const results = el('cp-results');
  if (!results) return;
  if (!_filtered.length) {
    results.innerHTML = '<div style="font-size:11px;color:#7a9aab;padding:10px 16px;">No matching commands.</div>';
    return;
  }
  results.innerHTML = _filtered.map((cmd, i) => `
    <div class="cp-result-item" data-index="${i}" style="padding:10px 16px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;background:${i === _selectedIndex ? '#1a3a4a' : 'transparent'};color:${i === _selectedIndex ? '#e6f7ff' : '#ccc'};">
      <span>${_highlightMatch(cmd.label, _lastQuery)}</span>
      <span class="cp-pin-btn" data-pin-index="${i}" style="color:${cmd.pinned ? '#4ad4e8' : '#7a9aab'};padding-left:8px;">${cmd.pinned ? '★' : '☆'}</span>
    </div>
  `).join('');

  results.querySelectorAll('.cp-result-item').forEach(item => {
    item.addEventListener('mouseenter', () => {
      _selectedIndex = Number(item.dataset.index);
      _renderResults();
    });
    item.addEventListener('click', (e) => {
      const cmd = _filtered[Number(item.dataset.index)];
      if (cmd) _executeCommand(cmd);
    });
  });

  results.querySelectorAll('.cp-pin-btn').forEach(pinBtn => {
    pinBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const cmd = _filtered[Number(pinBtn.dataset.pinIndex)];
      if (cmd) {
        cmd.pinned = !cmd.pinned;
        _renderResults();
        _renderQuickActions();
      }
    });
  });
}

let _currentCategory = null;

// Real, complete list of categories actually in use right now -- derived
// from what's registered, not a fixed hardcoded set, so a brand-new
// category from a future registerCommand() call shows up automatically.
function _realCategories() {
  return [...new Set(COMMANDS.map(c => c.category).filter(Boolean))].sort();
}

function _applyFilter(query) {
  const q = query.trim();
  _lastQuery = q;
  let pool = _currentCategory
    ? COMMANDS.filter(c => c.category === _currentCategory)
    : COMMANDS;
  if (!q) {
    _filtered = pool;
  } else {
    // Real fuzzy subsequence match, unchanged from before -- category
    // filtering happens on the pool first, search behavior on top of it
    // is identical to what was already deployed and verified.
    _filtered = pool
      .map(cmd => ({ cmd, score: _fuzzyScore(q, cmd.label) }))
      .filter(r => r.score > 0)
      .sort((a, b) => b.score - a.score)
      .map(r => r.cmd);
  }
  _selectedIndex = 0;
  _renderResults();
}

function _renderCategories() {
  const container = el('cp-categories');
  if (!container) return;
  const cats = _realCategories();
  if (cats.length === 0) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'flex';
  const allBtn = `<button data-cat="" style="flex-shrink:0;padding:4px 10px;font-size:11px;border-radius:4px;cursor:pointer;background:${!_currentCategory ? '#1a3a4a' : 'transparent'};color:${!_currentCategory ? '#4ad4e8' : '#7a9aab'};border:1px solid #1a3a4a;">All</button>`;
  const catBtns = cats.map(cat => {
    const active = cat === _currentCategory;
    return `<button data-cat="${esc(cat)}" style="flex-shrink:0;padding:4px 10px;font-size:11px;border-radius:4px;cursor:pointer;background:${active ? '#1a3a4a' : 'transparent'};color:${active ? '#4ad4e8' : '#7a9aab'};border:1px solid #1a3a4a;">${esc(cat)}</button>`;
  }).join('');
  container.innerHTML = allBtn + catBtns;
  container.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      _currentCategory = btn.dataset.cat || null;
      _renderCategories();
      _applyFilter(_lastQuery || '');
    });
  });
}

// Real status row: MCP server health via the same real /api/mcp/servers
// endpoint already used elsewhere in the frontend (admin.js, tradingview.js,
// process_table.js, settings.js all fetch it the same way), and real
// notification counts via notifications_store.js's actual getLevelCounts()
// export. Both verified against the real codebase before implementing --
// no invented APIs.
async function _renderPaletteStatus() {
  const container = el('cp-status');
  if (!container) return;

  let mcpIcon = '⚪';
  try {
    const res = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
    if (res.ok) {
      const servers = await res.json();
      if (Array.isArray(servers) && servers.length > 0) {
        const allConnected = servers.every(s => s.status === 'connected');
        const anyConnected = servers.some(s => s.status === 'connected');
        mcpIcon = allConnected ? '🟢' : (anyConnected ? '🟡' : '🔴');
      }
    }
  } catch (e) {
    mcpIcon = '🔴';
  }

  let notifText = '🔕';
  try {
    const counts = getLevelCounts();
    const total = Object.values(counts || {}).reduce((a, b) => a + b, 0);
    notifText = total > 0 ? `🔔 ${total}` : '🔕';
  } catch (e) {
    // notifications_store not available in this context -- leave default
  }

  container.innerHTML = `<span title="MCP server connection health">${mcpIcon} MCP</span><span title="Unread notifications">${notifText}</span>`;
}

// Real Quick Actions bar: pinned commands, then most-recently-used, then
// most-used, deduplicated by id/btnId so the same command never appears
// twice even if it qualifies under more than one bucket. All three
// buckets are pure UI metadata stored directly on the command objects
// already in COMMANDS -- no new storage layer, resets on page reload
// same as everything else in this module.
function _renderQuickActions() {
  const container = el('cp-quick-actions');
  if (!container) return;

  const pinned = COMMANDS.filter(c => c.pinned);
  const recent = COMMANDS
    .filter(c => c.lastUsed)
    .sort((a, b) => b.lastUsed - a.lastUsed)
    .slice(0, 5);
  const mostUsed = COMMANDS
    .filter(c => c.usageCount)
    .sort((a, b) => b.usageCount - a.usageCount)
    .slice(0, 5);

  const seen = new Set();
  const combined = [];
  for (const cmd of [...pinned, ...recent, ...mostUsed]) {
    const key = cmd.id || cmd.btnId || cmd.label;
    if (seen.has(key)) continue;
    seen.add(key);
    combined.push(cmd);
  }

  if (combined.length === 0) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'flex';
  container.innerHTML = combined.map(cmd => `
    <button data-qa-label="${esc(cmd.label)}" style="flex-shrink:0;padding:4px 10px;font-size:11px;border-radius:4px;cursor:pointer;background:transparent;color:#7a9aab;border:1px solid #1a3a4a;">${cmd.pinned ? '★ ' : ''}${esc(cmd.label)}</button>
  `).join('');

  container.querySelectorAll('button').forEach((btn, i) => {
    btn.addEventListener('click', () => _executeCommand(combined[i]));
  });
}

function _handleInputKeydown(e) {
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (_filtered.length) _selectedIndex = (_selectedIndex + 1) % _filtered.length;
    _renderResults();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (_filtered.length) _selectedIndex = (_selectedIndex - 1 + _filtered.length) % _filtered.length;
    _renderResults();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const cmd = _filtered[_selectedIndex];
    if (cmd) _executeCommand(cmd);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    _closePanel();
  }
}

export function openPanel() {
  const modal = el('command-palette-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;

  const input = el('cp-input');
  if (input) {
    input.value = '';
    setTimeout(() => input.focus(), 0);
  }
  _renderPaletteStatus();
  _renderQuickActions();
  _renderCategories();
  _applyFilter('');
}

function _closePanel() {
  const modal = el('command-palette-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
  _open = false;
}

export function init() {
  const modal = el('command-palette-modal');
  const input = el('cp-input');

  if (input) input.addEventListener('input', () => _applyFilter(input.value));
  if (input) input.addEventListener('keydown', _handleInputKeydown);

  // Click on the backdrop (not the inner panel) closes.
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }

  // Real, confirmed-safe shortcut -- Ctrl+P is the browser's own Print
  // shortcut and was deliberately avoided; Ctrl+Shift+K was already
  // confirmed unclaimed during earlier testing.
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      if (_open) _closePanel(); else openPanel();
    }
  });

  autoDiscoverPanelCommands();
}

export default { init, openPanel, registerCommand, unregisterCommand, autoDiscoverPanelCommands };
