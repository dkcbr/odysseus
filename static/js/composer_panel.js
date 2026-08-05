// static/js/composer_panel.js — Jarvis Vault Composer (ES6)
// Self-contained, matching command_palette.js / system_health_panel.js's
// pattern. First real write path in the Copilot integration work: fetches
// a real note's current content, lets the user edit it, shows a real
// line-based diff, and on approval writes the file AND re-ingests it via
// replace_fact() (server-side) so semantic memory stays in sync.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;
let _currentPath = null;
let _originalText = '';

// Real, simple LCS-based line diff -- no external library dependency.
// Returns an array of {type: 'same'|'add'|'del', line: string}.
function _lineDiff(a, b) {
  const linesA = a.split('\n');
  const linesB = b.split('\n');
  const n = linesA.length, m = linesB.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = linesA[i] === linesB[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const result = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (linesA[i] === linesB[j]) { result.push({ type: 'same', line: linesA[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { result.push({ type: 'del', line: linesA[i] }); i++; }
    else { result.push({ type: 'add', line: linesB[j] }); j++; }
  }
  while (i < n) { result.push({ type: 'del', line: linesA[i] }); i++; }
  while (j < m) { result.push({ type: 'add', line: linesB[j] }); j++; }
  return result;
}

function _renderDiff() {
  const container = el('composer-diff');
  const textarea = el('composer-textarea');
  if (!container || !textarea) return;
  const diff = _lineDiff(_originalText, textarea.value);
  container.innerHTML = diff.map(d => {
    const bg = d.type === 'add' ? 'rgba(74,212,232,0.12)' : d.type === 'del' ? 'rgba(232,74,74,0.12)' : 'transparent';
    const color = d.type === 'add' ? 'var(--color-subheader)' : d.type === 'del' ? 'var(--color-error)' : 'var(--color-muted)';
    const prefix = d.type === 'add' ? '+ ' : d.type === 'del' ? '- ' : '  ';
    return `<div style="background:${bg};color:${color};font-family:monospace;font-size:11px;white-space:pre-wrap;padding:1px 6px;">${esc(prefix + d.line)}</div>`;
  }).join('');
}

async function _loadNote(path) {
  _currentPath = path;
  const textarea = el('composer-textarea');
  const status = el('composer-status');
  if (status) status.textContent = 'Loading…';
  try {
    const res = await fetch(`/api/vault/note?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _originalText = data.text;
    if (textarea) textarea.value = data.text;
    if (status) status.textContent = path;
    _renderDiff();
  } catch (e) {
    if (status) status.textContent = `Failed to load: ${String(e)}`;
  }
}

async function _applyEdit() {
  const textarea = el('composer-textarea');
  const status = el('composer-status');
  if (!_currentPath || !textarea) return;
  if (textarea.value === _originalText) {
    if (status) status.textContent = 'No changes to apply.';
    return;
  }
  if (status) status.textContent = 'Applying…';
  try {
    const res = await fetch('/api/vault/note/apply', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: _currentPath, text: textarea.value }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _originalText = textarea.value;
    _renderDiff();
    if (status) status.textContent = `Saved: ${_currentPath}`;
  } catch (e) {
    if (status) status.textContent = `Failed to apply: ${String(e)}`;
  }
}

export async function openPanel(path) {
  const modal = el('composer-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  if (path) await _loadNote(path);
}

function _closePanel() {
  const modal = el('composer-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-composer-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-composer-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const applyBtn = el('composer-apply-btn');
  if (applyBtn) applyBtn.addEventListener('click', _applyEdit);

  const textarea = el('composer-textarea');
  if (textarea) textarea.addEventListener('input', _renderDiff);

  const modal = el('composer-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
