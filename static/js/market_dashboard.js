// static/js/market_dashboard.js — Market Dashboard panel (ES6)
// Real, curated dashboard over the confirmed, tested subset of the real
// tradingview MCP server's tools -- calls made directly via /api/mcp/call
// (same pattern as Task Queue Inspector's Diagnostics buttons), not routed
// through the task queue, since these are instant, on-demand reads.
//
// Panels: market_snapshot, top_gainers/top_losers, coin_analysis (symbol
// input), market_sentiment, financial_news, bitcoin_market_pulse -- all
// six confirmed real and callable against the actual server, nothing
// fabricated.

import uiModule from './ui.js';
import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return uiModule.esc(s); }

let _open = false;

async function _callTradingViewTool(tool, args) {
  const res = await fetch('/api/mcp/call', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ server: 'tradingview', tool, arguments: args || {} }),
  });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  const result = await res.json();
  if (result.error) throw new Error(result.error);
  return result.stdout || '';
}

function _panelShell(id, title, warningNote) {
  return `
    <div style="margin-bottom:14px;border:1px solid ${warningNote ? 'var(--red, #e74c3c)' : 'var(--border)'};border-radius:4px;padding:8px;">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;">${esc(title)}</div>
      ${warningNote ? `<div style="font-size:10px;color:var(--red, #e74c3c);margin-bottom:6px;">\u26a0\ufe0f ${esc(warningNote)}</div>` : ''}
      <div id="${id}" style="font-size:11px;">Loading...</div>
    </div>
  `;
}

async function _loadPanel(id, tool, args) {
  const container = el(id);
  if (!container) return;
  container.innerHTML = 'Loading...';
  try {
    const raw = await _callTradingViewTool(tool, args);
    let display = raw;
    try {
      const parsed = JSON.parse(raw);
      display = JSON.stringify(parsed, null, 2);
    } catch (e) {
      // Not JSON -- show as-is (e.g. financial_news may return formatted text)
    }
    container.innerHTML = `<pre style="white-space:pre-wrap;max-height:220px;overflow:auto;margin:0;">${esc(display)}</pre>`;
  } catch (e) {
    // Real, observed behavior: top_gainers/top_losers in particular can
    // time out or return stale/overlapping data under real load -- this
    // is genuine upstream unreliability, not an argument-schema bug (both
    // confirmed via direct, side-by-side testing with the real, correct
    // schema). A manual retry is the honest fix, not a longer guess.
    const retryId = `${id}-retry-btn`;
    container.innerHTML = `<div class="admin-empty">Failed to load: ${esc(e.message)}</div><button id="${retryId}" style="font-size:10px;margin-top:4px;padding:2px 8px;">Retry</button>`;
    const retryBtn = el(retryId);
    if (retryBtn) retryBtn.addEventListener('click', () => _loadPanel(id, tool, args));
  }
}

function _renderShell() {
  return `
    ${_panelShell('panel-market-snapshot', 'Market Snapshot (Indices + VIX)')}
    <div style="display:flex;gap:12px;">
      <div style="flex:1;">${_panelShell('panel-top-gainers', 'Top Gainers')}</div>
      <div style="flex:1;">${_panelShell('panel-top-losers', 'Top Losers (\u26a0\ufe0f Known Upstream Issue)', 'This tool currently returns the same symbols as Top Gainers in reverse order. Confirmed, reproducible upstream TradingView MCP server bug -- data shown is not a genuine losers scan.')}</div>
    </div>
    <div style="margin-bottom:14px;border:1px solid var(--border);border-radius:4px;padding:8px;">
      <div style="font-weight:600;font-size:12px;margin-bottom:6px;">Symbol Drill-Down</div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <input id="market-dashboard-symbol-input" placeholder="e.g. NVDA" style="font-size:11px;flex:1;" value="NVDA">
        <button id="market-dashboard-symbol-btn" style="font-size:11px;padding:2px 10px;">Analyze</button>
      </div>
      <div id="panel-coin-analysis" style="font-size:11px;">Enter a symbol and click Analyze.</div>
    </div>
    ${_panelShell('panel-market-sentiment', 'Market Sentiment')}
    ${_panelShell('panel-financial-news', 'Financial News')}
    ${_panelShell('panel-bitcoin-pulse', 'Bitcoin Market Pulse')}
  `;
}

async function _render() {
  const container = el('market-dashboard-content');
  if (!container) return;
  container.innerHTML = _renderShell();

  const symbolBtn = el('market-dashboard-symbol-btn');
  if (symbolBtn) {
    symbolBtn.addEventListener('click', () => {
      const symbol = (el('market-dashboard-symbol-input')?.value || '').trim().toUpperCase();
      if (!symbol) return;
      _loadPanel('panel-coin-analysis', 'coin_analysis', { symbol, exchange: 'NASDAQ' });
    });
  }

  // Load the panels that don't need user input immediately.
  _loadPanel('panel-market-snapshot', 'market_snapshot', {});
  _loadPanel('panel-top-gainers', 'top_gainers', { exchange: 'NASDAQ', timeframe: '1d' });
  _loadPanel('panel-top-losers', 'top_losers', { exchange: 'NASDAQ', timeframe: '1d' });
  _loadPanel('panel-market-sentiment', 'market_sentiment', { symbol: 'SPY' });
  _loadPanel('panel-financial-news', 'financial_news', {});
  _loadPanel('panel-bitcoin-pulse', 'bitcoin_market_pulse', {});
}

export function openPanel() {
  const modal = el('market-dashboard-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  _render();
}

function _closePanel() {
  const modal = el('market-dashboard-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
}

function _ensureRegistered() {
  if (Modals.isRegistered('market-dashboard-modal')) return;
  Modals.register('market-dashboard-modal', {
    railBtnId: 'rail-market-dashboard',
    sidebarBtnId: 'tool-market-dashboard-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-market-dashboard-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-market-dashboard-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);
}

export default { init, openPanel };
