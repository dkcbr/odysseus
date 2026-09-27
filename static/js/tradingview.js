// static/js/tradingview.js — TradingView panel (ES6)
// Real, small integration following the exact verified pattern from
// registry.js/capabilities.js: static-shell modal, namespace Modals import,
// private fetch helpers with credentials, real endpoint field names.
//
// Real endpoints used (none of these are fabricated):
//   GET  /api/mcp/servers                    -- find the real "tradingview" entry
//   GET  /api/mcp/servers/{id}/tools          -- real tool list (name, description)
//   POST /api/mcp/call                        -- {server, tool, arguments} (NOT "params")
// No "tradingview_agent" is passed -- that agent role doesn't exist yet in
// agent_capabilities.json, so `agent` is simply omitted (admin-only call,
// same as every other direct /api/mcp/call test used throughout today).

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;
let _serverId = null;

async function _fetchServerId() {
  const res = await fetch('/api/mcp/servers', { credentials: 'same-origin' });
  if (res.status === 401 || res.status === 403) throw new Error('Access denied');
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  const servers = await res.json();
  const match = servers.find(s => s.name === 'tradingview');
  if (!match) throw new Error('No MCP server registered with name "tradingview"');
  return match.id;
}

async function _fetchTools(serverId) {
  const res = await fetch(`/api/mcp/servers/${serverId}/tools`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json(); // real shape: array of {server_id, server_name, name, qualified_name, description, input_schema}
}

async function _callTool(toolName, args) {
  const res = await fetch('/api/mcp/call', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ server: 'tradingview', tool: toolName, arguments: args || {} }),
  });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  return res.json();
}

function _renderToolList(tools) {
  if (!tools.length) {
    return '<div class="admin-empty">No tools found.</div>';
  }
  const rows = tools.map(tool => `
    <tr data-tool="${esc(tool.name)}">
      <td>${esc(tool.name)}</td>
      <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(tool.description || '')}">${esc((tool.description || '').split('\n')[0])}</td>
      <td><button class="admin-btn-sm tradingview-run-btn" data-tool="${esc(tool.name)}">Run</button></td>
    </tr>`).join('');

  return `
    <table class="registry-table" style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="text-align:left;font-size:11px;text-transform:uppercase;opacity:0.5;">
          <th>Tool</th><th>Description</th><th></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <h4 style="font-size:12px;opacity:0.6;margin:12px 0 6px;">Output</h4>
    <pre id="tradingview-output" style="max-height:240px;overflow:auto;background:color-mix(in srgb, var(--fg) 5%, transparent);padding:8px;border-radius:4px;font-size:11px;white-space:pre-wrap;">(run a tool to see output)</pre>
  `;
}

async function _render() {
  const container = el('tradingview-content');
  if (!container) return;
  container.innerHTML = '<div class="admin-empty">Loading...</div>';
  try {
    if (!_serverId) {
      _serverId = await _fetchServerId();
    }
    const tools = await _fetchTools(_serverId);
    container.innerHTML = _renderToolList(tools);
    _attachRowListeners();
  } catch (e) {
    container.innerHTML = `<div class="admin-empty">Failed to load TradingView tools: ${esc(e.message)}</div>`;
  }
}

function _attachRowListeners() {
  document.querySelectorAll('.tradingview-run-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const toolName = btn.dataset.tool;
      const output = el('tradingview-output');
      if (output) output.textContent = `Running ${toolName}...`;
      try {
        const result = await _callTool(toolName, {});
        if (output) output.textContent = JSON.stringify(result, null, 2);
      } catch (e) {
        if (output) output.textContent = `Error: ${e.message}`;
        uiModule.showError(`Failed to run ${toolName}: ${e.message}`);
      }
    });
  });
}

export function openPanel() {
  const modal = el('tradingview-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
}

function _closePanel() {
  const modal = el('tradingview-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
}

function _ensureRegistered() {
  if (Modals.isRegistered('tradingview-modal')) return;
  Modals.register('tradingview-modal', {
    railBtnId: 'rail-tradingview',
    sidebarBtnId: 'tool-tradingview-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-tradingview-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-tradingview-modal');
  if (closeBtn) {
    closeBtn.addEventListener('click', _closePanel);
  }
}

export default { init, openPanel };
