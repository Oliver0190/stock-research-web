"""Shared MACD/KDJ calculations, chart history and concise factual readings."""
import pandas as pd


def oscillator_frame(frame):
    """Calculate over all available history before any display-range trimming."""
    closes = frame["close"]
    dif = closes.ewm(span=12, adjust=False).mean() - closes.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    low = frame["low"].rolling(9).min()
    high = frame["high"].rolling(9).max()
    spread = (high - low).replace(0, float("nan"))
    # Initial K/D = 50; a flat nine-session range has neutral RSV = 50.
    rsv = ((closes - low) / spread * 100).fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return pd.DataFrame({"date": frame["date"], "dif": dif, "dea": dea,
                         "macd_bar": 2 * (dif - dea), "k": k, "d": d, "j": 3 * k - 2 * d})


def crossing(current, previous, first, second):
    delta = current[first] - current[second]
    before = previous[first] - previous[second]
    if delta > 1e-9 and before <= 1e-9:
        return "golden"
    if delta < -1e-9 and before >= -1e-9:
        return "death"
    return None


def macd_reading(current, previous):
    dif, dea, bar = (float(current[k]) for k in ("dif", "dea", "macd_bar"))
    old_bar = float(previous["macd_bar"])
    cross = crossing(current, previous, "dif", "dea")
    position = "双线位于零轴上方" if min(dif, dea) > 1e-9 else "双线位于零轴下方" if max(dif, dea) < -1e-9 else "双线处于零轴附近或两侧"
    if abs(bar) <= 1e-9:
        movement = "柱值接近零，快慢线接近重合"
    elif abs(old_bar) <= 1e-9:
        movement = "柱值由零转正" if bar > 0 else "柱值由零转负"
    elif old_bar * bar < 0:
        movement = "柱值由负转正" if bar > 0 else "柱值由正转负"
    elif abs(abs(bar) - abs(old_bar)) <= 1e-9:
        movement = ("正柱" if bar > 0 else "负柱") + "与上一交易日基本持平"
    else:
        expanding = abs(bar) > abs(old_bar)
        movement = ("正柱" if bar > 0 else "负柱") + ("放大" if expanding else "缩短")
        movement += "，" + ("上行" if bar > 0 else "下行") + "动能" + ("增强" if expanding else "减弱")
    relation = {"golden": "DIF 当日上穿 DEA，形成新金叉。", "death": "DIF 当日下穿 DEA，形成新死叉。"}.get(cross)
    if relation is None:
        relation = "DIF 与 DEA 接近重合。" if abs(dif - dea) <= 1e-9 else f"DIF 仍在 DEA {'上' if dif > dea else '下'}方，当日没有新交叉。"
    return {"summary": position + "；" + movement + "。", "detail": relation, "cross": cross}


def kdj_reading(current, previous):
    k, d, j = (float(current[key]) for key in ("k", "d", "j"))
    cross = crossing(current, previous, "k", "d")
    if min(k, d) >= 80:
        position = "K、D 均在 80 以上，处于高位区"
    elif max(k, d) <= 20:
        position = "K、D 均在 20 以下，处于低位区"
    elif 20 <= k <= 80 and 20 <= d <= 80:
        position = "K、D 均在 20–80 之间，处于中间区"
    else:
        position = "K、D 位于不同参考区间"
    relation = {"golden": "K 当日上穿 D，形成新金叉", "death": "K 当日下穿 D，形成新死叉"}.get(cross)
    if relation is None:
        relation = "K 与 D 接近重合" if abs(k - d) <= 1e-9 else f"K 仍在 D {'上' if k > d else '下'}方，没有新交叉"
    if j > 100:
        detail = "J 超过 100，短线偏热；高位状态可能持续，不等同于即将回落。"
    elif j < 0:
        detail = "J 低于 0，短线偏弱；低位状态可能持续，不等同于即将反弹。"
    else:
        detail = "J 位于 0–100 之间。20／80 用于观察高低位，不能单独确认趋势反转。"
    return {"summary": position + "；" + relation + "。", "detail": detail, "cross": cross}


def chart_payload(frame):
    values = oscillator_frame(frame)
    count = len(values)
    current = values.iloc[-1]
    previous = values.iloc[-2] if count > 1 else current
    series = []
    for i in range(max(0, count - 120), count):
        row = values.iloc[i]
        point = {"date": row["date"].strftime("%Y-%m-%d")}
        for key in ("dif", "dea", "macd_bar", "k", "d", "j"):
            # Avoid drawing initialization values as mature observations.
            ready = i >= (34 if key in {"dif", "dea", "macd_bar"} else 8)
            point[key] = round(float(row[key]), 6) if ready else None
        series.append(point)
    return {"series": series,
            "macd": macd_reading(current, previous) if count >= 35 else None,
            "kdj": kdj_reading(current, previous) if count >= 9 else None,
            "sessions": count}
