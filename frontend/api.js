export async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
    signal: options.signal || AbortSignal.timeout(15000),
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('服务暂时没有响应，请确认本机程序仍在运行。'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作未完成，请检查输入后重试。');
  return data;
}

export const api = {
  overview: (day, market = 'HK') => request(`/overview?market=${market}${day ? `&day=${encodeURIComponent(day)}` : ''}`),
  stock: (symbol, day, market = 'HK') => request(`/stocks/${symbol}?market=${market}${day ? `&day=${encodeURIComponent(day)}` : ''}`),
  refresh: (symbol, market = 'HK') => request(`/refresh?market=${market}`, { method: 'POST', body: JSON.stringify({ symbol: symbol || null }) }),
  note: (symbol, note, market = 'HK') => request(`/stocks/${symbol}/note?market=${market}`, { method: 'PUT', body: JSON.stringify({ note }) }),
  read: (symbol, through, report_ids, market = 'HK') => request(`/stocks/${symbol}/read?market=${market}`, { method: 'POST', body: JSON.stringify({ through, report_ids }) }),
};
