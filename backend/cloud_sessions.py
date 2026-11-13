"""Run a bounded forward test against new market observations, with a private Git journal."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import pandas as pd

from backend.cloud_job import export_book, read, restore_book, watch_items, write
from backend.live_quotes import fetch_quotes
from backend.market import HK, calendar, instrument, market_context
from backend.paper import PaperService
from backend.service import ResearchService
from backend.settings import load_config
from backend.store import Store


def session_window(now, market, window):
    """Watch windows in exchange time. market_context separately gates trading."""
    cal = calendar(market)
    day = now.astimezone(HK).date().isoformat()
    if not cal.is_session(day):
        return None
    opening = cal.session_open(day).to_pydatetime()
    close = cal.session_close(day).to_pydatetime()
    lunch = cal.session_break_start(day)
    extra = timedelta(minutes=30 if market == 'A' else 0)
    if window == 'morning':
        return opening - extra, lunch.to_pydatetime() + extra if pd.notna(lunch) else close
    afternoon = cal.session_break_end(day)
    if window == 'afternoon' and pd.notna(afternoon):
        return afternoon.to_pydatetime(), close + extra
    return None


def scheduled_window(now, market):
    """Dispatch by the runner's actual start, including delayed or queued cron events."""
    for window in ('morning', 'afternoon'):
        interval = session_window(now, market, window)
        if interval and interval[0] - timedelta(hours=2) <= now < interval[1]:
            return window
    return None


def observe_outside_trading(paper, ctx):
    """Sample during the requested shoulders without passing quotes to the order engine."""
    symbols = set(paper.research.watch_items()) | set(paper.snapshot()['positions'])
    now = paper.clock()
    quotes = fetch_quotes(sorted(symbols), paper.market, now)
    label = {'morning': '盘前观察', 'break': '午间观察', 'settling': '盘后观察'}.get(ctx['phase'], ctx['label'])
    return f'{label} · {len(quotes)}/{len(symbols)} 只有新报价'


class Journal:
    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.last_pull = float('-inf')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, timeout=35,
                                       stderr=subprocess.PIPE, text=True).strip()

    def control(self, market):
        with self.lock:
            if time.monotonic() - self.last_pull >= 15:
                self.git('pull', '--ff-only', '--quiet')
                self.last_pull = time.monotonic()
            return read(self.root / 'control' / f'{market}.json')

    def save(self, paper, status, research_cache=None, *, sampled_at=None):
        market = paper.market
        folder = self.root / 'state' / market
        with self.lock:
            with paper.store.connect() as db:
                db.execute('BEGIN')
                export_book(db, folder, market)
            if research_cache is not None:
                write(folder / 'research.json', research_cache)
            view = paper.snapshot()
            now = paper.clock().isoformat(timespec='seconds')
            items = watch_items(list(paper.research.watch_items().values()), market)
            write(folder / 'watchlist.json', items)
            view.update(cloud_status=status, cloud_updated_at=now, cloud_sampled_at=sampled_at, cloud_watch_count=len(items),
                        cloud_mode='sessions')
            write(folder / 'view.json', view)
            latest = next((d for d in view['decisions'] if d.get('plan')), None)
            decision = f"[最近决策](decisions/{latest['id']}.json)" if latest else '暂无决策'
            (folder / 'README.md').write_text(
                f"# {market} 模拟账户\n\n{now} · {status}\n\n"
                f"| 币种 | 总资产 | 可用现金 | 持仓数 |\n|---|---:|---:|---:|\n"
                f"| {view['currency']} | {view['equity_cents']/100:,.2f} | {view['cash_cents']/100:,.2f} | {len(view['positions'])} |\n\n"
                f"{decision} · [持仓、订单与复盘](view.json) · [关注列表](watchlist.json)\n", encoding='utf-8')
            self.git('add', '--', 'state/' + market)
            if not self.git('diff', '--cached', '--name-only'):
                return
            self.git('commit', '-qm', f'Checkpoint {market} {now}')
            # Website controls live in different files. Rebase only over control edits.
            for attempt in range(3):
                self.git('fetch', '-q', 'origin', 'main')
                base = self.git('merge-base', 'HEAD', 'origin/main')
                changed = self.git('diff', '--name-only', base, 'origin/main').splitlines()
                if any(not path.startswith('control/') for path in changed):
                    raise RuntimeError('Unexpected second journal writer')
                self.git('rebase', 'origin/main')
                try:
                    self.git('push', '-q', 'origin', 'HEAD:main')
                    return
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        raise


def apply_control(paper, control):
    if type(control.get('enabled')) is not bool:
        raise ValueError('Invalid control')
    state = paper.snapshot()
    if not state['blocked'] and state['enabled'] != control['enabled']:
        paper.enable(control['enabled'], start_worker=False)
    items = watch_items(control['watchlist'], paper.market)
    with paper.research.lock:
        paper.research.items = {i['symbol']: {**i, **instrument(i['symbol'], paper.market)} for i in items}


def prepare_research(paper, ctx, folder):
    research = paper.research
    items = research.watch_items()
    for symbol, position in paper.snapshot()['positions'].items():
        items.setdefault(symbol, {'symbol': symbol, 'name': position['name']})
    cache_path = folder / 'research.json'
    cached = read(cache_path) if cache_path.exists() else {}
    missing = []
    for symbol, item in items.items():
        snapshot = cached.get(symbol, {})
        if snapshot.get('complete') and snapshot.get('data_date') == ctx['expected_session']:
            research.store.put_snapshot(symbol, snapshot, ctx['now'])
        else:
            missing.append(item)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(research.refresh_one, item, ctx, write_ai_report=False) for item in missing]
        for job in as_completed(jobs):
            try:
                job.result()
            except (ValueError, RuntimeError):
                continue
    result = {}
    for symbol in items:
        snapshot = research.store.snapshot(symbol, ctx['expected_session'])
        if snapshot and snapshot['data_date'] == ctx['expected_session'] and snapshot['complete']:
            result[symbol] = {key: snapshot.get(key) for key in (
                'data_date','complete','market','currency','source','fetched_at','analysis','changes','fundamentals')}
    return result


