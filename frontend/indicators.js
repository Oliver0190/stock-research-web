import { escape as e, direction } from './format.js';

const number = value => Number.isFinite(value) ? value.toFixed(3) : '—';
const definitions = {
  macd: { title: 'MACD', parameters: '12, 26, 9', keys: ['dif', 'dea', 'macd_bar'], labels: ['DIF', 'DEA', '柱值'], minimum: 35 },
  kdj: { title: 'KDJ', parameters: '9, 3, 3', keys: ['k', 'd', 'j'], labels: ['K', 'D', 'J'], minimum: 9 },
};

export function indicatorPlot(points, kind) {
  const spec = definitions[kind];
  const observed = points.filter(p => spec.keys.every(key => Number.isFinite(p[key])));
  if (observed.length < 2) return '<div class="indicator-empty">有效数据不足，暂不绘图。</div>';
  const values = observed.flatMap(p => spec.keys.map(key => p[key]));
  // Include the zero baseline and never clip J when it leaves the 0–100 range.
  let low = Math.min(0, ...values), high = Math.max(kind === 'kdj' ? 100 : 0, ...values);
  const padding = (high - low || 1) * .1;
  low -= padding;
  high += padding;
  const width = 760, height = 176, left = 22, right = 68, top = 16, bottom = 16;
  const step = (width - left - right) / Math.max(1, points.length - 1);
  const x = i => left + step * i;
  const y = value => top + (high - value) / (high - low) * (height - top - bottom);
  const tickNumber = value => kind === 'kdj' ? String(value) : Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(2);
  const ticks = kind === 'kdj' ? [20, 50, 80] : [low + padding, 0, high - padding].filter((v, i, all) => all.indexOf(v) === i);
  const guides = ticks.map(value => `<line class="indicator-guide ${value === 0 ? 'zero' : ''}" x1="${left}" x2="${width - right + 8}" y1="${y(value)}" y2="${y(value)}"/><text class="indicator-tick" x="${width - right + 17}" y="${y(value) + 5}">${tickNumber(value)}</text>`).join('');
  const line = key => {
    let connected = false;
    return points.map((p, i) => {
      if (!Number.isFinite(p[key])) { connected = false; return ''; }
      const command = connected ? 'L' : 'M';
      connected = true;
      return `${command}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`;
    }).join(' ');
  };
  const barWidth = Math.min(9, step * .58);
  const bars = kind === 'macd' ? points.map((p, i) => {
    if (!Number.isFinite(p.macd_bar)) return '';
    return `<rect class="indicator-bar ${direction(p.macd_bar)}" x="${x(i) - barWidth / 2}" y="${Math.min(y(0), y(p.macd_bar))}" width="${barWidth}" height="${Math.abs(y(p.macd_bar) - y(0))}"/>`;
  }).join('') : '';
  const lines = spec.keys.slice(0, kind === 'macd' ? 2 : 3).map((key, i) => `<path class="indicator-line series-${i + 1}" d="${line(key)}"/>`).join('');
  const inspection = points.map((p, i) => `<rect class="indicator-hit" x="${x(i) - step / 2}" y="${top}" width="${step}" height="${height - top - bottom}"><title>${e(p.date)} · ${spec.keys.map((key, n) => `${spec.labels[n]} ${number(p[key])}`).join(' · ')}</title></rect>`).join('');
  return `<div class="indicator-plot" tabindex="0" role="region" aria-label="${spec.title} 历史图，可横向滚动"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${spec.title} · ${e(points[0].date)} 至 ${e(points.at(-1).date)}">${guides}${bars}${lines}${inspection}</svg></div><div class="chart-dates indicator-dates"><span>${e(points[0].date)}</span><span>${e(points.at(-1).date)}</span></div>`;
}

export function indicatorPanels(snapshot, range) {
  const payload = snapshot?.indicator_chart;
  const points = (payload?.series || []).slice(-range);
  const latest = points.at(-1);
  return `<section class="indicator-section" aria-label="技术指标图表"><div class="indicator-context"><h2>技术指标</h2><span>${snapshot ? `${e(snapshot.data_date)} · ${snapshot.complete ? '已收盘' : '盘中，数值仍会变化'}` : '等待行情'}</span></div>${Object.entries(definitions).map(([kind, spec]) => {
    const reading = payload?.[kind];
    const legend = spec.keys.map((key, i) => `<span class="indicator-legend-item"><i aria-hidden="true" class="legend-line ${key === 'macd_bar' ? 'legend-bar' : `series-${i + 1}`}"></i>${spec.labels[i]} <b class="${key === 'macd_bar' ? direction(latest?.[key]) : ''}">${number(latest?.[key])}</b></span>`).join('');
    const missing = !snapshot ? '等待首次行情更新。' : !payload ? '这份存档尚未保存指标序列，更新行情后可查看新记录。' : `历史不足 ${spec.minimum} 个交易日，暂不生成解读。`;
    return `<article class="indicator-panel"><div class="indicator-heading"><h3>${spec.title} <span>(${spec.parameters})</span></h3><div class="indicator-legend">${legend}</div></div><p class="indicator-caption">${kind === 'macd' ? 'DIF / DEA 双线 · 正负柱从零轴起算' : 'K / D / J 三线 · 20 / 50 / 80 参考线'}${points.length ? ` · 最近 ${points.length} 个交易日` : ''}</p>${reading ? indicatorPlot(points, kind) : `<div class="indicator-empty">${missing}</div>`}${reading ? `<div class="indicator-reading"><p>${e(reading.summary)}</p><p>${e(reading.detail)}</p></div>` : ''}</article>`;
  }).join('')}<details class="indicator-method"><summary>计算口径</summary><p>与主图使用同一份日线。先用完整可用历史计算，再截取所选区间；切换 30／60／120 日不会改变最新指标值。MACD 柱值 = 2 × (DIF − DEA)。KDJ 的 K、D 以 50 起算，J 可能超出 0–100。</p><p>${snapshot ? `行情来源 ${e(snapshot.source)}。` : ''}解读由指标规则生成，比较最近两个交易日；早期初始化阶段不绘制。高低位及交叉不能单独确认反转。<a href="https://www.investor.org.cn/xxzx/tjzl/tjnrgmjytx/bk/kj/202302/P020230301605515676372.pdf#page=49" target="_blank" rel="noopener noreferrer">指标定义 ↗</a></p></details></section>`;
}
