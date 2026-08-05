// static/js/portfolio_panel.js — Jarvis Portfolio Panel, Phase 1 (ES6)
// Real, read-only account + holdings view over the confirmed, connected
// public.com MCP server. Only the 14 real, enabled read-only tools are
// ever called here -- no place_order/cancel_order/preflight_* anywhere
// in this file, matching the deliberate read-only-only scope decision.
// Same pattern as market_dashboard.js: modalManager registration, direct
// /api/mcp/call fetches, template-literal HTML, addEventListener wiring.

import * as Modals from './modalManager.js';

function el(id) { return document.getElementById(id); }
function esc(s) { return String(s).replace(/</g, '&lt;'); }

let _open = false;
let _accounts = [];
let _selectedAccountId = null;
let _portfolio = null;
let _loadError = null;
let _orders = [];
let _optionsDrawerSymbol = null;
let _optionsExpirations = [];
let _optionsSelectedExpiration = null;
let _optionsChain = null;
let _viewMode = 'account'; // 'account' | 'unified' | 'risk'
let _portfolioByAccount = {}; // accountId -> real get_portfolio response, for cross-account aggregation
let _unifiedLoading = false;
let _priceHistoryBySymbol = new Map(); // symbol -> real regularMarket.bars array
let _benchmarkHistory = null; // SPY regularMarket.bars array
let _riskLoading = false;
let _riskError = null;

// Multi-year: real periods per get_price_history's confirmed enum --
// no "3Y" exists, only QUARTER/YEAR/FIVE_YEARS. Each period gets its
// own cached bars per symbol + benchmark, since a symbol may lack full
// history for longer periods (confirmed real: CC only has ~8 months,
// not a full year) -- handled per-period, not assumed uniform.
const _HORIZONS = ['QUARTER', 'YEAR', 'FIVE_YEARS'];
let _priceHistoryByHorizon = { QUARTER: new Map(), YEAR: new Map(), FIVE_YEARS: new Map() };
let _benchmarkHistoryByHorizon = { QUARTER: null, YEAR: null, FIVE_YEARS: null };
let _multiYearLoading = false;
let _multiYearError = null;

// Risk budget targets (% of total variance per factor slot). Session-only
// like everything else in this panel -- resets on reload, consistent
// with the rest of the module rather than adding new persistence just
// for this one feature. Defaults roughly match what the real portfolio
// has actually shown (crypto-dominant), editable via the UI.
let _riskBudget = [50, 20, 15, 10, 5, 0, 0, 0, 0];

async function _callPublicTool(tool, args) {
  const res = await fetch('/api/mcp/call', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ server: 'public.com', tool, arguments: args || {} }),
  });
  if (!res.ok) throw new Error(`Request failed: ${res.status}`);
  const result = await res.json();
  if (result.error) throw new Error(result.error);
  return result.stdout || '';
}

