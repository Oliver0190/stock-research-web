"""Deterministic facts first; optional AI prose never controls event detection."""
import os

from backend import llm


def signals(analysis, previous=None):
    out = []
    kline = analysis["kline"]
    pct = kline["pct_change"]
    if abs(pct) >= 3:
        out.append({"key": "price_move", "title": f"单日{'上涨' if pct > 0 else '下跌'} {abs(pct):.2f}%", "severity": "attention"})
    if kline["volume_ratio"] >= 2:
        out.append({"key": "volume", "title": f"成交量为前 20 日均量的 {kline['volume_ratio']:.2f} 倍", "severity": "attention"})
    for key, name in [("macd", "MACD"), ("kdj", "KDJ")]:
        signal = analysis["indicators"][key]["signal"]
        if "金叉" in signal or "死叉" in signal:
            out.append({"key": key, "title": f"{name} {signal}", "severity": "attention"})
    if previous:
        # Compare yesterday's fixed reference zone; do not move the threshold with price.
        zone = previous["value_zone"]
        current, prior = kline["close"], previous["kline"]["close"]
        inside = lambda price: zone["zone_low"] <= price <= zone["zone_high"]
        if inside(current) and not inside(prior):
            out.append({"key": "zone_entry", "title": "进入上一交易日的技术参考区间", "severity": "attention"})
        elif inside(prior) and not inside(current):
            out.append({"key": "zone_exit", "title": "离开上一交易日的技术参考区间", "severity": "attention"})
        for field, crossed, title in [
            ("nearest_support", lambda level: current < level <= prior, "跌破上一交易日支撑"),
            ("nearest_resistance", lambda level: current > level >= prior, "突破上一交易日阻力"),
        ]:
            level = previous["support_resistance"].get(field)
            if level and crossed(level):
                out.append({"key": field, "title": f"{title} {level:.2f}", "severity": "attention"})
    return out


def intraday_events(item, observation, baseline):
    price, prior = observation["kline"]["close"], baseline["kline"]["close"]
    pct = (price / prior - 1) * 100
    result = []
    for field, match, label in [("alert_low", lambda p, x: p <= x, "低于设定下限"), ("alert_high", lambda p, x: p >= x, "高于设定上限")]:
        level = item.get(field)
        if level is not None and match(price, level):
            result.append((field, f"{label} {level:g}，观察价 {price:.2f}"))
    for field, match, label in [("nearest_support", lambda p, x: p < x, "低于昨日支撑"), ("nearest_resistance", lambda p, x: p > x, "高于昨日阻力")]:
        level = baseline["support_resistance"].get(field)
        if level and match(price, level):
            result.append((field, f"{label} {level:.2f}，观察价 {price:.2f}"))
    if abs(pct) >= 5:
        result.append(("big_gain" if pct > 0 else "big_drop", f"相对昨日收盘变动 {pct:+.2f}%"))
    zone = baseline["value_zone"]
    if zone["zone_low"] <= price <= zone["zone_high"] and not zone["zone_low"] <= prior <= zone["zone_high"]:
        result.append(("zone_entry", f"观察价进入昨日参考区间 {zone['zone_low']:.2f}–{zone['zone_high']:.2f}"))
    return result


def short_summary(analysis, changes):
    if changes:
        return "；".join(x["title"] for x in changes[:2])
    return f"收盘 {analysis['kline']['close']:.2f}，{analysis['indicators']['ma']['arrangement'].split('(')[0]}，未触发重点变化"


def rule_report(analysis, changes):
    k, sr, z = analysis["kline"], analysis["support_resistance"], analysis["value_zone"]
    support = f"{sr['nearest_support']:.2f}" if sr["nearest_support"] else "暂无明确支撑"
    resistance = f"{sr['nearest_resistance']:.2f}" if sr["nearest_resistance"] else "暂无明确阻力"
    events = "\n".join(f"• {s['title']}" for s in changes) or "本次没有触发重点变化。"
    return (f"**表现与位置**\n{k['date']} 收盘 {k['close']:.2f}，较上一交易日 {k['pct_change']:+.2f}%。"
            f"当日成交量为前 20 个交易日平均成交量的 {k['volume_ratio']:.2f} 倍。\n\n"
            f"**值得关注的变化**\n{events}\n\n**技术参考**\n支撑：{support}；阻力：{resistance}。"
            f"\n参考区间 {z['zone_low']:.2f}–{z['zone_high']:.2f}，由历史技术支撑推算。\n\n"
            f"**趋势观察**\n{analysis['indicators']['ma']['arrangement']}。"
            f"{analysis['indicators']['macd']['signal']}。\n历史技术指标不代表未来表现。")


def compose(kind, item, analysis, changes, model, fundamentals=None, ai=True):
    fallback = rule_report(analysis, changes)
    if not ai or not os.environ.get("DEEPSEEK_API_KEY"):
        return fallback, "rules", None
    try:
        text = llm.website_report(kind, item, analysis, changes, model, fundamentals)
        if not text.strip():
            raise ValueError("empty response")
        return text, "ai", None
    except Exception:
        return fallback, "rules", "AI 解读暂时不可用，已保留完整的规则分析，可再次更新补齐。"
