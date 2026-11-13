"""Price follow-up for affordable stocks the agent inspected and left alone."""
import json
import math
from datetime import datetime

from backend.market import calendar
from backend.paper import validate_action


HORIZONS = (1, 3, 5)


def affordable_symbols(state, evidence, market):
    pending = {o['symbol'] for o in state['orders'] if o['status'] == 'pending'}
    return {symbol for symbol, stock in evidence.items()
            if stock.get('lot', 0) > 0 and stock.get('raw_close', 0) > 0 and symbol not in pending
            and not validate_action({'symbol': symbol, 'side': 'buy', 'quantity': stock['lot']},
                                    state, evidence, market)}


def add_watch_review(state, record, affordable, market):
    inspected = {t.get('symbol') for t in record['trace'] if t.get('tool') == 'read_stock'}
    proposed = {a['symbol'] for a in record['plan']['actions']}
    selected = sorted((inspected & affordable) - proposed)
    if not selected:
        return
    sessions = calendar(market).sessions_window(record['session'], 6)
    targets = {str(h): sessions[h].date().isoformat() for h in HORIZONS}
    candidates = []
    for symbol in selected:
        stock = record['evidence'][symbol]
        candidates.append({'symbol': symbol, 'name': stock['name'], 'reference': stock['raw_close'],
                           'lot': stock['lot'], 'quote_at': stock.get('quote_at'), 'outcomes': {}})
    state.setdefault('watch_reviews', []).append({
        'decision_id': record['id'], 'session': record['session'], 'created_at': record['created_at'],
        'summary': record['plan']['summary'], 'targets': targets, 'stocks': candidates})


def historical_portfolio(state, record):
    """Replay recorded fills through the cutoff, valuing holdings only at saved quotes."""
    cutoff = datetime.fromisoformat(record['created_at'])
    cash, quantities = state['initial_cents'], {}
    for order in state['orders']:
        if datetime.fromisoformat(order['created_at']) >= cutoff:
            continue
        fill_at = order.get('fill_at')
        if fill_at and datetime.fromisoformat(fill_at) <= cutoff and order['status'] == 'filled':
            side = 1 if order['side'] == 'buy' else -1
            symbol = order['symbol']
            quantities[symbol] = quantities.get(symbol, 0) + side * order['quantity']
            cash -= side * order['notional_cents'] + order['fee_cents']
        elif order['status'] == 'filled' and not fill_at:
            return None
        elif not order.get('expires_at') or cutoff <= datetime.fromisoformat(order['expires_at']):
            return None  # No precise time at which the earlier order stopped being pending.
    positions = {}
    for symbol, quantity in quantities.items():
        if quantity:
            stock = record['evidence'].get(symbol)
            if not stock or not stock.get('raw_close') or quantity < 0:
                return None
            positions[symbol] = {'quantity': quantity, 'mark_price': stock['raw_close']}
    return {'cash_cents': cash, 'positions': positions, 'orders': []}


def upgrade_history(state, db, market):
    if state.get('watch_review_version') == 1:
        return
    rows = db.execute("SELECT payload FROM paper_decisions WHERE status='complete' ORDER BY rowid")
    live = state.get('live', {})
    today = []
    for row in rows:
        record = json.loads(row['payload'])
        if record.get('execution') != 'intraday':
            continue
        if record['session'] == live.get('day'):
            today.append(record)
        portfolio = historical_portfolio(state, record)
        if portfolio is not None:
            add_watch_review(state, record, affordable_symbols(portfolio, record['evidence'], market), market)
    if live:
        if len(today) != live.get('attempts'):
            # An older runner used failed attempts as the signal baseline.
            last = today[-1]['evidence'] if today else {}
            live['baseline'] = {s: d['raw_close'] for s, d in last.items()}
        live['attempts'] = len(today)
    state['watch_review_version'] = 1


def pending_symbols(state, through):
    return {stock['symbol'] for review in state.get('watch_reviews', [])
            for stock in review['stocks'] for horizon, target in review['targets'].items()
            if target <= through and stock['outcomes'].get(horizon, {}).get('status')
            not in {'priced', 'corporate-action'}}


def update_watch_prices(paper, quotes, through):
    with paper.transaction() as (state, _):
        for review in state.get('watch_reviews', []):
            for stock in review['stocks']:
                series = quotes.get(stock['symbol'], {})
                prices = {r['date']: r for r in series.get('rows', []) if r['date'] <= through}
                for horizon, target in review['targets'].items():
                    outcomes = stock['outcomes']
                    if target > through or outcomes.get(horizon, {}).get('status') in {'priced', 'corporate-action'}:
                        continue
                    days = calendar(paper.market).sessions_in_range(review['session'], target)[1:]
                    bars = [prices.get(d.date().isoformat()) for d in days]
                    if any(r and (r.get('split') or r.get('dividend')) for r in bars):
                        outcomes[horizon] = {'status': 'corporate-action'}
                    elif any(not r or 'split' not in r or 'dividend' not in r for r in bars):
                        outcomes[horizon] = {'status': 'missing'}
                    elif not math.isfinite(bars[-1]['close']) or bars[-1]['close'] <= 0 or bars[-1]['volume'] <= 0:
                        outcomes[horizon] = {'status': 'missing'}
                    else:
                        close = bars[-1]['close']
                        outcomes[horizon] = {'status': 'priced', 'close': close, 'source': series.get('source'),
                                             'change_pct': round(100 * (close / stock['reference'] - 1), 4)}
