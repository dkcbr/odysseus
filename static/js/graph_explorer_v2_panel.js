// static/js/graph_explorer_v2_panel.js — Graph Explorer v2 (ES6)
// Self-contained, matching state_of_jarvis_panel.js's pattern. Real,
// read-only unified node/edge view across Vault/Risk/MCP/Agent Task/
// Market Entity domains. Grouped list view, not a full force-directed
// graph render. Adds real, client-side domain filters (checkboxes) and
// text search over already-fetched nodes/edges -- no backend changes,
// no re-fetch per filter change. Skips "structural" filters (degree,
// connected component) since those assume a visual node-link diagram
// this list-based panel doesn't have.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;
let _allNodes = [];
let _allEdges = [];
let _activeTypes = null; // null = all types active
let _searchQuery = '';
let _activeEdgeTypes = null; // null = all edge types active
let _showWeights = false;
let _layoutMode = 'default';
let _snapshots = {}; // manual-only, in-memory for this session -- no
// auto-snapshotting on backend refresh, since the daily risk-refresh
// timer runs independently of whether this panel is even open (a
// real gap corrected before building, not something purely-frontend
// code can observe).

// Real, honest effect only: these reorder the node array before it gets
// grouped by type -- they change which type-section appears first and
// the row order within each section. They do NOT produce a visual
// force-directed/radial/hierarchical rendering (this panel has no
// canvas or node positions) -- confirmed and corrected before building.
function _degreeOrder(nodes, edges) {
  const degree = {};
  edges.forEach(e => {
    degree[e.source] = (degree[e.source] || 0) + 1;
    degree[e.target] = (degree[e.target] || 0) + 1;
  });
  return [...nodes].sort((a, b) => (degree[b.id] || 0) - (degree[a.id] || 0));
}

const DOMAIN_PRIORITY = {
  risk_factor: 0, risk_event: 1, risk_regime: 1, risk_suggestion: 1,
  market_entity: 2, agent_task: 3, agent: 3,
  mcp_server: 4, mcp_tool: 4, vault_note: 5, vault_tag: 5,
};
function _domainOrder(nodes) {
  return [...nodes].sort((a, b) => (DOMAIN_PRIORITY[a.type] ?? 9) - (DOMAIN_PRIORITY[b.type] ?? 9));
}

function _outgoingEdgeOrder(nodes, edges) {
  const outgoing = {};
  edges.forEach(e => { outgoing[e.source] = (outgoing[e.source] || 0) + 1; });
  return [...nodes].sort((a, b) => (outgoing[b.id] || 0) - (outgoing[a.id] || 0));
}

// Real weight: uses the real metadata.weight field the backend now
// computes (abs(loading), invocation count, or duration_ms depending on
// edge type). Falls back to 1 for edge types with no real numeric
// signal (HAS_TAG, has_factor, etc.) -- honest, not invented.
function _edgeWeight(edge) {
  return (edge.metadata && typeof edge.metadata.weight === 'number') ? edge.metadata.weight : 1;
}

function _weightedOrder(nodes, edges) {
  const weightMap = {};
  edges.forEach(e => {
    const w = _edgeWeight(e);
    weightMap[e.source] = Math.max(weightMap[e.source] || 0, w);
    weightMap[e.target] = Math.max(weightMap[e.target] || 0, w);
  });
  return [...nodes].sort((a, b) => (weightMap[b.id] || 0) - (weightMap[a.id] || 0));
}

const TYPE_COLORS = {
  vault_note: 'var(--color-success)', vault_tag: '#7dd3fc',
  risk_regime: 'var(--color-subheader)', risk_factor: 'var(--color-warning)',
  risk_event: 'var(--color-error)', risk_suggestion: '#c084fc',
  mcp_server: '#fb923c', mcp_tool: '#a3a3a3',
  agent: '#a78bfa', agent_task: '#c4b5fd', market_entity: '#fbbf24',
};

