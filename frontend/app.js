import { api } from './api.js';
import { escape as e } from './format.js';
import { overview, detail, sidebar, watchTable } from './views.js';
import { markets, draftKey, routeHref, parseRoute } from './markets.js';

const main = document.querySelector('#main');
const state = { market:'HK', route: 'overview', symbol: null, day: '', filter: 'all', kind: 'all', search: '', range: 60, drafts: {}, openReports: new Set(), closedReports: new Set() };
let data, stock, revision = 0, pollTimer, toastTimer;

function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.hidden = false;
  element.classList.toggle('error', error);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}

function renderNavigation() {
  const info = markets[state.market];
  document.body.dataset.market = state.market;
  document.querySelector('#brand-market').textContent = info.code;
  document.querySelector('.brand').href = routeHref(state.market);
  document.querySelector('#market-switch').dataset.market = state.market;
  document.querySelectorAll('#market-switch a').forEach(a => {
    const selected = a.dataset.market === state.market;
    a.classList.toggle('active', selected);
    if (selected) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
  });
  document.querySelectorAll('#main-nav a').forEach(a => {
    a.href = routeHref(state.market, a.dataset.route);
    const selected = a.dataset.route === state.route;
    a.classList.toggle('active', selected);
    if (selected) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  document.querySelector('#market-status').textContent = `${data?.market.label || '载入中'} · ${info.timezone_label}`;
  document.querySelector('#breadcrumb').innerHTML = `研究台 <span>/</span> ${info.name} <span>/</span> ${e(state.symbol ? stock?.name || '个股档案' : state.route === 'attention' ? '重点变化' : '今日总览')}`;
  document.title = `${state.symbol && stock ? stock.name : state.route === 'attention' ? '重点变化' : '今日总览'} · ${info.name} · 市场观察`;
}

function render() {
  const active = document.activeElement;
  const focused = active?.id;
  const selection = ['stock-note', 'stock-search'].includes(focused) ? [active.selectionStart, active.selectionEnd] : null;
  document.querySelector('#stock-nav').innerHTML = sidebar(data, state.symbol || state.route);
  document.querySelector('#watch-count').textContent = data.stocks.length;
  document.querySelector('#attention-count').textContent = new Set(data.reports.filter(r => r.importance === 'important').map(r => r.symbol)).size || '';
  renderNavigation();
  main.innerHTML = state.symbol && stock ? detail(data, stock, state) : overview(data, state);
  if (selection) {
    const input = document.getElementById(focused);
    input?.focus({ preventScroll: true });
    input?.setSelectionRange(...selection);
  }
}

async function load({ quiet = false, read = false } = {}) {
  const token = ++revision;
  const symbol = state.symbol, day = state.day, market = state.market;
  try {
    const [nextData, nextStock] = await Promise.all([api.overview(day, market), symbol ? api.stock(symbol, day, market) : null]);
    if (token !== revision) return;
    data = nextData; stock = nextStock;
    render();
    if (read && stock) {
      await api.read(stock.symbol, stock.retrieved_at, stock.reports.map(r => r.id), market);
      if (token !== revision) return;
      const current = data.stocks.find(s => s.symbol === stock.symbol);
      if (current && !day) current.unread = 0;
      document.querySelector('#stock-nav').innerHTML = sidebar(data, state.symbol);
    }
  } catch (error) {
    if (token !== revision) return;
    if (!data || main.querySelector('.initial-state') || (!quiet && symbol && !stock)) main.innerHTML = `<div class="empty-state connection-error"><h1>暂时无法打开${symbol ? '个股档案' : '工作区'}</h1><p>${e(error.message)}</p><button class="button primary" data-action="retry">重新连接</button><a href="${routeHref(market)}">返回总览</a></div>`;
    else if (!quiet) toast(error.message, true);
  } finally {
    if (token === revision) {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(() => load({ quiet: true }), data?.update.running ? 5000 : 30000);
    }
  }
}

function route() {
  const next = parseRoute(location.hash, state.market);
  const changed = next.market !== state.market;
  Object.assign(state, next);
  clearTimeout(pollTimer);
  main.innerHTML = `<div class="initial-state"><span class="spinner"></span><p>载入${markets[state.market].name}</p></div>`;
  if (changed) {
    Object.assign(state, { day:'', filter:'all', search:'' });
    state.openReports.clear(); state.closedReports.clear();
    data = null;
    document.querySelector('#stock-nav').innerHTML = '';
    document.querySelector('#watch-count').textContent = '—';
    document.querySelector('#attention-count').textContent = '';
  }
  state.kind = 'all';
  stock = null;
  renderNavigation();
  load({ read: Boolean(state.symbol) });
  window.scrollTo({ top: 0, behavior: 'instant' });
}

main.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const { action, value, symbol } = button.dataset;
  const market = state.market;
  if (action === 'filter') { state.filter = value; render(); return; }
  if (action === 'kind') { state.kind = value; render(); return; }
  if (action === 'range') { state.range = Number(value); render(); return; }
  if (action === 'retry') { load(); return; }
  button.disabled = true;
  try {
    if (action === 'refresh') {
      const result = await api.refresh(symbol, market);
      toast(result.started ? '已开始更新' : '正在更新');
      if (state.market === market) await load();
    } else if (action === 'save-note') {
      const text = document.querySelector('#stock-note').value;
      const saved = await api.note(symbol, text, market);
      const key = draftKey(market, symbol);
      if (state.drafts[key] === text) delete state.drafts[key];
      if (state.market === market && stock?.symbol === symbol) stock.profile = saved;
      toast('笔记已保存');
      if (data) render();
    }
  } catch (error) { toast(error.message, true); }
  finally { if (button.isConnected) button.disabled = false; }
});

main.addEventListener('input', event => {
  if (event.target.id === 'stock-note') {
    state.drafts[draftKey(state.market, state.symbol)] = event.target.value;
    document.querySelector('#note-state').textContent = '未保存';
  }
  if (event.target.id === 'stock-search') {
    state.search = event.target.value;
    document.querySelector('#watch-table').innerHTML = watchTable(data, state);
  }
});
main.addEventListener('change', event => {
  if (event.target.id === 'date-select') { state.day = event.target.value; load(); }
});
main.addEventListener('toggle', event => {
  const id = event.target.dataset?.report;
  if (!id) return;
  if (event.target.open) { state.openReports.add(id); state.closedReports.delete(id); }
  else { state.openReports.delete(id); state.closedReports.add(id); }
}, true);
window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', event => { if (Object.keys(state.drafts).length) { event.preventDefault(); event.returnValue = ''; } });
route();
