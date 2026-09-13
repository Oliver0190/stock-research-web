"""Small, explainable news ranking; no model calls or investment scores."""
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from zoneinfo import ZoneInfo

from curl_cffi import requests

NEWS_VERSION = 1
LABELS = {"earnings": "财报业绩", "operations": "经营数据", "material": "重大事项",
          "buyback": "日常回购", "market": "行情资金", "other": "公司动态"}


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()


def company_aliases(name):
    full = re.sub(r"[-－](?:SW|W|S|B|R)$", "", name, flags=re.I)
    short = re.sub(r"(?:控股|股份|集团|有限公司)+$", "", full)
    return list(dict.fromkeys(s for s in (full, short) if len(s) >= 2))


def classify(title, symbol, name):
    direct = any(alias.lower() in title.lower() for alias in company_aliases(name)) or bool(re.search(r"(?<!\d)" + re.escape(symbol) + r"(?!\d)", title))
    if re.search(r"回购|購回", title):
        category, score = ("material", 90) if re.search(r"计划|方案|上限|授权|首次|终止|取消|拟.{0,12}回购", title) else ("buyback", 10)
    elif re.search(r"财报|年报|中报|季报|半年报|季度业绩|中期业绩|全年业绩|业绩预告|业绩快报|盈利预警|盈警|净利|营收|营业收入|毛利率|扭亏|亏损|业绩指引", title):
        category, score = "earnings", 100
    elif re.search(r"立案|处罚|调查|诉讼|违约|重组|并购|收购|停产|退市|大额减值|分红方案|特别股息", title):
        category, score = "material", 90
    elif re.search(r"销量|产销|交付|销售额|订单|产能|产量|月活|日活|用户数|付费用户|订阅|经营数据|资本开支|现金流", title):
        category, score = "operations", 80
    elif re.search(r"资金流|主力资金|南向|北向|融资余额|融资净|龙虎榜|收盘|涨停|跌停|板块|指数|ETF", title, re.I):
        category, score = "market", 5
    else:
        category, score = "other", 30
    if re.search(r"ETF|主力资金|资金净流|板块.{0,8}(?:涨|跌|流入|流出)", title, re.I):
        category, score = "market", 5
    # Broad market stories mentioning the code only in their body must not become
    # this company's earnings just because another constituent reported results.
    if not direct:
        score = min(score, 20)
    return category, score


def rank_news(items, symbol, name, as_of=None, limit=150):
    today = date.fromisoformat(str(as_of)[:10]) if as_of else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    candidates = []
    for raw in items or []:
        title = clean(raw.get("title"))
        if not title:
            continue
        timestamp = clean(raw.get("time"))
        try:
            age = (today - date.fromisoformat(timestamp[:10])).days
            if age < 0 or age > 180:
                continue
        except ValueError:
            age = None
        category, score = classify(title, symbol, name)
        score = max(0, score - (30 if age is None or age > 90 else 15 if age > 45 else 0))
        candidates.append({**raw, "title": title, "summary": clean(raw.get("summary"))[:500],
            "time": timestamp, "category": category, "category_label": LABELS[category],
            "score": score, "importance": "important" if score >= 55 else "normal", "related_count": max(1, int(raw.get("related_count", 1)))})
    candidates.sort(key=lambda item: (item["score"], item["time"]), reverse=True)
    result, seen_urls, seen_titles, buyback = [], set(), set(), None
    for item in candidates:
        title_key = re.sub(r"[\W_]", "", item["title"]).lower()
        url_key = re.sub(r"^https?://", "", str(item.get("url", ""))).split("?")[0]
        if (url_key and url_key in seen_urls) or title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        if item["category"] == "buyback" and buyback is not None:
            buyback["related_count"] += item["related_count"]
            continue
        result.append(item)
        if item["category"] == "buyback":
            buyback = item
    return result[:limit]


def search_news(keyword):
    # The same public Eastmoney search used by AKShare, with a bounded timeout,
    # a larger candidate page and without its copied browser cookie.
    params = {"uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"], "client": "web",
              "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {
                  "searchScope": "default", "sort": "default", "pageIndex": 1, "pageSize": 50,
                  "preTag": "<em>", "postTag": "</em>"}}}
    response = requests.get("https://search-api-web.eastmoney.com/search/jsonp",
        params={"cb": "jQuery35101792940631092459_1764599530165", "param": json.dumps(params, ensure_ascii=False)},
        headers={"Referer": "https://so.eastmoney.com/", "User-Agent": "Mozilla/5.0"}, timeout=7)
    response.raise_for_status()
    text = response.text
    data = json.loads(text[text.index("(") + 1:text.rindex(")")])
    return [{"title": clean(row.get("title")), "summary": clean(row.get("content"))[:500],
             "time": clean(row.get("date")), "source": clean(row.get("mediaName")),
             "url": row.get("url") or "https://finance.eastmoney.com/a/" + str(row["code"]) + ".html"}
            for row in data["result"]["cmsArticleWebOld"]]


def fetch_news(symbol, name):
    aliases = company_aliases(name)
    company = aliases[-1] if aliases else symbol
    queries = list(dict.fromkeys([symbol, company, company + " 财报"]))
    items, failures = [], 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(search_news, query) for query in queries]
        for future in as_completed(futures):
            try:
                items.extend(future.result())
            except Exception:
                failures += 1
    if failures == len(queries):
        raise RuntimeError("新闻源暂时不可用，已保留上次内容")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return {"news": rank_news(items, symbol, name, today), "news_candidates": len(items),
            "news_version": NEWS_VERSION, "news_fetched_date": today,
            "news_issue": "部分新闻检索未完成，当前展示已获取的内容" if failures else None}
