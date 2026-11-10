"""Timestamped intraday observations. A polling interval is not a vendor freshness guarantee."""
from datetime import datetime, timedelta
import math
import re
import time

import requests

from backend.market import instrument


def parse_quotes(text, requested, now, *, max_age=90):
    found = {}
    for key, payload in re.findall(r'v_(\w+)="([^"]+)"', text):
        if key not in requested:
            continue
        fields = payload.split('~')
        symbol = requested[key]
        hk = key.startswith('r_hk')
        currency_index = 75 if hk else 82
        if len(fields) <= currency_index or fields[2] != symbol or fields[currency_index] != ('HKD' if hk else 'CNY'):
            continue
        try:
            at = datetime.strptime(fields[30], '%Y/%m/%d %H:%M:%S' if hk else '%Y%m%d%H%M%S').replace(tzinfo=now.tzinfo)
            price, prev, volume = (float(fields[i]) for i in (3,4,6))
        except (ValueError, OverflowError):
            continue
        if not 0 <= (now-at).total_seconds() <= max_age or not all(math.isfinite(n) and n > 0 for n in (price,prev,volume)):
            continue
        try:
            at_limit = not hk and (price >= float(fields[47]) or price <= float(fields[48]) or not 0<float(fields[48])<float(fields[47]))
        except (ValueError,IndexError):
            at_limit = True
        found[symbol] = {'symbol':symbol,'name':fields[1], 'price':price,'previous_close':prev,'at_limit':at_limit,
                         'volume':volume,'at':at.isoformat(timespec='seconds'),'change_pct':(price/prev-1)*100,
                         'currency':fields[currency_index]}
    return found


def fetch_quotes(symbols, market, now, *, max_age=90):
    started = time.monotonic()
    keys = {('r_hk' if market == 'HK' else instrument(s, market)['exchange'].lower())+s:s for s in symbols}
    found = {}
    for start in range(0,len(keys),50):
        batch = list(keys)[start:start+50]
        r = requests.get('https://qt.gtimg.cn/q='+','.join(batch), timeout=7)
        r.raise_for_status()
        received = now + timedelta(seconds=time.monotonic() - started)
        found.update(parse_quotes(r.content.decode('gbk',errors='replace'), keys, received, max_age=max_age))
    return found
