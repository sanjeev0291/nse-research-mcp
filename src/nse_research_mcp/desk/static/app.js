/* Stock Desk frontend: vanilla JS, talks to the local backend under /api. */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const api = {
  get: (u) => fetch(u).then(r => r.json()),
  post: (u, body) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }).then(r => r.json()),
  kite: (name, args) => api.post('/api/kite/call', { name, arguments: args || {} }),
};
const state = { connected: false, profile: null, holdings: null, enrich: {}, stock: null, loaded: {}, wl: [] };

// ---------- formatting ----------
const isNum = (n) => typeof n === 'number' && isFinite(n);
function inrGroup(n, d = 2) {
  const neg = n < 0; n = Math.abs(n);
  let [i, f] = n.toFixed(d).split('.');
  let last3 = i.slice(-3), rest = i.slice(0, -3);
  if (rest) { rest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ','); last3 = rest + ',' + last3; }
  return (neg ? '-' : '') + last3 + (d ? '.' + f : '');
}
const fmt = {
  inr: (n, d = 2) => isNum(n) ? '₹' + inrGroup(n, d) : '–',
  num: (n, d = 2) => isNum(n) ? inrGroup(n, d) : '–',
  int: (n) => isNum(n) ? inrGroup(n, 0) : '–',
  cr: (n) => isNum(n) ? '₹' + inrGroup(n, n >= 1000 ? 0 : 1) + ' cr' : '–',
  pct: (n, d = 2) => isNum(n) ? (n > 0 ? '+' : '') + n.toFixed(d) + '%' : '–',
  upct: (n, d = 2) => isNum(n) ? n.toFixed(d) + '%' : '–',
  cls: (n) => isNum(n) ? (n > 0 ? 'up' : n < 0 ? 'down' : '') : '',
};
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
function toast(msg, ms = 3500) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => { t.hidden = true; }, ms); }
function kv(el, pairs) { el.innerHTML = pairs.filter(p => p).map(([k, v, cls]) => `<span class="k">${esc(k)}</span><span class="${cls || ''}">${v ?? '–'}</span>`).join(''); }

// ---------- generic table ----------
function table(el, cols, rows, opts = {}) {
  if (!rows || !rows.length) { el.innerHTML = `<tr><td class="muted">${esc(opts.empty || 'Nothing to show')}</td></tr>`; return; }
  const sortKey = el._sort || opts.sort; const asc = el._asc ?? false;
  if (sortKey) rows = rows.slice().sort((a, b) => { const x = a[sortKey], y = b[sortKey]; if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1); });
  const head = cols.map(c => `<th data-k="${c.k}">${esc(c.label)}</th>`).join('');
  const body = rows.map((r, i) => `<tr class="${opts.onRow ? 'click' : ''}" data-i="${i}">` + cols.map(c => {
    const v = r[c.k];
    // formatters declared with a (value, row) signature get the row; plain number formatters only the value
    const out = c.fmt ? (c.fmt.length >= 2 ? c.fmt(v, r) : c.fmt(v)) : esc(v ?? '–');
    const cls = c.cls ? c.cls(v, r) : '';
    return `<td class="${cls}">${out}</td>`;
  }).join('') + '</tr>').join('');
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  $$('th', el).forEach(th => { th.onclick = () => { const k = th.dataset.k; el._asc = el._sort === k ? !el._asc : false; el._sort = k; table(el, cols, rows, opts); }; });
  if (opts.onRow) $$('tbody tr', el).forEach(tr => { tr.onclick = (e) => { if (e.target.tagName === 'BUTTON' || e.target.tagName === 'A') return; opts.onRow(rows[+tr.dataset.i]); }; });
  if (opts.onButton) $$('tbody button', el).forEach(b => { b.onclick = (e) => { e.stopPropagation(); opts.onButton(b.dataset.act, rows[+b.closest('tr').dataset.i]); }; });
}
// Kite tool results come in a few shapes; find the list we want.
function kiteRows(result, prefer = []) {
  if (Array.isArray(result)) return result;
  if (result && typeof result === 'object') {
    for (const k of prefer) if (Array.isArray(result[k])) return result[k];
    for (const k of Object.keys(result)) { if (Array.isArray(result[k])) return result[k]; }
    for (const k of Object.keys(result)) { if (result[k] && typeof result[k] === 'object') { const r = kiteRows(result[k], prefer); if (r.length) return r; } }
  }
  return [];
}

// ---------- tabs ----------
$$('#tabs button').forEach(b => { b.onclick = () => showTab(b.dataset.tab); });
function showTab(name) {
  $$('#tabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab').forEach(s => s.classList.toggle('active', s.id === 'tab-' + name));
  if (!state.loaded[name]) {
    state.loaded[name] = true;
    const loader = { portfolio: loadPortfolio, orders: loadOrders, market: loadMarket, watchlist: loadWatchlist, alerts: loadAlerts, ai: loadAiStatus }[name];
    if (loader) loader();
  }
}

// ---------- Zerodha connection ----------
async function refreshConn() {
  const s = await api.get('/api/kite/status');
  state.connected = !!s.connected; state.profile = s.profile;
  const pill = $('#connPill'), btn = $('#connBtn');
  if (s.connected) {
    const p = typeof s.profile === 'object' && s.profile ? s.profile : {};
    pill.textContent = `Zerodha: ${p.user_name || p.user_id || 'connected'}${p.user_id && p.user_name ? ' (' + p.user_id + ')' : ''}`;
    pill.className = 'pill on'; btn.textContent = 'Disconnect';
  } else {
    pill.textContent = s.error ? 'Zerodha: unreachable' : 'Zerodha: not logged in'; pill.className = 'pill off'; btn.textContent = btn._pending ? 'Verify login' : 'Connect';
  }
  return s.connected;
}
$('#connBtn').onclick = async () => {
  const btn = $('#connBtn');
  if (state.connected) { await api.post('/api/kite/logout'); btn._pending = false; state.loaded.portfolio = false; await refreshConn(); return; }
  if (btn._pending) {
    btn.disabled = true; const ok = await refreshConn(); btn.disabled = false;
    if (ok) { btn._pending = false; toast('Connected to Zerodha'); state.loaded.portfolio = false; showTab('portfolio'); }
    else toast('Still not logged in. Finish the Kite login in the other tab, then click Verify again.');
    return;
  }
  btn.disabled = true; const r = await api.post('/api/kite/login'); btn.disabled = false;
  if (r.error) { toast('Login failed: ' + r.error, 6000); return; }
  btn._pending = true; btn.textContent = 'Verify login';
  window.open(r.login_url, '_blank');
  toast('Log in with Kite (2FA) in the new tab, then click "Verify login" here.', 8000);
};

