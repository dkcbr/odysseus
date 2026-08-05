// static/js/mcp_server_detail_panel.js — MCP Server Detail (ES6)
// Self-contained, matching agent_task_detail_panel.js's pattern. Real,
// read-only single-server view via GET /api/mcp/server-detail?id=...,
// using only real, confirmed fields (no "ping"/"type"/"last_ping" --
// those don't exist, confirmed directly before building).

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

async function _loadServer(serverId) {
  const body = el('mcp-server-detail-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Loading…</div>';

  try {
    const res = await fetch(`/api/mcp/server-detail?id=${encodeURIComponent(serverId)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();

    const statusColor = s.status === 'connected' ? 'var(--color-success)' : 'var(--color-error)';
    const toolRows = (s.tools || []).map(t => `<div style="padding:2px 0;font-size:11px;color:var(--fg);">• ${esc(t)}</div>`).join('');

    body.innerHTML = `
      <div style="padding:10px 14px;border-bottom:1px solid var(--border);">
        <div style="font-size:13px;color:var(--color-subheader);font-weight:600;">MCP Server: ${esc(s.name || s.id)}</div>
        <div style="font-size:12px;color:${statusColor};margin-top:2px;">Status: ${esc(s.status || '')}</div>
      </div>
      <div style="padding:8px 14px;font-size:12px;color:var(--fg);">
        <div><b>Transport:</b> ${esc(s.transport || '')}</div>
        ${s.command ? `<div><b>Command:</b> ${esc(s.command)}</div>` : ''}
        ${s.url ? `<div><b>URL:</b> ${esc(s.url)}</div>` : ''}
        <div><b>Tool count:</b> ${esc(String(s.tool_count ?? ''))}</div>
        ${s.error ? `<div style="color:var(--color-error);"><b>Error:</b> ${esc(s.error)}</div>` : ''}
      </div>
      <div style="padding:8px 14px;border-top:1px solid var(--border);">
        <div style="font-size:11px;font-weight:600;color:var(--color-subheader);">Tools</div>
        ${toolRows || '<div style="color:var(--color-muted);font-size:11px;">No tools listed.</div>'}
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Failed to load: ${esc(String(e))}</div>`;
  }
}

export async function openPanel(serverId) {
  const modal = el('mcp-server-detail-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  if (serverId) await _loadServer(serverId);
}

function _closePanel() {
  const modal = el('mcp-server-detail-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const closeBtn = el('close-mcp-server-detail-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const modal = el('mcp-server-detail-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
