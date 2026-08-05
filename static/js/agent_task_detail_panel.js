// static/js/agent_task_detail_panel.js — Agent Task Detail (ES6)
// Self-contained, matching risk_panel.js's pattern. Real, read-only
// single-task view via GET /api/agent-tasks/task?id=..., using the real
// field names (arguments, result.stdout/stderr/exit_code,
// created_at/updated_at) -- not the "input/output/error/timestamp"
// names from an earlier, uncorrected proposal.

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;

function fmtTime(unixSeconds) {
  if (!unixSeconds) return 'n/a';
  return new Date(unixSeconds * 1000).toISOString();
}

async function _loadTask(taskId) {
  const body = el('agent-task-detail-body');
  if (!body) return;
  body.innerHTML = '<div style="padding:16px;color:var(--color-muted);font-size:12px;">Loading…</div>';

  try {
    const res = await fetch(`/api/agent-tasks/task?id=${encodeURIComponent(taskId)}`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const t = await res.json();

    const statusColor = t.status === 'success' ? 'var(--color-success)' : (t.status === 'error' || t.status === 'failed' ? 'var(--color-error)' : 'var(--color-muted)');
    const result = t.result || {};

    body.innerHTML = `
      <div style="padding:10px 14px;border-bottom:1px solid var(--border);">
        <div style="font-size:13px;color:var(--color-subheader);font-weight:600;">Agent Task: ${esc(t.id)}</div>
        <div style="font-size:12px;color:${statusColor};margin-top:2px;">Status: ${esc(t.status)}</div>
      </div>
      <div style="padding:8px 14px;font-size:12px;color:var(--fg);">
        <div><b>Agent:</b> ${esc(t.agent || '')}</div>
        <div><b>Server:</b> ${esc(t.server || '')}</div>
        <div><b>Tool:</b> ${esc(t.tool || '')}</div>
        <div style="font-size:10px;color:var(--color-muted);margin-top:4px;">
          Created: ${esc(fmtTime(t.created_at))} | Updated: ${esc(fmtTime(t.updated_at))}
        </div>
      </div>
      <div style="padding:8px 14px;border-top:1px solid var(--border);">
        <div style="font-size:11px;font-weight:600;color:var(--color-subheader);">Arguments</div>
        <pre style="background:var(--bg);padding:8px;border-radius:4px;font-size:11px;color:var(--fg);overflow-x:auto;white-space:pre-wrap;">${esc(JSON.stringify(t.arguments || {}, null, 2))}</pre>
      </div>
      <div style="padding:8px 14px;border-top:1px solid var(--border);">
        <div style="font-size:11px;font-weight:600;color:var(--color-subheader);">Result</div>
        <pre style="background:var(--bg);padding:8px;border-radius:4px;font-size:11px;color:var(--fg);overflow-x:auto;white-space:pre-wrap;">stdout: ${esc(result.stdout || '')}
stderr: ${esc(result.stderr || '')}
exit_code: ${esc(String(result.exit_code ?? ''))}</pre>
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div style="padding:16px;color:var(--color-error);font-size:12px;">Failed to load: ${esc(String(e))}</div>`;
  }
}

export async function openPanel(taskId) {
  const modal = el('agent-task-detail-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
  _open = true;
  if (taskId) await _loadTask(taskId);
}

function _closePanel() {
  const modal = el('agent-task-detail-panel-modal');
  if (modal) { modal.classList.add('hidden'); modal.style.display = 'none'; }
  _open = false;
}

export function init() {
  const closeBtn = el('close-agent-task-detail-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);

  const modal = el('agent-task-detail-panel-modal');
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) _closePanel();
    });
  }
}

export default { init, openPanel };
