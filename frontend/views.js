import { escape as e, money, pct, direction, time, dateLabel, kindLabel, statusLabel, markdown, safeUrl } from './format.js';
import { chart } from './chart.js';
import { indicatorPanels } from './indicators.js';

const empty = (title, text) => `<div class="empty-state"><h3>${title}</h3><p>${text}</p></div>`;
const badge = s => s ? `<span class="status-badge ${e(s.state)}">${e(statusLabel[s.state] || s.state)}</span>` : '<span class="status-badge">尚未更新</span>';

export function sidebar(data, route) {
  return data.stocks.map(s => `<a class="stock-link ${route === s.symbol ? 'active' : ''}" href="#stock/${s.symbol}" ${route === s.symbol ? 'aria-current="page"' : ''}><span class="stock-link-name">${e(s.name)}<small>${s.symbol}.HK</small></span>${s.unread ? `<span class="unread-badge" aria-label="${s.unread} 条重要记录未查看">${s.unread}</span>` : ''}</a>`).join('');
}

function datePicker(data, selected) {
  const dates = [...new Set([data.market.today, ...data.dates])];
  return `<label class="date-picker"><span class="sr-only">查看日期</span><select id="date-select"><option value="" ${!selected ? 'selected' : ''}>最新 · ${data.market.today}</option>${dates.map(d => `<option value="${d}" ${d === selected ? 'selected' : ''}>${dateLabel(d)}</option>`).join('')}</select></label>`;
}

function refreshButton(data, symbol) {
  return `<button class="button primary" data-action="refresh" ${symbol ? `data-symbol="${symbol}"` : ''} ${data.update.running ? 'disabled' : ''}><span class="${data.update.running ? 'spinner small' : 'refresh-icon'}">${data.update.running ? '' : '↻'}</span>${data.update.running ? `更新中 ${data.update.completed}/${data.update.total}` : symbol ? '更新行情' : '更新全部'}</button>`;
}

function statusBanner(data) {
  if (data.update.running) return `<div class="update-banner"><span class="spinner small"></span><span>更新中，已完成的记录可查看。</span><span class="banner-progress">${data.update.completed} / ${data.update.total}</span></div>`;
  const failures = data.stocks.filter(s => s.status && ['error', 'stale', 'partial'].includes(s.status.state));
  if (failures.length) return `<div class="update-banner warning"><span>${failures.length} 只待更新 · 上次成功记录已保留</span></div>`;
  return '';
}

