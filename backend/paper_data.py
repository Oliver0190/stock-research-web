"""Paper execution data, deliberately separate from adjusted research charts."""
from io import BytesIO
import math

import pandas as pd
import requests
import yfinance as yf

from backend.market import yahoo_symbol, instrument
from backend.prices import normalize_frame

SECURITIES_URL = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"


def board_lots():
    response = requests.get(SECURITIES_URL, timeout=15)
    response.raise_for_status()
    frame = pd.read_excel(BytesIO(response.content), header=2, dtype={"Stock Code": str})
    result = {}
    for _, row in frame.iterrows():
        if row.get("Trading Currency") == "HKD" and row.get("Category") == "Equity":
            lot = int(str(row["Board Lot"]).replace(",", ""))
            if lot > 0:
                result[str(row["Stock Code"]).zfill(5)] = lot
    if not result:
        raise ValueError("港交所交易单位文件无有效记录")
    return {"lots": result, "source": SECURITIES_URL}


def execution_data(symbol, market):
    ticker = yf.Ticker(yahoo_symbol(symbol, market))
    frame = ticker.history(period="1y", auto_adjust=False, actions=True, repair=False, timeout=15)
    if frame.empty:
        raise ValueError("模拟成交行情暂不可用")
    if not {"Dividends", "Stock Splits"}.issubset(frame.columns):
        raise ValueError("公司行动数据缺失")
    currency = ticker.fast_info.get("currency")
    if currency != ("HKD" if market == "HK" else "CNY"):
        raise ValueError("成交行情币种未通过核验")
    rows = []
    for timestamp, row in frame.iterrows():
        rows.append({"date": timestamp.strftime("%Y-%m-%d"), **{
            key.lower(): float(row[key]) for key in ("Open", "High", "Low", "Close", "Volume")},
            "dividend": float(row.get("Dividends", 0)), "split": float(row.get("Stock Splits", 0))})
    if any(not math.isfinite(r[k]) or r[k] < 0 for r in rows for k in ("dividend", "split")):
        raise ValueError("公司行动数据异常")
    source = "Yahoo Finance · auto_adjust=False"
    if market == "A":
        # Yahoo A-share OHLC can have incompatible historical adjustments. Tencent's
        # explicit unadjusted series supplies prices; Yahoo supplies action flags only.
        import akshare as ak
        raw = ak.stock_zh_a_hist_tx(symbol=instrument(symbol, market)["exchange"].lower() + symbol,
            start_date=rows[0]["date"].replace("-", ""), end_date=rows[-1]["date"].replace("-", ""),
            adjust="", timeout=12)
        if "volume" not in raw and "amount" in raw:
            raw["volume"] = pd.to_numeric(raw["amount"]) * (1 if symbol.startswith("688") else 100)
        verified = normalize_frame(raw)
        actions = {r["date"]: {k:r[k] for k in ("dividend", "split")} for r in rows}
        rows = []
        for row in verified.to_dict("records"):
            row["date"] = row["date"].strftime("%Y-%m-%d")
            if row["date"] not in actions:
                raise ValueError("成交行情与公司行动覆盖日期不一致")
            rows.append({**row, **actions[row["date"]]})
        source = "腾讯证券 · 不复权；Yahoo Finance 公司行动"
    normalize_frame(rows)
    return {"rows": rows, "currency": currency, "source": source}