// ---------- Portfolio ----------
const H_COLS = [
  { k: 'symbol', label: 'Symbol', fmt: (v, r) => `<b>${esc(v)}</b>${r.t1_quantity ? ' <span class="muted">T1</span>' : ''}` },
  { k: 'quantity', label: 'Qty', fmt: fmt.int },
  { k: 'average_price', label: 'Avg', fmt: fmt.num },
  { k: 'last_price', label: 'LTP', fmt: fmt.num },
  { k: 'day_change_pct', label: 'Day', fmt: fmt.pct, cls: fmt.cls },
  { k: 'invested', label: 'Invested', fmt: (v) => fmt.inr(v, 0) },
  { k: 'value', label: 'Value', fmt: (v) => fmt.inr(v, 0) },
  { k: 'pnl', label: 'P&L', fmt: (v) => fmt.inr(v, 0), cls: fmt.cls },
  { k: 'pnl_pct', label: 'P&L %', fmt: fmt.pct, cls: fmt.cls },
  { k: 'weight_pct', label: 'Weight', fmt: fmt.upct },
  { k: 'industry', label: 'Industry', fmt: (v, r) => esc(v || '–') + (r.cap_bucket ? ` <span class="muted">${r.cap_bucket}</span>` : '') },
];
const E_COLS = [
  { k: 'pe_ttm', label: 'PE', fmt: fmt.num }, { k: 'pct_from_52w_high', label: 'From 52w hi', fmt: fmt.pct, cls: fmt.cls },
  { k: 'week52_change_pct', label: '52w chg', fmt: fmt.pct, cls: fmt.cls }, { k: 'analyst_consensus', label: 'Analysts', fmt: (v) => esc((v || '–').replace('_', ' ')) },
];
async function loadPortfolio() {
  $('#pfMeta').textContent = 'Loading…';
  const r = await api.get('/api/kite/holdings');
  if (r.error) {
    $('#pfMeta').textContent = r.not_logged_in ? 'Connect Zerodha (top right) to see your holdings.' : 'Error: ' + r.error;
    $('#pfCards').innerHTML = ''; $('#pfTable').innerHTML = ''; $('#pfSectors').innerHTML = ''; $('#posTable').innerHTML = ''; $('#margins').innerHTML = '';
    return;
  }
  state.holdings = r; $('#pfMeta').textContent = `${r.totals.count} holdings · ${new Date().toLocaleTimeString()}`;
  const t = r.totals;
  $('#pfCards').innerHTML = [
    ['Invested', fmt.inr(t.invested, 0)], ['Current value', fmt.inr(t.current, 0)],
    ['Overall P&L', fmt.inr(t.pnl, 0), fmt.pct(t.pnl_pct), fmt.cls(t.pnl)], ["Today's P&L", fmt.inr(t.day_pnl, 0), '', fmt.cls(t.day_pnl)],
    ['Top 3 weight', fmt.upct(t.top3_weight_pct), 'concentration'],
  ].map(([l, v, s, c]) => `<div class="card"><div class="lbl">${l}</div><div class="val ${c || ''}">${v}</div><div class="sub ${c || ''}">${s || ''}</div></div>`).join('');
  renderHoldings();
  $('#pfSectors').innerHTML = r.sectors.slice(0, 12).map(s => `<div><div style="display:flex;justify-content:space-between"><span>${esc(s.industry)}</span><span class="muted">${fmt.upct(s.weight_pct)} · ${fmt.inr(s.value, 0)}</span></div><div class="bar"><i style="width:${Math.min(100, s.weight_pct || 0)}%"></i></div></div>`).join('');
  loadPositions(); loadMargins();
}
function renderHoldings() {
  const rows = state.holdings.holdings.map(h => ({ ...h, ...(state.enrich[h.symbol] || {}) }));
  const cols = Object.keys(state.enrich).length ? H_COLS.concat(E_COLS) : H_COLS;
  table($('#pfTable'), cols, rows, { onRow: (r) => openStock(r.symbol) });
}
$('#pfRefresh').onclick = loadPortfolio;
$('#pfEnrich').onclick = async () => {
  if (!state.holdings) { toast('Load your holdings first'); return; }
  $('#pfEnrich').disabled = true; $('#pfMeta').textContent = 'Fetching research for every holding…';
  state.enrich = await api.post('/api/kite/enrich', { symbols: state.holdings.holdings.map(h => h.symbol) });
  $('#pfEnrich').disabled = false; $('#pfMeta').textContent = 'Enriched with Yahoo Finance data'; renderHoldings();
};
async function loadPositions() {
  const r = await api.kite('get_positions');
  if (r.error) { $('#posTable').innerHTML = `<tr><td class="muted">${esc(r.error)}</td></tr>`; return; }
  const rows = kiteRows(r.result, ['net']);
  table($('#posTable'), [
    { k: 'tradingsymbol', label: 'Symbol' }, { k: 'product', label: 'Product' }, { k: 'quantity', label: 'Qty', fmt: fmt.int },
    { k: 'average_price', label: 'Avg', fmt: fmt.num }, { k: 'last_price', label: 'LTP', fmt: fmt.num },
    { k: 'pnl', label: 'P&L', fmt: (v) => fmt.inr(v, 0), cls: fmt.cls }, { k: 'm2m', label: 'M2M', fmt: (v) => fmt.inr(v, 0), cls: fmt.cls },
  ], rows, { empty: 'No open positions' });
}
async function loadMargins() {
  const r = await api.kite('get_margins');
  if (r.error) { kv($('#margins'), [['Margins', esc(r.error)]]); return; }
  const m = r.result || {}; const eq = m.equity || m; const av = eq.available || {}; const ut = eq.utilised || {};
  kv($('#margins'), [
    ['Net available', fmt.inr(eq.net)], ['Cash', fmt.inr(av.cash)], ['Live balance', fmt.inr(av.live_balance)],
    ['Collateral', fmt.inr(av.collateral)], ['Utilised', fmt.inr(ut.debits)],
  ]);
}

