"""Regression checks use synthetic OHLCV only, isolated from the user's database/API keys."""
import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend import analyzer, reports
from backend.app import create_app
from backend.market import HK, market_context
from backend.service import ResearchService, normalize_frame
from backend.store import Store


def history(last="2026-09-09", size=140):
    dates = pd.bdate_range(end=last, periods=size)
    return [{"date": d.strftime("%Y-%m-%d"), "open": 100 + i * .1,
             "close": 100.2 + i * .1, "high": 102 + i * .1,
             "low": 99 + i * .1, "volume": 1000.0} for i, d in enumerate(dates)]


def ctx(phase="closing", today="2026-09-10", expected="2026-09-10"):
    return {"today": today, "expected_session": expected, "phase": phase, "session": today,
            "now": today + "T18:30:00+08:00"}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / "data" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.config = {"watchlist": [{"symbol": "00700", "name": "测试股票", "alert_low": 200}], "llm": {"model": "test"}}
        self.rows = history()
        self.service = ResearchService(self.store, self.config, self.fetch)
        self.ai = patch.dict('os.environ', {"DEEPSEEK_API_KEY": ""})
        self.ai.start()

    def tearDown(self):
        self.ai.stop()
        self.service.close()
        assert Path(self.temp.name).resolve().is_relative_to(Path(__file__).resolve().parents[1] / "data" / "tests")
        self.temp.cleanup()

    def fetch(self, symbol, mode, argument):
        if mode == "market":
            return {"rows": copy.deepcopy(self.rows), "source": "Test fixture"}
        return {"financials": None, "news": None}

    def refresh(self, context=None):
        self.service.refresh_one(self.config["watchlist"][0], context or ctx())

    def test_stale_close_never_becomes_todays_report(self):
        self.refresh()
        self.assertEqual(self.store.reports(date="2026-09-10"), [])
        self.assertEqual(self.store.reports()[0]["data_date"], "2026-09-09")
        self.assertEqual(self.store.statuses()["00700"]["state"], "stale")

    def test_retry_new_data_fills_missing_close_without_duplicate(self):
        self.refresh()
        self.rows = history("2026-09-10")
        self.refresh()
        self.refresh()
        self.assertEqual(len(self.store.reports(date="2026-09-10")), 1)
        self.assertEqual(self.store.statuses()["00700"]["state"], "ready")

    def test_intraday_events_once_per_day_and_completed_baseline_preserved(self):
        self.rows = history("2026-09-10")
        live = ctx("intraday", expected="2026-09-09")
        self.refresh(live)
        self.refresh(live)
        events = [r for r in self.store.reports(date="2026-09-10") if r["kind"] == "intraday"]
        self.assertEqual(len([r for r in events if r["metadata"].get("event_key") == "alert_low"]), 1)
        self.assertTrue(self.store.snapshot("00700", "2026-09-09")["complete"])
        self.assertFalse(self.store.snapshot("00700")["complete"])

    def test_stale_daily_data_cannot_trigger_todays_intraday_event(self):
        self.refresh(ctx("intraday", expected="2026-09-09"))
        self.assertEqual(self.store.reports(date="2026-09-10"), [])

    def test_morning_uses_previous_session_but_files_under_today(self):
        self.refresh(ctx("morning", expected="2026-09-09"))
        report = self.store.reports()[0]
        self.assertEqual((report["kind"], report["report_date"], report["data_date"]), ("morning", "2026-09-10", "2026-09-09"))

    def test_failed_stock_does_not_stop_other_stock(self):
        self.service.items["09988"] = {"symbol": "09988", "name": "第二只"}
        original = self.service.fetcher
        self.service.fetcher = lambda symbol, mode, arg: (_ for _ in ()).throw(RuntimeError("测试错误")) if symbol == "00700" else original(symbol, mode, arg)
        with patch('backend.service.market_context', return_value=ctx()):
            self.service._run(["00700", "09988"])
        self.assertEqual(self.store.statuses()["00700"]["state"], "error")
        self.assertIsNotNone(self.store.snapshot("09988"))

    def test_bad_data_does_not_replace_existing_snapshot(self):
        self.refresh()
        saved = self.store.snapshot("00700")
        self.rows[-1]["close"] = float('nan')
        with self.assertRaises(ValueError):
            self.refresh()
        self.assertEqual(self.store.snapshot("00700"), saved)

    def test_insufficient_history_is_explicit(self):
        self.rows = history(size=5)
        with self.assertRaisesRegex(ValueError, "20"):
            self.refresh()

    def test_volume_reference_excludes_current_day(self):
        frame = normalize_frame(history(size=30))
        frame.loc[frame.index[-1], "volume"] = 3000
        self.assertEqual(analyzer.describe_last_kline(frame)["volume_ratio"], 3.0)

    def test_indicator_history_is_persisted_with_the_matching_snapshot(self):
        self.rows = history("2026-09-10")
        self.refresh(ctx("intraday", expected="2026-09-09"))
        for day in ("2026-09-09", "2026-09-10"):
            snapshot = self.store.snapshot("00700", day)
            last = snapshot["indicator_chart"]["series"][-1]
            self.assertEqual(last["date"], day)
            self.assertEqual(snapshot["chart"][-1]["date"], day)
            self.assertAlmostEqual(last["dif"], snapshot["analysis"]["indicators"]["macd"]["dif"], delta=.00051)
            self.assertIsNotNone(snapshot["indicator_chart"]["kdj"])

    def test_api_persists_notes_and_rejects_cross_site_writes(self):
        app = create_app(self.service, schedule=False)
        with TestClient(app) as client:
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/assets/app.js').status_code, 200)
            self.assertEqual(client.get('/api/stocks/00000').status_code, 404)
            self.assertEqual(client.get('/assets/../.env').status_code, 404)
            self.assertEqual(client.put('/api/stocks/00700/note', json={"note": "观察收入变化"}).status_code, 200)
            self.assertEqual(Store(self.store.path).profile("00700")["note"], "观察收入变化")
            self.assertEqual(client.post('/api/refresh', json={}, headers={"Origin": "https://untrusted.example"}).status_code, 403)
            self.assertEqual(client.put('/api/stocks/00700/note', json={"note": "a" * 10001}).status_code, 422)

    def test_historical_read_does_not_clear_other_days_or_new_events(self):
        self.refresh()
        old = self.store.reports()[0]
        self.rows = history("2026-09-10")
        self.refresh()
        self.store.mark_read("00700", "2026-09-10T19:00:00+08:00", "2026-09-10T19:00:00+08:00", [old["id"]])
        self.assertIsNotNone(self.store.report(old["id"])["read_at"])
        self.assertIsNone(self.store.reports(date="2026-09-10")[0]["read_at"])

    def test_ai_failure_keeps_report_and_retry_upgrades_it(self):
        self.rows = history("2026-09-10")
        with patch.dict('os.environ', {"DEEPSEEK_API_KEY": "test-only"}), patch('backend.llm.website_report', side_effect=RuntimeError()):
            self.refresh()
        self.assertEqual(self.store.reports()[0]["engine"], "rules")
        self.assertEqual(self.store.statuses()["00700"]["state"], "partial")
        with patch.dict('os.environ', {"DEEPSEEK_API_KEY": "test-only"}), patch('backend.llm.website_report', return_value="已补齐解读"):
            self.refresh()
        self.assertEqual(len(self.store.reports()), 1)
        self.assertEqual(self.store.reports()[0]["engine"], "ai")

    def test_same_day_cannot_start_overlapping_update(self):
        self.service.progress["running"] = True
        self.assertFalse(self.service.start())


class CalendarTests(unittest.TestCase):
    def test_weekend_and_holiday(self):
        weekend = market_context(datetime(2026, 9, 12, 12, tzinfo=HK))
        self.assertEqual(weekend["phase"], "holiday")
        self.assertEqual(weekend["expected_session"], "2026-09-11")
        holiday = market_context(datetime(2026, 10, 1, 12, tzinfo=HK))
        self.assertEqual(holiday["phase"], "holiday")

    def test_post_close_settlement_grace(self):
        settling = market_context(datetime(2026, 9, 10, 16, 5, tzinfo=HK))
        self.assertEqual(settling["expected_session"], "2026-09-09")
        closing = market_context(datetime(2026, 9, 10, 18, 30, tzinfo=HK))
        self.assertEqual(closing["expected_session"], "2026-09-10")


if __name__ == '__main__':
    unittest.main()