def changes(view):
    return (len(view['decisions']), [(o['id'], o['status']) for o in view['orders']],
            view['cash_cents'], view['enabled'], view['blocked'], view['generation'])


def verify_sources(paper, cached, ctx):
    # Closed-market quotes are diagnostic only. Trading always uses the default 90-second limit.
    samples = list(cached)[:3]
    if not samples:
        return not paper.research.watch_items()
    quotes = fetch_quotes(samples, paper.market, paper.clock(), max_age=14*86400)
    raw = paper.live_sessions.raw(samples)
    lots = paper.fetcher('', 'lots', '')['lots'] if paper.market == 'HK' else dict.fromkeys(samples, 100)
    usable = [symbol for symbol in samples if symbol in raw and symbol in quotes and lots.get(symbol)
              and any(row['date'] == ctx['expected_session'] for row in raw[symbol]['rows'])
              and quotes[symbol]['at'][:10] >= ctx['expected_session']]
    print(f'{paper.market} feed probe: quotes={len(quotes)}, history={len(raw)}, usable={len(usable)}/{len(samples)}', flush=True)
    return bool(usable)


def run_market(journal, market, window, temp):
    folder = journal.root / 'state' / market
    initial = journal.control(market)
    config = {**load_config(market), 'watchlist': watch_items(initial['watchlist'], market)}
    research = ResearchService(Store(temp / f'{market}.db'), config)
    paper = PaperService(research, mode='intraday')
    try:
        restore_book(folder, paper)
        apply_control(paper, initial)
        now = paper.clock()
        ctx = market_context(now, market)
        if not paper.snapshot()['enabled']:
            journal.save(paper, '已暂停')
            return
        if window == 'scheduled':
            window = scheduled_window(now, market)
            if window is None and ctx['phase'] == 'closing' and paper.snapshot()['last_session'] != ctx['expected_session']:
                window = 'close'
            print(f'{market} scheduled at {ctx["now"]}: {window or "outside session"}', flush=True)
            if window is None:
                return
        elif window == 'auto':
            window = scheduled_window(now, market) or 'check'
        if window in {'check','close'}:
            if window == 'close' and ctx['phase'] != 'closing':
                journal.save(paper, '等待下一交易时段')
                return
            cached = prepare_research(paper, ctx, folder)
            result = paper.live_sessions.tick(ctx) if window == 'close' else 'checked'
            if window == 'check' and not verify_sources(paper, cached, ctx):
                result = 'incomplete'
            journal.save(paper, '收盘已记账' if result in {'settled','already-complete'} else
                         '云端已连接，等待交易时段' if result == 'checked' else result, cached)
            print(f'{market} research {len(cached)}/{len(config["watchlist"])}; {result}', flush=True)
            if (config['watchlist'] and not cached) or result in {'incomplete', 'blocked'}:
                raise RuntimeError('Market data incomplete')
            return
        interval = session_window(now, market, window)
        if not interval or now >= interval[1] or now < interval[0]-timedelta(hours=2):
            return
        cached = prepare_research(paper, ctx, folder)
        journal.save(paper, '盘中任务已就绪', cached)
        last_save = float('-inf')
        retry = time.monotonic() + 600
        previous = changes(paper.snapshot())
        while paper.clock() < interval[1]:
            started = time.monotonic()
            control = journal.control(market)
            apply_control(paper, control)
            if not paper.snapshot()['enabled']:
                journal.save(paper, '已暂停')
                return
            now = paper.clock()
            if now < interval[0]:
                time.sleep(min(60, (interval[0] - now).total_seconds()))
                continue
            if now >= interval[1]:
                break
            ctx = market_context(now, market)
            if started >= retry and set(research.watch_items()) - cached.keys():
                cached = prepare_research(paper, ctx, folder)
                retry = time.monotonic() + 600
                continue  # Recheck time after network calls.
            sampled_at = None
            try:
                if ctx['phase'] == 'intraday':
                    result = paper.live_sessions.tick(ctx)
                    observed = (paper.snapshot().get('live') or {}).get('observed_at')
                    if observed and datetime.fromisoformat(observed) >= now:
                        sampled_at = observed
                else:
                    result = observe_outside_trading(paper, ctx)
                    sampled_at = now.isoformat(timespec='seconds')
            except (RuntimeError, ValueError, OSError):
                result = '行情源暂不可用'
            current = changes(paper.snapshot())
            if current != previous or started - last_save >= 300:
                journal.save(paper, result, sampled_at=sampled_at)
                last_save, previous = started, current
            remaining = (interval[1] - paper.clock()).total_seconds()
            time.sleep(max(0, min(remaining, 60 - (time.monotonic()-started))))
        journal.save(paper, '本交易时段结束')
    finally:
        paper.close()
        research.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('window', choices=['scheduled','morning','afternoon','close','check','auto'])
    parser.add_argument('journal', type=Path)
    args = parser.parse_args()
    journal = Journal(args.journal.resolve())
    if args.window == 'check':
        from backend.llm import _client
        try:
            with _client() as client:
                client.models.list()
        except Exception:
            raise RuntimeError('Model API authentication or connectivity check failed') from None
    journal.git('config','user.name','paper-session')
    journal.git('config','user.email','paper-session@users.noreply.github.com')
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(run_market, journal, market, args.window, Path(tmp)) for market in ('A','HK')]
        for job in jobs:
            job.result()


if __name__ == '__main__':
    main()
