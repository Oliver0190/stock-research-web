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
  overview: day => request(`/overview${day ? `?day=${encodeURIComponent(day)}` : ''}`),
  stock: (symbol, day) => request(`/stocks/${symbol}${day ? `?day=${encodeURIComponent(day)}` : ''}`),
  refresh: symbol => request('/refresh', { method: 'POST', body: JSON.stringify({ symbol: symbol || null }) }),
  note: (symbol, note) => request(`/stocks/${symbol}/note`, { method: 'PUT', body: JSON.stringify({ note }) }),
  read: (symbol, through, report_ids) => request(`/stocks/${symbol}/read`, { method: 'POST', body: JSON.stringify({ through, report_ids }) }),
};
