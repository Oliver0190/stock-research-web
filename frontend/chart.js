import { escape, money } from './format.js';

export function chart(points = [], { compact = false } = {}) {
  const valid = points.filter(p => Number.isFinite(p.close));
  if (valid.length < 2) return compact ? '<span class="muted">—</span>' : '<div class="chart-empty">更新行情后，在这里查看历史走势</div>';
  const width = compact ? 110 : 760, height = compact ? 34 : 220;
  const pad = compact ? 3 : 22, right = compact ? 3 : 68;
  const prices = valid.map(p => p.close), low = Math.min(...prices), high = Math.max(...prices);
  const spread = (high - low) || high * .02 || 1;
  const x = i => pad + i / (valid.length - 1) * (width - pad - right);
  const y = price => height - pad - ((price - low) / spread) * (height - 2 * pad);
  const line = valid.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(p.close).toFixed(2)}`).join(' ');
  const grid = compact ? '' : [0, 1, 2, 3].map(i => {
    const val = low + spread * i / 3;
    return `<line x1="${pad}" x2="${width-right+8}" y1="${y(val)}" y2="${y(val)}" class="chart-grid"/><text x="${width-right+18}" y="${y(val)+4}" class="chart-label">${money(val)}</text>`;
  }).join('');
  const color = compact ? (prices.at(-1) >= prices[0] ? 'spark-up' : 'spark-down') : 'chart-purple';
  return `<div class="${compact ? 'sparkline' : 'price-chart'}"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(valid[0].date)} 至 ${escape(valid.at(-1).date)} 的收盘价走势">${grid}${!compact ? `<path class="chart-area" d="${line} L${x(valid.length-1)},${height-pad} L${pad},${height-pad} Z"/>` : ''}<path class="chart-line ${color}" d="${line}"/>${!compact ? `<circle class="chart-last" cx="${x(valid.length-1)}" cy="${y(prices.at(-1))}" r="4"/>` : ''}</svg>${!compact ? `<div class="chart-dates"><span>${escape(valid[0].date)}</span><span>${escape(valid.at(-1).date)}</span></div>` : ''}</div>`;
}