// ---------- Orders ----------
const OPEN_STATES = ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED', 'PUT ORDER REQ RECEIVED', 'MODIFY VALIDATION PENDING', 'OPEN PENDING'];
async function loadOrders() {
  const [o, g, t] = await Promise.all([api.kite('get_orders'), api.kite('get_gtts'), api.kite('get_trades')]);
  const err = (el, e) => { el.innerHTML = `<tr><td class="muted">${esc(e.not_logged_in ? 'Connect Zerodha to see orders.' : e.error)}</td></tr>`; };
  if (o.error) err($('#ordTable'), o); else table($('#ordTable'), [
    { k: 'order_timestamp', label: 'Time', fmt: (v) => esc(String(v || '').slice(11, 19) || v) }, { k: 'tradingsymbol', label: 'Symbol' },
    { k: 'transaction_type', label: 'Side', cls: (v) => v === 'BUY' ? 'up' : 'down' }, { k: 'order_type', label: 'Type' }, { k: 'product', label: 'Product' },
    { k: 'quantity', label: 'Qty', fmt: fmt.int }, { k: 'filled_quantity', label: 'Filled', fmt: fmt.int }, { k: 'price', label: 'Price', fmt: fmt.num },
    { k: 'average_price', label: 'Avg', fmt: fmt.num }, { k: 'status', label: 'Status', fmt: (v, r) => esc(v) + (r.status_message ? ` <span class="muted">${esc(r.status_message)}</span>` : '') },
    { k: 'order_id', label: '', fmt: (v, r) => OPEN_STATES.includes(r.status) ? `<button class="btn small danger" data-act="cancel">Cancel</button>` : '' },
  ], kiteRows(o.result, ['orders']), { empty: 'No orders today', onButton: cancelOrder });
  if (g.error) err($('#gttTable'), g); else table($('#gttTable'), [
    { k: 'id', label: 'ID' }, { k: 'condition', label: 'Symbol', fmt: (v) => esc(v && v.tradingsymbol || '–') }, { k: 'type', label: 'Type' },
    { k: 'condition', label: 'Trigger', fmt: (v) => esc(((v && v.trigger_values) || []).join(' / ')) },
    { k: 'orders', label: 'Order', fmt: (v) => (v || []).map(x => `${esc(x.transaction_type)} ${x.quantity} @ ${fmt.num(x.price)}`).join('<br>') },
    { k: 'status', label: 'Status' }, { k: 'created_at', label: 'Created', fmt: (v) => esc(String(v || '').slice(0, 10)) },
    { k: 'id', label: '', fmt: (v, r) => r.status === 'active' ? `<button class="btn small danger" data-act="delete">Delete</button>` : '' },
  ], kiteRows(g.result, ['gtts']), { empty: 'No GTT triggers', onButton: deleteGtt });
  if (t.error) err($('#tradeTable'), t); else table($('#tradeTable'), [
    { k: 'fill_timestamp', label: 'Time', fmt: (v) => esc(String(v || '').slice(11, 19) || v) }, { k: 'tradingsymbol', label: 'Symbol' },
    { k: 'transaction_type', label: 'Side', cls: (v) => v === 'BUY' ? 'up' : 'down' }, { k: 'quantity', label: 'Qty', fmt: fmt.int }, { k: 'average_price', label: 'Price', fmt: fmt.num },
  ], kiteRows(t.result, ['trades']), { empty: 'No trades today' });
}
$('#ordRefresh').onclick = loadOrders;
function showResult(r) { const el = $('#orderResult'); el.hidden = false; el.textContent = JSON.stringify(r.error ? r : r.result, null, 2); }
$('#orderForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const a = { variety: f.variety, exchange: f.exchange, tradingsymbol: f.tradingsymbol.trim().toUpperCase(), transaction_type: f.transaction_type, quantity: +f.quantity, product: f.product, order_type: f.order_type, validity: f.validity };
  if (['LIMIT', 'SL'].includes(a.order_type)) { if (!f.price) { toast('Price is required for ' + a.order_type); return; } a.price = +f.price; }
  if (['SL', 'SL-M'].includes(a.order_type)) { if (!f.trigger_price) { toast('Trigger price is required for ' + a.order_type); return; } a.trigger_price = +f.trigger_price; }
  const summary = `${a.transaction_type} ${a.quantity} × ${a.tradingsymbol} (${a.exchange}) ${a.order_type}${a.price ? ' @ ' + a.price : ''}${a.trigger_price ? ' trigger ' + a.trigger_price : ''}, ${a.product}, ${a.variety}.`;
  if (!window.confirm(summary + '\n\nSend this order to Zerodha?')) return;
  const r = await api.kite('place_order', a); showResult(r); toast(r.error ? 'Order failed: ' + r.error : 'Order sent'); loadOrders();
};
$('#gttForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const sym = f.tradingsymbol.trim().toUpperCase();
  const ltp = await api.kite('get_ltp', { instruments: [`${f.exchange}:${sym}`] });
  const first = ltp.result && typeof ltp.result === 'object' ? Object.values(ltp.result)[0] : null;
  const lp = first && typeof first === 'object' ? first.last_price : first;
  if (!isNum(+lp)) { toast('Could not fetch last price for ' + sym + (ltp.error ? ': ' + ltp.error : '')); return; }
  const a = { exchange: f.exchange, tradingsymbol: sym, transaction_type: f.transaction_type, product: f.product, trigger_type: 'single', trigger_value: +f.trigger_value, limit_price: +f.limit_price, quantity: +f.quantity, last_price: +lp };
  if (!window.confirm(`GTT: ${a.transaction_type} ${a.quantity} × ${sym} when price hits ${a.trigger_value} (limit ${a.limit_price}). LTP now ${lp}.\n\nCreate this trigger?`)) return;
  const r = await api.kite('place_gtt_order', a); showResult(r); toast(r.error ? 'GTT failed: ' + r.error : 'GTT created'); loadOrders();
};
async function cancelOrder(act, row) {
  if (!window.confirm(`Cancel order ${row.order_id} (${row.transaction_type} ${row.quantity} ${row.tradingsymbol})?`)) return;
  const r = await api.kite('cancel_order', { variety: row.variety || 'regular', order_id: String(row.order_id) }); showResult(r); loadOrders();
}
async function deleteGtt(act, row) {
  if (!window.confirm(`Delete GTT ${row.id} on ${row.condition && row.condition.tradingsymbol}?`)) return;
  const r = await api.kite('delete_gtt_order', { trigger_id: +row.id }); showResult(r); loadOrders();
}
function prefillTrade(sym) { showTab('orders'); $('#orderForm').tradingsymbol.value = sym; $('#gttForm').tradingsymbol.value = sym; $('#orderForm').quantity.focus(); }

