"""Validate OHLCV before choosing a data provider or replacing a snapshot."""
import numpy as np
import pandas as pd


def normalize_frame(rows):
    frame = pd.DataFrame(rows)
    required = ["date", "open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        raise ValueError("行情字段不完整")
    frame = frame[required].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for col in required[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if frame.isna().any().any() or not np.isfinite(frame[required[1:]].values).all():
        raise ValueError("行情含缺失或非有限值，本次未覆盖已有数据")
    if (frame["close"] <= 0).any() or (frame["low"] <= 0).any() or (frame["volume"] < 0).any():
        raise ValueError("行情价格或成交量异常")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any() or (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError("行情高低价不一致")
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    frame["pct_change"] = frame["close"].pct_change().fillna(0) * 100
    return frame
