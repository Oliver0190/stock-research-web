"""All job dates use Hong Kong exchange sessions, not the host's local date."""
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

HK = ZoneInfo("Asia/Hong_Kong")


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar("XHKG")


def market_context(now=None):
    now = (now or datetime.now(HK)).astimezone(HK)
    cal = calendar()
    today = now.date().isoformat()
    session = cal.date_to_session(today, direction="previous")
    is_session = cal.is_session(today)
    close = cal.session_close(session).to_pydatetime()
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
        elif now < close + timedelta(minutes=15):
            phase, label = "settling", "等待收盘数据"
        else:
            phase, label = "closing", "已收盘"
    completed = session
    if now < close + timedelta(minutes=15):
        completed = cal.previous_session(session)
    return {"today": today, "phase": phase, "label": label,
            "expected_session": completed.date().isoformat(),
            "session": session.date().isoformat(),
            "now": now.isoformat(timespec="seconds"), "timezone": "Asia/Hong_Kong"}