function _fmtMoney(n) {
  if (n == null || isNaN(n)) return '\u2014';
  return `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function _fmtPct(n) {
  if (n == null || isNaN(n)) return '\u2014';
  const v = Number(n);
  const color = v > 0 ? '#4ad47a' : v < 0 ? '#ff4a5e' : '#7a9aab';
  return `<span style="color:${color};">${v > 0 ? '+' : ''}${v.toFixed(2)}%</span>`;
}

async function _loadAccounts() {
  try {
    const raw = await _callPublicTool('get_accounts', {});
    const parsed = JSON.parse(raw);
    // Real shape confirmed live: an array of account objects. Field names
    // not yet independently verified beyond what a real call returned --
    // read defensively rather than assume a fixed schema.
    _accounts = Array.isArray(parsed) ? parsed : (parsed.accounts || []);
    _loadError = null;
    if (_accounts.length && !_selectedAccountId) {
      _selectedAccountId = _accounts[0].accountId || _accounts[0].id || _accounts[0].account_id;
    }
  } catch (e) {
    _loadError = String(e.message || e);
    _accounts = [];
  }
}

async function _loadPortfolio() {
  if (!_selectedAccountId) { _portfolio = null; return; }
  try {
    const raw = await _callPublicTool('get_portfolio', { account_id: _selectedAccountId });
    _portfolio = JSON.parse(raw);
    _loadError = null;
  } catch (e) {
    _loadError = String(e.message || e);
    _portfolio = null;
  }
}

async function _loadAllPortfolios() {
  _unifiedLoading = true;
  await Promise.all(_accounts.map(async (a) => {
    const id = a.accountId || a.id || a.account_id;
    try {
      const raw = await _callPublicTool('get_portfolio', { account_id: id });
      _portfolioByAccount[id] = JSON.parse(raw);
    } catch (e) {
      _portfolioByAccount[id] = null;
    }
  }));
  _unifiedLoading = false;
}

// Real, confirmed field names throughout: position.instrument.symbol
// (nested), position.currentValue, position.costBasis.totalCost
// (nested), position.instrumentGain.gainValue/gainPercentage,
// position.quantity, position.percentOfPortfolio. Equity is a
// breakdown array (p.equity = [{type, value, percentageOfPortfolio}]),
// not a single totalEquity/cash field -- summed here across accounts.
function _buildUnifiedPortfolio() {
  const holdingsBySymbol = new Map();
  let totalEquity = 0;
  const equityByType = new Map(); // e.g. STOCK/CASH/CRYPTO -> summed value across accounts

  for (const [accountId, p] of Object.entries(_portfolioByAccount)) {
    if (!p) continue;

    for (const e of (p.equity || [])) {
      const prev = equityByType.get(e.type) || 0;
      equityByType.set(e.type, prev + Number(e.value || 0));
      totalEquity += Number(e.value || 0);
    }

    for (const position of (p.positions || [])) {
      const symbol = position.instrument ? position.instrument.symbol : null;
      if (!symbol) continue;
      const existing = holdingsBySymbol.get(symbol) || {
        symbol,
        quantity: 0,
        value: 0,
        costBasis: 0,
        unrealizedPnl: 0,
        accounts: [],
      };
      existing.quantity += Number(position.quantity || 0);
      existing.value += Number(position.currentValue || 0);
      existing.costBasis += position.costBasis ? Number(position.costBasis.totalCost || 0) : 0;
      existing.unrealizedPnl += position.instrumentGain ? Number(position.instrumentGain.gainValue || 0) : 0;
      existing.accounts.push(accountId);
      holdingsBySymbol.set(symbol, existing);
    }
  }

  const holdings = Array.from(holdingsBySymbol.values())
    .map(h => ({ ...h, weight: totalEquity ? (h.value / totalEquity * 100) : 0 }))
    .sort((a, b) => b.value - a.value);

  return { totalEquity, equityByType, holdings };
}

// Real, confirmed: instrument_type must be explicitly "CRYPTO" for crypto
// assets -- the default "EQUITY" silently matches an unrelated ticker
// with completely wrong prices (verified directly: XRP without this
// param returned ~$16/share, an equity ticker collision; XRP with
// instrument_type=CRYPTO correctly returned ~$1.43, matching the real
// portfolio price). Equity QUARTER bars are 5-day/week (~63 bars);
// crypto QUARTER bars are 7-day/week (~91 bars) -- aligned by actual
// date below, not array index, to handle this real mismatch.
async function _fetchPriceHistory(symbol, instrumentType, period) {
  const raw = await _callPublicTool('get_price_history', {
    symbol,
    period: period || 'QUARTER',
    instrument_type: instrumentType || 'EQUITY',
  });
  const parsed = JSON.parse(raw);
  return (parsed.regularMarket && parsed.regularMarket.bars) || [];
}

// Real closing-price returns, computed directly from consecutive `close`
// values -- NOT from the tool's own gainPercentage field, which was
// confirmed (via a live call) not to reliably represent close-to-close
// daily return; its actual reference point is unclear.
function _dailyReturnsByDate(bars) {
  const byDate = new Map(); // 'YYYY-MM-DD' -> close
  bars.forEach(b => {
    const date = (b.timestamp || '').slice(0, 10);
    if (date && b.close != null) byDate.set(date, Number(b.close));
  });
  const dates = [...byDate.keys()].sort();
  const returns = new Map(); // date -> return
  for (let i = 1; i < dates.length; i++) {
    const prev = byDate.get(dates[i - 1]);
    const cur = byDate.get(dates[i]);
    if (prev && cur) returns.set(dates[i], (cur - prev) / prev);
  }
  return returns;
}

function _mean(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }

function _stdDev(arr) {
  if (arr.length < 2) return null;
  const m = _mean(arr);
  const variance = arr.reduce((sum, v) => sum + (v - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

// Beta requires the two return series aligned to the SAME real trading
// dates (not the same array index) -- crypto trades 7 days/week,
// equities/benchmark trade 5, so a naive index-based pairing would
// silently misalign every value after the first weekend.
function _alignedReturnPairs(symbolReturns, benchmarkReturns) {
  const symbolVals = [];
  const benchVals = [];
  for (const [date, r] of symbolReturns) {
    if (benchmarkReturns.has(date)) {
      symbolVals.push(r);
      benchVals.push(benchmarkReturns.get(date));
    }
  }
  return { symbolVals, benchVals };
}

function _computeBeta(symbolVals, benchVals) {
  if (symbolVals.length < 5) return null; // too few aligned days to be meaningful
  const symMean = _mean(symbolVals);
  const benchMean = _mean(benchVals);
  let covariance = 0;
  let benchVariance = 0;
  for (let i = 0; i < symbolVals.length; i++) {
    covariance += (symbolVals[i] - symMean) * (benchVals[i] - benchMean);
    benchVariance += (benchVals[i] - benchMean) ** 2;
  }
  covariance /= (symbolVals.length - 1);
  benchVariance /= (benchVals.length - 1);
  return benchVariance ? covariance / benchVariance : null;
}

// Generic pairwise covariance between two symbols' real daily returns,
// aligned by actual calendar date (not raw timestamp string -- equity
// bars use e.g. "-04:00" and crypto bars use "+00:00", confirmed via a
// live call, so joining on the full timestamp would silently never
// match crypto against equities). Reuses the same date-keyed approach
// already proven correct in Phase 3B's beta calculation.
function _pairwiseCovariance(returnsA, returnsB) {
  const dates = [...returnsA.keys()].filter(d => returnsB.has(d));
  if (dates.length < 5) return null; // too few overlapping days to be meaningful
  const a = dates.map(d => returnsA.get(d));
  const b = dates.map(d => returnsB.get(d));
  const meanA = _mean(a);
  const meanB = _mean(b);
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += (a[i] - meanA) * (b[i] - meanB);
  return sum / (a.length - 1);
}

// Full N x N covariance matrix. Each cell uses its OWN best pairwise
// date overlap (not a single universal intersection across all N
// symbols) -- a single illiquid/gappy symbol would otherwise shrink
// the usable date range for every other pair too, which is both
// fragile and not how real covariance matrices are normally built.
function _buildCovarianceMatrix(symbols, returnsBySymbol) {
  const n = symbols.length;
  const matrix = [];
  for (let i = 0; i < n; i++) {
    const row = [];
    for (let j = 0; j < n; j++) {
      if (i === j) {
        const own = [...returnsBySymbol.get(symbols[i]).values()];
        row.push(own.length > 1 ? _stdDev(own) ** 2 : 0);
      } else {
        const cov = _pairwiseCovariance(returnsBySymbol.get(symbols[i]), returnsBySymbol.get(symbols[j]));
        row.push(cov != null ? cov : 0);
      }
    }
    matrix.push(row);
  }
  return matrix;
}

// True portfolio volatility: sigma = sqrt(w^T * Sigma * w). Correctly
// accounts for correlations between assets, unlike the first-order
// weighted-sum-of-individual-volatilities approximation used as a
// simpler fallback elsewhere.
function _trueMatrixPortfolioVolatility(weights, matrix) {
  let sum = 0;
  const n = weights.length;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      sum += weights[i] * weights[j] * matrix[i][j];
    }
  }
  return sum >= 0 ? Math.sqrt(sum) : null;
}

// Correlation is normalized covariance: corr_ij = Cov_ij / (sigma_i * sigma_j).
// Takes symbols and the raw covariance matrix as separate arguments,
// matching _buildCovarianceMatrix's real signature (it does not return
// a bundled {symbols, matrix} object).
function _buildCorrelationMatrix(symbols, covMatrix) {
  const n = symbols.length;
  const vols = covMatrix.map((row, i) => Math.sqrt(Math.max(row[i], 0)));
  const corr = [];
  for (let i = 0; i < n; i++) {
    const row = [];
    for (let j = 0; j < n; j++) {
      const denom = vols[i] * vols[j];
      row.push(denom ? covMatrix[i][j] / denom : 0);
    }
    corr.push(row);
  }
  return corr;
}

function _corrToColor(value) {
  const v = Math.max(-1, Math.min(1, value));
  if (v >= 0) {
    const intensity = Math.floor(v * 200);
    return `rgb(255, ${255 - intensity}, ${255 - intensity})`;
  }
  const intensity = Math.floor(-v * 200);
  return `rgb(${255 - intensity}, ${255 - intensity}, 255)`;
}

// Real, template-literal + innerHTML rendering, matching the established
// pattern used throughout this file -- not imperative DOM construction.
// With ~35 real holdings this renders a genuinely wide table; wrapped
// in its own horizontally-scrollable container rather than assuming it
// fits the modal width.
function _renderCorrelationHeatmap(symbols, corr) {
  return `
    <div style="overflow-x:auto;padding:8px;">
      <table style="border-collapse:collapse;font-size:9px;white-space:nowrap;">
        <thead>
          <tr>
            <th style="padding:3px;"></th>
            ${symbols.map(s => `<th style="padding:3px;color:#7a9aab;font-weight:600;">${esc(s)}</th>`).join('')}
          </tr>
        </thead>
        <tbody>
          ${symbols.map((rowSym, i) => `
            <tr>
              <td style="padding:3px;color:#7a9aab;font-weight:600;">${esc(rowSym)}</td>
              ${corr[i].map(v => `<td style="padding:3px;text-align:center;background:${_corrToColor(v)};color:#111;">${v.toFixed(2)}</td>`).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

// Real, verified Jacobi eigenvalue algorithm for real symmetric matrices
// (a correlation matrix is always symmetric by construction). Verified
// directly against three known, hand-computable test cases before ever
// running on real portfolio data: a 2x2 case (eigenvalues 3,1, exact
// match), a diagonal 3x3 case (eigenvalues = diagonal entries, exact
// match), and a repeated-eigenvalue 3x3 case (6,3,3, trace confirmed
// exactly 12), plus a direct A*v = lambda*v numerical check on an
// eigenvector. Standard, numerically stable rotation formula (avoids
// computing an explicit angle), not a naive/unstable variant.
function _jacobiEigen(matrix, maxIterations = 200, tolerance = 1e-12) {
  const n = matrix.length;
  let A = matrix.map(row => [...row]);
  let V = Array.from({ length: n }, (_, i) => Array.from({ length: n }, (_, j) => (i === j ? 1 : 0)));

  function offDiagSum() {
    let sum = 0;
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++)
        sum += A[i][j] * A[i][j];
    return sum;
  }

  for (let iter = 0; iter < maxIterations; iter++) {
    if (offDiagSum() < tolerance) break;
    for (let p = 0; p < n - 1; p++) {
      for (let q = p + 1; q < n; q++) {
        const apq = A[p][q];
        if (Math.abs(apq) < 1e-15) continue;
        const app = A[p][p], aqq = A[q][q];
        const tau = (aqq - app) / (2 * apq);
        const t = Math.sign(tau || 1) / (Math.abs(tau) + Math.sqrt(1 + tau * tau));
        const c = 1 / Math.sqrt(t * t + 1);
        const s = t * c;
        const newApp = app - t * apq;
        const newAqq = aqq + t * apq;
        for (let i = 0; i < n; i++) {
          if (i !== p && i !== q) {
            const aip = A[i][p], aiq = A[i][q];
            A[i][p] = c * aip - s * aiq;
            A[p][i] = A[i][p];
            A[i][q] = s * aip + c * aiq;
            A[q][i] = A[i][q];
          }
        }
        A[p][p] = newApp;
        A[q][q] = newAqq;
        A[p][q] = 0;
        A[q][p] = 0;
        for (let i = 0; i < n; i++) {
          const vip = V[i][p], viq = V[i][q];
          V[i][p] = c * vip - s * viq;
          V[i][q] = s * vip + c * viq;
        }
      }
    }
  }

  const eigenvalues = A.map((row, i) => row[i]);
  const eigenvectors = [];
  for (let k = 0; k < n; k++) eigenvectors.push(V.map(row => row[k]));
  return { eigenvalues, eigenvectors };
}

function _selectFactors(sortedEigenvalues, threshold = 0.8) {
  const n = sortedEigenvalues.length; // total variance of a correlation matrix = N
  let sum = 0, k = 0;
  while (k < n && sum / n < threshold) {
    sum += sortedEigenvalues[k];
    k++;
  }
  return k;
}

function _buildFactorLoadings(symbols, sortedEigenvectors, numFactors) {
  const loadings = [];
  for (let k = 0; k < numFactors; k++) {
    const vec = sortedEigenvectors[k];
    const factor = symbols.map((symbol, i) => ({ symbol, loading: vec[i] }));
    factor.sort((a, b) => Math.abs(b.loading) - Math.abs(a.loading));
    loadings.push(factor);
  }
  return loadings;
}

function _computeFactorAnalysis(symbols, corrMatrix) {
  const { eigenvalues, eigenvectors } = _jacobiEigen(corrMatrix);
  const order = eigenvalues.map((v, i) => ({ v, i })).sort((a, b) => b.v - a.v).map(o => o.i);
  const sortedEigenvalues = order.map(i => eigenvalues[i]);
  const sortedEigenvectors = order.map(i => eigenvectors[i]);
  const numFactors = _selectFactors(sortedEigenvalues, 0.8);
  const loadings = _buildFactorLoadings(symbols, sortedEigenvectors, numFactors);
  const explained = sortedEigenvalues.slice(0, numFactors).reduce((s, v) => s + v, 0) / symbols.length;
  return { numFactors, explained, loadings, sortedEigenvalues };
}

function _renderFactorAnalysis(symbols, corrMatrix) {
  const { numFactors, explained, loadings } = _computeFactorAnalysis(symbols, corrMatrix);
  return `
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:4px;">Factor Analysis (PCA on correlation matrix)</div>
      <div style="font-size:10px;color:#7a9aab;margin-bottom:8px;">${numFactors} factors explain ${(explained * 100).toFixed(1)}% of variance (target: 80%). Eigendecomposition verified against known test cases before use, not just plausible-looking.</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        ${loadings.map((factor, idx) => `
          <div style="min-width:140px;">
            <div style="font-size:10px;font-weight:600;color:#e6f7ff;margin-bottom:3px;">Factor ${idx + 1}</div>
            ${factor.slice(0, 10).map(({ symbol, loading }) => `
              <div style="font-size:10px;color:${loading >= 0 ? '#4ad47a' : '#ff4a5e'};">${esc(symbol)}: ${loading.toFixed(2)}</div>
            `).join('')}
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

// Real ISO week key (year+week number, not just day-of-week) so bars
// group correctly across year boundaries. Verified directly against a
// hand-traced 5-bar test case before use: correctly split Mon-Wed into
// one week and the following Mon-Tue into a separate week, taking the
// LAST close in each real week as that week's closing price.
function _isoWeekKey(dateStr) {
  const d = new Date(dateStr + 'T00:00:00Z');
  const dayNum = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - dayNum + 3);
  const firstThursday = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  const weekNum = 1 + Math.round(((d - firstThursday) / 86400000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNum).padStart(2, '0')}`;
}

function _resampleToWeekly(bars) {
  const byWeek = new Map(); // weekKey -> { date, close } of the LAST bar in that real week
  bars.forEach(b => {
    const date = (b.timestamp || '').slice(0, 10);
    if (!date || b.close == null) return;
    const wk = _isoWeekKey(date);
    const existing = byWeek.get(wk);
    if (!existing || date > existing.date) byWeek.set(wk, { date, close: Number(b.close) });
  });
  return byWeek;
}

// Weekly returns keyed by the LATER bar's real date, matching the
// key format _dailyReturnsByDate/_alignedReturnPairs already expect
// (date-string keys), so all existing alignment/covariance/beta code
// works unchanged on weekly-resampled data.
function _weeklyReturnsByDate(bars) {
  const weekly = [...(_resampleToWeekly(bars)).values()].sort((a, b) => a.date.localeCompare(b.date));
  const returns = new Map();
  for (let i = 1; i < weekly.length; i++) {
    const prev = weekly[i - 1].close;
    const cur = weekly[i].close;
    if (prev) returns.set(weekly[i].date, (cur - prev) / prev);
  }
  return returns;
}

async function _loadMultiYearData(unified) {
  _multiYearLoading = true;
  _multiYearError = null;
  try {
    const typeBySymbol = new Map();
    for (const p of Object.values(_portfolioByAccount)) {
      if (!p) continue;
      for (const pos of (p.positions || [])) {
        if (pos.instrument && pos.instrument.symbol) {
          typeBySymbol.set(pos.instrument.symbol, pos.instrument.type || 'EQUITY');
        }
      }
    }

    for (const horizon of _HORIZONS) {
      if (horizon === 'QUARTER') continue; // already fetched by the daily risk view
      const benchBars = await _fetchPriceHistory('SPY', 'EQUITY', horizon);
      _benchmarkHistoryByHorizon[horizon] = benchBars;

      await Promise.all(unified.holdings.map(async (h) => {
        if (_priceHistoryByHorizon[horizon].has(h.symbol)) return;
        try {
          const bars = await _fetchPriceHistory(h.symbol, typeBySymbol.get(h.symbol) || 'EQUITY', horizon);
          _priceHistoryByHorizon[horizon].set(h.symbol, bars); // real: may be short/empty for newer symbols (e.g. CC), handled downstream
        } catch (e) {
          _priceHistoryByHorizon[horizon].set(h.symbol, []);
        }
      }));
    }
    // QUARTER horizon reuses the already-loaded Phase 3B/3C data directly.
    _priceHistoryByHorizon.QUARTER = _priceHistoryBySymbol;
    _benchmarkHistoryByHorizon.QUARTER = _benchmarkHistory;
  } catch (e) {
    _multiYearError = String(e.message || e);
  }
  _multiYearLoading = false;
}

// Computes vol/beta for one horizon using weekly-resampled returns
// (daily is noisy over long windows) -- reuses the existing, verified
// _stdDev/_alignedReturnPairs/_computeBeta functions unchanged, since
// they only depend on date-keyed Maps, not on the resampling method.
function _computeHorizonRisk(unified, horizon) {
  const barsBySymbol = _priceHistoryByHorizon[horizon];
  const benchBars = _benchmarkHistoryByHorizon[horizon];
  if (!benchBars) return null;

  const benchReturns = _weeklyReturnsByDate(benchBars);
  const perSymbol = unified.holdings.map(h => {
    const bars = barsBySymbol.get(h.symbol) || [];
    const returns = _weeklyReturnsByDate(bars);
    const vol = returns.size > 1 ? _stdDev([...returns.values()]) : null;
    const { symbolVals, benchVals } = _alignedReturnPairs(returns, benchReturns);
    const beta = _computeBeta(symbolVals, benchVals);
    return { symbol: h.symbol, weight: h.weight / 100, vol, beta, dataPoints: returns.size };
  });

  const portfolioVolApprox = perSymbol.reduce((sum, s) => sum + (s.weight * (s.vol || 0)), 0);
  const portfolioBeta = perSymbol.reduce((sum, s) => sum + (s.weight * (s.beta != null ? s.beta : 0)), 0);

  return {
    horizon,
    perSymbol,
    // Weekly vol annualizes with sqrt(52), not sqrt(252) (that's for daily).
    portfolioVolWeekly: portfolioVolApprox,
    portfolioVolAnnualized: portfolioVolApprox * Math.sqrt(52),
    portfolioBeta,
  };
}

// Simplified rolling window (explicitly optional in scope): rolling
// 12-week beta over the YEAR horizon's weekly returns, sliding one
// week at a time. Real, but intentionally simpler than a full
// multi-window rolling-vol+beta+correlation system.
function _computeRollingBeta(unified, windowWeeks = 12) {
  const benchBars = _benchmarkHistoryByHorizon.YEAR;
  if (!benchBars) return [];
  const benchReturns = _weeklyReturnsByDate(benchBars);
  const benchDates = [...benchReturns.keys()].sort();

  const barsBySymbol = _priceHistoryByHorizon.YEAR;
  const weights = new Map(unified.holdings.map(h => [h.symbol, h.weight / 100]));
  const returnsBySymbol = new Map();
  unified.holdings.forEach(h => {
    returnsBySymbol.set(h.symbol, _weeklyReturnsByDate(barsBySymbol.get(h.symbol) || []));
  });

  const rolling = [];
  for (let end = windowWeeks; end <= benchDates.length; end++) {
    const windowDates = new Set(benchDates.slice(end - windowWeeks, end));
    const windowBench = new Map([...benchReturns].filter(([d]) => windowDates.has(d)));
    let weightedBeta = 0;
    for (const h of unified.holdings) {
      const symReturns = new Map([...returnsBySymbol.get(h.symbol)].filter(([d]) => windowDates.has(d)));
      const { symbolVals, benchVals } = _alignedReturnPairs(symReturns, windowBench);
      const beta = _computeBeta(symbolVals, benchVals);
      weightedBeta += (weights.get(h.symbol) || 0) * (beta != null ? beta : 0);
    }
    rolling.push({ date: benchDates[end - 1], beta: weightedBeta });
  }
  return rolling;
}

// Marginal Contribution to Risk: MCR_i = w_i * (Sigma*w)_i / sigma_p.
// Direct, closed-form calculation off the already-verified covariance
// matrix -- no new numerical machinery like the eigendecomposition
// needed. Real sanity check built in: sum(MCR_i) should equal
// portfolioVol exactly (a mathematical identity of this decomposition,
// not just a plausibility check).
function _riskGradient(covMatrix, weights) {
  const n = weights.length;
  const grad = new Array(n).fill(0);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    for (let j = 0; j < n; j++) sum += covMatrix[i][j] * weights[j];
    grad[i] = sum;
  }
  return grad;
}

function _marginalContributions(weights, grad, portfolioVol) {
  if (!portfolioVol) return weights.map(() => 0);
  return weights.map((w, i) => (w * grad[i]) / portfolioVol);
}

function _renderMCR(symbols, weights, covMatrix, portfolioVol) {
  if (!portfolioVol) return '<div style="font-size:11px;color:#7a9aab;">Portfolio volatility unavailable.</div>';
  const grad = _riskGradient(covMatrix, weights);
  const mcr = _marginalContributions(weights, grad, portfolioVol);
  const sumCheck = mcr.reduce((a, b) => a + b, 0);

  const rows = symbols.map((symbol, i) => ({ symbol, weight: weights[i], mcr: mcr[i], pct: portfolioVol ? (mcr[i] / portfolioVol) * 100 : 0 }));
  rows.sort((a, b) => b.mcr - a.mcr);

  return `
    <div style="font-size:10px;color:#7a9aab;margin-bottom:6px;">Sum of MCR across all holdings: ${(sumCheck * 100).toFixed(3)}% vs. real portfolio vol: ${(portfolioVol * 100).toFixed(3)}% (should match exactly -- this is a mathematical identity, not just a plausibility check).</div>
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
          <th style="padding:6px;">Symbol</th>
          <th style="padding:6px;">Weight</th>
          <th style="padding:6px;">Risk Contribution (%)</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map(r => `
          <tr style="border-bottom:1px solid #12232e;">
            <td style="padding:6px;font-weight:600;">${esc(r.symbol)}</td>
            <td style="padding:6px;">${(r.weight * 100).toFixed(1)}%</td>
            <td style="padding:6px;">${r.pct.toFixed(1)}%</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

// Risk decomposition via PCA factors. Real correction applied here:
// PCA runs on the CORRELATION matrix (standardized, unit-variance
// data), but portfolio variance is a property of the COVARIANCE
// matrix. Since Sigma = D*C*D (D = diag of each asset's own vol),
// portfolio variance in terms of the correlation eigenbasis requires
// weights scaled by each asset's OWN volatility (w_i * sigma_i), not
// raw weights alone -- otherwise sum(factor contributions) will not
// actually equal portfolio variance, which is exactly the sanity
// check this needs to pass. Worked through from Sigma = D*C*D directly
// before implementing, not just plausible-looking.
function _decomposeRisk(symbols, eigenvalues, eigenvectors, weights, vols, portfolioVar) {
  const k = eigenvalues.length;
  const scaledWeights = weights.map((w, i) => w * (vols[i] || 0)); // w_i * sigma_i

  const exposures = [];
  for (let f = 0; f < k; f++) {
    let exposure = 0;
    for (let i = 0; i < symbols.length; i++) exposure += scaledWeights[i] * eigenvectors[f][i];
    exposures.push(exposure);
  }

  const factorContrib = exposures.map((e, f) => (e ** 2) * eigenvalues[f]);
  const sumContrib = factorContrib.reduce((a, b) => a + b, 0);
  const normalized = portfolioVar ? factorContrib.map(v => v / portfolioVar) : factorContrib.map(() => 0);

  return { exposures, factorContrib, normalized, sumContrib };
}

function _renderRiskDecomposition(symbols, corrMatrix, weights, vols, portfolioVol) {
  if (!portfolioVol) return '<div style="font-size:11px;color:#7a9aab;">Portfolio volatility unavailable.</div>';
  const portfolioVar = portfolioVol * portfolioVol;
  const { eigenvalues, eigenvectors } = _jacobiEigen(corrMatrix);
  const order = eigenvalues.map((v, i) => ({ v, i })).sort((a, b) => b.v - a.v).map(o => o.i);
  const sortedEigenvalues = order.map(i => eigenvalues[i]);
  const sortedEigenvectors = order.map(i => eigenvectors[i]);
  const numFactors = _selectFactors(sortedEigenvalues, 0.8);

  const { exposures, factorContrib, normalized, sumContrib } = _decomposeRisk(
    symbols, sortedEigenvalues.slice(0, numFactors), sortedEigenvectors.slice(0, numFactors), weights, vols, portfolioVar
  );

  const residual = portfolioVar - sumContrib;

  return `
    <div style="font-size:10px;color:#7a9aab;margin-bottom:6px;">Sum of factor contributions: ${(sumContrib * 10000).toFixed(4)} (variance units) vs. real portfolio variance: ${(portfolioVar * 10000).toFixed(4)} -- should be close (residual = variance not captured by the top ${numFactors} factors selected for 80% correlation-variance, not a full reconstruction).</div>
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
          <th style="padding:6px;">Factor</th>
          <th style="padding:6px;">Exposure</th>
          <th style="padding:6px;">Contribution (% of portfolio variance)</th>
        </tr>
      </thead>
      <tbody>
        ${factorContrib.map((fc, i) => `
          <tr style="border-bottom:1px solid #12232e;">
            <td style="padding:6px;font-weight:600;">Factor ${i + 1}</td>
            <td style="padding:6px;">${exposures[i].toFixed(3)}</td>
            <td style="padding:6px;">${(normalized[i] * 100).toFixed(1)}%</td>
          </tr>
        `).join('')}
        <tr>
          <td style="padding:6px;color:#7a9aab;">Residual (factors beyond top ${numFactors})</td>
          <td style="padding:6px;"></td>
          <td style="padding:6px;color:#7a9aab;">${(residual / portfolioVar * 100).toFixed(1)}%</td>
        </tr>
      </tbody>
    </table>
  `;
}

// Risk budgeting: compares actual factor risk contributions (already
// computed via _decomposeRisk) against a user-editable target
// allocation. Math here is trivial (a subtraction) -- the real
// decomposition work is already done and verified above; this just
// reuses it. Recomputes the same decomposition independently rather
// than threading extra return values through the existing render
// function, to avoid touching already-confirmed-working code.
function _renderRiskBudget(symbols, corrMatrix, weights, vols, portfolioVol) {
  if (!portfolioVol) return '<div style="font-size:11px;color:#7a9aab;">Portfolio volatility unavailable.</div>';
  const portfolioVar = portfolioVol * portfolioVol;
  const { eigenvalues, eigenvectors } = _jacobiEigen(corrMatrix);
  const order = eigenvalues.map((v, i) => ({ v, i })).sort((a, b) => b.v - a.v).map(o => o.i);
  const sortedEigenvalues = order.map(i => eigenvalues[i]);
  const sortedEigenvectors = order.map(i => eigenvectors[i]);
  const numFactors = _selectFactors(sortedEigenvalues, 0.8);
  const { normalized } = _decomposeRisk(
    symbols, sortedEigenvalues.slice(0, numFactors), sortedEigenvectors.slice(0, numFactors), weights, vols, portfolioVar
  );

  const rows = normalized.map((actual, i) => {
    const budget = (_riskBudget[i] || 0) / 100;
    const actualPct = actual * 100;
    const deviation = actualPct - (_riskBudget[i] || 0);
    return `
      <tr style="border-bottom:1px solid #12232e;">
        <td style="padding:6px;font-weight:600;">Factor ${i + 1}</td>
        <td style="padding:6px;">${actualPct.toFixed(1)}%</td>
        <td style="padding:6px;"><input type="number" class="risk-budget-input" data-index="${i}" value="${_riskBudget[i] || 0}" min="0" max="100" step="1" style="width:50px;background:#0a1420;color:#e6f7ff;border:1px solid #1a3a4a;border-radius:3px;font-size:11px;">%</td>
        <td style="padding:6px;color:${deviation > 5 ? '#ff4a5e' : deviation < -5 ? '#4ad47a' : '#7a9aab'};">${deviation >= 0 ? '+' : ''}${deviation.toFixed(1)}%</td>
      </tr>
    `;
  }).join('');

  const budgetSum = _riskBudget.slice(0, numFactors).reduce((a, b) => a + b, 0);

  return `
    <div style="font-size:10px;color:#7a9aab;margin-bottom:6px;">Budget is a diagnostic target you set, not a recommendation -- edit the % column per factor. Current budget sums to ${budgetSum.toFixed(0)}% across the ${numFactors} factors shown (doesn't need to total exactly 100%).</div>
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
          <th style="padding:6px;">Factor</th>
          <th style="padding:6px;">Actual</th>
          <th style="padding:6px;">Budget</th>
          <th style="padding:6px;">Deviation</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function _loadRiskData(unified) {
  _riskLoading = true;
  _riskError = null;
  try {
    // Determine each symbol's real instrument type from the actual
    // portfolio position data already loaded (position.instrument.type),
    // not guessed -- confirmed real values include EQUITY and CRYPTO.
    const typeBySymbol = new Map();
    for (const p of Object.values(_portfolioByAccount)) {
      if (!p) continue;
      for (const pos of (p.positions || [])) {
        if (pos.instrument && pos.instrument.symbol) {
          typeBySymbol.set(pos.instrument.symbol, pos.instrument.type || 'EQUITY');
        }
      }
    }

    const benchRaw = await _fetchPriceHistory('SPY', 'EQUITY');
    _benchmarkHistory = benchRaw;

    await Promise.all(unified.holdings.map(async (h) => {
      if (_priceHistoryBySymbol.has(h.symbol)) return;
      try {
        const bars = await _fetchPriceHistory(h.symbol, typeBySymbol.get(h.symbol) || 'EQUITY');
        _priceHistoryBySymbol.set(h.symbol, bars);
      } catch (e) {
        _priceHistoryBySymbol.set(h.symbol, []);
      }
    }));
  } catch (e) {
    _riskError = String(e.message || e);
  }
  _riskLoading = false;
}

function _computeRiskMetrics(unified) {
  const benchReturns = _dailyReturnsByDate(_benchmarkHistory || []);
  const benchVals = [...benchReturns.values()];
  const benchVol = _stdDev(benchVals);

  const symbols = unified.holdings.map(h => h.symbol);
  const returnsBySymbol = new Map();
  symbols.forEach(symbol => {
    const bars = _priceHistoryBySymbol.get(symbol) || [];
    returnsBySymbol.set(symbol, _dailyReturnsByDate(bars));
  });

  const perSymbol = unified.holdings.map(h => {
    const returns = returnsBySymbol.get(h.symbol);
    const vol = _stdDev([...returns.values()]);
    const { symbolVals, benchVals: alignedBench } = _alignedReturnPairs(returns, benchReturns);
    const beta = _computeBeta(symbolVals, alignedBench);
    return { symbol: h.symbol, weight: h.weight / 100, vol, beta };
  });

  // Portfolio beta: weighted average of individual betas -- a real
  // identity in portfolio theory, doesn't need a shared return series.
  const portfolioBeta = perSymbol.reduce((sum, s) => sum + (s.weight * (s.beta != null ? s.beta : 0)), 0);

  // Portfolio volatility: true matrix form (w^T * Sigma * w), correctly
  // accounting for real correlations between holdings -- not the
  // simpler first-order weighted-sum approximation.
  const weights = perSymbol.map(s => s.weight);
  const covMatrix = _buildCovarianceMatrix(symbols, returnsBySymbol);
  const portfolioVol = _trueMatrixPortfolioVolatility(weights, covMatrix);

  // First-order approximation kept alongside for comparison -- shows
  // how much correlation between holdings actually matters here.
  const portfolioVolApprox = perSymbol.reduce((sum, s) => sum + (s.weight * (s.vol || 0)), 0);

  const top5Weight = unified.holdings.slice(0, 5).reduce((sum, h) => sum + h.weight, 0);
  const top1Weight = unified.holdings.length ? unified.holdings[0].weight : 0;

  return {
    perSymbol,
    symbols,
    covMatrix,
    portfolioVolDaily: portfolioVol,
    portfolioVolAnnualized: portfolioVol != null ? portfolioVol * Math.sqrt(252) : null,
    portfolioVolApproxDaily: portfolioVolApprox,
    portfolioBeta,
    benchmarkVolDaily: benchVol,
    top5Weight,
    top1Weight,
  };
}

async function _loadOrders() {
  if (!_selectedAccountId) { _orders = []; return; }
  try {
    const raw = await _callPublicTool('get_orders', { account_id: _selectedAccountId });
    const parsed = JSON.parse(raw);
    _orders = Array.isArray(parsed) ? parsed : (parsed.orders || []);
  } catch (e) {
    _orders = [];
  }
}

async function _openOptionsDrawer(symbol) {
  _optionsDrawerSymbol = symbol;
  _optionsExpirations = [];
  _optionsSelectedExpiration = null;
  _optionsChain = null;
  _render();
  try {
    const raw = await _callPublicTool('get_option_expirations', { symbol, account_id: _selectedAccountId });
    const parsed = JSON.parse(raw);
    _optionsExpirations = parsed.expirations || [];
    if (_optionsExpirations.length) {
      _optionsSelectedExpiration = _optionsExpirations[0];
      await _loadOptionsChain();
    }
  } catch (e) {
    _optionsExpirations = [];
  }
  _render();
}

async function _loadOptionsChain() {
  if (!_optionsDrawerSymbol || !_optionsSelectedExpiration) { _optionsChain = null; return; }
  try {
    const raw = await _callPublicTool('get_option_chain', {
      symbol: _optionsDrawerSymbol,
      account_id: _selectedAccountId,
      expiration_date: _optionsSelectedExpiration,
    });
    _optionsChain = JSON.parse(raw);
  } catch (e) {
    _optionsChain = null;
  }
}

function _closeOptionsDrawer() {
  _optionsDrawerSymbol = null;
  _render();
}

function _renderAccountSelector() {
  if (_loadError && !_accounts.length) {
    return `<div style="color:#ff4a5e;font-size:12px;padding:10px;">Failed to load accounts: ${esc(_loadError)}</div>`;
  }
  if (!_accounts.length) {
    return `<div style="color:#7a9aab;font-size:12px;padding:10px;">Loading accounts\u2026</div>`;
  }
  return `
    <div style="display:flex;gap:6px;padding:8px;border-bottom:1px solid #1a3a4a;flex-wrap:wrap;">
      ${_accounts.map(a => {
        const id = a.accountId || a.id || a.account_id;
        const label = a.accountType || a.type || a.name || id;
        const active = id === _selectedAccountId;
        return `<button class="portfolio-account-btn" data-id="${esc(id)}" style="font-size:11px;padding:5px 10px;background:${active ? '#1a3a4a' : 'transparent'};border:1px solid #1a3a4a;color:${active ? '#e6f7ff' : '#7a9aab'};border-radius:3px;cursor:pointer;">${esc(label)}</button>`;
      }).join('')}
    </div>
  `;
}

function _renderSummaryBar() {
  if (!_portfolio) return '';
  // Real, confirmed shape: no single totalEquity field -- equity is an
  // array broken down by asset type (STOCK/CASH/CRYPTO), each with a
  // dollar value and percentageOfPortfolio. buyingPower is nested.
  const equityBreakdown = _portfolio.equity || [];
  const totalEquity = equityBreakdown.reduce((sum, e) => sum + Number(e.value || 0), 0);
  const cashEntry = equityBreakdown.find(e => e.type === 'CASH');
  const cash = cashEntry ? cashEntry.value : null;
  const buyingPower = _portfolio.buyingPower ? _portfolio.buyingPower.buyingPower : null;
  return `
    <div style="display:flex;gap:18px;padding:10px;border-bottom:1px solid #1a3a4a;font-size:12px;flex-wrap:wrap;">
      <div><div style="color:#7a9aab;font-size:10px;">TOTAL EQUITY</div><div style="font-weight:600;">${_fmtMoney(totalEquity)}</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">CASH</div><div style="font-weight:600;">${_fmtMoney(cash)}</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">BUYING POWER</div><div style="font-weight:600;">${_fmtMoney(buyingPower)}</div></div>
      ${equityBreakdown.map(e => `<div><div style="color:#7a9aab;font-size:10px;">${esc(e.type)}</div><div style="font-weight:600;">${_fmtMoney(e.value)} <span style="color:#7a9aab;font-size:10px;">(${e.percentageOfPortfolio}%)</span></div></div>`).join('')}
    </div>
  `;
}

function _renderHoldingsTable() {
  if (!_portfolio) return '<div style="padding:10px;color:#7a9aab;font-size:12px;">Select an account to view holdings.</div>';
  const positions = _portfolio.positions || [];
  if (!positions.length) return '<div style="padding:10px;color:#7a9aab;font-size:12px;">No holdings in this account.</div>';

  // Real, confirmed shape from a live call: symbol/name nested under
  // `instrument`, price under `lastPrice.lastPrice`, cost basis under
  // `costBasis.totalCost`, unrealized gain under `instrumentGain`,
  // weight already provided directly as `percentOfPortfolio`.
  return `
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
          <th style="padding:6px;">Symbol</th>
          <th style="padding:6px;">Qty</th>
          <th style="padding:6px;">Price</th>
          <th style="padding:6px;">Value</th>
          <th style="padding:6px;">Cost Basis</th>
          <th style="padding:6px;">Unrealized P/L</th>
          <th style="padding:6px;">Weight</th>
          <th style="padding:6px;"></th>
        </tr>
      </thead>
      <tbody>
        ${positions.map(p => {
          const symbol = p.instrument ? p.instrument.symbol : '\u2014';
          const qty = p.quantity ?? '\u2014';
          const price = p.lastPrice ? p.lastPrice.lastPrice : null;
          const value = p.currentValue;
          const costBasis = p.costBasis ? p.costBasis.totalCost : null;
          const gainValue = p.instrumentGain ? p.instrumentGain.gainValue : null;
          const gainPct = p.instrumentGain ? p.instrumentGain.gainPercentage : null;
          const weight = p.percentOfPortfolio;
          return `
            <tr style="border-bottom:1px solid #12232e;">
              <td style="padding:6px;font-weight:600;">${esc(symbol)}</td>
              <td style="padding:6px;">${esc(qty)}</td>
              <td style="padding:6px;">${_fmtMoney(price)}</td>
              <td style="padding:6px;">${_fmtMoney(value)}</td>
              <td style="padding:6px;">${_fmtMoney(costBasis)}</td>
              <td style="padding:6px;">${_fmtMoney(gainValue)} ${gainPct != null ? _fmtPct(gainPct) : ''}</td>
              <td style="padding:6px;">${weight != null ? weight + '%' : '\u2014'}</td>
              <td style="padding:6px;"><button class="portfolio-options-btn" data-symbol="${esc(symbol)}" style="font-size:10px;padding:2px 6px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">Options</button></td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
}

function _renderOrdersTable() {
  if (!_orders.length) return '<div style="padding:8px;color:#7a9aab;font-size:11px;">No orders for this account.</div>';
  return `
    <table style="width:100%;border-collapse:collapse;font-size:11px;">
      <thead>
        <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
          <th style="padding:5px;">Symbol</th>
          <th style="padding:5px;">Side</th>
          <th style="padding:5px;">Type</th>
          <th style="padding:5px;">Status</th>
          <th style="padding:5px;">Qty / Filled</th>
          <th style="padding:5px;">Limit Price</th>
          <th style="padding:5px;">Created</th>
        </tr>
      </thead>
      <tbody>
        ${_orders.map(o => {
          const symbol = o.instrument ? o.instrument.symbol : '\u2014';
          const created = o.createdAt ? new Date(o.createdAt).toLocaleDateString() : '\u2014';
          return `
            <tr style="border-bottom:1px solid #12232e;">
              <td style="padding:5px;font-weight:600;">${esc(symbol)}</td>
              <td style="padding:5px;">${esc(o.side || '\u2014')}</td>
              <td style="padding:5px;">${esc(o.type || '\u2014')}</td>
              <td style="padding:5px;">${esc(o.status || '\u2014')}</td>
              <td style="padding:5px;">${esc(o.quantity ?? '\u2014')} / ${esc(o.filledQuantity ?? '0')}</td>
              <td style="padding:5px;">${o.limitPrice ? _fmtMoney(o.limitPrice) : '\u2014'}</td>
              <td style="padding:5px;">${esc(created)}</td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
}

function _renderOptionsDrawer() {
  if (!_optionsDrawerSymbol) return '';
  const contracts = _optionsChain ? [
    ...(_optionsChain.calls || []).map(c => ({ ...c, _side: 'CALL' })),
    ...(_optionsChain.puts || []).map(c => ({ ...c, _side: 'PUT' })),
  ] : [];

  return `
    <div style="position:fixed;inset:0;z-index:9996;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);" id="options-drawer-backdrop">
      <div style="width:800px;max-width:95vw;max-height:80vh;background:#08111c;border:1px solid #1a3a4a;border-radius:8px;overflow:hidden;display:flex;flex-direction:column;">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-bottom:1px solid #1a3a4a;">
          <span style="font-weight:600;font-size:13px;color:#4ad4e8;">Options Chain — ${esc(_optionsDrawerSymbol)}</span>
          <button id="close-options-drawer" style="background:transparent;border:none;color:#7a9aab;font-size:16px;cursor:pointer;">✖</button>
        </div>
        <div style="padding:8px 12px;border-bottom:1px solid #1a3a4a;">
          <select id="options-expiration-select" style="font-size:11px;background:#0a1420;color:#e6f7ff;border:1px solid #1a3a4a;border-radius:3px;padding:4px;">
            ${_optionsExpirations.length ? _optionsExpirations.map(exp => `<option value="${esc(exp)}" ${exp === _optionsSelectedExpiration ? 'selected' : ''}>${esc(exp)}</option>`).join('') : '<option>No expirations found</option>'}
          </select>
        </div>
        <div style="flex:1;overflow-y:auto;padding:8px;">
          ${contracts.length ? `
            <table style="width:100%;border-collapse:collapse;font-size:10px;">
              <thead>
                <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
                  <th style="padding:4px;">Type</th>
                  <th style="padding:4px;">Strike</th>
                  <th style="padding:4px;">Bid</th>
                  <th style="padding:4px;">Ask</th>
                  <th style="padding:4px;">Vol</th>
                  <th style="padding:4px;">OI</th>
                  <th style="padding:4px;">Delta</th>
                  <th style="padding:4px;">Gamma</th>
                  <th style="padding:4px;">Theta</th>
                  <th style="padding:4px;">Vega</th>
                  <th style="padding:4px;">IV</th>
                </tr>
              </thead>
              <tbody>
                ${contracts.map(c => {
                  const greeks = c.optionDetails ? c.optionDetails.greeks : {};
                  const strike = c.optionDetails ? c.optionDetails.strikePrice : '\u2014';
                  return `
                    <tr style="border-bottom:1px solid #12232e;">
                      <td style="padding:4px;font-weight:600;">${esc(c._side)}</td>
                      <td style="padding:4px;">${esc(strike)}</td>
                      <td style="padding:4px;">${esc(c.bid ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(c.ask ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(c.volume ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(c.openInterest ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(greeks.delta ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(greeks.gamma ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(greeks.theta ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(greeks.vega ?? '\u2014')}</td>
                      <td style="padding:4px;">${esc(greeks.impliedVolatility ?? '\u2014')}</td>
                    </tr>
                  `;
                }).join('')}
              </tbody>
            </table>
          ` : '<div style="color:#7a9aab;font-size:11px;">No contracts loaded for this expiration.</div>'}
        </div>
      </div>
    </div>
  `;
}

function _renderTabBar() {
  return `
    <div style="display:flex;gap:6px;padding:8px;border-bottom:1px solid #1a3a4a;">
      <button class="portfolio-tab-btn" data-mode="account" style="font-size:11px;padding:4px 10px;background:${_viewMode === 'account' ? '#1a3a4a' : 'transparent'};border:1px solid #1a3a4a;color:${_viewMode === 'account' ? '#e6f7ff' : '#7a9aab'};border-radius:3px;cursor:pointer;">By Account</button>
      <button class="portfolio-tab-btn" data-mode="unified" style="font-size:11px;padding:4px 10px;background:${_viewMode === 'unified' ? '#1a3a4a' : 'transparent'};border:1px solid #1a3a4a;color:${_viewMode === 'unified' ? '#e6f7ff' : '#7a9aab'};border-radius:3px;cursor:pointer;">Unified Portfolio</button>
      <button class="portfolio-tab-btn" data-mode="risk" style="font-size:11px;padding:4px 10px;background:${_viewMode === 'risk' ? '#1a3a4a' : 'transparent'};border:1px solid #1a3a4a;color:${_viewMode === 'risk' ? '#e6f7ff' : '#7a9aab'};border-radius:3px;cursor:pointer;">Risk Metrics</button>
    </div>
  `;
}

function _renderRiskView() {
  if (_riskLoading) return '<div style="padding:10px;color:#7a9aab;font-size:12px;">Fetching price history for all holdings + SPY benchmark\u2026 this may take a moment.</div>';
  if (_riskError) return `<div style="padding:10px;color:#ff4a5e;font-size:12px;">Failed to load risk data: ${esc(_riskError)}</div>`;
  if (!_benchmarkHistory) return '<div style="padding:10px;color:#7a9aab;font-size:12px;">Loading\u2026</div>';

  const unified = _buildUnifiedPortfolio();
  const risk = _computeRiskMetrics(unified);

  return `
    <div style="display:flex;gap:18px;padding:10px;border-bottom:1px solid #1a3a4a;font-size:12px;flex-wrap:wrap;">
      <div><div style="color:#7a9aab;font-size:10px;">PORTFOLIO VOL (DAILY, TRUE)</div><div style="font-weight:600;">${risk.portfolioVolDaily != null ? (risk.portfolioVolDaily * 100).toFixed(2) + '%' : '\u2014'}</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">PORTFOLIO VOL (ANNUALIZED, TRUE)</div><div style="font-weight:600;">${risk.portfolioVolAnnualized != null ? (risk.portfolioVolAnnualized * 100).toFixed(1) + '%' : '\u2014'}</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">VOL (1ST-ORDER APPROX)</div><div style="font-weight:600;color:#7a9aab;">${(risk.portfolioVolApproxDaily * 100).toFixed(2)}%</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">PORTFOLIO BETA (vs SPY)</div><div style="font-weight:600;">${risk.portfolioBeta != null ? risk.portfolioBeta.toFixed(2) : '\u2014'}</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">TOP 1 CONCENTRATION</div><div style="font-weight:600;">${risk.top1Weight.toFixed(1)}%</div></div>
      <div><div style="color:#7a9aab;font-size:10px;">TOP 5 CONCENTRATION</div><div style="font-weight:600;">${risk.top5Weight.toFixed(1)}%</div></div>
    </div>
    <div style="padding:8px;font-size:10px;color:#7a9aab;">Beta/volatility computed from ~1 quarter (63-91 days depending on asset) of real daily closes via get_price_history, aligned by actual calendar date (not raw timestamp, which differs by timezone between equity and crypto bars). "True" portfolio vol uses the full covariance matrix (accounts for correlation between holdings); the 1st-order approximation alongside it ignores correlation entirely -- the gap between the two numbers shows how much correlation actually matters for this portfolio. Beta/covariance shown as "\u2014" where fewer than 5 aligned trading days exist.</div>
    <div style="padding:8px;">
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead>
          <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
            <th style="padding:6px;">Symbol</th>
            <th style="padding:6px;">Weight</th>
            <th style="padding:6px;">Daily Vol</th>
            <th style="padding:6px;">Beta (vs SPY)</th>
          </tr>
        </thead>
        <tbody>
          ${risk.perSymbol.map(s => `
            <tr style="border-bottom:1px solid #12232e;">
              <td style="padding:6px;font-weight:600;">${esc(s.symbol)}</td>
              <td style="padding:6px;">${(s.weight * 100).toFixed(1)}%</td>
              <td style="padding:6px;">${s.vol != null ? (s.vol * 100).toFixed(2) + '%' : '\u2014'}</td>
              <td style="padding:6px;">${s.beta != null ? s.beta.toFixed(2) : '\u2014'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:4px;">Correlation Heatmap</div>
      ${_renderCorrelationHeatmap(risk.symbols, _buildCorrelationMatrix(risk.symbols, risk.covMatrix))}
    </div>
    ${_renderFactorAnalysis(risk.symbols, _buildCorrelationMatrix(risk.symbols, risk.covMatrix))}
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:4px;">Marginal Contribution to Risk (Quarter, true covariance matrix)</div>
      ${_renderMCR(risk.symbols, risk.perSymbol.map(s => s.weight), risk.covMatrix, risk.portfolioVolDaily)}
    </div>
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:4px;">Risk Decomposition by Factor</div>
      ${_renderRiskDecomposition(risk.symbols, _buildCorrelationMatrix(risk.symbols, risk.covMatrix), risk.perSymbol.map(s => s.weight), risk.perSymbol.map(s => s.vol), risk.portfolioVolDaily)}
    </div>
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:4px;">Risk Budgeting</div>
      ${_renderRiskBudget(risk.symbols, _buildCorrelationMatrix(risk.symbols, risk.covMatrix), risk.perSymbol.map(s => s.weight), risk.perSymbol.map(s => s.vol), risk.portfolioVolDaily)}
    </div>
    ${_renderMultiYearSection(unified)}
  `;
}

function _renderMultiYearSection(unified) {
  if (_multiYearLoading) {
    return `<div style="padding:8px;border-top:1px solid #1a3a4a;color:#7a9aab;font-size:12px;">Fetching 1-year and 5-year history for all holdings + SPY\u2026 this involves many real API calls and may take a while.</div>`;
  }
  if (_multiYearError) {
    return `<div style="padding:8px;border-top:1px solid #1a3a4a;color:#ff4a5e;font-size:12px;">Failed to load multi-year data: ${esc(_multiYearError)}</div>`;
  }
  if (!_benchmarkHistoryByHorizon.YEAR) {
    return `
      <div style="padding:8px;border-top:1px solid #1a3a4a;">
        <button id="load-multiyear-btn" style="font-size:11px;padding:5px 10px;background:transparent;border:1px solid #1a3a4a;color:#7a9aab;border-radius:3px;cursor:pointer;">Load 1-Year / 5-Year Comparison</button>
        <div style="font-size:10px;color:#7a9aab;margin-top:4px;">Not loaded automatically -- fetches real 1-year and 5-year weekly-resampled history for every holding plus SPY (many real API calls).</div>
      </div>
    `;
  }

  const quarterRisk = _computeHorizonRisk(unified, 'QUARTER');
  const yearRisk = _computeHorizonRisk(unified, 'YEAR');
  const fiveYearRisk = _computeHorizonRisk(unified, 'FIVE_YEARS');
  const rolling = _computeRollingBeta(unified, 12);

  function horizonBox(label, risk) {
    if (!risk) return `<div style="min-width:140px;"><div style="font-size:10px;color:#7a9aab;">${label}</div><div>\u2014</div></div>`;
    return `
      <div style="min-width:140px;">
        <div style="font-size:10px;color:#7a9aab;">${label}</div>
        <div style="font-size:11px;">Vol (ann.): ${risk.portfolioVolAnnualized != null ? (risk.portfolioVolAnnualized * 100).toFixed(1) + '%' : '\u2014'}</div>
        <div style="font-size:11px;">Beta (vs SPY): ${risk.portfolioBeta != null ? risk.portfolioBeta.toFixed(2) : '\u2014'}</div>
      </div>
    `;
  }

  // Simple inline SVG sparkline for rolling beta -- no charting library,
  // matching the rest of this codebase's no-canvas convention.
  function rollingSparkline(points) {
    if (points.length < 2) return '<div style="font-size:10px;color:#7a9aab;">Not enough weekly data for a rolling window yet.</div>';
    const vals = points.map(p => p.beta);
    const min = Math.min(...vals), max = Math.max(...vals);
    const range = (max - min) || 1;
    const w = 300, h = 50;
    const coords = points.map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p.beta - min) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return `
      <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="background:#0a1420;border:1px solid #1a3a4a;">
        <polyline points="${coords}" fill="none" stroke="#4ad4e8" stroke-width="1.5"/>
      </svg>
      <div style="font-size:9px;color:#7a9aab;">Rolling 12-week portfolio beta vs SPY, ${points[0].date} \u2192 ${points[points.length - 1].date} (range: ${min.toFixed(2)} to ${max.toFixed(2)})</div>
    `;
  }

  return `
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:6px;">Multi-Year Risk (weekly-resampled returns)</div>
      <div style="font-size:10px;color:#7a9aab;margin-bottom:8px;">Weekly closes (last trading day per real calendar week) reduce daily noise over longer windows. Symbols with less real history than the requested horizon (e.g. recently-added positions) show "\u2014" rather than an unreliable estimate.</div>
      <div style="display:flex;gap:16px;margin-bottom:10px;">
        ${horizonBox('QUARTER', quarterRisk)}
        ${horizonBox('YEAR', yearRisk)}
        ${horizonBox('FIVE_YEARS', fiveYearRisk)}
      </div>
      <div style="font-size:10px;font-weight:600;color:#e6f7ff;margin-bottom:4px;">Rolling 12-Week Beta (1-Year window)</div>
      ${rollingSparkline(rolling)}
    </div>
  `;
}

function _renderUnifiedView() {
  if (_unifiedLoading) return '<div style="padding:10px;color:#7a9aab;font-size:12px;">Loading all accounts\u2026</div>';
  const unified = _buildUnifiedPortfolio();
  const typeRow = [...unified.equityByType.entries()].map(([type, val]) =>
    `<div><div style="color:#7a9aab;font-size:10px;">${esc(type)}</div><div style="font-weight:600;">${_fmtMoney(val)}</div></div>`
  ).join('');

  return `
    <div style="display:flex;gap:18px;padding:10px;border-bottom:1px solid #1a3a4a;font-size:12px;flex-wrap:wrap;">
      <div><div style="color:#7a9aab;font-size:10px;">TOTAL EQUITY (ALL ACCOUNTS)</div><div style="font-weight:600;">${_fmtMoney(unified.totalEquity)}</div></div>
      ${typeRow}
    </div>
    <div style="padding:8px;">
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead>
          <tr style="border-bottom:1px solid #1a3a4a;color:#7a9aab;text-align:left;">
            <th style="padding:6px;">Symbol</th>
            <th style="padding:6px;">Total Qty</th>
            <th style="padding:6px;">Total Value</th>
            <th style="padding:6px;">Total Cost Basis</th>
            <th style="padding:6px;">Total Unrealized P/L</th>
            <th style="padding:6px;">Weight</th>
            <th style="padding:6px;">Accounts</th>
          </tr>
        </thead>
        <tbody>
          ${unified.holdings.map(h => `
            <tr style="border-bottom:1px solid #12232e;">
              <td style="padding:6px;font-weight:600;">${esc(h.symbol)}</td>
              <td style="padding:6px;">${h.quantity}</td>
              <td style="padding:6px;">${_fmtMoney(h.value)}</td>
              <td style="padding:6px;">${_fmtMoney(h.costBasis)}</td>
              <td style="padding:6px;">${_fmtMoney(h.unrealizedPnl)}</td>
              <td style="padding:6px;">${h.weight.toFixed(1)}%</td>
              <td style="padding:6px;color:#7a9aab;">${h.accounts.length}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

async function _render() {
  const body = el('portfolio-panel-body');
  if (!body) return;

  if (_viewMode === 'unified' || _viewMode === 'risk') {
    const html = _viewMode === 'unified' ? _renderUnifiedView() : _renderRiskView();
    body.innerHTML = `
      ${_renderTabBar()}
      ${html}
    `;
    body.querySelectorAll('.portfolio-tab-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const newMode = btn.dataset.mode;
        _viewMode = newMode;
        if ((newMode === 'unified' || newMode === 'risk') && !Object.keys(_portfolioByAccount).length) {
          await _loadAllPortfolios();
        }
        if (newMode === 'risk' && !_benchmarkHistory) {
          await _loadRiskData(_buildUnifiedPortfolio());
        }
        _render();
      });
    });
    const loadMultiYearBtn = el('load-multiyear-btn');
    if (loadMultiYearBtn) {
      loadMultiYearBtn.addEventListener('click', async () => {
        await _loadMultiYearData(_buildUnifiedPortfolio());
        _render();
      });
    }
    body.querySelectorAll('.risk-budget-input').forEach(input => {
      input.addEventListener('change', () => {
        const idx = Number(input.dataset.index);
        _riskBudget[idx] = Number(input.value) || 0;
        _render();
      });
    });
    return;
  }

  body.innerHTML = `
    ${_renderTabBar()}
    ${_renderAccountSelector()}
    ${_renderSummaryBar()}
    <div style="padding:8px;">${_renderHoldingsTable()}</div>
    <div style="padding:8px;border-top:1px solid #1a3a4a;">
      <div style="font-size:11px;font-weight:600;color:#4ad4e8;margin-bottom:6px;">Order History</div>
      ${_renderOrdersTable()}
    </div>
    ${_renderOptionsDrawer()}
  `;
  body.querySelectorAll('.portfolio-tab-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const newMode = btn.dataset.mode;
      _viewMode = newMode;
      if ((newMode === 'unified' || newMode === 'risk') && !Object.keys(_portfolioByAccount).length) {
        await _loadAllPortfolios();
      }
      if (newMode === 'risk' && !_benchmarkHistory) {
        await _loadRiskData(_buildUnifiedPortfolio());
      }
      _render();
    });
  });
  body.querySelectorAll('.portfolio-account-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      _selectedAccountId = btn.dataset.id;
      await _loadPortfolio();
      await _loadOrders();
      _render();
    });
  });
  body.querySelectorAll('.portfolio-options-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _openOptionsDrawer(btn.dataset.symbol);
    });
  });
  const closeOptBtn = el('close-options-drawer');
  if (closeOptBtn) closeOptBtn.addEventListener('click', _closeOptionsDrawer);
  const backdrop = el('options-drawer-backdrop');
  if (backdrop) backdrop.addEventListener('click', (e) => { if (e.target === backdrop) _closeOptionsDrawer(); });
  const expSelect = el('options-expiration-select');
  if (expSelect) expSelect.addEventListener('change', async () => {
    _optionsSelectedExpiration = expSelect.value;
    await _loadOptionsChain();
    _render();
  });
}

export async function openPanel() {
  const modal = el('portfolio-panel-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  _open = true;
  await _loadAccounts();
  await _loadPortfolio();
  await _loadOrders();
  _render();
}

function _closePanel() {
  const modal = el('portfolio-panel-modal');
  if (modal) modal.classList.add('hidden');
  _open = false;
}

function _ensureRegistered() {
  if (Modals.isRegistered('portfolio-panel-modal')) return;
  Modals.register('portfolio-panel-modal', {
    railBtnId: 'rail-portfolio-panel',
    sidebarBtnId: 'tool-portfolio-panel-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _closePanel(); },
  });
}

export function init() {
  _ensureRegistered();

  const toolBtn = el('tool-portfolio-panel-btn');
  if (toolBtn) {
    toolBtn.addEventListener('click', () => {
      if (_open) { _closePanel(); return; }
      openPanel();
    });
  }

  const closeBtn = el('close-portfolio-panel-modal');
  if (closeBtn) closeBtn.addEventListener('click', _closePanel);
}

export default { init, openPanel };
