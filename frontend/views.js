import { escape as e, money, pct, direction, time, dateLabel, kindLabel, statusLabel, markdown, safeUrl } from './format.js';
import { chart } from './chart.js';

const empty = (title, text) => `<div class="empty-state"><span class="empty-symbol">◇</span><h3>${title}</h3><p>${text}</p></div>`;
const badge = s => s ? `<span class="status-badge ${e(s.state)}">${e(statusLabel[s.state] || s.state)}</span>` : '<span class="status-badge">尚未更新</span>';
const avatar = (s, size = '') => `<span class="stock-avatar ${size} tone-${Number(s.symbol) % 5}">${e(s.name.slice(0, 1))}</span>`;

export function sidebar(data, route) {
  return data.stocks.map(s => `<a class="stock-link ${route === s.symbol ? 'active' : ''}" href="#stock/${s.symbol}" ${route === s.symbol ? 'aria-current="page"' : ''}>${avatar(s)}<span class="stock-link-name">${e(s.name)}<small>${s.symbol}.HK</small></span>${s.unread ? `<span class="unread-badge" aria-label="${s.unread} 条重要记录未查看">${s.unread}</span>` : ''}</a>`).join('');
}

function datePicker(data, selected) {
  const dates = [...new Set([data.market.today, ...data.dates])];
  return `<label class="date-picker"><span class="sr-only">查看日期</span><select id="date-select"><option value="" ${!selected ? 'selected' : ''}>最新 · ${data.market.today}</option>${dates.map(d => `<option value="${d}" ${d === selected ? 'selected' : ''}>${dateLabel(d)}</option>`).join('')}</select></label>`;
}

function refreshButton(data, symbol) {
  return `<button class="button primary" data-action="refresh" ${symbol ? `data-symbol="${symbol}"` : ''} ${data.update.running ? 'disabled' : ''}><span class="${data.update.running ? 'spinner small' : 'refresh-icon'}">${data.update.running ? '' : '↻'}</span>${data.update.running ? `更新中 ${data.update.completed}/${data.update.total}` : symbol ? '更新这只股票' : '更新全部'}</button>`;
}

function statusBanner(data) {
  if (data.update.running) return `<div class="update-banner"><span class="spinner small"></span><span>正在更新关注列表。已保存的数据可以先看，解读会陆续补齐。</span><span class="banner-progress">${data.update.completed} / ${data.update.total}</span></div>`;
  const failures = data.stocks.filter(s => s.status && ['error', 'stale', 'partial'].includes(s.status.state));
  if (failures.length) return `<div class="update-banner warning"><span>ⓘ</span><span>${failures.length} 只股票的数据或解读待补齐。已保留上次成功记录，可进入个股单独重试。</span></div>`;
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
  return `<div class="page-heading"><div><div class="eyebrow">${dateLabel(data.selected_date)} <span>·</span> ${e(state.day ? '历史记录' : data.market.label)}</div><h1>${attention ? '重点变化' : '今日总览'}</h1><p>${attention ? '只看值得继续跟进的变化。' : '先看变化，再走进每一只股票。'}</p></div><div class="heading-actions">${datePicker(data, state.day)}${refreshButton(data)}</div></div>
    ${statusBanner(data)}
    <section class="metric-grid" aria-label="关注列表概况">
      <div class="metric-card featured"><div class="metric-label">值得关注的股票 <span>◇</span></div><div class="metric-value">${eventStocks.size}<small>只</small></div><div class="metric-note">${events.length ? `${events.length} 条重要记录，已按股票归并` : '有新的重要变化时，会出现在这里'}</div></div>
      <div class="metric-card"><div class="metric-label">${state.day ? '已有历史数据' : '行情已更新'} <span>↻</span></div><div class="metric-value">${state.day ? snapshots.length : fresh.length}<small>/ ${stocks.length}</small></div><div class="metric-note">${state.day ? `截至 ${state.day} 的已保存数据` : `最近应完成交易日 ${data.market.expected_session}`}</div></div>
      <div class="metric-card"><div class="metric-label">${state.day ? '所选日期' : '今日'}涨跌分布 <span>↗</span></div><div class="metric-value"><span class="up">${rising}</span><span class="metric-divider">/</span><span class="down">${falling}</span><small>只</small></div><div class="metric-note">上涨 / 下跌 · ${dated.length} 只具有所选日期行情</div></div>
    </section>
    <div class="overview-grid"><section class="panel watchlist-panel"><div class="panel-heading"><div><h2>我的自选 <span class="count-label">${stocks.length}</span></h2><p>价格与分析日期分别标注</p></div><label class="search-box"><span>⌕</span><input id="stock-search" type="search" placeholder="搜索名称或代码" aria-label="搜索自选股票" value="${e(state.search)}"></label></div>
    <div class="table-toolbar"><div class="filter-group" aria-label="股票筛选">${[['all', '全部'], ['important', '有重要变化'], ['unread', '未查看']].map(([v, l]) => `<button data-action="filter" data-value="${v}" class="filter ${state.filter === v ? 'selected' : ''}" aria-pressed="${state.filter === v}">${l}</button>`).join('')}</div><span class="subtle">港元 HKD</span></div>
    <div id="watch-table">${watchTable(data, state)}</div></section>
    <aside class="focus-column"><section class="panel focus-panel"><div class="panel-heading"><h2>值得关注</h2><span class="focus-mark">◇</span></div>${groupedEvents.length ? `<div class="focus-list">${groupedEvents.slice(0, 5).map(({ stock: s, report: r }) => `<a class="focus-item" href="#stock/${s.symbol}"><div class="focus-item-meta"><span>${e(s.name)}</span><small>${kindLabel[r.kind]}</small></div><h3>${e(r.summary)}</h3><div class="focus-item-footer"><span>数据 ${r.data_date}</span><span>查看记录 ↗</span></div></a>`).join('')}</div>` : empty('暂时没有重点变化', snapshots.length ? '所选日期暂无重要记录。可以切换日期，或打开个股查看完整分析。' : '首次更新完成后，重要事件会按股票整理在这里。')}</section>
    <section class="quiet-card"><div class="quiet-icon">☷</div><h3>每只股票，都有自己的记录</h3><p>盘前观察、盘中事件、收盘复盘按日期归档。点击自选股，继续上次的研究。</p></section></aside></div>
    ${closingDigest(data)}`;
}

