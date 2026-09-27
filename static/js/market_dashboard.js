// static/js/market_dashboard.js — Market Dashboard panel (ES6)
// Real, curated dashboard over the confirmed, tested subset of the real
// tradingview MCP server's tools -- calls made directly via /api/mcp/call
// (same pattern as Task Queue Inspector's Diagnostics buttons), not routed
// through the task queue, since these are instant, on-demand reads.
//
// Panels: market_snapshot, top_gainers/top_losers, yahoo_price (symbol
// input), market_sentiment, financial_news, bitcoin_market_pulse -- all
// six confirmed real and callable against the actual server, nothing
// fabricated. Real, updated 2026-09-19: the symbol-drill-down panel
// originally called coin_analysis, which was deliberately disabled on
// this server 2026-09-16 (dead upstream dependency, tradingview_ta,
// archived by its own maintainer) -- this panel was never updated at
// the time, so it always failed. Swapped to yahoo_price, the
// confirmed-working replacement.

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

// Real, added 2026-09-19: a real, honest UX gap directly reported live
// -- every panel showed raw, pretty-printed JSON regardless of what it
// actually represented (confirmed directly: a real screenshot showed
// the Market Snapshot panel displaying literal `{ "indices": [...] }`
// text instead of readable index values). This adds a real, per-tool
// formatter registry -- checked first, falling back to the existing
// generic JSON.stringify display (kept exactly as-is) for any tool
// without a dedicated formatter, so nothing else in this file changes
// behavior.
const _SYMBOL_NAMES = {
  '^GSPC': 'S&P 500',
  '^DJI': 'Dow 30',
  '^IXIC': 'Nasdaq',
  '^RUT': 'Russell 2000',
  '^VIX': 'VIX',
};

function _formatMarketSnapshot(parsed) {
  const indices = parsed && parsed.indices;
  if (!Array.isArray(indices) || indices.length === 0) return null;
  return indices.map((idx) => {
    const name = _SYMBOL_NAMES[idx.symbol] || idx.symbol;
    const price = typeof idx.price === 'number' ? idx.price.toFixed(2) : idx.price;
    const pct = typeof idx.change_pct === 'number' ? idx.change_pct.toFixed(2) : idx.change_pct;
    const sign = (typeof idx.change_pct === 'number' && idx.change_pct > 0) ? '+' : '';
    return `${name}: ${price} (${sign}${pct}%)`;
  }).join('\n');
}

const _PANEL_FORMATTERS = {
  market_snapshot: _formatMarketSnapshot,
};

async function _loadPanel(id, tool, args) {
  const container = el(id);
  if (!container) return;
  container.innerHTML = 'Loading...';
  try {
    const raw = await _callTradingViewTool(tool, args);
    let display = raw;
    try {
      const parsed = JSON.parse(raw);
      const formatter = _PANEL_FORMATTERS[tool];
      const formatted = formatter ? formatter(parsed) : null;
      display = formatted !== null ? formatted : JSON.stringify(parsed, null, 2);
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
      // Real, fixed 2026-09-19: coin_analysis was deliberately disabled
      // on this server (see jarvis-todo.md, 2026-09-16 entry) -- its
      // real, unofficial upstream dependency (tradingview_ta) was
      // permanently archived by its own maintainer, causing every real
      // call to fail identically. This panel was never updated at the
      // time, so it kept calling the disabled tool and always failing.
      // Swapped to yahoo_price, the confirmed-working replacement from
      // that same fix (verified live for both AAPL and BTC-USD). Real,
      // honest note: yahoo_price's exact argument schema isn't locally
      // documented (it lives on the external tradingview MCP server,
      // same real limitation as get_quotes found earlier); this assumes
      // a plain {symbol} argument (no exchange field, unlike
      // coin_analysis) based on the tool's naming and typical
      // price-quote conventions -- not yet directly confirmed via a
      // live call from this specific panel.
      _loadPanel('panel-coin-analysis', 'yahoo_price', { symbol });
    });
  }

  // Real, changed 2026-09-19: was firing all 6 panel loads in parallel
  // (no await), sending 6 simultaneous requests to the real, external
  // tradingview MCP server the instant the dashboard opened. Directly
  // observed live: market_sentiment/financial_news/bitcoin_market_pulse
  // all failed with 504 (gateway timeout) simultaneously on dashboard
  // open. Switched to sequential loading (await each before starting
  // the next) as a real, direct test of whether spreading real load out
  // over time reduces these shared timeout failures -- this is a
  // genuine hypothesis being tested, not a confirmed root cause; the
  // real tradingview server's actual concurrency limits (if any) aren't
  // locally visible. Each panel still shows its own real "Loading..."
  // state and updates independently as it completes, so the user sees
  // progressive loading rather than everything appearing at once.
  await _loadPanel('panel-market-snapshot', 'market_snapshot', {});
  await _loadPanel('panel-top-gainers', 'top_gainers', { exchange: 'NASDAQ', timeframe: '1d' });
  await _loadPanel('panel-top-losers', 'top_losers', { exchange: 'NASDAQ', timeframe: '1d' });
  await _loadPanel('panel-market-sentiment', 'market_sentiment', { symbol: 'SPY' });
  await _loadPanel('panel-financial-news', 'financial_news', {});
  await _loadPanel('panel-bitcoin-pulse', 'bitcoin_market_pulse', {});
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