// ---------- Research ----------
let sugTimer;
$('#rsQuery').oninput = () => {
  clearTimeout(sugTimer); const q = $('#rsQuery').value.trim();
  if (q.length < 2) { $('#rsSuggest').hidden = true; return; }
  sugTimer = setTimeout(async () => {
    const rows = await api.get('/api/research/search?q=' + encodeURIComponent(q));
    const box = $('#rsSuggest'); box.hidden = !rows.length;
    box.innerHTML = rows.map(r => `<div data-s="${esc(r.symbol)}"><span><b>${esc(r.symbol)}</b> <span class="muted">${esc(r.name)}</span></span><span class="muted">${esc(r.industry || r.series || '')}</span></div>`).join('');
    $$('div', box).forEach(d => { d.onclick = () => { box.hidden = true; $('#rsQuery').value = d.dataset.s; openStock(d.dataset.s); }; });
  }, 250);
};
$('#rsQuery').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); const first = $('#rsSuggest div'); $('#rsSuggest').hidden = true; openStock(first ? first.dataset.s : $('#rsQuery').value.trim().toUpperCase()); } };
document.addEventListener('click', (e) => { if (!e.target.closest('#rsSuggest, #rsQuery')) $('#rsSuggest').hidden = true; });
$('#rsWatch').onclick = async () => { if (!state.stock) return; await api.post('/api/watchlist', { action: 'add', symbols: [state.stock] }); toast(state.stock + ' added to watchlist'); state.loaded.watchlist = false; };
$('#rsTrade').onclick = () => { if (state.stock) prefillTrade(state.stock); };
$$('.seg button').forEach(b => { b.onclick = () => { $$('.seg button').forEach(x => x.classList.toggle('active', x === b)); loadFinancials(state.stock, b.dataset.period); }; });

