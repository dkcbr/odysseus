// static/js/vault_graph_panel.js — Jarvis Vault Graph Explorer (ES6)
// Self-contained, matching command_palette.js / system_health_panel.js's
// pattern (no modalManager involvement). Purely read-only: renders the
// real VaultNote/Tag/HAS_TAG subgraph from /api/vault/graph-explorer.
// No creates, no edits, no relation changes -- observational only.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;
let _graphData = null;
let _activeTag = null;
let _searchQuery = '';

function _renderTagList() {
  const container = el('vault-graph-tags');
  if (!container || !_graphData) return;
  const allBtn = `<div class="vg-tag-item" data-tag-id="" style="padding:6px 10px;cursor:pointer;border-radius:4px;background:${!_activeTag ? 'var(--border)' : 'transparent'};color:${!_activeTag ? 'var(--color-subheader)' : 'var(--fg)'};font-size:12px;">All (${_graphData.notes.length})</div>`;
  const tagBtns = _graphData.tags.map(t => `
    <div class="vg-tag-item" data-tag-id="${esc(t.id)}" style="padding:6px 10px;cursor:pointer;border-radius:4px;background:${_activeTag === t.id ? 'var(--border)' : 'transparent'};color:${_activeTag === t.id ? 'var(--color-subheader)' : 'var(--fg)'};font-size:12px;">${esc(t.name)} (${t.noteCount})</div>
  `).join('');
  container.innerHTML = allBtn + tagBtns;
  container.querySelectorAll('.vg-tag-item').forEach(item => {
    item.addEventListener('click', () => {
      _activeTag = item.dataset.tagId || null;
      _renderTagList();
      _renderCanvas();
    });
  });
}

function _renderCanvas() {
  const container = el('vault-graph-canvas');
  if (!container || !_graphData) return;

  let visibleNotes = _activeTag
    ? _graphData.notes.filter(n => n.tags.some(t => `tag:${t}` === _activeTag))
    : _graphData.notes;

  if (_searchQuery) {
    const q = _searchQuery.toLowerCase();
    visibleNotes = visibleNotes.filter(n =>
      n.title.toLowerCase().includes(q) || n.path.toLowerCase().includes(q));
  }

  const rows = visibleNotes.map(n => `
    <div class="vg-note-item" data-note-id="${esc(n.id)}" style="padding:6px 10px;cursor:pointer;border-radius:4px;font-size:12px;color:var(--fg);border-bottom:1px solid var(--border);">
      ● ${esc(n.title)}
      <span style="color:var(--color-muted);font-size:10px;"> — ${n.tags.map(esc).join(', ')}</span>
    </div>
  `).join('');
  container.innerHTML = rows || '<div style="padding:12px;color:var(--color-muted);font-size:12px;">No notes for this tag.</div>';

  container.querySelectorAll('.vg-note-item').forEach(item => {
    item.addEventListener('click', () => _renderDetails(item.dataset.noteId));
    item.addEventListener('dblclick', async () => {
      const note = _graphData.notes.find(n => n.id === item.dataset.noteId);
      if (note) {
        const composerModule = await import('./composer_panel.js');
        composerModule.openPanel(note.path);
      }
    });
  });
}

function _renderDetails(noteId) {
  const container = el('vault-graph-details');
  if (!container || !_graphData) return;
  const note = _graphData.notes.find(n => n.id === noteId);
  if (!note) {
    container.innerHTML = '<div style="padding:12px;color:var(--color-muted);font-size:12px;">Select a note to see details.</div>';
    return;
  }
  container.innerHTML = `
    <div style="padding:12px;font-size:12px;color:var(--fg);">
      <div style="font-weight:600;margin-bottom:6px;">${esc(note.title)}</div>
      <div style="color:var(--color-muted);margin-bottom:6px;">${esc(note.path)}</div>
      <div>Tags: ${note.tags.map(esc).join(', ') || '(none)'}</div>
    </div>
  `;
}

async function _render() {
  const canvas = el('vault-graph-canvas');
  if (canvas) canvas.innerHTML = '<div style="padding:16px;font-size:12px;color:var(--color-muted);">Loading…</div>';

  try {
    const res = await fetch('/api/vault/graph-explorer', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _graphData = await res.json();
  } catch (e) {
    if (canvas) canvas.innerHTML = `<div style="padding:16px;font-size:12px;color:var(--color-error);">Failed to load: ${esc(String(e))}</div>`;
    return;
  }

  _activeTag = null;
  _renderTagList();
  _renderCanvas();
  _renderDetails(null);
}

export async function openPanel(focusSearch, highlightNoteId) {
  const modal = el('vault-graph-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();
  if (focusSearch) {
    const input = el('vault-graph-search');
    if (input) input.focus();
  }
  if (highlightNoteId) {
    // Real, existing data-note-id attribute (confirmed directly) -- not
    // the fabricated data-entity selector from an earlier proposal.
    // Matches risk_panel.js's own scroll + outline-flash style, not a
    // new CSS class, for visual consistency across panels.
    const target = document.querySelector(`[data-note-id="${highlightNoteId}"]`);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.style.outline = '2px solid var(--color-subheader)';
      setTimeout(() => { target.style.outline = 'none'; }, 1200);
    }
  }
}

function _closePanel() {
  const modal = el('vault-graph-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-vault-graph-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-vault-graph-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const searchInput = el('vault-graph-search');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      _searchQuery = searchInput.value;
      _renderCanvas();
    });
  }

  const modal = el('vault-graph-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