export function overview(data, state) {
  const stocks = data.stocks, records = data.reports;
  const events = records.filter(r => r.importance === 'important');
  const eventStocks = new Set(events.map(r => r.symbol));
  const snapshots = stocks.filter(s => s.snapshot);
  const fresh = snapshots.filter(s => s.snapshot.data_date >= data.market.expected_session);
  const dated = snapshots.filter(s => s.snapshot.data_date === data.selected_date);
  const rising = dated.filter(s => s.snapshot.analysis.kline.pct_change > 0).length;
  const falling = dated.filter(s => s.snapshot.analysis.kline.pct_change < 0).length;
  const attention = state.route === 'attention';
  const groupedEvents = [...eventStocks].map(symbol => ({ stock: stocks.find(s => s.symbol === symbol), report: events.find(r => r.symbol === symbol) }));
  return `<div class="page-heading"><div><div class="eyebrow">${dateLabel(data.selected_date)} <span>·</span> ${e(state.day ? '历史记录' : data.market.label)}</div><h1>${attention ? '重点变化' : '今日总览'}</h1></div><div class="heading-actions">${datePicker(data, state.day)}${refreshButton(data)}</div></div>
    ${statusBanner(data)}
    <section class="metric-grid" aria-label="关注列表概况">
      <div class="metric-card featured"><div class="metric-label">重点股票</div><div class="metric-value">${eventStocks.size}<small>只</small></div><div class="metric-note">${events.length ? `${events.length} 条事件` : '所选日期无重点事件'}</div></div>
      <div class="metric-card"><div class="metric-label">${state.day ? '已有历史数据' : '行情已更新'}</div><div class="metric-value">${state.day ? snapshots.length : fresh.length}<small>/ ${stocks.length}</small></div><div class="metric-note">${state.day ? `截至 ${state.day} 的已保存数据` : `基准交易日 ${data.market.expected_session}`}</div></div>
      <div class="metric-card"><div class="metric-label">${state.day ? '所选日期' : '今日'}涨跌分布</div><div class="metric-value"><span class="up">${rising}</span><span class="metric-divider">/</span><span class="down">${falling}</span><small>只</small></div><div class="metric-note">上涨 / 下跌 · ${dated.length} 只当日有数据</div></div>
    </section>
    <div class="overview-grid"><section class="panel watchlist-panel"><div class="panel-heading"><div><h2>关注列表 <span class="count-label">${stocks.length}</span></h2></div><label class="search-box"><input id="stock-search" type="search" placeholder="名称 / 代码" aria-label="搜索自选股票" value="${e(state.search)}"></label></div>
    <div class="table-toolbar"><div class="filter-group" aria-label="股票筛选">${[['all', '全部'], ['important', '重点'], ['unread', '未查看']].map(([v, l]) => `<button data-action="filter" data-value="${v}" class="filter ${state.filter === v ? 'selected' : ''}" aria-pressed="${state.filter === v}">${l}</button>`).join('')}</div><span class="subtle">港元 HKD</span></div>
    <div id="watch-table">${watchTable(data, state)}</div></section>
    <aside class="focus-column"><section class="panel focus-panel"><div class="panel-heading"><h2>观察要点</h2></div>${groupedEvents.length ? `<div class="focus-list">${groupedEvents.slice(0, 5).map(({ stock: s, report: r }, index) => `<a class="focus-item" href="#stock/${s.symbol}"><span class="focus-number">${String(index + 1).padStart(2, '0')}</span><div class="focus-item-meta"><span>${e(s.name)}</span><small>${kindLabel[r.kind]}</small></div><h3>${e(r.summary)}</h3><div class="focus-item-footer"><span>数据 ${r.data_date}</span><span>详情 →</span></div></a>`).join('')}</div>` : empty('暂无重点事件', snapshots.length ? '可切换日期查看历史记录。' : '等待行情。')}</section>
    </aside></div>
    ${closingDigest(data)}`;
}