async function openStock(sym) {
  if (!sym) return; showTab('research'); state.stock = sym.toUpperCase();
  $('#rsEmpty').textContent = 'Loading ' + state.stock + '…'; $('#rsEmpty').hidden = false; $('#rsView').hidden = true;
  const d = await api.get('/api/research/stock/' + encodeURIComponent(state.stock));
  if (d.quote.error && d.profile.error) { $('#rsEmpty').textContent = d.quote.error; return; }
  $('#rsEmpty').hidden = true; $('#rsView').hidden = false;
  const q = d.quote.error ? {} : d.quote, p = d.profile.error ? {} : d.profile;
  $('#rsName').textContent = `${p.name || q.name || state.stock} (${state.stock})`;
  $('#rsSub').textContent = [p.sector, p.industry, p.nse_industry && p.nse_industry !== p.industry ? p.nse_industry : null, p.cap_bucket ? p.cap_bucket + ' cap' : null, p.market_cap_cr ? fmt.cr(p.market_cap_cr) : null, ...(p.index_membership || []).slice(0, 2)].filter(Boolean).join(' · ');
  $('#rsPrice').textContent = fmt.inr(q.price); $('#rsChange').textContent = `${fmt.inr(q.change)} (${fmt.pct(q.change_pct)})`; $('#rsChange').className = fmt.cls(q.change);
  const h = d.history.error ? null : d.history;
  drawChart($('#rsChart'), h ? h.candles : []);
  $('#rsChartMeta').textContent = h ? `${h.from} → ${h.to} · 1y ${fmt.pct(h.returns_pct && h.returns_pct['1y'])}` : d.history.error;
  const tech = (h && h.technicals) || {}, ret = (h && h.returns_pct) || {};
  kv($('#rsStats'), [
    ['52-week range', `${fmt.num(q.week52_low)} – ${fmt.num(q.week52_high)}`], ['From 52w high', fmt.pct(q.pct_from_52w_high), fmt.cls(q.pct_from_52w_high)],
    ['Day range', `${fmt.num(q.day_low)} – ${fmt.num(q.day_high)}`], ['Volume vs 3m avg', fmt.upct(q.volume_vs_avg_pct)],
    h && ['Returns 1m / 3m / 6m', ['1m', '3m', '6m'].map(k => fmt.pct(ret[k])).join(' / ')],
    h && ['SMA 50 / 200', `${fmt.num(tech.sma50)} / ${fmt.num(tech.sma200)}`], h && ['RSI (14)', fmt.num(tech.rsi14, 1)],
    h && ['Max drawdown (1y)', fmt.pct(tech.max_drawdown_pct), 'down'], h && ['Volatility (ann.)', fmt.upct(tech.annualized_volatility_pct)],
  ]);
  const r = d.ratios.error ? null : d.ratios;
  $('#rsRatios').innerHTML = r ? [
    ['Market cap', fmt.cr(r.valuation.market_cap_cr)], ['PE (ttm)', fmt.num(r.valuation.pe_ttm)], ['PE (fwd)', fmt.num(r.valuation.pe_forward)], ['PB', fmt.num(r.valuation.pb)],
    ['EV/EBITDA', fmt.num(r.valuation.ev_to_ebitda)], ['P/S', fmt.num(r.valuation.price_to_sales_ttm)], ['ROE', fmt.upct(r.profitability_pct.roe)], ['ROA', fmt.upct(r.profitability_pct.roa)],
    ['Op. margin', fmt.upct(r.profitability_pct.operating_margin)], ['Net margin', fmt.upct(r.profitability_pct.net_margin)], ['Revenue growth', fmt.pct(r.growth_pct.revenue_growth_yoy)], ['Earnings growth', fmt.pct(r.growth_pct.earnings_growth_yoy)],
    ['Debt/Equity', fmt.upct(r.balance_sheet.debt_to_equity_pct)], ['Current ratio', fmt.num(r.balance_sheet.current_ratio)], ['EPS (ttm)', fmt.num(r.per_share.eps_ttm)], ['Dividend yield', fmt.upct(r.per_share.dividend_yield_pct)],
    ['Promoters/insiders', fmt.upct(r.ownership_pct.insiders_promoters)], ['Institutions', fmt.upct(r.ownership_pct.institutions)], ['Beta', fmt.num(r.risk.beta)], ['FCF', fmt.cr(r.cash_flow.free_cash_flow_cr)],
  ].map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${v}</div></div>`).join('') : `<span class="muted">${esc(d.ratios.error)}</span>`;
  const a = d.analyst.error ? null : d.analyst;
  kv($('#rsAnalyst'), a ? [
    ['Consensus', esc((a.consensus || '–').replace('_', ' ')) + (a.analysts ? ` <span class="muted">(${a.analysts} analysts)</span>` : '')],
    ['Target mean', `${fmt.inr(a.target_mean)} <span class="${fmt.cls(a.upside_to_mean_target_pct)}">${fmt.pct(a.upside_to_mean_target_pct)}</span>`],
    ['Target range', `${fmt.inr(a.target_low)} – ${fmt.inr(a.target_high)}`],
    a.earnings_dates && a.earnings_dates[0] && ['Next / last result', a.earnings_dates.slice(0, 2).map(e => `${e.date}${isNum(e.surprise_pct) ? ' (' + fmt.pct(e.surprise_pct) + ' surprise)' : ''}`).join(', ')],
  ] : [['Analysts', esc(d.analyst.error)]]);
  const sh = d.shareholding.error ? null : d.shareholding;
  if (sh) table($('#rsShp'), [{ k: 'quarter_end', label: 'Quarter' }, { k: 'promoter_pct', label: 'Promoter %', fmt: fmt.num }, { k: 'public_pct', label: 'Public %', fmt: fmt.num }], sh.trend);
  else $('#rsShp').innerHTML = `<tr><td class="muted">${esc(d.shareholding.error)}</td></tr>`;
  const ac = d.actions.error ? null : d.actions;
  $('#rsActions').innerHTML = ac ? (ac.upcoming_board_meetings.map(m => `<div class="item"><b>Board meeting ${esc(m.date)}</b>: ${esc(m.purpose)}</div>`).join('') + ac.corporate_actions.slice(0, 8).map(x => `<div class="item">${esc(x.subject)}<div class="d">ex-date ${esc(x.ex_date)}${x.upcoming ? ' · upcoming' : ''}</div></div>`).join('')) || '<span class="muted">None on record</span>' : `<span class="muted">${esc(d.actions.error)}</span>`;
  const an = d.announcements.error ? null : d.announcements;
  $('#rsAnn').innerHTML = an ? an.announcements.map(x => `<div class="item"><b>${esc(x.subject)}</b> ${x.attachment ? `<a href="${esc(x.attachment)}" target="_blank" rel="noopener">PDF</a>` : ''}<div>${esc(x.text)}</div><div class="d">${esc(x.date)}</div></div>`).join('') : `<span class="muted">${esc(d.announcements.error)}</span>`;
  $$('.seg button').forEach((b, i) => b.classList.toggle('active', i === 0));
  loadFinancials(state.stock, 'annual');
}
async function loadFinancials(sym, period) {
  $('#rsFin').innerHTML = '<span class="muted">Loading…</span>';
  const f = await api.get(`/api/research/financials/${encodeURIComponent(sym)}?period=${period}`);
  if (f.error) { $('#rsFin').innerHTML = `<span class="muted">${esc(f.error)}</span>`; return; }
  const blocks = [['Income statement', f.income_statement], ['Balance sheet', f.balance_sheet], ['Cash flow', f.cash_flow]].filter(b => b[1]);
  $('#rsFin').innerHTML = blocks.map(([title, s]) => `<table><thead><tr><th>${title} (₹ cr)</th>${s.periods.map(p => `<th>${esc(p.slice(0, 7))}</th>`).join('')}</tr></thead><tbody>${Object.entries(s.rows).map(([k, vals]) => `<tr><td>${esc(k)}</td>${vals.map(v => `<td>${fmt.num(v, k.includes('EPS') ? 2 : 0)}</td>`).join('')}</tr>`).join('')}</tbody></table>`).join('<br>');
}
function drawChart(svg, candles) {
  const W = 640, H = 260, L = 48, R = 8, T = 10, B = 24;
  svg.innerHTML = '';
  if (!candles || candles.length < 2) return;
  const cl = candles.map(c => c.close).filter(isNum); const min = Math.min(...cl), max = Math.max(...cl), span = (max - min) || 1;
  const x = (i) => L + (i / (candles.length - 1)) * (W - L - R); const y = (v) => T + (1 - (v - min) / span) * (H - T - B);
  const pts = candles.map((c, i) => `${x(i).toFixed(1)},${y(c.close).toFixed(1)}`);
  const up = cl[cl.length - 1] >= cl[0]; const col = up ? 'var(--up)' : 'var(--down)';
  let g = '';
  for (let k = 0; k <= 4; k++) { const v = min + (span * k) / 4; g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1"/><text x="${L - 6}" y="${y(v) + 4}" font-size="10" text-anchor="end" fill="var(--muted)">${inrGroup(v, v > 1000 ? 0 : 1)}</text>`; }
  [0, 0.25, 0.5, 0.75, 1].forEach(f => { const i = Math.round(f * (candles.length - 1)); g += `<text x="${x(i)}" y="${H - 6}" font-size="10" text-anchor="${f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}" fill="var(--muted)">${candles[i].date.slice(0, 7)}</text>`; });
  g += `<path d="M${pts[0]} L${pts.slice(1).join(' L')} L${x(candles.length - 1)},${y(min)} L${x(0)},${y(min)} Z" fill="${col}" opacity="0.08"/>`;
  g += `<polyline points="${pts.join(' ')}" fill="none" stroke="${col}" stroke-width="1.6"/>`;
  svg.innerHTML = g;
}

