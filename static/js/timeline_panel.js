// static/js/timeline_panel.js — Unified Timeline (ES6)
// Self-contained, matching state_of_jarvis_panel.js's pattern. Real,
// read-only combined event stream (risk + agent tasks only, per explicit
// scope decision) via GET /api/timeline.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

function sourceColor(source) {
  if (source === 'risk') return 'var(--color-subheader)';
  if (source === 'agent') return 'var(--color-muted)';
  return 'var(--fg)';
}

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleString();
}

async function _render() {
  const body = el('timeline-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Loading…</div>';

  try {
    const res = await fetch('/api/timeline?limit=100', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const rows = data.events.map(e => `
      <div class="tl-row" data-entity-type="${esc(e.entity_type || '')}" data-entity-id="${esc(e.entity_id || '')}"
        style="padding:6px 12px;border-bottom:1px solid var(--border);cursor:${e.entity_id ? 'pointer' : 'default'};">
        <div style="font-size:10px;color:var(--color-muted);">${esc(fmtTime(e.ts))} — <span style="color:${sourceColor(e.source)};">${esc(e.source)}</span></div>
        <div style="font-size:12px;color:var(--fg);">${esc(e.summary)}</div>
      </div>
    `).join('');

    body.innerHTML = rows || '<div style="padding:16px;color:var(--color-muted);font-size:12px;">No events yet.</div>';

    body.querySelectorAll('.tl-row').forEach(row => {
      const entityId = row.dataset.entityId;
      const entityType = row.dataset.entityType;
      if (!entityId) return;
      row.addEventListener('click', async () => {
        _closePanel();
        if (entityType === 'agent-task') {
          const m = await import('./agent_task_detail_panel.js');
          m.default.openPanel(entityId);
        } else if (entityType === 'regime' || entityType === 'riskevent') {
          const m = await import('./risk_panel.js');
          m.default.openPanel(entityType === 'regime' ? 'regime' : 'events');
        }
      });
    });
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Failed to load: ${esc(String(e))}</div>`;
  }
}

export async function openPanel() {
  const modal = el('timeline-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();
}

function _closePanel() {
  const modal = el('timeline-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-timeline-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-timeline-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const modal = el('timeline-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