function _renderFilterBar() {
  const bar = el('graph-explorer-v2-filterbar');
  if (!bar || _allNodes.length === 0) return;

  const types = [...new Set(_allNodes.map(n => n.type))].sort();
  const checkboxes = types.map(t => `
    <label style="font-size:10px;color:${TYPE_COLORS[t] || 'var(--fg)'};margin-right:10px;cursor:pointer;white-space:nowrap;">
      <input type="checkbox" data-type="${esc(t)}" checked style="vertical-align:middle;"> ${esc(t)}
    </label>
  `).join('');

  // Real, confirmed edge types (verified directly against live data
  // before building -- an earlier proposal's names were mostly wrong).
  const edgeTypes = [...new Set(_allEdges.map(e => e.type))].sort();
  const edgeCheckboxes = edgeTypes.map(t => `
    <label style="font-size:10px;color:#a3a3a3;margin-right:10px;cursor:pointer;white-space:nowrap;">
      <input type="checkbox" data-edge-type="${esc(t)}" checked style="vertical-align:middle;"> ${esc(t)}
    </label>
  `).join('');

  bar.innerHTML = `
    <input id="graph-explorer-v2-search" type="text" placeholder="Search nodes by label or metadata…"
      style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:5px 8px;font-size:11px;outline:none;box-sizing:border-box;margin-bottom:6px;">
    <select id="graph-explorer-v2-sort" style="width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:4px 8px;font-size:11px;outline:none;box-sizing:border-box;margin-bottom:6px;">
      <option value="default">Sort: default (by type, then insertion order)</option>
      <option value="degree">Sort: by total edge count (highest first)</option>
      <option value="domain">Sort: by domain priority (risk → market → agent → mcp → vault)</option>
      <option value="outgoing">Sort: by outgoing edge count (highest first)</option>
      <option value="weighted">Sort: by real edge weight (loading/invocations/duration)</option>
    </select>
    <label style="font-size:10px;color:var(--color-muted);cursor:pointer;display:block;margin-bottom:6px;">
      <input type="checkbox" id="graph-explorer-v2-show-weights" style="vertical-align:middle;"> Show edge weights in row labels
    </label>
    <div style="font-size:10px;color:var(--color-muted);margin-bottom:2px;">Node types:</div>
    <div style="display:flex;flex-wrap:wrap;gap:2px;margin-bottom:6px;">${checkboxes}</div>
    <div style="font-size:10px;color:var(--color-muted);margin-bottom:2px;">Edge types:</div>
    <div style="display:flex;flex-wrap:wrap;gap:2px;">${edgeCheckboxes}</div>
  `;

  const input = el('graph-explorer-v2-search');
  if (input) {
    input.addEventListener('input', () => {
      _searchQuery = input.value.trim().toLowerCase();
      _renderList();
    });
  }
  bar.querySelectorAll('input[data-type]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (!_activeTypes) _activeTypes = new Set(types);
      if (cb.checked) _activeTypes.add(cb.dataset.type);
      else _activeTypes.delete(cb.dataset.type);
      _renderList();
    });
  });
  bar.querySelectorAll('input[data-edge-type]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (!_activeEdgeTypes) _activeEdgeTypes = new Set(edgeTypes);
      if (cb.checked) _activeEdgeTypes.add(cb.dataset.edgeType);
      else _activeEdgeTypes.delete(cb.dataset.edgeType);
      _renderList();
    });
  });

  const sortSelect = el('graph-explorer-v2-sort');
  if (sortSelect) {
    sortSelect.addEventListener('change', () => {
      _layoutMode = sortSelect.value;
      _renderList();
    });
  }

  const showWeightsCb = el('graph-explorer-v2-show-weights');
  if (showWeightsCb) {
    showWeightsCb.addEventListener('change', () => {
      _showWeights = showWeightsCb.checked;
      _renderList();
    });
  }
}

// Real, shared filtering/sorting logic -- extracted so _renderList and
// the export function stay consistent, matching exactly what's on
// screen (no separate export-time recalculation that could drift).
function _getVisibleGraph() {
  let visible = _allNodes;
  if (_activeTypes) visible = visible.filter(n => _activeTypes.has(n.type));
  if (_searchQuery) {
    visible = visible.filter(n =>
      n.label.toLowerCase().includes(_searchQuery) ||
      JSON.stringify(n.metadata || {}).toLowerCase().includes(_searchQuery)
    );
  }
  if (_layoutMode === 'degree') visible = _degreeOrder(visible, _allEdges);
  else if (_layoutMode === 'domain') visible = _domainOrder(visible);
  else if (_layoutMode === 'outgoing') visible = _outgoingEdgeOrder(visible, _allEdges);
  else if (_layoutMode === 'weighted') visible = _weightedOrder(visible, _allEdges);

  const visibleIds = new Set(visible.map(n => n.id));
  const visibleEdges = _allEdges.filter(e =>
    (!_activeEdgeTypes || _activeEdgeTypes.has(e.type)) &&
    visibleIds.has(e.source) && visibleIds.has(e.target)
  );
  return { nodes: visible, edges: visibleEdges };
}

