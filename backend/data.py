"""Daily OHLCV adapters. Always retain source and trading dates."""
import io
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import requests

from backend.market import yahoo_symbol, instrument
from backend.prices import normalize_frame


def _fetch_yfinance(symbol: str, lookback_days: int, market="HK") -> pd.DataFrame:
    import yfinance as yf
    yf_symbol = yahoo_symbol(symbol, market)
    # Yahoo's end boundary is exclusive. Include today's candle when available.
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=lookback_days + 30)
    start_s = start.strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")

    strategies = [
        lambda: yf.Ticker(yf_symbol).history(start=start_s, end=end_s, interval="1d", auto_adjust=True, timeout=12),
        lambda: yf.Ticker(yf_symbol).history(period="2y" if lookback_days > 365 else "1y", interval="1d", auto_adjust=True, timeout=12),
    ]

    df = None
    last_err = None
    for attempt in range(1):
        for strat in strategies:
            try:
                candidate = strat()
                if candidate is not None and not candidate.empty:
                    df = candidate
                    break
            except Exception as e:
                last_err = e
        if df is not None and not df.empty:
            break
        time.sleep(1 + attempt * 2)

    if df is None or df.empty:
        raise RuntimeError(f"yfinance no data for {symbol} ({yf_symbol}); last_err={last_err}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index().rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })[["date", "open", "high", "low", "close", "volume"]]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["pct_change"] = df["close"].pct_change().fillna(0) * 100
    return df.tail(lookback_days).reset_index(drop=True)


def _fetch_stooq(symbol: str, lookback_days: int, market="HK") -> pd.DataFrame:
    """Stooq 是欧洲金融数据站, 国内一般能直连, 当 yfinance 不可用时的兜底."""
    yf_symbol = yahoo_symbol(symbol, market).lower()
    end = datetime.now()
    start = end - timedelta(days=lookback_days + 30)
    url = (
        f"https://stooq.com/q/d/l/?s={yf_symbol}"
        f"&i=d&d1={start.strftime('%Y%m%d')}&d2={end.strftime('%Y%m%d')}"
    )
    resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    if "No data" in resp.text or len(resp.text) < 100:
        raise RuntimeError(f"stooq no data for {yf_symbol}")

    df = pd.read_csv(io.StringIO(resp.text))
    df = df.rename(columns={
        "Date": "date", "Open": "open", "High": "high",
        "Low": "low", "Close": "close", "Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["pct_change"] = df["close"].pct_change().fillna(0) * 100
    return df.tail(lookback_days).reset_index(drop=True)


def _fetch_akshare(symbol: str, lookback_days: int, market="HK") -> pd.DataFrame:
    import akshare as ak
    end = datetime.now()
    start = end - timedelta(days=lookback_days + 30)
    fetch = ak.stock_hk_hist if market == "HK" else ak.stock_zh_a_hist
    df = fetch(
        symbol=symbol, period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if df is None or df.empty:
        raise RuntimeError(f"akshare no data for {symbol}")
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "涨跌幅": "pct_change",
    })
    df["date"] = pd.to_datetime(df["date"])
    if market == "A":
        df["volume"] = pd.to_numeric(df["volume"]) * 100  # AKShare A-share volume is in lots.
    df = df.sort_values("date").reset_index(drop=True)
    return df.tail(lookback_days).reset_index(drop=True)


def _fetch_tencent(symbol: str, lookback_days: int, market="A") -> pd.DataFrame:
    import akshare as ak
    if market != "A":
        raise ValueError("腾讯适配器仅用于沪深 A 股")
    end = datetime.now()
    start = end - timedelta(days=lookback_days + 30)
    code = instrument(symbol, market)["exchange"].lower() + symbol
    frame = ak.stock_zh_a_hist_tx(symbol=code, start_date=start.strftime("%Y%m%d"),
                                end_date=end.strftime("%Y%m%d"), adjust="qfq", timeout=12)
    if frame is None or frame.empty:
        raise RuntimeError("腾讯日线暂不可用")
    frame = frame.copy()
    if "volume" not in frame and "amount" in frame:
        # The pinned older AKShare labels volume as amount. Main/ChiNext are lots;
        # STAR is already shares, verified against matching completed Yahoo bars.
        frame["volume"] = pd.to_numeric(frame["amount"]) * (1 if symbol.startswith("688") else 100)
    return frame.tail(lookback_days).reset_index(drop=True)


def fetch_daily(symbol: str, lookback_days: int = 365, market="HK") -> pd.DataFrame:
    source = os.environ.get("DATA_SOURCE_A" if market == "A" else "DATA_SOURCE", "yfinance").lower()
    errors = []

    sources = [_fetch_yfinance, _fetch_tencent, _fetch_akshare, _fetch_stooq] if market == "A" else [_fetch_yfinance, _fetch_stooq, _fetch_akshare]
    preferred = {"yfinance": _fetch_yfinance, "akshare": _fetch_akshare, "stooq": _fetch_stooq, "tencent": _fetch_tencent}.get(source)
    if preferred in sources:
        sources.remove(preferred)
        sources.insert(0, preferred)

    for fn in sources:
        try:
            df = fn(symbol, lookback_days, market)
            if df is not None and not df.empty:
                df = normalize_frame(df)
                df.attrs["source"] = {"_fetch_yfinance": "Yahoo Finance", "_fetch_stooq": "Stooq", "_fetch_akshare": "东方财富 / AKShare", "_fetch_tencent": "腾讯证券 / AKShare"}[fn.__name__]
                return df
        except Exception as e:
            errors.append(f"{fn.__name__}: {e}")
    raise RuntimeError(f"all data sources failed for {symbol}: {' | '.join(errors)}")