export function watchTable(data, state) {
  const list = data.stocks.filter(s => `${s.name}${s.symbol}`.toLowerCase().includes(state.search.toLowerCase())).filter(s => state.route !== 'attention' || s.important).filter(s => state.filter === 'important' ? s.important : state.filter === 'unread' ? s.unread : true);
  if (!list.length) return empty('无匹配股票', '修改关键词或筛选条件。');
  return `<div class="table-scroll"><table class="stock-table"><thead><tr><th>股票</th><th class="numeric">最近价格</th><th class="numeric">当日涨跌</th><th class="trend-column">近 30 日</th><th>最近观察</th><th><span class="sr-only">打开档案</span></th></tr></thead><tbody>${list.map(s => {
    const p = s.snapshot, a = p?.analysis;
    return `<tr><td><a class="stock-cell" href="#stock/${s.symbol}"><span><strong>${e(s.name)}</strong><small>${s.symbol}.HK ${s.unread ? '<span class="new-indicator">新</span>' : ''}</small></span></a></td><td class="numeric"><strong class="price">${money(a?.kline.close)}</strong><small>${p ? `${p.data_date}${p.complete ? '' : ' · 盘中'}` : '等待行情'}</small></td><td class="numeric"><span class="change-pill ${direction(a?.kline.pct_change)}">${pct(a?.kline.pct_change)}</span></td><td class="trend-column">${chart(p?.chart?.slice(-30), { compact: true })}</td><td class="observation-cell"><span>${e(s.summary || (a ? a.indicators.ma.arrangement.split('(')[0] : '等待首次更新'))}</span>${badge(s.status)}</td><td><a class="row-link" href="#stock/${s.symbol}" aria-label="打开${e(s.name)}档案">→</a></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function closingDigest(data) {
  const closing = data.reports.filter(r => r.kind === 'closing');
  return `<section class="panel closing-panel"><div class="closing-content"><div class="closing-heading"><h2>收盘摘要</h2><span>${data.selected_date}</span></div>${closing.length ? `<p>${closing.length} / ${data.stocks.length} 只已归档。${closing.some(r => r.importance === 'important') ? '含重点变化。' : '无重点变化。'}</p><div class="digest-links">${closing.slice(0, 6).map(r => `<a href="#stock/${r.symbol}">${e(data.stocks.find(s => s.symbol === r.symbol)?.name || r.symbol)} <span>→</span></a>`).join('')}</div>` : '<p>尚未归档。待完整收盘数据到齐后更新。</p>'}</div></section>`;
}

export function detail(data, stock, state) {
  const p = stock.snapshot, a = p?.analysis;
  const note = state.drafts[stock.symbol] ?? stock.profile.note;
  return `<div class="detail-heading"><div class="stock-title"><div><div class="eyebrow">个股档案 <span>·</span> ${stock.symbol}.HK</div><h1>${e(stock.name)}</h1><div class="stock-title-meta">${badge(stock.status)}<span>${p ? `${p.complete ? '收盘' : '盘中参考'} · ${p.data_date}` : '等待行情'}</span></div></div></div><div class="heading-actions">${datePicker(data, state.day)}${refreshButton(data, stock.symbol)}</div></div>
    ${stock.status && ['error', 'stale', 'partial'].includes(stock.status.state) ? `<div class="update-banner warning"><span>${e(stock.status.message)}</span></div>` : ''}
    <div class="detail-grid"><div class="detail-main"><section class="panel chart-panel"><div class="chart-heading"><div><div class="metric-label">${p?.complete ? '最近收盘价' : '最近参考价'} <span class="subtle">HKD</span></div><div class="quote-price ${direction(a?.kline.pct_change)}">${money(a?.kline.close)}<span class="quote-change ${direction(a?.kline.pct_change)}">${pct(a?.kline.pct_change)}</span></div></div><div class="range-picker" aria-label="图表范围">${[30, 60, 120].map(n => `<button data-action="range" data-value="${n}" class="${state.range === n ? 'selected' : ''}" aria-pressed="${state.range === n}">${n} 日</button>`).join('')}</div></div>
      ${chart(p?.chart?.slice(-state.range), { change: a?.kline.pct_change })}<div class="chart-source">${p ? `${e(p.source)} · 获取于 ${time(p.fetched_at)} · ${p.complete ? '已完成日线' : '日线盘中值，非实时行情'}` : '尚无行情数据'}</div>
      <div class="key-levels"><div><span>最近支撑</span><strong>${money(a?.support_resistance.nearest_support)}</strong></div><div><span>最近阻力</span><strong>${money(a?.support_resistance.nearest_resistance)}</strong></div><div><span>技术参考区间</span><strong>${a ? `${money(a.value_zone.zone_low)}–${money(a.value_zone.zone_high)}` : '—'}</strong></div></div></section>
      ${indicatorPanels(p, state.range)}
      <section class="timeline-section"><div class="timeline-heading"><h2>研究记录 <span class="count-label">${stock.reports.length}</span></h2><span class="subtle">最新记录在前</span></div><div class="timeline-filters" aria-label="记录类型">${[['all', '全部记录'], ['morning', '盘前'], ['intraday', '盘中'], ['closing', '盘后']].map(([v, label]) => `<button class="filter ${state.kind === v ? 'selected' : ''}" data-action="kind" data-value="${v}" aria-pressed="${state.kind === v}">${label}</button>`).join('')}</div><div class="timeline">${timeline(stock, state)}</div></section></div>
      <aside class="profile-column"><section class="panel note-panel"><div class="panel-heading"><h2>观察笔记</h2></div><label class="sr-only" for="stock-note">${e(stock.name)}的观察笔记</label><textarea id="stock-note" maxlength="10000" placeholder="关注逻辑、待核实的问题…">${e(note)}</textarea><div class="note-footer"><span id="note-state">${state.drafts[stock.symbol] !== undefined ? '未保存' : stock.profile.updated_at ? `上次保存 ${time(stock.profile.updated_at)}` : '本地存储'}</span><button class="button secondary small-button" data-action="save-note" data-symbol="${stock.symbol}">保存笔记</button></div></section>
      <section class="panel technical-panel"><div class="panel-heading"><h2>技术状态</h2></div>${a ? `<div class="technical-row"><span>均线趋势</span><strong>${e(a.indicators.ma.arrangement.split('(')[0])}</strong></div><div class="technical-row"><span>MACD</span><strong>${e(a.indicators.macd.signal)}</strong></div><div class="technical-row"><span>布林带</span><strong>${e(a.indicators.boll.position)}</strong></div><div class="technical-row"><span>KDJ</span><strong>${e(a.indicators.kdj.signal)}</strong></div><div class="technical-footnote">基于 ${a.coverage.sessions} 个交易日<br>${a.coverage.start} 至 ${a.coverage.end}</div>` : '<p class="panel-empty">暂无技术数据</p>'}</section>
      ${fundamentals(p?.fundamentals)}
      <section class="method-note"><strong>口径说明</strong><p>区间由历史支撑推算，仅用于观察价格位置，不代表公司估值。</p></section></aside></div>`;
}

function timeline(stock, state) {
  const items = stock.reports.filter(r => state.kind === 'all' || r.kind === state.kind);
  if (!items.length) return empty('暂无研究记录', '更新后按交易日期归档。');
  let lastDay = '';
  return items.map((r, i) => {
    const day = r.report_date !== lastDay ? `<div class="timeline-day">${dateLabel(r.report_date)}</div>` : '';
    lastDay = r.report_date;
    return `${day}<article class="timeline-item ${r.kind}"><div class="timeline-label">${r.kind === 'morning' ? '盘前' : r.kind === 'intraday' ? '盘中' : '盘后'}</div><div class="report-card"><div class="report-meta"><span class="report-kind">${kindLabel[r.kind]}</span><span>${time(r.created_at)} 生成</span>${r.importance === 'important' ? '<span class="important-label">重点</span>' : ''}</div><h3>${e(r.kind === 'intraday' ? r.title : r.summary)}</h3><details data-report="${e(r.id)}" ${state.openReports.has(r.id) || (i === 0 && !state.closedReports.has(r.id)) ? 'open' : ''}><summary><span class="expand-text">展开全文</span><span class="collapse-text">收起</span></summary><div class="report-body">${markdown(r.body)}</div></details><div class="report-footer"><span>${r.engine === 'ai' ? 'AI 解读' : '规则解读'} · 数据 ${r.data_date}</span><span>${e(r.metadata.source || '')}</span></div>${r.metadata.ai_issue ? `<div class="report-warning">${e(r.metadata.ai_issue)}</div>` : ''}</div></article>`;
  }).join('');
}

function fundamentals(fund) {
  const financial = fund?.financials?.periods?.[0];
  const number = n => n == null ? '—' : Math.abs(n) >= 1e8 ? `${(n / 1e8).toFixed(2)} 亿` : `${(n / 1e4).toFixed(2)} 万`;
  return `<section class="panel fundamental-panel"><div class="panel-heading"><h2>财报与新闻</h2></div>${financial ? `<div class="financial-period">${e(financial.report_date)} · ${e(financial.report_period_type)}</div><div class="technical-row"><span>营收</span><strong>${number(financial.revenue)}</strong></div><div class="technical-row"><span>净利润</span><strong>${number(financial.net_profit)}</strong></div><p class="financial-source">来源：东方财富 / AKShare · 金额按源数据口径，币种尚未核验</p>` : '<p class="panel-empty">暂无财报数据</p>'}${fund?.next_earnings_date ? `<div class="earnings-date">下次财报预估 <strong>${e(fund.next_earnings_date)}</strong></div>` : ''}${fund?.news?.length ? `<div class="news-list">${fund.news.slice(0, 4).map(n => `<article class="news-item"><small>${e(n.time?.slice(0, 10))} · ${e(n.source || '来源未标注')}</small>${safeUrl(n.url) ? `<a href="${safeUrl(n.url)}" target="_blank" rel="noopener noreferrer">${e(n.title)} ↗</a>` : `<p>${e(n.title)}</p>`}</article>`).join('')}</div>` : '<div class="news-empty">暂无新闻记录</div>'}</section>`;
}
