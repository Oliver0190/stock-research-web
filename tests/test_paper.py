"""Paper execution invariants, using isolated ledgers and synthetic prices only."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.market import HK, calendar
from backend.paper import PaperService, INITIAL, cents, position_value, validate_action, quantity_ok
from backend.paper_agent import Plan
from backend.service import ResearchService
from backend.store import Store, encode


def bar(day, price=10, **extra):
    return {"date": day, "open": price, "close": price, "low": price - .1, "high": price + .1,
            "volume": 100000, "split": 0, "dividend": 0, **extra}


class PaperTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "data" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.now = datetime(2026, 11, 2, 18, tzinfo=HK)
        self.services, self.papers = {}, {}
        for market, symbol in [("HK", "00700"), ("A", "002594")]:
            research = ResearchService(Store(Path(self.temp.name) / f"{market}.db"),
                {"market": market, "watchlist": [{"symbol": symbol, "name": "测试股票"}], "llm": {"model": "test"}})
            self.services[market] = research
            self.papers[market] = PaperService(research, clock=lambda: self.now)
        self.paper = self.papers["A"]

    def tearDown(self):
        for s in self.services.values():
            s.close()
        for p in self.papers.values():
            p.close()
        self.temp.cleanup()

    def prepare(self, paper=None):
        paper = paper or self.paper
        with paper.transaction() as (s, _):
            s.update(enabled=True, last_session="2026-11-02")
            s["equity"] = [{"date": "2026-11-02", "value_cents": INITIAL}]

    def proposal(self, side="buy", quantity=100, symbol="002594", paper=None):
        paper = paper or self.paper
        evidence = {symbol: {"tradable": True, "raw_close": 10, "lot": 100, "name": "测试股票"}}
        plan = {"summary": "测试基于已保存资料形成的判断", "actions": [{"symbol": symbol, "side": side, "quantity": quantity,
                "reason": "合成资料支持这次模拟操作，供测试验证。", "invalidation": "下一交易日价格偏离时重新核验。"}]}
        record = {"id": f"test-{len(paper.snapshot()['decisions'])}", "session": "2026-11-02", "plan": plan,
                  "created_at": self.now.isoformat(), "evidence": evidence, "trace": []}
        with paper.transaction() as (_, db):
            db.execute("INSERT INTO paper_decisions VALUES(?,?,?,?)", (record["id"], record["session"], "running", encode(record)))
        paper.commit_plan(record, evidence, paper.snapshot()["generation"])
        return record

    def test_accounts_isolated_and_restart_preserves_cash(self):
        with self.paper.transaction() as (s, _):
            s["cash_cents"] -= 1200
        self.assertEqual(self.papers["HK"].snapshot()["cash_cents"], INITIAL)
        restarted = PaperService(self.services["A"], clock=lambda: self.now)
        self.assertEqual(restarted.snapshot()["cash_cents"], INITIAL - 1200)
        self.assertEqual(restarted.snapshot()["currency"], "CNY")
        self.assertEqual(self.papers["HK"].snapshot()["currency"], "HKD")

    def test_decision_cannot_fill_same_day_and_repeated_settlement_is_idempotent(self):
        self.prepare()
        self.proposal()
        self.assertEqual(self.paper.snapshot()["orders"][0]["eligible_session"], "2026-11-03")
        self.paper.settle({"002594": {"rows": [bar("2026-11-02")]}}, "2026-11-02")
        self.assertEqual(self.paper.snapshot()["cash_cents"], INITIAL)
        quotes = {"002594": {"rows": [bar("2026-11-03", close=10.2)]}}
        self.paper.settle(quotes, "2026-11-03")
        one = self.paper.snapshot()
        self.assertEqual(one["orders"][0]["status"], "filled")
        self.assertEqual(one["orders"][0]["fee_cents"], 500)
        self.assertEqual(one["cash_cents"], INITIAL - 100550)
        self.assertEqual(one["positions"]["002594"]["value_cents"], 102000)
        self.paper.settle(quotes, "2026-11-03")
        self.assertEqual(self.paper.snapshot()["cash_cents"], one["cash_cents"])
        restarted = PaperService(self.services["A"], clock=lambda: self.now)
        restarted.settle(quotes, "2026-11-03")
        self.assertEqual(restarted.snapshot()["cash_cents"], one["cash_cents"])

    def test_late_model_result_never_backdates_to_passed_open(self):
        self.prepare()
        self.now = datetime(2026, 11, 3, 10, tzinfo=HK)
        self.proposal()
        self.assertEqual(self.paper.snapshot()["orders"][0]["eligible_session"], "2026-11-04")

    def test_pause_cancels_pending_and_inflight_generation(self):
        self.prepare()
        record = self.proposal()
        old_generation = self.paper.snapshot()["generation"]
        self.paper.enable(False)
        self.assertEqual(self.paper.snapshot()["orders"][0]["status"], "cancelled")
        self.paper.commit_plan(record, record["evidence"], old_generation)
        self.assertEqual(len(self.paper.snapshot()["orders"]), 1)
        self.assertEqual(self.paper.snapshot()["decisions"][0]["status"], "complete")
        record["id"] = "inflight"
        with self.paper.transaction() as (_, db):
            db.execute("INSERT INTO paper_decisions VALUES(?,?,?,?)", (record["id"], record["session"], "running", encode(record)))
        self.paper.commit_plan(record, record["evidence"], old_generation)
        self.assertEqual(self.paper.decision("inflight")["status"], "cancelled")
        self.assertEqual(len(self.paper.snapshot()["orders"]), 1)

    def test_batch_reservations_limit_combined_exposure(self):
        self.prepare()
        evidence = {f"00000{i}": {"tradable": True, "raw_close": 10, "lot": 100, "name": "测试"} for i in range(1, 6)}
        record = {"id": "batch", "session": "2026-11-02", "created_at": self.now.isoformat(), "evidence": evidence,
                  "plan": {"summary": "测试多只股票的累计仓位检查", "actions": [
                      {"symbol": s, "side": "buy", "quantity": 1900, "reason": "这是合成测试的操作原因，不用于真实投资", "invalidation": "价格偏离后失效"} for s in evidence]}}
        with self.paper.transaction() as (_, db):
            db.execute("INSERT INTO paper_decisions VALUES(?,?,?,?)", (record["id"], record["session"], "running", encode(record)))
        self.paper.commit_plan(record, evidence, self.paper.snapshot()["generation"])
        orders = self.paper.snapshot()["orders"]
        self.assertEqual(sum(o["status"] == "pending" for o in orders), 4)
        self.assertEqual(orders[-1]["status"], "rejected")
        self.paper.commit_plan(record, evidence, self.paper.snapshot()["generation"])
        self.assertEqual(len(self.paper.snapshot()["orders"]), 5)

    def test_missing_quotes_do_not_create_zero_price_or_postpone_order(self):
        self.prepare()
        self.proposal()
        self.paper.settle({}, "2026-11-03")
        s = self.paper.snapshot()
        self.assertEqual(s["cash_cents"], INITIAL)
        self.assertEqual(s["orders"][0]["status"], "expired")
        self.paper.settle({"002594": {"rows": [bar("2026-11-04")]}}, "2026-11-04")
        self.assertFalse(self.paper.snapshot()["positions"])

    def test_gap_and_zero_volume_reject_fills(self):
        for row in (bar("2026-11-03", 11), bar("2026-11-03", volume=0)):
            with self.paper.transaction() as (s, _):
                s["orders"] = []
            self.prepare()
            self.proposal()
            self.paper.settle({"002594": {"rows": [row]}}, "2026-11-03")
            self.assertEqual(self.paper.snapshot()["orders"][0]["status"], "expired")

    def test_corporate_action_and_missing_holding_prices_stop_valuation(self):
        self.prepare()
        self.proposal()
        self.paper.settle({"002594": {"rows": [bar("2026-11-03")]}}, "2026-11-03")
        self.assertFalse(self.paper.settle({}, "2026-11-04"))
        self.assertEqual(self.paper.snapshot()["last_session"], "2026-11-03")
        self.assertFalse(self.paper.settle({"002594": {"rows": [bar("2026-11-04", dividend=.2)]}}, "2026-11-04"))
        self.assertTrue(self.paper.snapshot()["blocked"])
        self.assertFalse(self.paper.snapshot()["enabled"])
        with self.assertRaises(ValueError):
            self.paper.enable(True)

    def test_a_t_plus_one_and_no_shorting(self):
        self.prepare()
        with self.paper.transaction() as (s, _):
            buy = {"symbol": "002594", "side": "buy", "quantity": 100, "lot": 100, "name": "测试", "decision_id": "d"}
            self.paper.fill(s, buy, bar("2026-11-03"), "2026-11-03")
            sell = {**buy, "side": "sell"}
            self.paper.fill(s, sell, bar("2026-11-03"), "2026-11-03")
            self.assertEqual(sell["status"], "rejected")
            self.paper.fill(s, sell, bar("2026-11-04"), "2026-11-04")
            self.assertEqual(sell["status"], "filled")
            self.assertEqual(sell["realized_cents"], -1100)
            self.assertFalse(s["positions"])
            again = {**sell}
            self.paper.fill(s, again, bar("2026-11-05"), "2026-11-05")
            self.assertEqual(again["status"], "rejected")

    def test_fractional_hk_price_keeps_integer_cent_portfolio_value(self):
        self.assertEqual(position_value({"quantity": 10000, "mark_price": .123}), 123000)
        self.assertEqual(cents(10.005), 1001)

    def test_lots_strict_schema_and_position_limit(self):
        self.assertTrue(quantity_ok("688008", 201, 200, "A"))
        self.assertFalse(quantity_ok("688008", 100, 200, "A"))
        self.assertFalse(quantity_ok("00700", 100, 500, "HK"))
        self.prepare()
        self.proposal(quantity=3000)
        self.assertEqual(self.paper.snapshot()["orders"][0]["status"], "rejected")
        with self.assertRaises(ValueError):
            Plan.model_validate({"summary": "测试结构校验", "actions": [{"symbol": "002594", "side": "buy", "quantity": True,
                "reason": "需要明确的数据支持这次操作", "invalidation": "观察后续行情变化"}]})

    def test_five_day_review_uses_fifth_following_session_and_is_once_only(self):
        self.prepare()
        record = self.proposal()
        self.paper.enable(False)
        days = calendar("A").sessions_in_range("2026-11-03", "2026-11-09")
        rows = [bar(d.date().isoformat(), 10 + i) for i, d in enumerate(days)]
        self.paper.settle({"002594": {"rows": rows[:-1]}}, "2026-11-06")
        self.assertFalse(self.paper.snapshot()["reviews"])
        self.paper.settle({"002594": {"rows": rows}}, "2026-11-09")
        reviews = self.paper.snapshot()["reviews"]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["date"], "2026-11-09")
        self.assertIn("+40.00%", reviews[0]["body"])
        self.paper.settle({"002594": {"rows": rows}}, "2026-11-09")
        self.assertEqual(len(self.paper.snapshot()["reviews"]), 1)
        self.assertEqual(self.paper.decision(record["id"])["evidence"]["002594"]["raw_close"], 10)

    def test_cycle_uses_tools_once_per_session_and_stale_data_never_calls_model(self):
        self.prepare()
        def agent(context, evidence, model):
            calls.append(context)
            return {"plan": {"summary": "当前证据不足，继续观察并等待", "actions": []}, "trace": [], "model": "test"}
        def fetch(symbol, mode, argument):
            days = calendar("A").sessions_in_range("2026-09-01", "2026-11-02")
            return {"rows": [bar(d.date().isoformat()) for d in days], "currency": "CNY", "source": "test"}
        calls = []
        self.paper.agent, self.paper.fetcher = agent, fetch
        self.paper.cycle()
        self.assertFalse(calls)
        self.services["A"].store.put_snapshot("002594", {"data_date": "2026-11-02", "complete": True}, self.now.isoformat())
        self.paper.cycle()
        self.assertEqual(len(calls), 1)
        self.paper.cycle()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.paper.snapshot()["decisions"][0]["plan"]["actions"], [])

    def test_api_market_scope_and_local_write_boundary(self):
        app = create_app(services=self.services, schedule=False)
        with TestClient(app) as client:
            a = client.get("/api/paper?market=A").json()
            hk = client.get("/api/paper?market=HK").json()
            self.assertEqual(a["currency"], "CNY")
            self.assertEqual(hk["currency"], "HKD")
            self.assertEqual(a["cash_cents"], INITIAL)
            self.assertEqual(client.put("/api/paper/control?market=A", json={"enabled": False}).status_code, 200)
            self.assertEqual(client.put("/api/paper/control", json={"enabled": False}, headers={"origin": "https://example.com"}).status_code, 403)
            self.assertEqual(client.get("/api/paper/decisions/missing?market=A").status_code, 404)


if __name__ == "__main__":
    unittest.main()
