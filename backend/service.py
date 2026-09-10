"""Collection, analysis and scheduling. No notification transports live here."""
import json
import logging
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd

from backend import analyzer, reports
from backend.indicators import chart_payload
from backend.market import HK, market_context
from backend.settings import ROOT, load_config
from backend.store import Store, encode

log = logging.getLogger(__name__)


def isolated_fetch(symbol, mode, argument):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run([sys.executable, "-m", "backend.fetch_worker", symbol, mode, str(argument)],
                                cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                timeout=65 if mode == "market" else 25)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("数据源响应超时，可稍后重试") from e
    if result.returncode:
        raise RuntimeError("行情源暂时不可用，已保留上次成功的数据")
    return json.loads(result.stdout)


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


class ResearchService:
    def __init__(self, store=None, config=None, fetcher=isolated_fetch):
        self.store = store or Store()
        self.config = config or load_config()
        self.fetcher = fetcher
        self.items = {s["symbol"]: s for s in self.config["watchlist"]}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.progress = {"running": False, "completed": 0, "total": 0, "failures": 0, "started_at": None}
        self.auto_refresh = os.environ.get("STOCK_AGENT_AUTO_REFRESH", "1") != "0"

    def status(self):
        with self.lock:
            return {**self.progress, "auto_refresh": self.auto_refresh}

    def start(self, symbols=None):
        symbols = symbols or list(self.items)
        if any(symbol not in self.items for symbol in symbols):
            raise ValueError("股票不在关注列表中")
        with self.lock:
            if self.progress["running"]:
                return False
            self.progress = {"running": True, "completed": 0, "total": len(symbols), "failures": 0,
                             "started_at": datetime.now(HK).isoformat(timespec="seconds")}
        threading.Thread(target=self._run, args=(symbols,), daemon=True, name="stock-refresh").start()
        return True

    def _run(self, symbols):
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(self.refresh_one, self.items[s]): s for s in symbols}
                for future in as_completed(futures):
                    symbol, failed = futures[future], False
                    try:
                        future.result()
                    except Exception as e:
                        failed = True
                        message = str(e) if isinstance(e, (RuntimeError, ValueError)) else "本次更新未完成，可单独重试"
                        self.store.set_status(symbol, "error", message, datetime.now(HK).isoformat(timespec="seconds"))
                        log.warning("Stock refresh failed: %s (%s)", symbol, type(e).__name__)
                    with self.lock:
                        self.progress["completed"] += 1
                        self.progress["failures"] += int(failed)
        finally:
            with self.lock:
                self.progress["running"] = False

    def refresh_one(self, item, context=None):
        ctx = context or market_context()
        now, symbol = ctx["now"], item["symbol"]
        self.store.set_status(symbol, "loading", "正在获取行情", now)
        raw = self.fetcher(symbol, "market", self.config.get("analyzer", {}).get("lookback_days", 730))
        frame = normalize_frame(raw["rows"])
        frame = frame[frame["date"].dt.strftime("%Y-%m-%d") <= ctx["today"]].reset_index(drop=True)
        if len(frame) < 20:
            raise ValueError("有效行情不足 20 个交易日，暂不生成技术分析")
        latest_date = frame.iloc[-1]["date"].strftime("%Y-%m-%d")
        completed = frame[frame["date"].dt.strftime("%Y-%m-%d") <= ctx["expected_session"]].reset_index(drop=True)
        if len(completed) < 20:
            raise ValueError("已完成的日线不足 20 个交易日")
        min_distance = self.config.get("analyzer", {}).get("support_resistance_min_distance", 20)
        baseline = analyzer.full_analysis(completed, sr_min_distance=min_distance)
        previous = analyzer.full_analysis(completed.iloc[:-1], sr_min_distance=min_distance) if len(completed) > 20 else None
        changes = reports.signals(baseline, previous)
        old = self.store.snapshot(symbol)
        fund = old.get("fundamentals") if old else None
        payload = self._payload(completed, baseline, raw["source"], now, changes, fund, True)
        self.store.put_snapshot(symbol, payload, now)
        # Historical data is stored under its actual date, never relabelled as today's close.
        data_date = baseline["kline"]["date"]
        kind = "morning" if ctx["phase"] == "morning" and data_date == ctx["expected_session"] else "closing"
        report_date = ctx["today"] if kind == "morning" else data_date
        report_id = f"{symbol}:{report_date}:{kind}"
        prior_report = self.store.report(report_id)
        report = {"id": report_id, "symbol": symbol, "report_date": report_date, "data_date": data_date,
                  "kind": kind, "title": "盘前观察" if kind == "morning" else "收盘复盘",
                  "summary": reports.short_summary(baseline, changes), "importance": "important" if changes else "normal",
                  "created_at": now, "metadata": {"source": raw["source"], "changes": changes},
                  "body": reports.rule_report(baseline, changes), "engine": "rules"}
        self.store.put_report(report)
        if ctx["phase"] in {"intraday", "break", "settling"} and latest_date == ctx["today"] and data_date == ctx["expected_session"]:
            observed = analyzer.full_analysis(frame, sr_min_distance=min_distance)
            observation = self._payload(frame, observed, raw["source"], now, [], fund, False)
            self.store.put_snapshot(symbol, observation, now)
            for key, description in reports.intraday_events(item, observed, baseline):
                event_id = f"{symbol}:{ctx['today']}:intraday:{key}"
                if self.store.report(event_id):
                    continue
                self.store.put_report({"id": event_id, "symbol": symbol, "report_date": ctx["today"], "data_date": latest_date,
                    "kind": "intraday", "title": description, "summary": description, "importance": "important", "engine": "rules",
                    "created_at": now, "body": description + "。\n使用数据源最新日线中的盘中参考价，可能存在延迟，收盘前仍会变化。技术位来自上一交易日。",
                    "metadata": {"source": raw["source"], "event_key": key}})
        stale = data_date != ctx["expected_session"]
        if stale:
            self.store.set_status(symbol, "stale", f"收盘数据截至 {data_date}，等待 {ctx['expected_session']} 的数据", now)
        else:
            self.store.set_status(symbol, "enriching", "行情已保存，正在补充解读", now)
        # Persist facts before optional slow enrichments. Repeated refreshes reuse successful prose.
        ai_issue = None
        if not prior_report or prior_report["engine"] != "ai":
            if not fund or fund.get("fetched_date") != ctx["today"]:
                try:
                    fund = self.fetcher(symbol, "fundamentals", item["name"])
                    fund["fetched_date"] = ctx["today"]
                    payload["fundamentals"] = fund
                    self.store.put_snapshot(symbol, payload, now)
                    if latest_date == ctx["today"] and ctx["phase"] in {"intraday", "break", "settling"} and not stale:
                        observation["fundamentals"] = fund
                        self.store.put_snapshot(symbol, observation, now)
                except Exception:
                    pass
            body, engine, ai_issue = reports.compose(kind, item, baseline, changes, self.config["llm"]["model"], fund)
            self.store.put_report({**report, "body": body, "engine": engine,
                                   "metadata": {**report["metadata"], "ai_issue": ai_issue}})
        if not stale:
            self.store.set_status(symbol, "partial" if ai_issue else "ready", ai_issue or "更新完成", now)

    @staticmethod
    def _payload(frame, analysis, source, now, changes, fund, complete):
        chart = [{"date": row["date"].strftime("%Y-%m-%d"), "close": round(float(row["close"]), 3),
                  "volume": float(row["volume"])} for _, row in frame.tail(120).iterrows()]
        return json.loads(encode({"analysis": analysis, "data_date": analysis["kline"]["date"], "source": source,
                                 "fetched_at": now, "complete": complete, "changes": changes,
                                 "chart": chart, "indicator_chart": chart_payload(frame), "fundamentals": fund}))

    def overview(self, date=None):
        ctx = market_context()
        day = date or ctx["today"]
        stocks, statuses = [], self.store.statuses()
        for symbol, item in self.items.items():
            snapshot = self.store.snapshot(symbol, date)
            stock_reports = self.store.reports(symbol, day)
            all_reports = self.store.reports(symbol)
            stocks.append({**item, "snapshot": snapshot, "status": statuses.get(symbol),
                           "summary": stock_reports[0]["summary"] if stock_reports else None,
                           "unread": sum(r["importance"] == "important" and not r["read_at"] for r in all_reports),
                           "important": any(r["importance"] == "important" for r in stock_reports)})
        return {"stocks": stocks, "market": ctx, "selected_date": day, "dates": self.store.dates(),
                "reports": self.store.reports(date=day), "update": self.status(),
                "ai_available": bool(os.environ.get("DEEPSEEK_API_KEY"))}

    def detail(self, symbol, date=None):
        if symbol not in self.items:
            raise KeyError(symbol)
        return {**self.items[symbol], "snapshot": self.store.snapshot(symbol, date),
                "reports": self.store.reports(symbol, date), "profile": self.store.profile(symbol),
                "status": self.store.statuses().get(symbol), "retrieved_at": datetime.now(HK).isoformat(timespec="seconds")}

    def scheduler(self):
        # While this local process is alive, refresh at startup, then during relevant sessions.
        self.start()
        last_slot = None
        while not self.stop_event.wait(30):
            if not self.auto_refresh:
                continue
            ctx = market_context()
            now = datetime.now(HK)
            eligible = ctx["phase"] == "intraday" or (ctx["phase"] == "morning" and now.hour >= 8) or (ctx["phase"] == "closing" and now.hour < 20)
            slot = f"{ctx['today']}:{ctx['phase']}:{now.hour}:{now.minute // 10}"
            if eligible and slot != last_slot and not self.status()["running"]:
                self.start()
                last_slot = slot

    def close(self):
        self.stop_event.set()
