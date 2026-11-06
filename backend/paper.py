"""Local, daily paper accounts. No broker or real-money execution exists here.

All money is integer cents. Network/model calls happen outside SQLite transactions.
Decisions are frozen before the next eligible open; fills are accounted for only
once its daily bar is available. Corporate actions fail closed in this first version.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import partial
import json
import os
import threading
import uuid

from backend.market import calendar, market_context
from backend.paper_agent import Plan, propose
from backend.prices import normalize_frame
from backend.service import isolated_fetch
from backend.store import encode

INITIAL = 10_000_000
VERSION = "paper-v1"


def cents(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def position_value(position):
    return cents(Decimal(str(position["mark_price"])) * position["quantity"])


def fee(notional, market):
    return max(500, int((Decimal(notional) * (Decimal(".002") if market == "HK" else Decimal(".001")))
                       .quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def policy(market):
    return {"version": VERSION, "max_position_pct": 20, "max_exposure_pct": 80, "max_positions": 5,
            "fee_bps": 20 if market == "HK" else 10, "min_fee": 5,
            "slippage_bps": 10 if market == "HK" else 5, "max_open_gap_pct": 3,
            "execution": "下一交易日开盘价加滑点；收盘日线到齐后记账。订单只在该日有效。",
            "limitations": "费用为每边综合模拟成本，非券商实际报价。无盘口排队、冲击成本或实盘保证；分红、拆并股触发暂停核对。"}


def quantity_ok(symbol, quantity, lot, market):
    if market == "A" and symbol.startswith("688"):
        return quantity >= 200
    return lot > 0 and quantity % lot == 0


def validate_action(action, account, available, market):
    symbol, qty = action["symbol"], action["quantity"]
    data = available.get(symbol)
    if not data or not data.get("tradable"):
        return "资料或交易单位未核验，不能交易"
    if not quantity_ok(symbol, qty, data["lot"], market):
        return "数量不符合该股票的交易单位"
    position = account["positions"].get(symbol)
    if action["side"] == "sell":
        if not position or position["quantity"] < qty:
            return "可卖持仓不足，禁止做空"
        return None
    value = cents(Decimal(str(data["raw_close"])) * qty)
    holding = sum(position_value(p) for p in account["positions"].values())
    equity = account["cash_cents"] + holding
    current = position_value(position) if position else 0
    after_fees = equity - fee(value, market)
    if current + value > after_fees * .20:
        return "超过单只股票 20% 仓位上限"
    if holding + value > after_fees * .80:
        return "超过总仓位 80% 上限"
    if not position and len(account["positions"]) >= 5:
        return "持仓已达到 5 只上限"
    if value + fee(value, market) > account["cash_cents"]:
        return "可用虚拟现金不足"
    return None


class PaperService:
    def __init__(self, research, fetcher=None, agent=None, clock=None):
        self.research, self.store, self.market = research, research.store, research.market
        self.fetcher = fetcher or partial(isolated_fetch, market=self.market)
        self.agent = agent or propose
        self.clock = clock or (lambda: datetime.now(research.tz))
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.running = False
        now = self.clock().isoformat(timespec="seconds")
        initial = {"market": self.market, "currency": research.info["currency"], "initial_cents": INITIAL,
                   "cash_cents": INITIAL, "enabled": False, "generation": 0, "created_at": now,
                   "last_session": None, "positions": {}, "orders": [], "equity": [], "reviews": [],
                   "message": "账户已准备好。启动后，会在收盘数据齐备时研究当前关注列表。", "blocked": False}
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_decisions (id TEXT PRIMARY KEY, session TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS paper_decision_session ON paper_decisions(session);
            """)
            db.execute("INSERT OR IGNORE INTO paper_account VALUES(1,?)", (encode(initial),))
            # An interrupted model run never acquires orders on recovery. An explicit retry is allowed.
            db.execute("UPDATE paper_decisions SET status='interrupted' WHERE status='running'")

    @contextmanager
    def transaction(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT payload FROM paper_account WHERE id=1").fetchone()[0])
            yield state, db
            db.execute("UPDATE paper_account SET payload=? WHERE id=1", (encode(state),))

    def snapshot(self):
        with self.store.connect() as db:
            state = json.loads(db.execute("SELECT payload FROM paper_account WHERE id=1").fetchone()[0])
            decisions = [{**json.loads(r["payload"]), "status": r["status"]} for r in db.execute(
                "SELECT * FROM paper_decisions ORDER BY rowid DESC LIMIT 100")]
        for p in state["positions"].values():
            p["value_cents"] = position_value(p)
        exposure = sum(p["value_cents"] for p in state["positions"].values())
        equity = state["cash_cents"] + exposure
        peak, drawdown = INITIAL, 0
        for point in state["equity"]:
            peak = max(peak, point["value_cents"])
            drawdown = max(drawdown, (peak - point["value_cents"]) / peak * 100)
        # Large tool results stay durable in SQLite and are fetched only for a particular decision.
        for decision in decisions:
            decision.pop("evidence", None)
            decision["trace"] = [{k: t.get(k) for k in ("tool", "symbol")} for t in decision.get("trace", [])]
        return {**state, "orders": state["orders"][-100:], "reviews": state["reviews"][-100:],
                "decisions": decisions, "running": self.running, "policy": policy(self.market),
                "ai_available": bool(os.environ.get("DEEPSEEK_API_KEY")), "equity_cents": equity,
                "pnl_cents": equity - INITIAL, "return_pct": (equity / INITIAL - 1) * 100,
                "exposure_pct": exposure / equity * 100 if equity > 0 else 0, "max_drawdown_pct": drawdown}

    def decision(self, identifier):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM paper_decisions WHERE id=?", (identifier,)).fetchone()
        return {**json.loads(row["payload"]), "status": row["status"]} if row else None

    def enable(self, enabled):
        with self.transaction() as (state, _):
            if enabled and state["blocked"]:
                raise ValueError("账本存在公司行动待核对，请先处理记录中的问题")
            state["enabled"] = enabled
            state["generation"] += 1
            state["message"] = "已启动，等待收盘数据和研究资料。" if enabled else "已暂停决策，未成交订单已取消；仍可更新持仓估值。"
            if not enabled:
                for order in state["orders"]:
                    if order["status"] == "pending":
                        order.update(status="cancelled", result="用户暂停模拟账户")
        if enabled:
            self.start()

    def start(self):
        with self.lock:
            if self.running or self.stop.is_set():
                return False
            self.running = True
        threading.Thread(target=self._worker, daemon=True, name=f"paper-{self.market}").start()
        return True

    def _worker(self):
        try:
            self.cycle()
        except Exception:
            with self.transaction() as (state, _):
                state["message"] = "本轮未完成，账本已保留。可稍后重新运行。"
        finally:
            with self.lock:
                self.running = False

    def scheduler(self):
        attempted = None
        while not self.stop.is_set():
            try:
                ctx = market_context(self.clock(), self.market)
                slot = (ctx["today"], self.clock().hour, self.clock().minute // 10)
                if ctx["phase"] in {"closing", "holiday"} and slot != attempted and self.snapshot()["enabled"]:
                    attempted = slot
                    self.start()
            except Exception:
                pass
            self.stop.wait(30)

    def close(self):
        self.stop.set()

    def cycle(self):
        ctx = market_context(self.clock(), self.market)
        if ctx["phase"] not in {"closing", "holiday"}:
            with self.transaction() as (state, _):
                state["message"] = "等待收盘数据。第一版每天收盘后决策，不做盘中撮合。"
            return
        before = self.snapshot()
        if before["blocked"]:
            return
        session = ctx["expected_session"]
        if before["last_session"] == session and any(d["session"] == session and d["status"] in {"complete", "running"} for d in before["decisions"]):
            return
        watch = self.research.watch_items()
        symbols = set(watch) | set(before["positions"]) | {o["symbol"] for o in before["orders"] if o["status"] == "pending"}
        quotes, errors = {}, {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {pool.submit(self.fetcher, s, "paper", ""): s for s in symbols}
            for future in as_completed(jobs):
                symbol = jobs[future]
                try:
                    value = future.result()
                    normalize_frame(value["rows"])
                    if value.get("currency") != before["currency"]:
                        raise ValueError("币种不匹配")
                    value["rows"] = [r for r in value["rows"] if r["date"] <= session]
                    if not value["rows"] or value["rows"][-1]["date"] != session:
                        raise ValueError("未取得最新已完成交易日的成交行情")
                    quotes[symbol] = value
                except Exception:
                    errors[symbol] = "未取得已核验的最新成交行情"
        if not self.settle(quotes, session):
            return
        current = self.snapshot()
        if not current["enabled"] or self.stop.is_set():
            return
        # A successful or running plan is unique for a signal session, including a hold decision.
        if any(d["session"] == session and d["status"] in {"complete", "running"} for d in current["decisions"]):
            return
        if not os.environ.get("DEEPSEEK_API_KEY") and self.agent is propose:
            with self.transaction() as (state, _):
                state["message"] = "尚未配置模型密钥，已更新账本，没有生成交易。"
            return
        lots = {}
        if self.market == "HK":
            try:
                lots = self.fetcher("", "lots", "")["lots"]
            except Exception:
                pass
        evidence = {}
        for symbol in sorted(symbols):
            item = watch.get(symbol) or current["positions"].get(symbol, {})
            saved = self.store.snapshot(symbol, session)
            raw = quotes.get(symbol)
            lot = lots.get(symbol, 0) if self.market == "HK" else 200 if symbol.startswith("688") else 100
            fresh = bool(saved and saved.get("data_date") == session and saved.get("complete"))
            # Avoid unknown special A-share price-limit regimes in v1.
            special = self.market == "A" and ("ST" in item.get("name", "").upper() or symbol.startswith("689"))
            tradable = bool(raw and fresh and lot and len(raw["rows"]) >= 30 and not special)
            fund = (saved or {}).get("fundamentals") or {}
            evidence[symbol] = {"symbol": symbol, "name": item.get("name", symbol), "tradable": tradable,
                "lot": lot, "raw_close": raw["rows"][-1]["close"] if raw else None, "session": session,
                "price_source": raw.get("source") if raw else None, "research_fetched_at": (saved or {}).get("fetched_at"),
                "analysis": (saved or {}).get("analysis"), "changes": (saved or {}).get("changes"),
                "financials": fund.get("financials"), "news": (fund.get("news") or [])[:6],
                "availability": "资料齐备" if tradable else errors.get(symbol, "研究日期、交易单位或股票类型未通过核验")}
        if not any(v["tradable"] for v in evidence.values()):
            with self.transaction() as (state, _):
                state["message"] = "没有资料齐备的股票。请先更新关注列表行情；模拟账户不会使用旧数据下单。"
            return
        identifier, now = uuid.uuid4().hex, self.clock().isoformat(timespec="seconds")
        record = {"id": identifier, "session": session, "created_at": now, "version": VERSION,
                  "evidence": evidence, "trace": [], "plan": None}
        with self.transaction() as (state, db):
            if not state["enabled"] or state["generation"] != current["generation"]:
                return
            if db.execute("SELECT 1 FROM paper_decisions WHERE session=? AND status IN ('complete','running')", (session,)).fetchone():
                return
            db.execute("INSERT INTO paper_decisions VALUES(?,?,?,?)", (identifier, session, "running", encode(record)))
            state["message"] = "正在读取个股资料并形成模拟方案。"
        try:
            context = {"market": self.market, "currency": current["currency"], "cutoff": now, "session": session,
                       "cash": current["cash_cents"] / 100, "positions": current["positions"], "policy": policy(self.market),
                       "recent_reviews": current["reviews"][-5:],
                       "stocks": [{k: v[k] for k in ("symbol", "name", "tradable", "raw_close", "lot", "availability")} for v in evidence.values()]}
            answer = self.agent(context, evidence, self.research.config.get("llm", {}).get("model", "deepseek-chat"))
            plan = Plan.model_validate(answer["plan"]).model_dump()
            if len({a["symbol"] for a in plan["actions"]}) != len(plan["actions"]):
                raise ValueError("同一只股票不能重复下单")
            record.update(plan=plan, trace=answer.get("trace", []), model=answer.get("model", "unknown"))
            self.commit_plan(record, evidence, current["generation"])
        except Exception as error:
            record["error"] = str(error) if isinstance(error, ValueError) else "模型服务暂不可用，本轮未下单"
            with self.transaction() as (state, db):
                db.execute("UPDATE paper_decisions SET status='error',payload=? WHERE id=?", (encode(record), identifier))
                state["message"] = record["error"]

    def commit_plan(self, record, evidence, generation):
        # Decide the next open after the model FINISHES, never backdate to a passed opening.
        now = self.clock()
        cal = calendar(self.market)
        next_day = cal.date_to_session(now.date().isoformat(), direction="next")
        if cal.session_open(next_day).to_pydatetime() <= now:
            next_day = cal.next_session(next_day)
        eligible = next_day.date().isoformat()
        with self.transaction() as (state, db):
            existing = db.execute("SELECT status FROM paper_decisions WHERE id=?", (record["id"],)).fetchone()
            if not existing or existing[0] != "running":
                return
            if self.stop.is_set() or not state["enabled"] or state["generation"] != generation:
                db.execute("UPDATE paper_decisions SET status='cancelled',payload=? WHERE id=?", (encode(record), record["id"]))
                return
            # Reserve against a shadow account, so a batch cannot overcommit cash/exposure.
            shadow = json.loads(encode(state))
            for action in record["plan"]["actions"]:
                symbol, qty = action["symbol"], action["quantity"]
                error = validate_action(action, shadow, evidence, self.market)
                detail = evidence.get(symbol, {})
                order = {**action, "id": uuid.uuid4().hex, "decision_id": record["id"], "created_at": now.isoformat(),
                         "eligible_session": eligible, "name": detail.get("name", symbol),
                         "reference": detail.get("raw_close"), "lot": detail.get("lot", 0),
                         "status": "rejected" if error else "pending", "result": error or "等待下一交易日开盘日线"}
                state["orders"].append(order)
                if not error:
                    value = cents(Decimal(str(detail["raw_close"])) * qty)
                    if action["side"] == "buy":
                        shadow["cash_cents"] -= value + fee(value, self.market)
                        p = shadow["positions"].setdefault(symbol, {"quantity": 0, "mark_price": detail["raw_close"]})
                        p["quantity"] += qty
                    # Do not pre-spend expected sell proceeds before a sale actually fills.
            db.execute("UPDATE paper_decisions SET status='complete',payload=? WHERE id=?", (encode(record), record["id"]))
            state["message"] = "本轮方案已保存，等待下一交易日。" if record["plan"]["actions"] else "本轮选择继续观察，已保存判断依据。"

    def settle(self, quotes, through):
        with self.transaction() as (state, db):
            if state["blocked"]:
                return False
            if state["last_session"] is None:
                state["last_session"] = through
                state["equity"].append({"date": self.clock().date().isoformat(), "value_cents": INITIAL})
                return True
            if state["last_session"] >= through:
                return True
            cal = calendar(self.market)
            days = cal.sessions_in_range(cal.next_session(state["last_session"]), through)
            lookup = {s: {r["date"]: r for r in q["rows"]} for s, q in quotes.items()}
            for session in days:
                day = session.date().isoformat()
                pending = [o for o in state["orders"] if o["status"] == "pending" and o["eligible_session"] <= day]
                # Do not fabricate marks for missing/suspended holdings or skip missing action history.
                if any(day not in lookup.get(s, {}) for s in state["positions"]):
                    state["message"] = f"{day} 持仓行情不完整，估值停留在上次成功日期。"
                    return False
                if any(lookup[s][day].get("dividend", 0) or lookup[s][day].get("split", 0) for s in state["positions"]):
                    state.update(blocked=True, enabled=False, message=f"{day} 检测到持仓分红或拆并股，已暂停，账本等待公司行动核对。")
                    for order in state["orders"]:
                        if order["status"] == "pending":
                            order.update(status="cancelled", result="公司行动待核对")
                    return False
                # Risk checks use opening marks, never the same day's close.
                for symbol, p in state["positions"].items():
                    p["mark_price"] = lookup[symbol][day]["open"]
                for order in pending:
                    row = lookup.get(order["symbol"], {}).get(day)
                    error = None
                    if order["eligible_session"] != day or not row:
                        error = "指定交易日行情缺失，订单到期；不延后补成交"
                    elif row.get("dividend", 0) or row.get("split", 0) or any(
                        r.get("split", 0) for r in lookup[order["symbol"]].values() if r["date"] > day):
                        error = "公司行动影响价格口径，未模拟成交"
                    elif row["volume"] <= 0 or row["high"] == row["low"]:
                        error = "缺少可成交证据（停牌或单一价格日）"
                    elif abs(row["open"] / order["reference"] - 1) > .03:
                        error = "开盘相对决策参考价偏离超过 3%，订单到期"
                    if not error:
                        self.fill(state, order, row, day)
                    else:
                        order.update(status="expired", result=error)
                for symbol, p in state["positions"].items():
                    p.update(mark_price=lookup[symbol][day]["close"], mark_date=day)
                state["last_session"] = day
                equity = state["cash_cents"] + sum(position_value(p) for p in state["positions"].values())
                point = {"date": day, "value_cents": equity}
                if state["equity"] and state["equity"][-1]["date"] == day:
                    state["equity"][-1] = point
                else:
                    state["equity"].append(point)
            self.review(state, db, lookup, through)
            state["message"] = f"账本已更新至 {through}。"
            return True

    def fill(self, state, order, row, day):
        symbol, qty, side = order["symbol"], order["quantity"], order["side"]
        slip = Decimal(".001") if self.market == "HK" else Decimal(".0005")
        price = Decimal(str(row["open"])) * (1 + slip if side == "buy" else 1 - slip)
        price = price.quantize(Decimal(".0001"), rounding=ROUND_HALF_UP)
        value, costs = cents(price * qty), fee(cents(price * qty), self.market)
        available = {symbol: {"tradable": True, "raw_close": float(price), "lot": order["lot"]}}
        error = validate_action(order, state, available, self.market)
        position = state["positions"].get(symbol)
        if side == "sell" and self.market == "A" and position and position["opened_on"] >= day:
            error = "A 股当日新买入持仓不能卖出"
        if error:
            order.update(status="rejected", result=error)
            return
        realized = None
        if side == "buy":
            state["cash_cents"] -= value + costs
            position = state["positions"].setdefault(symbol, {"name": order["name"], "quantity": 0,
                "cost_cents": 0, "opened_on": day, "mark_price": row["open"], "mark_date": day})
            position["quantity"] += qty
            position["cost_cents"] += value + costs
        else:
            allocated = position["cost_cents"] if qty == position["quantity"] else round(position["cost_cents"] * qty / position["quantity"])
            state["cash_cents"] += value - costs
            position["quantity"] -= qty
            position["cost_cents"] -= allocated
            realized = value - costs - allocated
            if not position["quantity"]:
                del state["positions"][symbol]
        order.update(status="filled", fill_date=day, fill_price=float(price), fee_cents=costs,
                     notional_cents=value, realized_cents=realized, result="按预设日线模型模拟成交")
        if realized is not None:
            state["reviews"].append({"decision_id": order["decision_id"], "date": day, "symbol": symbol,
                "title": "已实现交易复盘", "body": f"本次卖出扣除分摊成本和模拟费用后，盈亏 {realized / 100:.2f}。"
                "盈亏只是结果，尚不能确定判断失误或归因。应核对原始论点、触发条件和执行成本。",
                "status": "待验证", "suggestion": "对照原始依据与同类样本，验证后再决定是否修改策略。"})

    def review(self, state, db, lookup, through):
        reviewed = {r["decision_id"] for r in state["reviews"] if r.get("horizon") == 5}
        cal = calendar(self.market)
        for row in db.execute("SELECT * FROM paper_decisions WHERE status='complete'"):
            record = json.loads(row["payload"])
            if record["id"] in reviewed or len(cal.sessions_in_range(record["session"], through)) < 6:
                continue
            end = cal.sessions_window(record["session"], 6)[-1].date().isoformat()
            facts = []
            for action in record["plan"]["actions"]:
                symbol = action["symbol"]
                bar = lookup.get(symbol, {}).get(end)
                ref = record["evidence"].get(symbol, {}).get("raw_close")
                actions = any(r.get("split") or r.get("dividend") for d, r in lookup.get(symbol, {}).items() if record["session"] < d <= end)
                if bar and ref and not actions:
                    change = (bar["close"] / ref - 1) * 100
                    facts.append(f"{symbol} 决策后 5 个交易日价格变化 {change:+.2f}%")
                else:
                    facts.append(f"{symbol} 对照行情不足或存在公司行动，暂不能评价")
            state["reviews"].append({"decision_id": record["id"], "date": end, "horizon": 5,
                "title": "五日观察复盘", "body": "；".join(facts) if facts else "该轮选择继续观察。未产生模拟交易，不能计算交易胜负。",
                "status": "待验证", "suggestion": "价格变化不等于交易收益或错误原因。结合成交记录核验原论点；策略版本保持不变。"})
