import { escape as e, money, pct, direction, time } from './format.js';
import { markets, routeHref } from './markets.js';
import { chart } from './chart.js';

const amount = value => money(value / 100);
const statuses = {pending:'待成交', filled:'已成交', rejected:'未通过检查', expired:'已到期', cancelled:'已取消', complete:'已完成', running:'研究中', error:'未完成', interrupted:'运行中断'};
const sessionStatuses = {
  observed: '已检查行情', decided: '模型已完成本轮判断', 'no-quotes': '本轮无新报价',
  'no-tradable': '等待可交易行情', 'no-research': '等待研究资料', 'model-error': '模型本轮未完成',
  incomplete: '等待完整行情', 'missing-holding-data': '等待持仓行情核验',
};
const blank = (title, body) => `<div class="empty-state"><h3>${title}</h3><p>${body}</p></div>`;
const stockLink = (market, symbol, name, active) => active.has(symbol)
  ? `<a href="${routeHref(market, 'stock', symbol)}">${e(name || symbol)} <small>${e(symbol)} ↗</small></a>`
  : `<span>${e(name || symbol)} <small>${e(symbol)} · 已移出关注列表</small></span>`;

export function paperView(account, data, tab = 'account') {
  if (!account) return blank('载入模拟账户', '正在读取本地账本。');
  const market = account.market, info = markets[market], p = account.policy;
  const active = new Set(data.stocks.map(s => s.symbol));
  const orders = [...account.orders].reverse(), decisions = account.decisions, reviews = [...account.reviews].reverse();
  const enabled = account.enabled;
  const cloudSessions = account.cloud && account.intraday;
  const watchHours = market === 'A' ? '09:00–12:00、13:00–15:30' : '09:30–12:00、13:00–16:00';
  const sampledAt = 'cloud_sampled_at' in account ? account.cloud_sampled_at
    : sessionStatuses[account.cloud_status] && account.live?.observed_at;
  const sampleAge = Date.now() - Date.parse(sampledAt);
  const recentSample = !account.cloud_stale && sampleAge >= 0 && sampleAge < 8 * 60000;
  let accountLabel = enabled ? '已启用' : '已暂停';
  if (enabled && cloudSessions) accountLabel = recentSample ? '盯盘中' : '自动盯盘已启用';
  if (account.blocked) accountLabel = '待核对';
  const tabs = [['account','账户'],['decisions','决策日志'],['orders','模拟成交'],['reviews','复盘']];
  let content;
  if (tab === 'decisions') {
    content = decisions.length ? decisions.map(d => `<article class="paper-entry"><div class="paper-entry-meta"><span>${e(d.session)} · ${e(statuses[d.status] || d.status)}</span><span>${e(d.model || '—')} / ${e(d.version)}</span></div><h3>${d.plan?.actions.length ? `${d.plan.actions.length} 项交易提议` : d.plan ? '继续观察' : '本轮记录'}</h3><p>${e(d.plan?.summary || d.error || '正在读取资料；中断的运行不会自动下单。')}</p>${(d.plan?.actions || []).map(a => `<div class="paper-thesis"><strong>${a.side === 'buy' ? '买入' : '卖出'} ${e(a.symbol)} · ${a.quantity} 股</strong><p>${e(a.reason)}</p><small>验证条件 · ${e(a.invalidation)}</small></div>`).join('')}<div class="paper-entry-meta"><span>${e(time(d.created_at))} · 读取 ${d.trace?.length || 0} 次个股资料</span><button class="text-button" data-action="paper-evidence" data-value="${e(d.id)}">查看原始依据 ↗</button></div></article>`).join('')
      : blank('从第一份判断开始', '启动后，Agent 会读取资料并记录交易或继续观察的理由。没有模型密钥时会明确提示。');
  } else if (tab === 'orders') {
    content = orders.length ? `<div class="paper-table-wrap"><table class="paper-table"><thead><tr><th>股票 / 方向</th><th>数量</th><th>计划日期</th><th>成交价</th><th>费用</th><th>结果</th></tr></thead><tbody>${orders.map(o => `<tr><td>${stockLink(market,o.symbol,o.name,active)}<small>${o.side === 'buy' ? '买入' : '卖出'}</small></td><td>${o.quantity}</td><td>${e(o.eligible_session)}</td><td>${o.fill_price ? money(o.fill_price) : '—'}</td><td>${o.fee_cents != null ? amount(o.fee_cents) : '—'}</td><td><strong>${e(statuses[o.status])}</strong><small>${e(o.result)}</small></td></tr>`).join('')}</tbody></table></div>`
      : blank('还没有订单', account.intraday ? '决策后须等到新的有效报价才能模拟成交。' : '决策会先保存，最早在下一交易日模拟成交。未通过检查的提议也会保留。');
  } else if (tab === 'reviews') {
    content = `<p class="paper-intro">每笔卖出与每轮决策后的第五个交易日留下复盘。先记录事实，再验证原因；待验证建议不会直接改动策略。</p>${reviews.length ? reviews.map(r => `<article class="paper-entry"><div class="paper-entry-meta"><span>${e(r.date)} · ${e(r.status)}</span><span>${e(r.symbol || '')}</span></div><h3>${e(r.title)}</h3><p>${e(r.body)}</p><div class="paper-thesis"><small>下一步验证</small><p>${e(r.suggestion)}</p></div><button class="text-button" data-action="paper-evidence" data-value="${e(r.decision_id)}">回看当时依据 ↗</button></article>`).join('') : blank('让判断留下可比较的记录', '产生交易或积累五个交易日后，这里会出现复盘。亏损本身不会被当成错误原因。')}`;
  } else {
    const positions = Object.entries(account.positions);
    content = `<div class="paper-account-grid"><section class="panel"><div class="panel-heading"><h2>资产轨迹</h2><span class="subtle">${e(info.currency)} · ${e(account.last_session || '尚未开始')}</span></div>${account.equity.length > 1 ? chart(account.equity.map(x => ({date:x.date,close:x.value_cents/100})), {label:'模拟账户总资产'}) : blank('100,000.00', '初始虚拟资金 · 运行后逐日积累资产记录')}<div class="paper-chart-note">含现金与持仓市值，已扣模拟费用。最大回撤 ${money(account.max_drawdown_pct)}%。估值以最新成功记账日为准。</div></section><aside class="paper-rules"><div class="eyebrow">实验约定 / V1</div><h2>先把每一步记清楚</h2><dl><div><dt>初始资金</dt><dd>100,000 ${e(info.currency)}</dd></div><div><dt>单只 / 总仓位</dt><dd>20% / 80%</dd></div><div><dt>最多持仓</dt><dd>5 只 · 仅做多</dd></div><div><dt>每边模拟费用</dt><dd>${p.fee_bps / 100}% · 最低 ${p.min_fee}</dd></div><div><dt>每边滑点</dt><dd>${p.slippage_bps / 100}%</dd></div><div><dt>开盘偏离保护</dt><dd>超过 3% 取消</dd></div></dl><p>${e(p.execution)}</p><small>${e(p.limitations)}</small><p>${account.cloud ? (account.intraday ? '交易时段由 GitHub 采样，约每分钟一次。' : '收盘任务由 GitHub 执行。') : '自动运行依赖本机服务保持开启。'}两个账户分别记账、分别启动。</p></aside></div><section class="panel"><div class="panel-heading"><h2>当前持仓 <span class="count-label">${positions.length}</span></h2><span class="subtle">持仓 ${money(account.exposure_pct)}%</span></div>${positions.length ? `<div class="paper-table-wrap"><table class="paper-table"><thead><tr><th>股票</th><th>持股数</th><th>含费用均价</th><th>参考市值</th><th>浮动盈亏</th></tr></thead><tbody>${positions.map(([s,v]) => `<tr><td>${stockLink(market,s,v.name,active)}<small>估值 ${e(v.mark_date)}</small></td><td>${v.quantity}</td><td>${money(v.cost_cents / v.quantity / 100)}</td><td>${amount(v.value_cents)}</td><td class="${direction(v.value_cents-v.cost_cents)}">${amount(v.value_cents-v.cost_cents)}</td></tr>`).join('')}</tbody></table></div>` : blank('目前为空仓', 'Agent 可以选择等待。资金不会为了展示效果而自动买入。')}</section>`;
  }
  return `<div class="page-heading"><div><div class="eyebrow">PAPER ACCOUNT <span>·</span> ${account.intraday ? '盘中模拟 ·' : ''} ${e(info.name)} / ${e(info.currency)}</div><h1>模拟交易</h1></div><div class="heading-actions"><span class="paper-state ${enabled ? 'active' : ''}">${accountLabel}</span><button class="button" data-action="paper-run" ${account.running || account.blocked ? 'disabled' : ''}>${account.running ? '处理中…' : enabled ? '执行一轮' : '更新账本'}</button><button class="button primary" data-action="paper-control" data-value="${enabled ? 'pause' : 'start'}" ${account.blocked ? 'disabled' : ''}>${enabled ? '暂停模拟' : '启动模拟'}</button></div></div><p class="paper-intro">一份独立的 ${e(info.currency_label)} 虚拟账本。从现有研究出发，记录判断、执行与结果。</p><div class="update-banner ${account.blocked ? 'warning' : ''}" role="status">${account.running ? '<span class="spinner small"></span>' : ''}<span>${account.cloud ? `GitHub 云端 · ${e(account.cloud_updated_at)} · ${e(sessionStatuses[account.cloud_status] || account.cloud_status || '')}${account.cloud_stale ? ' · 离线缓存' : ''} · ` : ''}${e(account.message)}</span></div>${account.cloud ? `<p class="paper-chart-note">${cloudSessions ? `交易日 ${watchHours} · ` : ''}云端关注 ${account.cloud_watch_count} 只。修改本机自选后，<button class="text-button" data-action="paper-sync">同步关注列表</button>。<a href="${e(account.cloud_actions)}" target="_blank" rel="noopener">查看云端任务 ↗</a></p>` : ''}<section class="metric-grid paper-metrics" aria-label="模拟账户资产"><div class="metric-card featured"><div class="metric-label">总资产 · ${e(info.currency)}</div><div class="metric-value">${amount(account.equity_cents)}</div><div class="metric-note">初始 100,000.00</div></div><div class="metric-card"><div class="metric-label">可用虚拟现金</div><div class="metric-value">${amount(account.cash_cents)}</div><div class="metric-note">待成交买单将在执行时再次检查余额</div></div><div class="metric-card"><div class="metric-label">累计收益</div><div class="metric-value ${direction(account.pnl_cents)}">${pct(account.return_pct)}</div><div class="metric-note">${amount(account.pnl_cents)} ${e(info.currency)}</div></div></section><div class="paper-tabs" aria-label="模拟账户视图">${tabs.map(([id,label]) => `<button class="filter ${tab === id ? 'selected' : ''}" data-action="paper-tab" data-value="${id}" aria-pressed="${tab === id}">${label}</button>`).join('')}</div>${content}<dialog id="paper-evidence-dialog" class="paper-evidence-dialog" aria-labelledby="paper-evidence-title"></dialog>`;
}

export function evidenceView(record) {
  return `<div class="panel-heading"><h2 id="paper-evidence-title">当时的判断依据</h2><button class="button" data-action="paper-close">关闭</button></div><p>${e(record.created_at)} · ${e(record.model || '—')} · ${e(record.version)}</p><p>${e(record.plan?.summary || record.error || '运行记录')}</p><p class="subtle">这里保存的是当时读取的数据，不会用后来的新闻覆盖。查询详情包含行情来源、交易单位、研究日期和新闻。</p><pre>${e(JSON.stringify({plan:record.plan,queries:record.trace,evidence:record.evidence},null,2))}</pre>`;
}
