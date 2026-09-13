"""Code-only instrument lookup; the selected market is always explicit."""
import re

import requests

from backend.market import instrument


def normalize_code(value, market):
    symbol = value.strip()
    if market == "HK" and re.fullmatch(r"[0-9]{1,5}", symbol):
        symbol = symbol.zfill(5)
    instrument(symbol, market)
    return symbol


def resolve_stock(symbol, market):
    info = instrument(symbol, market)
    quote_code = info["exchange"].lower() + symbol
    response = requests.get("https://qt.gtimg.cn/q=" + quote_code, timeout=8)
    response.raise_for_status()
    response.encoding = "gbk"
    match = re.search(r'v_' + re.escape(quote_code) + r'="([^"]*)"', response.text)
    fields = match[1].split("~") if match else []
    if len(fields) < 4 or fields[2] != symbol or not fields[1].strip():
        raise ValueError("未找到这只股票，请核对代码和所选市场")
    return {**info, "name": fields[1].strip()[:80]}