export function watchTable(data, state) {
  const list = data.stocks.filter(s => `${s.name}${s.symbol}`.toLowerCase().includes(state.search.toLowerCase())).filter(s => state.route !== 'attention' || s.important).filter(s => state.filter === 'important' ? s.important : state.filter === 'unread' ? s.unread : true);
  if (!list.length) return empty('没有符合条件的股票', '试试其他关键词或切换到「全部」。');
  return `<div class="table-scroll"><table class="stock-table"><thead><tr><th>股票</th><th class="numeric">最近价格</th><th class="numeric">当日涨跌</th><th class="trend-column">近 30 日</th><th>最近观察</th><th><span class="sr-only">打开档案</span></th></tr></thead><tbody>${list.map(s => {
    const p = s.snapshot, a = p?.analysis;
    return `<tr><td><a class="stock-cell" href="#stock/${s.symbol}">${avatar(s)}<span><strong>${e(s.name)}</strong><small>${s.symbol}.HK ${s.unread ? '<span class="new-indicator">新</span>' : ''}</small></span></a></td><td class="numeric"><strong class="price">${money(a?.kline.close)}</strong><small>${p ? `${p.data_date}${p.complete ? '' : ' · 盘中'}` : '等待行情'}</small></td><td class="numeric"><span class="change-pill ${direction(a?.kline.pct_change)}">${pct(a?.kline.pct_change)}</span></td><td class="trend-column">${chart(p?.chart?.slice(-30), { compact: true })}</td><td class="observation-cell"><span>${e(s.summary || (a ? a.indicators.ma.arrangement.split('(')[0] : '首次更新后生成观察'))}</span>${badge(s.status)}</td><td><a class="row-link" href="#stock/${s.symbol}" aria-label="打开${e(s.name)}档案">↗</a></td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function closingDigest(data) {
  const closing = data.reports.filter(r => r.kind === 'closing');
  return `<section class="panel closing-panel"><div class="closing-icon">☷</div><div class="closing-content"><div class="closing-heading"><h2>收盘摘要</h2><span>${data.selected_date}</span></div>${closing.length ? `<p>${closing.length} / ${data.stocks.length} 只股票已归档收盘复盘。${closing.some(r => r.importance === 'important') ? '以下股票出现了值得继续跟进的变化。' : '已归档股票没有触发重点变化。'}</p><div class="digest-links">${closing.slice(0, 6).map(r => `<a href="#stock/${r.symbol}">${e(data.stocks.find(s => s.symbol === r.symbol)?.name || r.symbol)} <span>↗</span></a>`).join('')}</div>` : '<p>所选日期还没有收盘复盘。完整日线到齐后会逐股归档；过期数据不会计入当日复盘。</p>'}</div></section>`;
}

export function detail(data, stock, state) {
  const p = stock.snapshot, a = p?.analysis;
  const note = state.drafts[stock.symbol] ?? stock.profile.note;
  return `<div class="detail-heading"><div class="stock-title">${avatar(stock, 'large')}<div><div class="eyebrow">个股档案 <span>·</span> ${stock.symbol}.HK</div><h1>${e(stock.name)}</h1><div class="stock-title-meta">${badge(stock.status)}<span>${p ? `${p.complete ? '收盘' : '盘中参考'} · ${p.data_date}` : '等待首次行情更新'}</span></div></div></div><div class="heading-actions">${datePicker(data, state.day)}${refreshButton(data, stock.symbol)}</div></div>
    ${stock.status && ['error', 'stale', 'partial'].includes(stock.status.state) ? `<div class="update-banner warning">ⓘ <span>${e(stock.status.message)}</span></div>` : ''}
    <div class="detail-grid"><div class="detail-main"><section class="panel chart-panel"><div class="chart-heading"><div><div class="metric-label">${p?.complete ? '最近收盘价' : '最近参考价'} <span class="subtle">HKD</span></div><div class="quote-price">${money(a?.kline.close)}<span class="quote-change ${direction(a?.kline.pct_change)}">${pct(a?.kline.pct_change)}</span></div></div><div class="range-picker" aria-label="图表范围">${[30, 60, 120].map(n => `<button data-action="range" data-value="${n}" class="${state.range === n ? 'selected' : ''}" aria-pressed="${state.range === n}">${n} 日</button>`).join('')}</div></div>
      ${chart(p?.chart?.slice(-state.range))}<div class="chart-source">${p ? `${e(p.source)} · 获取于 ${time(p.fetched_at)} · ${p.complete ? '已完成日线' : '日线盘中值，非实时行情'}` : '尚无行情数据'}</div>
      <div class="key-levels"><div><span>最近支撑</span><strong>${money(a?.support_resistance.nearest_support)}</strong></div><div><span>最近阻力</span><strong>${money(a?.support_resistance.nearest_resistance)}</strong></div><div><span>技术参考区间</span><strong>${a ? `${money(a.value_zone.zone_low)}–${money(a.value_zone.zone_high)}` : '—'}</strong></div></div></section>
      <section class="timeline-section"><div class="timeline-heading"><h2>研究时间线 <span class="count-label">${stock.reports.length}</span></h2><span class="subtle">最新记录在前</span></div><div class="timeline-filters" aria-label="记录类型">${[['all', '全部记录'], ['morning', '盘前'], ['intraday', '盘中'], ['closing', '盘后']].map(([v, label]) => `<button class="filter ${state.kind === v ? 'selected' : ''}" data-action="kind" data-value="${v}" aria-pressed="${state.kind === v}">${label}</button>`).join('')}</div><div class="timeline">${timeline(stock, state)}</div></section></div>
      <aside class="profile-column"><section class="panel note-panel"><div class="panel-heading"><h2>我的观察笔记</h2><span>✎</span></div><p>关注它的原因，或者下次想确认的事。</p><label class="sr-only" for="stock-note">${e(stock.name)}的观察笔记</label><textarea id="stock-note" maxlength="10000" placeholder="例如：关注下一次财报的收入变化，继续观察关键支撑…">${e(note)}</textarea><div class="note-footer"><span id="note-state">${state.drafts[stock.symbol] !== undefined ? '有未保存的修改' : stock.profile.updated_at ? `上次保存 ${time(stock.profile.updated_at)}` : '只保存在本机'}</span><button class="button secondary small-button" data-action="save-note" data-symbol="${stock.symbol}">保存笔记</button></div></section>
      <section class="panel technical-panel"><div class="panel-heading"><h2>当前技术状态</h2></div>${a ? `<div class="technical-row"><span>均线趋势</span><strong>${e(a.indicators.ma.arrangement.split('(')[0])}</strong></div><div class="technical-row"><span>MACD</span><strong>${e(a.indicators.macd.signal)}</strong></div><div class="technical-row"><span>布林带</span><strong>${e(a.indicators.boll.position)}</strong></div><div class="technical-row"><span>KDJ</span><strong>${e(a.indicators.kdj.signal)}</strong></div><div class="technical-footnote">基于 ${a.coverage.sessions} 个交易日<br>${a.coverage.start} 至 ${a.coverage.end}</div>` : '<p class="panel-empty">更新后显示技术状态</p>'}</section>
      ${fundamentals(p?.fundamentals)}
      <section class="method-note"><strong>关于技术参考区间</strong><p>根据历史支撑位推算，用来观察价格位置。它不是公司估值，也不代表买入建议。</p></section></aside></div>`;
}

function timeline(stock, state) {
  const items = stock.reports.filter(r => state.kind === 'all' || r.kind === state.kind);
  if (!items.length) return empty('研究记录从这里开始', '更新后，盘前观察、盘中事件和收盘复盘会按日期保存在这只股票的档案里。');
  let lastDay = '';
  return items.map((r, i) => {
    const day = r.report_date !== lastDay ? `<div class="timeline-day">${dateLabel(r.report_date)}</div>` : '';
    lastDay = r.report_date;
    return `${day}<article class="timeline-item ${r.kind}"><div class="timeline-dot">${r.kind === 'morning' ? '☀' : r.kind === 'intraday' ? '◇' : '☷'}</div><div class="report-card"><div class="report-meta"><span class="report-kind">${kindLabel[r.kind]}</span><span>${time(r.created_at)} 生成</span>${r.importance === 'important' ? '<span class="important-label">重点</span>' : ''}</div><h3>${e(r.kind === 'intraday' ? r.title : r.summary)}</h3><details data-report="${e(r.id)}" ${state.openReports.has(r.id) || (i === 0 && !state.closedReports.has(r.id)) ? 'open' : ''}><summary><span class="expand-text">展开完整解读</span><span class="collapse-text">收起解读</span></summary><div class="report-body">${markdown(r.body)}</div></details><div class="report-footer"><span>${r.engine === 'ai' ? 'AI 解读' : '规则解读'} · 数据 ${r.data_date}</span><span>${e(r.metadata.source || '')}</span></div>${r.metadata.ai_issue ? `<div class="report-warning">${e(r.metadata.ai_issue)}</div>` : ''}</div></article>`;
  }).join('');
}

function fundamentals(fund) {
  const financial = fund?.financials?.periods?.[0];
  const number = n => n == null ? '—' : Math.abs(n) >= 1e8 ? `${(n / 1e8).toFixed(2)} 亿` : `${(n / 1e4).toFixed(2)} 万`;
  return `<section class="panel fundamental-panel"><div class="panel-heading"><h2>财报与新闻</h2></div>${financial ? `<div class="financial-period">${e(financial.report_date)} · ${e(financial.report_period_type)}</div><div class="technical-row"><span>营收</span><strong>${number(financial.revenue)}</strong></div><div class="technical-row"><span>净利润</span><strong>${number(financial.net_profit)}</strong></div><p class="financial-source">来源：东方财富 / AKShare · 金额按源数据口径，币种尚未核验</p>` : '<p class="panel-empty">尚未获取到可展示的财报数据</p>'}${fund?.next_earnings_date ? `<div class="earnings-date">下次财报预估 <strong>${e(fund.next_earnings_date)}</strong></div>` : ''}${fund?.news?.length ? `<div class="news-list">${fund.news.slice(0, 4).map(n => `<article class="news-item"><small>${e(n.time?.slice(0, 10))} · ${e(n.source || '来源未标注')}</small>${safeUrl(n.url) ? `<a href="${safeUrl(n.url)}" target="_blank" rel="noopener noreferrer">${e(n.title)} ↗</a>` : `<p>${e(n.title)}</p>`}</article>`).join('')}</div>` : '<div class="news-empty">新闻暂未获取到，有数据后会在这里展示。</div>'}</section>`;
}
