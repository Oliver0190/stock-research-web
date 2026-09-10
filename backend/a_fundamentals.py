"""A-share financials normalized into the website's existing report schema."""
from backend.fundamentals import _safe_num, fetch_news, fetch_next_earnings_date
from backend.market import instrument


def financial_records(frame, max_periods=4):
    if frame is None or frame.empty or "REPORT_DATE" not in frame:
        return None
    periods = []
    for _, row in frame.sort_values("REPORT_DATE", ascending=False).head(max_periods).iterrows():
        date = str(row["REPORT_DATE"])[:10]
        revenue = _safe_num(row.get("TOTAL_OPERATE_INCOME"))
        if revenue is None:
            revenue = _safe_num(row.get("OPERATE_INCOME"))
        profit = _safe_num(row.get("PARENT_NETPROFIT"))
        if revenue is None and profit is None:
            continue
        periods.append({"report_date": date, "report_period_type": {
            "03-31": "一季报(1–3月)", "06-30": "中报(1–6月累计)",
            "09-30": "三季报(1–9月累计)", "12-31": "年报(全年累计)"}.get(date[-5:], "未知报告期"),
            "revenue": revenue, "net_profit": profit, "net_profit_label": "归母净利润",
            "currency": "CNY", "unit": "元", "source": "东方财富 / AKShare"})
    return {"periods": periods, "currency": "CNY", "unit": "元", "source": "东方财富 / AKShare"} if periods else None


def fetch_fundamentals(symbol, configured_name=""):
    financials = None
    try:
        import akshare as ak
        exchange = instrument(symbol, "A")["exchange"]
        financials = financial_records(ak.stock_profit_sheet_by_report_em(symbol=exchange + symbol))
    except Exception:
        pass
    return {"configured_name": configured_name, "financials": financials, "news": fetch_news(symbol),
            "next_earnings_date": fetch_next_earnings_date(symbol, market="A")}
