export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const money = n => n == null || !Number.isFinite(Number(n)) ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const pct = n => n == null ? '—' : `${n > 0 ? '+' : ''}${Number(n).toFixed(2)}%`;
export const direction = n => n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
export const time = v => v ? new Date(v).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Hong_Kong', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
export const dateLabel = v => v ? `${v.slice(0, 4)} 年 ${Number(v.slice(5, 7))} 月 ${Number(v.slice(8, 10))} 日` : '—';
export const kindLabel = { morning: '盘前观察', intraday: '盘中事件', closing: '收盘复盘' };
export const statusLabel = { loading: '获取行情中', enriching: '补充解读中', stale: '等待新数据', error: '更新失败', ready: '已更新', partial: '解读待补齐' };
export function markdown(text) {
  // The only supported markup is escaped bold and line breaks. Never execute AI/news HTML.
  return escape(text).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>');
}
export function safeUrl(value) {
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? escape(url.href) : null; } catch { return null; }
}
