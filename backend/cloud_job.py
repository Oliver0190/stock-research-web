"""One bounded Actions run. Only paper journals and stock names/codes leave the machine."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from backend.market import MARKETS, instrument, market_context
from backend.paper import INITIAL, PaperService
from backend.service import ResearchService
from backend.settings import database_path, load_config
from backend.store import Store, encode


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix('.tmp').write_text(encode(obj) + '\n', encoding='utf-8')
    path.with_suffix('.tmp').replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def watch_items(items, market):
    seen, clean = set(), []
    if not isinstance(items, list) or len(items) > 300:
        raise ValueError('Invalid watchlist')
    for i in items:
        s = instrument(i['symbol'], market)['symbol']
        if s in seen or not isinstance(i['name'], str) or not 0 < len(i['name']) <= 100:
            raise ValueError('Invalid watchlist entry')
        seen.add(s)
        clean.append({'symbol': s, 'name': i['name']})
    return clean


def record_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Invalid decision identifier')
    return value


def export_book(db, folder, market):
    account = json.loads(db.execute('SELECT payload FROM paper_account WHERE id=1').fetchone()[0])
    if account['market'] != market or account['currency'] != MARKETS[market]['currency'] or account['initial_cents'] != INITIAL:
        raise ValueError('Wrong paper account')
    ids = []
    for identifier, session, status, payload in db.execute('SELECT id,session,status,payload FROM paper_decisions ORDER BY rowid'):
        ids.append(record_id(identifier))
        write(folder / 'decisions' / (identifier + '.json'), {**json.loads(payload), 'session': session, 'status': status})
    write(folder / 'account.json', {'schema': 1, 'account': account, 'decisions': ids})


def restore_book(folder, paper):
    # Missing/corrupt journals are an error, never a reason to silently reset balances.
    manifest = read(folder / 'account.json')
    state = manifest['account']
    if (manifest['schema'] != 1 or state['market'] != paper.market or state['initial_cents'] != INITIAL
            or state['currency'] != MARKETS[paper.market]['currency'] or not isinstance(state['enabled'], bool)
            or not isinstance(state['cash_cents'], int) or state['cash_cents'] < 0):
        raise ValueError('Invalid paper checkpoint')
    with paper.store.connect() as db:
        db.execute('UPDATE paper_account SET payload=? WHERE id=1', (encode(state),))
        for identifier in manifest['decisions']:
            d = read(folder / 'decisions' / (record_id(identifier) + '.json'))
            if d['id'] != identifier:
                raise ValueError('Mismatched decision record')
            status = d.pop('status')
            db.execute('INSERT INTO paper_decisions VALUES(?,?,?,?)',
                (identifier, d['session'], 'interrupted' if status == 'running' else status, encode(d)))


def seed(root, market):
    folder = root / market
    if folder.exists():
        raise ValueError('Refusing to overwrite an existing cloud account')
    with sqlite3.connect(database_path(market).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        account = json.loads(db.execute('SELECT payload FROM paper_account WHERE id=1').fetchone()[0])
        if account['enabled'] or db.execute("SELECT 1 FROM paper_decisions WHERE status='running'").fetchone():
            raise ValueError('Pause the local account before migration')
        items = [json.loads(r[0]) for r in db.execute('SELECT payload FROM watchlist WHERE active=1 ORDER BY position')]
        export_book(db, folder, market)
        write(folder / 'watchlist.json', watch_items(items, market))


def run_market(root, market, operation, watchlist=None):
    folder = root / market
    items = watch_items(read(folder / 'watchlist.json') if watchlist is None else watchlist, market)
    with tempfile.TemporaryDirectory() as temp:
        research = ResearchService(Store(Path(temp) / 'session.sqlite3'), {**load_config(market), 'watchlist': items})
        paper = PaperService(research, mode='daily')
        try:
            restore_book(folder, paper)
            if operation == 'start':
                paper.enable(True, start_worker=False)
            if operation == 'pause':
                paper.enable(False)
            ctx = market_context(market=market)
            initial = paper.snapshot()
            done = any(d['session'] == ctx['expected_session'] and d['status'] == 'complete' for d in initial['decisions'])
            status = 'paused' if not initial['enabled'] else 'waiting-close'
            if operation in {'verify', 'sync'}:
                status = 'verified' if operation == 'verify' else 'watchlist-updated'
            elif initial['blocked']:
                status = 'corporate-action-blocked'
            elif initial['enabled'] and ctx['phase'] == 'closing' and done:
                status = 'already-complete'
            elif initial['enabled'] and ctx['phase'] == 'closing':
                if not os.environ.get('DEEPSEEK_API_KEY'):
                    status = 'missing-model-secret'
                else:
                    # Reuse daily analysis/news; cloud paper trading does not pay for an extra LLM article per stock.
                    stocks = research.watch_items()
                    stocks.update({s: {'symbol': s, 'name': p['name']} for s,p in initial['positions'].items() if s not in stocks})
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        futures = [pool.submit(research.refresh_one, item, ctx, write_ai_report=False) for item in stocks.values()]
                        for f in as_completed(futures):
                            try:
                                f.result()
                            except Exception:
                                pass  # Per-symbol coverage is checked again by the paper engine.
                    paper.cycle()
                    result = paper.snapshot()
                    status = 'complete' if any(d['session'] == ctx['expected_session'] and d['status'] == 'complete' for d in result['decisions']) else 'incomplete'
            with paper.store.connect() as db:
                export_book(db, folder, market)
            if watchlist is not None:
                write(folder / 'watchlist.json', items)
            view = paper.snapshot()
            view.update(cloud_status=status, cloud_updated_at=ctx['now'], cloud_watch_count=len(items))
            write(folder / 'view.json', view)
            # Private GitHub mobile/browser view, available even with the local website offline.
            latest = next((d for d in view['decisions'] if d.get('plan')), None)
            link = f"[最近决策及原始依据](decisions/{record_id(latest['id'])}.json)" if latest else '暂无决策'
            (folder / 'README.md').write_text(f"# {market} · 模拟账户\n\n{ctx['now']} · `{status}`\n\n"
                f"| 币种 | 总资产 | 可用现金 | 持仓数 | 最近估值日 |\n|---|---:|---:|---:|---|\n"
                f"| {view['currency']} | {view['equity_cents']/100:,.2f} | {view['cash_cents']/100:,.2f} | {len(view['positions'])} | {view['last_session'] or '—'} |\n\n"
                f"{link} · [持仓、订单与复盘](view.json) · [关注列表](watchlist.json)\n",encoding='utf-8')
            print(f'{market}: {status} ({ctx["today"]})', flush=True)
            return status not in {'missing-model-secret', 'incomplete', 'corporate-action-blocked'}
        finally:
            paper.close()
            research.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('operation', choices=['seed','import-seed','verify','run','start','pause','sync'])
    p.add_argument('state', type=Path)
    p.add_argument('--market', choices=['A','HK','both'], default='both')
    args = p.parse_args()
    markets = ('A','HK') if args.market == 'both' else (args.market,)
    update = os.environ.get('PAPER_WATCHLIST', '')
    if update and (len(markets) != 1 or args.operation != 'sync'):
        raise ValueError('Watchlist only accepted for a single-market sync')
    if args.operation == 'seed':
        for market in markets:
            seed(args.state, market)
            run_market(args.state, market, 'verify')
        return
    if args.operation == 'import-seed':
        # Import before the first web-server startup; never replace an existing account.
        targets = [database_path(m) for m in markets]
        if any(p.exists() for p in targets):
            raise ValueError('Target database already exists. Import requires an empty volume.')
        for m, target in zip(markets,targets):
            config = {**load_config(m),'watchlist':watch_items(read(args.state/m/'watchlist.json'),m)}
            with tempfile.TemporaryDirectory(dir=target.parent if target.parent.exists() else None) as tmp:
                built=Path(tmp)/'verified.sqlite3'
                research=ResearchService(Store(built),config)
                paper=PaperService(research)
                restore_book(args.state/m,paper)
                with research.store.connect() as db:
                    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                paper.close();research.close()
                target.parent.mkdir(parents=True,exist_ok=True)
                import shutil
                with target.open('xb') as dst, built.open('rb') as src: shutil.copyfileobj(src,dst)
        return
    with ThreadPoolExecutor(max_workers=len(markets)) as pool:
        jobs = [pool.submit(run_market, args.state, m, args.operation, json.loads(update) if update else None) for m in markets]
        results = [f.result() for f in jobs]
    # A data/model failure still has a valid journal. Persist it before marking CI red.
    if status_path := os.environ.get('PAPER_JOB_RESULT'):
        Path(status_path).write_text('ok' if all(results) else 'incomplete', encoding='ascii')
    elif not all(results):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