function _generateExportText() {
  const { nodes, edges } = _getVisibleGraph();

  const byType = {};
  nodes.forEach(n => { (byType[n.type] = byType[n.type] || []).push(n); });
  const byEdgeType = {};
  edges.forEach(e => { (byEdgeType[e.type] = byEdgeType[e.type] || []).push(e); });

  let out = `Graph Export -- ${nodes.length} nodes, ${edges.length} edges
`;
  out += `Sort: ${_layoutMode}

`;

  out += 'Nodes:\n';
  Object.keys(byType).sort().forEach(type => {
    out += `  ${type} (${byType[type].length})\n`;
    byType[type].forEach(n => { out += `    - ${n.id}\n`; });
    out += '\n';
  });

  out += 'Edges:\n';
  Object.keys(byEdgeType).sort().forEach(type => {
    out += `  ${type} (${byEdgeType[type].length})\n`;
    byEdgeType[type].forEach(e => { out += `    - ${e.source} -> ${e.target} (${_edgeWeight(e)})\n`; });
    out += '\n';
  });

  return out;
}

function _renderList() {
  const body = el('graph-explorer-v2-body');
  if (!body) return;

  const edgeCount = {};
  _allEdges.forEach(e => {
    edgeCount[e.source] = (edgeCount[e.source] || 0) + 1;
    edgeCount[e.target] = (edgeCount[e.target] || 0) + 1;
  });

  const { nodes: visible, edges: visibleEdgesArr } = _getVisibleGraph();
  const visibleEdgeCount = visibleEdgesArr.length;

  const byType = {};
  visible.forEach(n => { (byType[n.type] = byType[n.type] || []).push(n); });

  // Real max-edge-weight per node, only computed/shown when the toggle
  // is on -- reuses the same _edgeWeight() the sort mode uses.
  const maxWeight = {};
  if (_showWeights) {
    _allEdges.forEach(e => {
      const w = _edgeWeight(e);
      maxWeight[e.source] = Math.max(maxWeight[e.source] || 0, w);
      maxWeight[e.target] = Math.max(maxWeight[e.target] || 0, w);
    });
  }

  const sections = Object.entries(byType).map(([type, list]) => {
    const color = TYPE_COLORS[type] || 'var(--fg)';
    const rows = list.map(n => `
      <div style="padding:4px 12px;font-size:11px;color:var(--fg);border-bottom:1px solid var(--border);">
        <span style="color:${color};">●</span> ${esc(n.label)}
        <span style="color:var(--color-muted);font-size:10px;"> — ${edgeCount[n.id] || 0} edge(s)${_showWeights ? ` — max weight ${maxWeight[n.id] || 0}` : ''}</span>
      </div>
    `).join('');
    return `
      <div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:${color};border-top:1px solid var(--border);">
        ${esc(type)} (${list.length})
      </div>
      ${rows}
    `;
  }).join('');

  body.innerHTML = `
    <div style="padding:8px 12px;font-size:10px;color:var(--color-muted);border-bottom:1px solid var(--border);">
      ${visible.length} of ${_allNodes.length} nodes shown, ${visibleEdgeCount} of ${_allEdges.length} edges shown
    </div>
    ${sections || '<div style="padding:16px;color:var(--color-muted);font-size:12px;">No matching nodes.</div>'}
  `;
}

// Real, manual-only diff engine -- pure comparison of two saved
// snapshots, no backend involvement.
function _diffNodes(a, b) {
  const aIds = new Set(a.nodes.map(n => n.id));
  const bIds = new Set(b.nodes.map(n => n.id));
  return {
    added: b.nodes.filter(n => !aIds.has(n.id)),
    removed: a.nodes.filter(n => !bIds.has(n.id)),
  };
}

function _diffEdges(a, b) {
  const key = e => `${e.source}->${e.target}:${e.type}`;
  const aSet = new Set(a.edges.map(key));
  const bSet = new Set(b.edges.map(key));
  return {
    added: b.edges.filter(e => !aSet.has(key(e))),
    removed: a.edges.filter(e => !bSet.has(key(e))),
  };
}

