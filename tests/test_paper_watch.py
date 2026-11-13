import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from backend.paper_watch import affordable_symbols, add_watch_review, historical_portfolio, pending_symbols, update_watch_prices


class WatchReviewTests(unittest.TestCase):
    def test_legacy_affordability_uses_fills_and_marks_known_at_decision(self):
        record = {'created_at':'2026-11-13T11:00:00+08:00','evidence':{'X':{'raw_close':15}}}
        order = {'created_at':'2026-11-13T10:00:00+08:00','fill_at':'2026-11-13T10:01:00+08:00',
                 'status':'filled','side':'buy','symbol':'X','quantity':100,'notional_cents':100000,'fee_cents':500}
        state = {'initial_cents':10000000,'orders':[order, {**order,'created_at':'2026-11-13T12:00:00+08:00'}]}
        self.assertEqual(historical_portfolio(state, record),
                         {'orders':[], 'positions':{'X':{'quantity':100,'mark_price':15}}, 'cash_cents':9899500})
        self.assertIsNone(historical_portfolio(state, {**record,'evidence':{}}))

    def test_review_is_per_inspected_affordable_stock_and_uses_exchange_days(self):
        state = {'cash_cents':10000000, 'positions':{}, 'orders':[{'symbol':'P','status':'pending'}]}
        evidence = {s:{'tradable':True, 'name':s, 'lot':100, 'raw_close':p}
                    for s,p in {'A':15,'B':415,'C':10,'D':15,'E':5,'P':12}.items()}
        record = {'id':'test', 'session':'2026-11-13','created_at':'2026-11-13T10:30:00+08:00',
                  'evidence':evidence,'trace':[{'tool':'read_stock','symbol':s} for s in 'AABCDEPP'],
                  'plan':{'actions':[{'symbol':'C','side':'buy'}],'summary':'暂时观察其余股票'}}
        record['trace'] = [t for t in record['trace'] if t['symbol'] != 'E']
        add_watch_review(state, record, affordable_symbols(state,evidence,'HK'), 'HK')
        review, = state['watch_reviews']
        a, d = review['stocks']
        self.assertEqual([a['symbol'],d['symbol']], ['A','D'])
        self.assertEqual(review['targets'], {'1':'2026-11-16','3':'2026-11-18','5':'2026-11-20'})
        @contextmanager
        def transaction():
            yield state, None
        paper = SimpleNamespace(market='HK', transaction=transaction)
        rows = [{'date':f'2026-11-{i}', 'close':16.5, 'split':0, 'dividend':0, 'volume':100}
                for i in range(16,21)]
        raw = {'A':{'rows':rows}, 'D':{'rows':[{**r, 'split':2 if i==1 else 0} for i,r in enumerate(rows)]}}
        update_watch_prices(paper, raw, '2026-11-16')
        self.assertEqual(a['outcomes']['1']['change_pct'],10.)
        self.assertNotIn('3', a['outcomes'])
        update_watch_prices(paper, {'D':raw['D']}, '2026-11-20')
        self.assertEqual(d['outcomes']['3']['status'],'corporate-action')
        self.assertEqual(pending_symbols(state,'2026-11-20'), {'A'})
        update_watch_prices(paper, raw, '2026-11-20')
        self.assertEqual(pending_symbols(state,'2026-11-20'), set())
        self.assertEqual(a['outcomes']['5']['change_pct'],10.)
