export const markets = {
  HK: { id:'HK', name:'港股', code:'HK', currency:'HKD', currency_label:'港元', timezone_label:'香港时间' },
  A: { id:'A', name:'A股', code:'CN', currency:'CNY', currency_label:'人民币', timezone_label:'北京时间' },
};

export const marketInfo = data => ({ ...markets[data?.market?.id || 'HK'], ...data?.market });
export const draftKey = (market, symbol) => `${market || 'HK'}:${symbol}`;
export const routeHref = (market = 'HK', route = 'overview', symbol) => `#${market}/${route}${symbol ? '/' + encodeURIComponent(symbol) : ''}`;
export const stockCode = (stock, market = 'HK') => stock.display_symbol || `${stock.symbol}.${market === 'HK' ? 'HK' : stock.symbol.startsWith('6') ? 'SH' : 'SZ'}`;

export function parseRoute(hash, fallback = 'HK') {
  const path = hash.replace(/^#/, '');
  const match = /^(?:(HK|A)\/)?stock\/(\d{5,6})$/.exec(path);
  if (match) {
    const market = match[1] || (match[2].length === 6 ? 'A' : 'HK');
    if ((market === 'HK' && match[2].length === 5) || (market === 'A' && /^[036]\d{5}$/.test(match[2]))) {
      return { market, route:'stock', symbol:match[2] };
    }
    return { market, route:'overview', symbol:null };
  }
  const page = /^(?:(HK|A)\/)?(overview|attention)$/.exec(path);
  return { market:page?.[1] || fallback, route:page?.[2] || 'overview', symbol:null };
}