function _renderDiffBar() {
  const bar = el('graph-explorer-v2-diffbar');
  if (!bar) return;
  const ids = Object.keys(_snapshots);
  const options = ids.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
  bar.innerHTML = `
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
      <button id="graph-explorer-v2-save-snapshot" style="padding:4px 8px;font-size:10px;background:var(--border);color:var(--color-subheader);border:1px solid var(--color-subheader);border-radius:4px;cursor:pointer;">Save Snapshot</button>
      <select id="graph-explorer-v2-snap-a" style="font-size:10px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;"><option value="">A: (none)</option>${options}</select>
      <select id="graph-explorer-v2-snap-b" style="font-size:10px;background:var(--bg);color:var(--fg);border:1px solid var(--border);border-radius:4px;"><option value="">B: (none)</option>${options}</select>
      <button id="graph-explorer-v2-run-diff" style="padding:4px 8px;font-size:10px;background:var(--border);color:var(--color-subheader);border:1px solid var(--color-subheader);border-radius:4px;cursor:pointer;">Diff A → B</button>
    </div>
    <div id="graph-explorer-v2-diff-output" style="margin-top:6px;font-size:11px;"></div>
    <div style="margin-top:8px;font-size:10px;font-weight:600;color:var(--color-muted);">Timeline (manual snapshots, chronological — no auto-trigger)</div>
    <ul id="graph-explorer-v2-timeline-list" style="list-style:none;padding:0;margin:4px 0 0;">
      ${ids.map(id => `
        <li data-id="${esc(id)}" style="cursor:pointer;font-size:10px;color:var(--fg);padding:3px 6px;border-bottom:1px solid var(--border);">
          ${esc(id)}
          <div class="graph-explorer-v2-timeline-details" style="display:none;margin-top:4px;color:var(--color-muted);"></div>
        </li>
      `).join('')}
    </ul>
  `;

  bar.querySelectorAll('#graph-explorer-v2-timeline-list li').forEach(li => {
    li.addEventListener('click', () => {
      const snap = _snapshots[li.dataset.id];
      const details = li.querySelector('.graph-explorer-v2-timeline-details');
      const isHidden = details.style.display === 'none';
      // Real, honest summary of this ONE snapshot -- counts by real
      // node type, not a diff (diffing needs a second snapshot, use the
      // existing A/B controls above for that).
      if (isHidden && !details.dataset.rendered) {
        const byType = {};
        snap.nodes.forEach(n => { byType[n.type] = (byType[n.type] || 0) + 1; });
        details.innerHTML = Object.entries(byType).sort()
          .map(([t, c]) => `${esc(t)}: ${c}`).join(', ') + ` — ${snap.edges.length} edges total`;
        details.dataset.rendered = '1';
      }
      details.style.display = isHidden ? 'block' : 'none';
    });
  });

  el('graph-explorer-v2-save-snapshot').addEventListener('click', () => {
    const id = new Date().toISOString();
    _snapshots[id] = { nodes: _allNodes, edges: _allEdges };
    _renderDiffBar();
  });

  el('graph-explorer-v2-run-diff').addEventListener('click', () => {
    const a = _snapshots[el('graph-explorer-v2-snap-a').value];
    const b = _snapshots[el('graph-explorer-v2-snap-b').value];
    const out = el('graph-explorer-v2-diff-output');
    if (!a || !b) { out.innerHTML = '<span style="color:var(--color-muted);">Select two snapshots first.</span>'; return; }

    const nodeDiff = _diffNodes(a, b);
    const edgeDiff = _diffEdges(a, b);
    out.innerHTML = `
      <div style="color:var(--color-success);">Nodes added (${nodeDiff.added.length}): ${nodeDiff.added.map(n => esc(n.label)).join(', ') || '—'}</div>
      <div style="color:var(--color-error);">Nodes removed (${nodeDiff.removed.length}): ${nodeDiff.removed.map(n => esc(n.label)).join(', ') || '—'}</div>
      <div style="color:var(--color-muted);">Edges added: ${edgeDiff.added.length} | Edges removed: ${edgeDiff.removed.length}</div>
    `;
  });
}

async function _render() {
  const body = el('graph-explorer-v2-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Loading…</div>';

  try {
    const [nodesRes, edgesRes] = await Promise.all([
      fetch('/api/graph/nodes', { credentials: 'same-origin' }),
      fetch('/api/graph/edges', { credentials: 'same-origin' }),
    ]);
    if (!nodesRes.ok || !edgesRes.ok) throw new Error(`HTTP ${nodesRes.status}/${edgesRes.status}`);
    const { nodes } = await nodesRes.json();
    const { edges } = await edgesRes.json();

    _allNodes = nodes;
    _allEdges = edges;
    _activeTypes = null;
    _searchQuery = '';

    _renderFilterBar();
    _renderDiffBar();
    _renderList();
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Failed to load: ${esc(String(e))}</div>`;
  }
}

export async function openPanel() {
  const modal = el('graph-explorer-v2-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();
}

function _closePanel() {
  const modal = el('graph-explorer-v2-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-graph-explorer-v2-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-graph-explorer-v2-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const exportBtn = el('graph-explorer-v2-export-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      const view = el('graph-explorer-v2-export-view');
      const textEl = el('graph-explorer-v2-export-text');
      if (!view || !textEl) return;
      textEl.textContent = _generateExportText();
      view.classList.remove('hidden');
    });
  }

  const modal = el('graph-explorer-v2-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