// ---------- Screener ----------
const SC_COLS = [
  { k: 'symbol', label: 'Symbol', fmt: (v, r) => `<b>${esc(v)}</b> <span class="muted">${esc(r.name || '')}</span>` }, { k: 'price', label: 'Price', fmt: fmt.num },
  { k: 'change_1d_pct', label: '1d', fmt: fmt.pct, cls: fmt.cls }, { k: 'market_cap_cr', label: 'Mcap', fmt: fmt.cr }, { k: 'pe_ttm', label: 'PE', fmt: fmt.num },
  { k: 'pb', label: 'PB', fmt: fmt.num }, { k: 'dividend_yield_pct', label: 'Div %', fmt: fmt.num }, { k: 'change_52w_pct', label: '52w', fmt: fmt.pct, cls: fmt.cls },
  { k: 'pct_from_52w_high', label: 'From hi', fmt: fmt.pct, cls: fmt.cls }, { k: 'analyst_rating', label: 'Analysts' }, { k: 'nse_industry', label: 'Industry', fmt: (v, r) => esc(v || '–') + (r.cap_bucket ? ` <span class="muted">${r.cap_bucket}</span>` : '') },
];
$('#scForm').onsubmit = async (e) => {
  e.preventDefault(); $('#scMeta').textContent = 'Screening…';
  const body = {};
  for (const [k, v] of new FormData(e.target).entries()) { if (v === '') continue; body[k] = k === 'ascending' ? v === 'true' : (k === 'sector' || k === 'sort_by') ? v : +v; }
  const r = await api.post('/api/research/screen', body);
  if (r.error) { $('#scMeta').textContent = r.error; return; }
  $('#scMeta').textContent = `${r.total_matches ?? r.returned} matches, showing ${r.returned}`;
  table($('#scTable'), SC_COLS, r.results, { onRow: (row) => openStock(row.symbol) });
};

// ---------- Market ----------
const MOVER_COLS = [{ k: 'symbol', label: 'Symbol' }, { k: 'ltp', label: 'LTP', fmt: fmt.num }, { k: 'change_pct', label: 'Chg', fmt: fmt.pct, cls: fmt.cls }];
async function loadMarket() {
  $('#mkMeta').textContent = 'Loading…';
  const m = await api.get('/api/research/market');
  const p = m.pulse.error ? null : m.pulse;
  $('#mkMeta').textContent = p ? (p.market_status || []).filter(s => s.market && s.market !== 'null').map(s => `${s.market}: ${s.status}`).join(' · ') : m.pulse.error;
  $('#mkIndices').innerHTML = p ? p.key_indices.map(i => `<div class="card"><div class="lbl">${esc(i.index)}</div><div class="val">${fmt.num(i.last)}</div><div class="sub ${fmt.cls(i.change_pct)}">${fmt.pct(i.change_pct)}${isNum(i.pe) ? ` <span class="muted">PE ${fmt.num(i.pe, 1)}</span>` : ''}</div></div>`).join('') : '';
  kv($('#mkSectors'), p ? [...(p.sector_leaders || []).map(s => [s.index, fmt.pct(s.change_pct), 'up']), ...(p.sector_laggards || []).map(s => [s.index, fmt.pct(s.change_pct), 'down'])] : []);
  kv($('#mkFlows'), p && Array.isArray(p.fii_dii_latest) ? p.fii_dii_latest.map(f => [`${f.category} (${f.date})`, `net ${fmt.num(f.net_cr, 0)} · buy ${fmt.num(f.buy_cr, 0)} · sell ${fmt.num(f.sell_cr, 0)}`, fmt.cls(f.net_cr)]) : [['FII/DII', 'unavailable']]);
  const rows = (x, k) => x.error ? [] : x[k];
  table($('#mkGainers'), MOVER_COLS, rows(m.gainers, 'stocks'), { onRow: (r) => openStock(r.symbol) });
  table($('#mkLosers'), MOVER_COLS, rows(m.losers, 'stocks'), { onRow: (r) => openStock(r.symbol) });
  table($('#mkActive'), [{ k: 'symbol', label: 'Symbol' }, { k: 'price', label: 'LTP', fmt: fmt.num }, { k: 'change_pct', label: 'Chg', fmt: fmt.pct, cls: fmt.cls }, { k: 'traded_value_cr', label: 'Value', fmt: fmt.cr }], rows(m.active, 'stocks'), { onRow: (r) => openStock(r.symbol) });
  const c = m.nifty_chain.error ? null : m.nifty_chain;
  kv($('#mkChain'), c ? [['Spot', fmt.num(c.spot)], ['Expiry', c.expiry], ['PCR (OI)', fmt.num(c.pcr_oi)], ['Max pain', fmt.int(c.max_pain_strike)], ['ATM IV (C/P)', `${fmt.num(c.atm_iv.call, 1)} / ${fmt.num(c.atm_iv.put, 1)}`],
    ['Call walls', c.call_oi_walls_resistance.slice(0, 3).map(w => fmt.int(w.strike)).join(', ')], ['Put walls', c.put_oi_walls_support.slice(0, 3).map(w => fmt.int(w.strike)).join(', ')]] : [['Options', esc(m.nifty_chain.error)]]);
  const BR = [{ k: 'symbol', label: 'Symbol', fmt: (v, r) => `<b>${esc(v)}</b> <span class="muted">${esc(r.name || '')}</span>` }, { k: 'ltp', label: 'LTP', fmt: fmt.num }, { k: 'change_pct', label: 'Chg', fmt: fmt.pct, cls: fmt.cls }, { k: 'new_52w_level', label: 'New level', fmt: fmt.num }];
  table($('#mkHighs'), BR, rows(m.highs, 'stocks'), { onRow: (r) => openStock(r.symbol) });
  table($('#mkLows'), BR, rows(m.lows, 'stocks'), { onRow: (r) => openStock(r.symbol) });
  const d = m.deals.error ? null : m.deals;
  table($('#mkDeals'), [{ k: 'kind', label: 'Type' }, { k: 'symbol', label: 'Symbol', fmt: (v, r) => `<b>${esc(v)}</b> <span class="muted">${esc(r.name || '')}</span>` }, { k: 'client', label: 'Client' }, { k: 'side', label: 'Side', cls: (v) => v === 'BUY' ? 'up' : 'down' }, { k: 'quantity', label: 'Qty', fmt: fmt.int }, { k: 'avg_price', label: 'Price', fmt: fmt.num }],
    d ? [...d.bulk_deals.map(x => ({ kind: 'Bulk', ...x })), ...d.block_deals.map(x => ({ kind: 'Block', ...x }))] : [], { onRow: (r) => openStock(r.symbol), empty: d ? 'No deals reported yet' : m.deals.error });
  loadStrip(p);
}
function loadStrip(p) {
  if (!p) return;
  const want = ['NIFTY 50', 'NIFTY BANK', 'INDIA VIX'];
  const status = (p.market_status || []).filter(s => s.market === 'Capital Market').map(s => 'NSE ' + String(s.status || '').toLowerCase()).join('');
  $('#marketStrip').innerHTML = p.key_indices.filter(i => want.includes(i.index)).map(i => `<span>${esc(i.index)} <b>${fmt.num(i.last)}</b> <span class="${fmt.cls(i.change_pct)}">${fmt.pct(i.change_pct)}</span></span>`).join('') + `<span class="muted">${esc(status)}</span>`;
}
$('#mkRefresh').onclick = loadMarket;

