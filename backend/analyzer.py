import numpy as np
import pandas as pd
from scipy.signal import find_peaks


# ---------- K 线形态 ----------

def describe_last_kline(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    body = last["close"] - last["open"]
    rng = last["high"] - last["low"]
    upper_shadow = last["high"] - max(last["close"], last["open"])
    lower_shadow = min(last["close"], last["open"]) - last["low"]

    if rng == 0:
        pattern = "一字线"
    elif abs(body) / rng < 0.1:
        pattern = "十字星(多空僵持)"
    elif body > 0 and lower_shadow > abs(body) * 2:
        pattern = "锤头线(下方支撑显现)"
    elif body < 0 and upper_shadow > abs(body) * 2:
        pattern = "倒锤头/流星(上方压力显现)"
    elif body > 0 and abs(body) / rng > 0.7:
        pattern = "大阳线(多方强势)"
    elif body < 0 and abs(body) / rng > 0.7:
        pattern = "大阴线(空方强势)"
    else:
        pattern = "小阳线" if body > 0 else "小阴线"

    vol_ma = df["volume"].iloc[:-1].tail(20).mean()
    vol_ratio = last["volume"] / vol_ma if vol_ma > 0 else 1.0

    return {
        "date": last["date"].strftime("%Y-%m-%d"),
        "open": round(last["open"], 2),
        "close": round(last["close"], 2),
        "high": round(last["high"], 2),
        "low": round(last["low"], 2),
        "pct_change": round(last["pct_change"], 2),
        "pattern": pattern,
        "volume_ratio": round(vol_ratio, 2),
        "prev_close": round(prev["close"], 2),
    }


# ---------- 历史区间 + 多高低点 ----------

def range_stats(df: pd.DataFrame, top_n: int = 2) -> dict:
    """实际数据区间, 找出 top-N 高点和低点(局部极值)."""
    cur = float(df["close"].iloc[-1])
    highs = df["high"].values
    lows = df["low"].values

    high_peaks, _ = find_peaks(highs, distance=20)
    low_peaks, _ = find_peaks(-lows, distance=20)

    top_highs = sorted(
        [{"price": round(float(highs[i]), 2), "date": df.iloc[i]["date"].strftime("%Y-%m-%d")} for i in high_peaks],
        key=lambda x: -x["price"],
    )[:top_n]
    top_lows = sorted(
        [{"price": round(float(lows[i]), 2), "date": df.iloc[i]["date"].strftime("%Y-%m-%d")} for i in low_peaks],
        key=lambda x: x["price"],
    )[:top_n]

    abs_high = float(highs.max())
    abs_low = float(lows.min())
    abs_high_idx = int(np.argmax(highs))
    abs_low_idx = int(np.argmin(lows))

    closes = df["close"].values
    percentile = float((closes < cur).sum() / len(closes) * 100)

    return {
        "current": round(cur, 2),
        "abs_high": round(abs_high, 2),
        "abs_high_date": df.iloc[abs_high_idx]["date"].strftime("%Y-%m-%d"),
        "abs_low": round(abs_low, 2),
        "abs_low_date": df.iloc[abs_low_idx]["date"].strftime("%Y-%m-%d"),
        "top_highs": top_highs,
        "top_lows": top_lows,
        "percentile": round(percentile, 1),
        "drawdown_from_high": round((cur - abs_high) / abs_high * 100, 2),
        "rebound_from_low": round((cur - abs_low) / abs_low * 100, 2),
    }


# ---------- 技术指标 ----------

def _safe(v):
    """numpy nan 转 None, 否则转 float"""
    if pd.isna(v):
        return None
    return float(v)


def technical_indicators(df: pd.DataFrame) -> dict:
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    cur = float(closes.iloc[-1])

    # ---- MA ----
    ma5 = closes.rolling(5).mean()
    ma20 = closes.rolling(20).mean()
    ma60 = closes.rolling(60).mean()
    ma120 = closes.rolling(120).mean()
    ma5v, ma20v, ma60v, ma120v = ma5.iloc[-1], ma20.iloc[-1], ma60.iloc[-1], ma120.iloc[-1]

    def _arr(v5, v20, v60, v120):
        vals = [v5, v20, v60, v120]
        if any(pd.isna(v) for v in vals):
            return "数据不足"
        if v5 > v20 > v60 > v120:
            return "多头排列(短期>长期, 上涨趋势)"
        if v5 < v20 < v60 < v120:
            return "空头排列(短期<长期, 下跌趋势)"
        return "均线纠缠(无明确趋势)"

    # ---- BOLL (20, 2) ----
    boll_mid = ma20.iloc[-1]
    std20 = closes.rolling(20).std().iloc[-1]
    boll_upper = boll_mid + 2 * std20
    boll_lower = boll_mid - 2 * std20

    if pd.isna(boll_upper) or pd.isna(boll_lower):
        boll_pos = "数据不足"
    elif cur > boll_upper:
        boll_pos = "上轨上方(超买/强势)"
    elif cur < boll_lower:
        boll_pos = "下轨下方(超卖/弱势)"
    elif cur > boll_mid:
        boll_pos = "中轨上方(偏强)"
    else:
        boll_pos = "中轨下方(偏弱)"

    # ---- MACD (12, 26, 9) ----
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_bar = (dif - dea) * 2

    dif_now, dif_prev = dif.iloc[-1], dif.iloc[-2] if len(dif) >= 2 else dif.iloc[-1]
    dea_now, dea_prev = dea.iloc[-1], dea.iloc[-2] if len(dea) >= 2 else dea.iloc[-1]

    if dif_now > dea_now and dif_prev <= dea_prev:
        macd_signal = "金叉(看涨信号)"
    elif dif_now < dea_now and dif_prev >= dea_prev:
        macd_signal = "死叉(看跌信号)"
    elif dif_now > 0 and dea_now > 0:
        macd_signal = "零轴上方多头" + ("(柱状放大)" if macd_bar.iloc[-1] > macd_bar.iloc[-2] else "(柱状缩短)")
    elif dif_now < 0 and dea_now < 0:
        macd_signal = "零轴下方空头" + ("(柱状缩短-修复中)" if macd_bar.iloc[-1] > macd_bar.iloc[-2] else "(柱状放大-加速下跌)")
    else:
        macd_signal = "零轴附近(变盘临界)"

    # ---- KDJ (9, 3, 3) ----
    low9 = lows.rolling(9).min()
    high9 = highs.rolling(9).max()
    rsv = ((closes - low9) / (high9 - low9) * 100).fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    k_now, d_now, j_now = k.iloc[-1], d.iloc[-1], j.iloc[-1]
    if j_now > 100:
        kdj_signal = "J超买(高位预警)"
    elif j_now < 0:
        kdj_signal = "J超卖(低位预警/可能反弹)"
    elif k_now > d_now and k.iloc[-2] <= d.iloc[-2]:
        kdj_signal = "金叉(短线转强)"
    elif k_now < d_now and k.iloc[-2] >= d.iloc[-2]:
        kdj_signal = "死叉(短线转弱)"
    elif k_now > d_now:
        kdj_signal = "K高于D(短线偏多)"
    else:
        kdj_signal = "K低于D(短线偏空)"

    return {
        "ma": {
            "ma5": _safe(ma5v), "ma20": _safe(ma20v),
            "ma60": _safe(ma60v), "ma120": _safe(ma120v),
            "current_vs_ma20_pct": round((cur - ma20v) / ma20v * 100, 2) if not pd.isna(ma20v) else None,
            "current_vs_ma60_pct": round((cur - ma60v) / ma60v * 100, 2) if not pd.isna(ma60v) else None,
            "arrangement": _arr(ma5v, ma20v, ma60v, ma120v),
        },
        "boll": {
            "upper": _safe(boll_upper), "mid": _safe(boll_mid), "lower": _safe(boll_lower),
            "position": boll_pos,
            "band_width_pct": round((boll_upper - boll_lower) / boll_mid * 100, 2) if not pd.isna(boll_mid) else None,
        },
        "macd": {
            "dif": round(_safe(dif_now), 3) if dif_now is not None else None,
            "dea": round(_safe(dea_now), 3) if dea_now is not None else None,
            "macd_bar": round(_safe(macd_bar.iloc[-1]), 3),
            "signal": macd_signal,
        },
        "kdj": {
            "k": round(_safe(k_now), 1), "d": round(_safe(d_now), 1), "j": round(_safe(j_now), 1),
            "signal": kdj_signal,
        },
    }


# ---------- 支撑/阻力位 ----------

def find_support_resistance(df: pd.DataFrame, min_distance: int = 20) -> dict:
    highs = df["high"].values
    lows = df["low"].values
    cur = float(df["close"].iloc[-1])

    high_peaks, _ = find_peaks(highs, distance=min_distance)
    low_peaks, _ = find_peaks(-lows, distance=min_distance)

    resistance_levels = sorted([float(highs[i]) for i in high_peaks if highs[i] > cur])
    support_levels = sorted([float(lows[i]) for i in low_peaks if lows[i] < cur], reverse=True)

    return {
        "nearest_support": round(support_levels[0], 2) if support_levels else None,
        "nearest_resistance": round(resistance_levels[0], 2) if resistance_levels else None,
        "key_supports": [round(x, 2) for x in support_levels[:3]],
        "key_resistances": [round(x, 2) for x in resistance_levels[:3]],
    }


# ---------- 技术参考区间 (v2: 锚定到最近支撑位) ----------

def value_zone(df: pd.DataFrame, sr_min_distance: int = 20) -> dict:
    """Anchor to a historical support, ±3%; preserve broken levels instead of moving them to price.

    This is a technical reference, not a valuation. Event detection separately freezes
    the previous session's zone so recalculation cannot manufacture an entry event.
    """
    closes = df["close"]
    lows = df["low"].values
    cur = float(closes.iloc[-1])

    # 局部低点作支撑候选
    low_peaks, _ = find_peaks(-lows, distance=sr_min_distance)
    all_supports = sorted([float(lows[i]) for i in low_peaks], reverse=True)
    supports_below = [s for s in all_supports if s < cur]
    supports_above = [s for s in all_supports if s >= cur]

    # 布林下轨作备用锚
    ma20 = closes.rolling(20).mean().iloc[-1]
    std20 = closes.rolling(20).std().iloc[-1]
    boll_lower = float(ma20 - 2 * std20) if not pd.isna(ma20) else cur * 0.92

    if supports_below:
        anchor = supports_below[0]
        anchor_desc = f"最近下方支撑位 {anchor:.2f}"
    elif supports_above:
        anchor = min(supports_above)
        anchor_desc = f"已跌破近期支撑 {anchor:.2f}，保留原技术参考位"
    else:
        anchor = boll_lower
        anchor_desc = f"以布林下轨 {boll_lower:.2f} 为参考"

    # 常规区间: anchor ± 3%
    zone_low = round(anchor * 0.97, 2)
    zone_high = round(anchor * 1.03, 2)

    # 状态判断
    if zone_low <= cur <= zone_high:
        position = "in_zone"
        distance_pct = 0.0
        position_desc = "当前价已在技术参考区间内"
    elif cur > zone_high:
        position = "above_zone"
        distance_pct = round((cur - zone_high) / cur * 100, 2)
        position_desc = f"当前价高于区间上沿 {distance_pct}%"
    else:
        position = "below_zone"
        distance_pct = round((zone_low - cur) / cur * 100, 2)
        position_desc = f"当前价已低于区间下沿 {distance_pct}% (破位风险)"

    return {
        "zone_low": zone_low,
        "zone_high": zone_high,
        "zone_width_pct": round((zone_high - zone_low) / anchor * 100, 2),
        "current": round(cur, 2),
        "anchor": round(anchor, 2),
        "anchor_desc": anchor_desc,
        "method": "锚定下方最近技术支撑位, 上下浮动3%",
        "position": position,
        "position_desc": position_desc,
        "distance_pct": distance_pct,
        "in_zone": bool(zone_low <= cur <= zone_high),
    }


# ---------- 汇总 ----------

def full_analysis(df: pd.DataFrame, ma_window: int = 60, sr_min_distance: int = 20) -> dict:
    if len(df) < 20:
        raise ValueError("至少需要 20 个交易日的有效行情才能生成技术分析")
    return {
        "kline": describe_last_kline(df),
        "coverage": {"start": df.iloc[0]["date"].strftime("%Y-%m-%d"), "end": df.iloc[-1]["date"].strftime("%Y-%m-%d"), "sessions": len(df)},
        "range": range_stats(df, top_n=2),
        "indicators": technical_indicators(df),
        "support_resistance": find_support_resistance(df, sr_min_distance),
        "value_zone": value_zone(df, sr_min_distance),
    }
