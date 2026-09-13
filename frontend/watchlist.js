import { api } from './api.js';
import { escape as e } from './format.js';
import { markets, stockCode } from './markets.js';

export function watchlistRows(stocks, market, removing = null) {
  return stocks.length ? stocks.map(s => `<div class="manage-stock"><div><strong>${e(s.name)}</strong><small>${e(stockCode(s, market))}</small></div>${removing === s.symbol ? `<div class="remove-confirm"><span>移出关注？历史记录保留</span><button class="text-button" data-manage="cancel">取消</button><button class="text-button danger" data-manage="confirm" data-symbol="${e(s.symbol)}">确认移除</button></div>` : `<button class="text-button" data-manage="remove" data-symbol="${e(s.symbol)}" aria-label="移除${e(s.name)}">移除</button>`}</div>`).join('') : '<p class="manage-empty">还没有关注的股票，从上方输入代码添加。</p>';
}

export function initWatchlist({ getMarket, onChange }) {
  const dialog = document.querySelector('#watchlist-dialog');
  let market, stocks = [], removing = null, busy = false;
  const list = () => dialog.querySelector('#manage-list');
  const message = (text, error = false) => {
    const target = dialog.querySelector('#manage-message');
    target.textContent = text;
    target.classList.toggle('error', error);
  };
  function lock(value) {
    busy = value;
    dialog.querySelectorAll('button,input').forEach(el => { el.disabled = value; });
    dialog.setAttribute('aria-busy', String(value));
  }
  function renderList() {
    list().innerHTML = watchlistRows(stocks, market, removing);
    dialog.querySelector('#manage-count').textContent = stocks.length;
  }
  async function open() {
    if (dialog.open) return;
    market = getMarket(); stocks = []; removing = null;
    dialog.innerHTML = `<div class="manage-heading"><div><div class="eyebrow">${markets[market].code} · ${markets[market].name}</div><h2 id="manage-title">管理关注列表 <span id="manage-count" class="count-label">—</span></h2></div><button class="text-button" data-manage="close" aria-label="关闭关注列表管理">关闭</button></div>
      <form id="add-stock-form"><label for="add-stock-code">股票代码</label><div class="add-stock-row"><input id="add-stock-code" name="symbol" inputmode="numeric" autocomplete="off" maxlength="6" placeholder="${market === 'HK' ? '例如 00700' : '例如 002594'}" required autofocus><button class="button primary" type="submit">添加股票</button></div><p class="subtle">${market === 'HK' ? '港股代码可省略前导 0。' : '输入 6 位沪深 A 股代码，保留前导 0。'} 自动核验名称并获取数据。</p></form>
      <p id="manage-message" class="manage-message" role="status" aria-live="polite"></p><div id="manage-list" class="manage-list"></div><p class="manage-footnote">移除后停止后续采集，保留研究记录和已保存笔记；重新添加可恢复。</p>`;
    dialog.showModal(); lock(true); message('正在读取关注列表…');
    try { stocks = (await api.watchlist(market)).stocks; renderList(); message(''); }
    catch (error) { message(error.message, true); }
    finally { lock(false); dialog.querySelector('#add-stock-code').focus(); }
  }
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  document.addEventListener('click', event => {
    if (event.target.closest('[data-action="manage-watchlist"]')) open();
  });
  dialog.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    const input = dialog.querySelector('#add-stock-code');
    const value = input.value.trim();
    if (!(market === 'HK' ? /^[0-9]{1,5}$/ : /^[036][0-9]{5}$/).test(value)) {
      message(market === 'HK' ? '请输入 1–5 位港股代码。' : '请输入以 0、3 或 6 开头的 6 位沪深股票代码。', true); input.focus(); return;
    }
    lock(true); message('正在核验股票代码…');
    try {
      const result = await api.addStock(value, market);
      if (!stocks.some(s => s.symbol === result.stock.symbol)) stocks.push(result.stock);
      removing = null; renderList(); input.value = '';
      message(result.added ? `已添加 ${result.stock.name}，数据正在后台更新。` : `${result.stock.name}已在关注列表中。`);
      await onChange(market);
    } catch (error) { message(error.message, true); }
    finally { lock(false); input.focus(); }
  });
  dialog.addEventListener('click', async event => {
    const button = event.target.closest('[data-manage]');
    if (!button || busy) return;
    const action = button.dataset.manage, symbol = button.dataset.symbol;
    if (action === 'close') { dialog.close(); return; }
    if (action === 'remove' || action === 'cancel') {
      removing = action === 'remove' ? symbol : null; renderList();
      list().querySelector(removing ? '[data-manage="confirm"]' : '[data-manage="remove"]')?.focus(); return;
    }
    if (action !== 'confirm') return;
    lock(true);
    try {
      await api.removeStock(symbol, market);
      const name = stocks.find(s => s.symbol === symbol)?.name || symbol;
      stocks = stocks.filter(s => s.symbol !== symbol); removing = null; renderList();
      message(`已移除 ${name}，历史记录和已保存笔记已保留。`);
      await onChange(market, symbol);
    } catch (error) { message(error.message, true); }
    finally { lock(false); dialog.querySelector('#add-stock-code').focus(); }
  });
}