// ---------- Watchlist ----------
async function loadWatchlist() {
  const r = await api.get('/api/watchlist'); state.wl = r.symbols || [];
  const rows = (r.quotes || []).length ? r.quotes : state.wl.map(s => ({ symbol: s }));
  table($('#wlTable'), [
    { k: 'symbol', label: 'Symbol', fmt: (v, r) => `<b>${esc(v)}</b> <span class="muted">${esc(r.name || '')}</span>` }, { k: 'price', label: 'Price', fmt: fmt.num }, { k: 'sector', label: 'Sector' },
    { k: 'pe_ttm', label: 'PE', fmt: fmt.num }, { k: 'week52_change_pct', label: '52w', fmt: fmt.pct, cls: fmt.cls }, { k: 'pct_from_52w_high', label: 'From hi', fmt: fmt.pct, cls: fmt.cls },
    { k: 'analyst_consensus', label: 'Analysts', fmt: (v) => esc((v || '–').replace('_', ' ')) }, { k: 'symbol', label: '', fmt: () => `<button class="btn small danger" data-act="remove">Remove</button>` },
  ], rows, { empty: 'Watchlist is empty', onRow: (row) => openStock(row.symbol), onButton: async (act, row) => { await api.post('/api/watchlist', { action: 'remove', symbols: [row.symbol] }); loadWatchlist(); } });
}
$('#wlAddBtn').onclick = async () => { const s = $('#wlAdd').value.trim(); if (!s) return; await api.post('/api/watchlist', { action: 'add', symbols: [s] }); $('#wlAdd').value = ''; loadWatchlist(); };
$('#wlAdd').onkeydown = (e) => { if (e.key === 'Enter') $('#wlAddBtn').click(); };
$('#wlRefresh').onclick = loadWatchlist;

