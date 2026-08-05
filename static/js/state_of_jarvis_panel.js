// static/js/state_of_jarvis_panel.js — State of Jarvis dashboard (ES6)
// Self-contained, matching risk_panel.js's pattern. Real, read-only
// aggregation of Vault + Risk + real MCP servers + real recent agent
// tasks, via GET /api/system/state.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

async function _render() {
  const body = el('state-of-jarvis-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Loading…</div>';

  try {
    const res = await fetch('/api/system/state', { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    const vaultHtml = `
      <div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);">Vault Status</div>
      <div style="padding:4px 12px;font-size:12px;color:var(--fg);">${data.vault.note_count} notes, ${data.vault.tag_count} tags</div>
    `;

    let riskHtml = `<div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Risk Status</div>`;
    if (data.risk.available) {
      const df = data.risk.dominant_factor;
      riskHtml += `
        <div class="soj-link" data-route="regime" style="padding:4px 12px;font-size:12px;color:var(--fg);cursor:pointer;">
          Regime: ${esc(data.risk.regime)} | ${data.risk.event_count} event(s), ${data.risk.suggestion_count} suggestion(s)
        </div>
        <div class="soj-link" data-route="factors" style="padding:2px 12px;font-size:10px;color:var(--color-muted);cursor:pointer;">
          Dominant factor: ${df ? esc(df.id) + ' (' + (parseFloat(df.variance_share) * 100).toFixed(1) + '%)' : 'n/a'}
        </div>
      `;
    } else {
      riskHtml += `<div style="padding:4px 12px;font-size:12px;color:var(--color-muted);">No risk data yet.</div>`;
    }

    const mcpRows = data.mcp_servers.map(s => {
      const color = s.status === 'connected' ? 'var(--color-success)' : 'var(--color-error)';
      return `<div class="soj-link" data-route="mcp-server" data-server-id="${esc(s.id)}" style="padding:3px 12px;font-size:11px;color:var(--fg);cursor:pointer;">● <span style="color:${color};">${esc(s.status)}</span> — ${esc(s.name)}</div>`;
    }).join('');

    const taskRows = data.recent_tasks.map(t => {
      const color = t.status === 'success' ? 'var(--color-success)' : (t.status === 'error' ? 'var(--color-error)' : 'var(--color-muted)');
      return `<div class="soj-link" data-route="task" data-task-id="${esc(t.id)}" style="padding:3px 12px;font-size:11px;color:var(--fg);cursor:pointer;">${esc(t.agent || '')} → ${esc(t.server || '')} / ${esc(t.tool || '')} <span style="color:${color};">[${esc(t.status || '')}]</span></div>`;
    }).join('');

    body.innerHTML = `
      ${vaultHtml}
      ${riskHtml}
      <div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">MCP Servers (${data.mcp_servers.length})</div>
      ${mcpRows}
      <div style="padding:8px 12px 4px;font-size:11px;font-weight:600;color:var(--color-subheader);border-top:1px solid var(--border);">Recent Agent Tasks</div>
      ${taskRows || '<div style="padding:4px 12px;font-size:12px;color:var(--color-muted);">No recent tasks.</div>'}
    `;
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Failed to load: ${esc(String(e))}</div>`;
    return;
  }

  body.querySelectorAll('.soj-link').forEach(item => {
    item.addEventListener('click', async () => {
      // Real, extended Cross Search pattern: dynamic import of the real,
      // already-existing panel's own openPanel export -- no global
      // router, matching each panel's own real signature.
      _closePanel();
      if (item.dataset.route === 'task') {
        const m = await import('./agent_task_detail_panel.js');
        m.default.openPanel(item.dataset.taskId);
      } else if (item.dataset.route === 'mcp-server') {
        const m = await import('./mcp_server_detail_panel.js');
        m.default.openPanel(item.dataset.serverId);
      } else {
        const m = await import('./risk_panel.js');
        m.default.openPanel(item.dataset.route);
      }
    });
  });
}

export async function openPanel() {
  const modal = el('state-of-jarvis-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  await _render();
}

function _closePanel() {
  const modal = el('state-of-jarvis-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const toolBtn = el('tool-state-of-jarvis-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-state-of-jarvis-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const modal = el('state-of-jarvis-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
