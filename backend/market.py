"""Exchange calendars and instrument identifiers for the two research markets."""
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

HK = ZoneInfo("Asia/Hong_Kong")
MARKETS = {
    "HK": {"id": "HK", "name": "港股", "code": "HK", "currency": "HKD", "currency_label": "港元",
           "timezone": "Asia/Hong_Kong", "timezone_label": "香港时间", "calendar": "XHKG", "settlement_minutes": 15},
    "A": {"id": "A", "name": "A股", "code": "CN", "currency": "CNY", "currency_label": "人民币",
          "timezone": "Asia/Shanghai", "timezone_label": "北京时间", "calendar": "XSHG", "settlement_minutes": 45},
}


def instrument(symbol, market="HK"):
    valid = symbol.isascii() and symbol.isdigit() and (
        len(symbol) == 5 if market == "HK" else len(symbol) == 6 and symbol[0] in "036")
    if market not in MARKETS or not valid:
        raise ValueError("股票代码与市场不匹配；A 股当前支持沪深股票")
    exchange = "HK" if market == "HK" else "SH" if symbol.startswith("6") else "SZ"
    return {"symbol": symbol, "market": market, "exchange": exchange,
            "display_symbol": f"{symbol}.{exchange}", "currency": MARKETS[market]["currency"]}


def yahoo_symbol(symbol, market="HK"):
    info = instrument(symbol, market)
    return symbol.lstrip("0").zfill(4) + ".HK" if market == "HK" else symbol + (".SS" if info["exchange"] == "SH" else ".SZ")


@lru_cache(maxsize=2)
def calendar(market="HK"):
    return xcals.get_calendar(MARKETS[market]["calendar"])


def market_context(now=None, market="HK"):
    info = MARKETS[market]
    timezone = ZoneInfo(info["timezone"])
    now = (now or datetime.now(timezone)).astimezone(timezone)
    cal = calendar(market)
    today = now.date().isoformat()
    session = cal.date_to_session(today, direction="previous")
    is_session = cal.is_session(today)
    close = cal.session_close(session).to_pydatetime()
    completed_after = close + timedelta(minutes=info["settlement_minutes"])
    if not is_session:
        phase, label = "holiday", "休市日"
    else:
        opening = cal.session_open(session).to_pydatetime()
        break_start = cal.session_break_start(session)
        break_end = cal.session_break_end(session)
        if now < opening:
            phase, label = "morning", "盘前"
        elif now < close:
            if not pd.isna(break_start) and break_start.to_pydatetime() <= now < break_end.to_pydatetime():
                phase, label = "break", "午间休市"
            else:
                phase, label = "intraday", "交易时段"
        elif now < completed_after:
            phase, label = "settling", "等待收盘数据"
        else:
            phase, label = "closing", "已收盘"
    completed = session
    if now < completed_after:
        completed = cal.previous_session(session)
    return {**info, "today": today, "phase": phase, "label": label,
            "expected_session": completed.date().isoformat(),
            "session": session.date().isoformat(),
            "now": now.isoformat(timespec="seconds")}