// ---------- Ask Claude ----------
async function loadAiStatus() {
  const s = await api.get('/api/ai/status');
  if (!s.available) { $('#aiNote').textContent = 'Claude Code CLI was not found on this machine, so Ask Claude is unavailable. Install it from claude.com/claude-code.'; $('#aiAsk').disabled = true; }
}
$$('#aiPresets button').forEach(b => { b.onclick = () => { $('#aiQ').value = b.textContent; $('#aiQ').focus(); }; });
$('#aiAsk').onclick = async () => {
  const q = $('#aiQ').value.trim(); if (!q) return;
  $('#aiAsk').disabled = true; $('#aiMeta').textContent = 'Thinking (this can take a minute or two)…'; $('#aiAnswer').innerHTML = '';
  const r = await api.post('/api/ai/ask', { question: q, include_portfolio: $('#aiPf').checked });
  $('#aiAsk').disabled = false;
  if (r.error) { $('#aiMeta').textContent = ''; $('#aiAnswer').innerHTML = `<span class="down">${esc(r.error)}</span>`; return; }
  $('#aiMeta').textContent = [r.turns ? `${r.turns} turns` : '', isNum(r.cost_usd) ? `$${r.cost_usd.toFixed(3)}` : ''].filter(Boolean).join(' · ');
  $('#aiAnswer').innerHTML = md(r.answer || '');
};
function md(src) {
  const lines = src.replace(/\r/g, '').split('\n'); let out = '', i = 0;
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>');
  const isTable = (l) => /^\|.*\|\s*$/.test(l);
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith('```')) { let j = i + 1; const buf = []; while (j < lines.length && !lines[j].startsWith('```')) buf.push(lines[j++]); out += `<pre>${esc(buf.join('\n'))}</pre>`; i = j + 1; continue; }
    if (isTable(l)) {
      const rows = [];
      while (i < lines.length && isTable(lines[i])) { const cells = lines[i].trim().slice(1, -1).split('|').map(c => c.trim()); if (!cells.every(c => /^:?-+:?$/.test(c))) rows.push(cells); i++; }
      out += '<table>' + rows.map((r, k) => '<tr>' + r.map(c => `<${k ? 'td' : 'th'}>${inline(c)}</${k ? 'td' : 'th'}>`).join('') + '</tr>').join('') + '</table>'; continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/); if (h) { out += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`; i++; continue; }
    if (/^\s*[-*]\s+/.test(l)) { out += '<ul>'; while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { out += `<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`; i++; } out += '</ul>'; continue; }
    if (/^\s*\d+[.)]\s+/.test(l)) { out += '<ol>'; while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) { out += `<li>${inline(lines[i].replace(/^\s*\d+[.)]\s+/, ''))}</li>`; i++; } out += '</ol>'; continue; }
    if (!l.trim()) { i++; continue; }
    const buf = []; while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\||\s*[-*]\s|\s*\d+[.)]\s)/.test(lines[i])) buf.push(lines[i++]);
    out += `<p>${inline(buf.join(' '))}</p>`;
  }
  return out;
}

// ---------- Alerts ----------
let alertsSince = Date.now() / 1000, unread = 0;
const DIR_LABEL = { either: 'up or down', up: 'up only', down: 'down only' };
function ruleText(r, kinds) {
  const k = (kinds && kinds[r.kind]) || r.kind;
  const x = r.kind === 'above' || r.kind === 'below' ? fmt.inr(r.value) : r.value + '%';
  return k.replace('X%', x).replace('X', x) + (r.kind === 'move' || r.kind.startsWith('portfolio') ? ` (${DIR_LABEL[r.direction] || ''})` : '');
}
function updateBell() { const b = $('#bellCount'); b.hidden = unread === 0; b.textContent = unread; }
async function loadAlerts() {
  const d = await api.get('/api/alerts'); state.alerts = d;
  const st = d.status || {};
  $('#alStatus').textContent = st.waiting ? `Paused: ${st.waiting}` : st.last_check_ist ? `Last check ${st.last_check_ist} IST · ${(st.last_summary || {}).quotes || 0} quotes` : (st.running ? 'Running, no check yet' : 'Not running');
  table($('#alRules'), [
    { k: 'symbol', label: 'Symbol', fmt: (v) => `<b>${esc(v)}</b>` },
    { k: 'kind', label: 'Rule', fmt: (v, r) => esc(ruleText(r, d.kinds)) + (r.note ? ` <span class="muted">${esc(r.note)}</span>` : '') },
    { k: 'repeat', label: 'Repeat', fmt: (v) => v ? 'yes' : 'once' },
    { k: 'state', label: 'Last price', fmt: (v) => fmt.num(v && v.last_price) },
    { k: 'state', label: 'Last fired', fmt: (v) => { const t = Math.max(0, ...Object.values((v && v.last_fired) || {})); return t ? new Date(t * 1000).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–'; } },
    { k: 'enabled', label: 'Status', fmt: (v) => `<button class="btn small" data-act="toggle">${v ? 'on' : 'off'}</button>`, cls: (v) => v ? 'up' : 'muted' },
    { k: 'id', label: '', fmt: () => `<button class="btn small danger" data-act="delete">Delete</button>` },
  ], d.rules, { empty: 'No alerts yet. Add one above.', onButton: async (act, row) => { await api.post(`/api/alerts/rules/${row.id}`, { action: act }); loadAlerts(); } });
  renderEvents(d.events);
  const s = d.settings || {}; const f = $('#alSettings');
  f.interval_sec.value = s.interval_sec ?? 60; f.cooldown_min.value = s.cooldown_min ?? 30; f.only_market_hours.checked = !!s.only_market_hours; f.desktop.checked = !!s.desktop;
  f.telegram_token.placeholder = s.telegram_token_set ? `set (${s.telegram_token_hint}); blank = keep` : 'not set'; f.telegram_chat_id.value = s.telegram_chat_id || '';
  unread = 0; updateBell();
}
function renderEvents(evs) {
  $('#alEvents').innerHTML = (evs && evs.length) ? evs.slice(0, 30).map(e => `<div class="item"><b>${esc(e.message)}</b><div class="d">${esc(e.time_ist)}${e.note ? ' · ' + esc(e.note) : ''}${e.delivered ? ` · ${e.delivered.desktop ? 'desktop ✓' : ''} ${e.delivered.telegram ? 'telegram ✓' : ''}` : ''}</div></div>`).join('') : '<span class="muted">Nothing has fired yet.</span>';
}
$('#alKind').onchange = () => { $('#alSymbolWrap').style.display = $('#alKind').value.startsWith('portfolio') ? 'none' : ''; };
$('#alForm').onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  const r = await api.post('/api/alerts/rules', { symbol: f.symbol, kind: f.kind, value: +f.value, direction: f.direction, repeat: f.repeat === 'true', note: f.note });
  if (r.error) { toast(r.error); return; }
  toast(`Alert added: ${ruleText(r, state.alerts && state.alerts.kinds)}`); e.target.reset(); $('#alKind').onchange(); loadAlerts();
};
$('#alCheck').onclick = async () => { $('#alCheck').disabled = true; const r = await api.post('/api/alerts/check'); $('#alCheck').disabled = false; toast(`Checked ${r.rules} rules, ${r.quotes} quotes, ${r.events.length} fired${r.note ? ' · ' + r.note : ''}`); loadAlerts(); };
$('#alSettings').onsubmit = async (e) => {
  e.preventDefault(); const f = e.target;
  const r = await api.post('/api/alerts/settings', { interval_sec: +f.interval_sec.value, cooldown_min: +f.cooldown_min.value, only_market_hours: f.only_market_hours.checked, desktop: f.desktop.checked, telegram_token: f.telegram_token.value.trim(), telegram_chat_id: f.telegram_chat_id.value.trim() });
  $('#alSettingsMeta').textContent = r.error ? r.error : 'Saved'; f.telegram_token.value = ''; loadAlerts();
};
$('#alTest').onclick = async () => { const r = await api.post('/api/alerts/test'); toast(`Test sent: desktop ${r.desktop ? '✓' : '✗'}, telegram ${r.telegram ? '✓' : (state.alerts && state.alerts.settings.telegram_token_set ? '✗' : 'not set')}`, 6000); };
$('#alBrowser').onclick = async () => { if (!('Notification' in window)) { toast('This browser has no notification support'); return; } const p = await Notification.requestPermission(); toast('Browser notifications: ' + p); };
async function pollAlerts() {
  try {
    const r = await api.get('/api/alerts/events?since=' + alertsSince);
    alertsSince = r.now || alertsSince;
    for (const e of r.events || []) {
      toast(e.message, 8000);
      if ('Notification' in window && Notification.permission === 'granted') new Notification('Stock Desk: ' + e.symbol, { body: e.message });
    }
    if ((r.events || []).length) { unread += r.events.length; updateBell(); if ($('#tab-alerts').classList.contains('active')) loadAlerts(); }
  } catch (err) { /* server may be restarting */ }
}
setInterval(pollAlerts, 30000);
$('#bell').onclick = () => showTab('alerts');

// ---------- deep links (#market, #research/TCS) ----------
const TABS = ['portfolio', 'orders', 'research', 'screener', 'market', 'watchlist', 'alerts', 'ai'];
function applyHash() {
  const [tab, arg] = location.hash.replace(/^#/, '').split('/');
  if (tab === 'research' && arg) { openStock(decodeURIComponent(arg)); return; }
  if (TABS.includes(tab)) showTab(tab);
}
window.addEventListener('hashchange', applyHash);
const _showTab = showTab;
showTab = function (name) { _showTab(name); if (!(name === 'research' && state.stock)) history.replaceState(null, '', '#' + name); };
const _openStock = openStock;
openStock = function (sym) { if (sym) history.replaceState(null, '', '#research/' + encodeURIComponent(sym.toUpperCase())); return _openStock(sym); };

// ---------- boot ----------
(async () => {
  await refreshConn();
  api.get('/api/research/market').then(m => loadStrip(m.pulse && m.pulse.error ? null : m.pulse)).catch(() => {});
  if (location.hash && location.hash !== '#portfolio') { state.loaded.portfolio = true; loadPortfolio(); applyHash(); }
  else { state.loaded.portfolio = true; loadPortfolio(); }
})();
