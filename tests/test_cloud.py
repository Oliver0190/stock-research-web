import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.cloud_client import CloudError, CloudPaper
from backend.cloud_job import export_book, read, restore_book, run_market, watch_items, write
from backend.live_quotes import parse_quotes
from backend.market import HK
from backend.paper import PaperService
from backend.paper_live import live_tick
from backend.cloud_sessions import session_window, scheduled_window, apply_control, observe_outside_trading
from backend.service import ResearchService
from backend.store import Store, encode


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paper = self.paper_at('first.db')

    def paper_at(self, name):
        r = ResearchService(Store(self.root / name), {'market':'A','watchlist':[],'llm':{'model':'test'}})
        self.addCleanup(r.close)
        return PaperService(r)

    def test_durable_roundtrip_excludes_research_notes_and_preserves_history(self):
        key = 'a'*32
        self.paper.store.save_note('000001','DO_NOT_EXPORT','now')
        with self.paper.transaction() as (s, db):
            s['cash_cents'] -= 1234
            db.execute('INSERT INTO paper_decisions VALUES(?,?,?,?)',(key,'2026-11-09','complete',encode({'id':key,'session':'2026-11-09','plan':{'actions':[]},'trace':[]})))
        folder = self.root / 'state' / 'A'
        with self.paper.store.connect() as db:
            export_book(db, folder, 'A')
        write(folder/'watchlist.json',[])
        self.assertTrue(run_market(self.root/'state','A','verify'))
        restored = self.paper_at('second.db')
        restore_book(folder, restored)
        self.assertEqual(restored.snapshot()['cash_cents'], 10_000_000-1234)
        self.assertEqual(restored.decision(key)['status'],'complete')
        self.assertFalse(any('DO_NOT_EXPORT' in p.read_text(encoding='utf-8') for p in folder.rglob('*.json')))

    def test_missing_or_wrong_market_does_not_reset_account(self):
        with self.assertRaises(FileNotFoundError):
            restore_book(self.root/'lost',self.paper)
        folder=self.root/'A'
        with self.paper.store.connect() as db:
            export_book(db, folder,'A')
        manifest=read(folder/'account.json'); manifest['account']['currency']='HKD'; write(folder/'account.json',manifest)
        with self.assertRaises(ValueError):
            restore_book(folder,self.paper)

    def test_cloud_unavailable_never_starts_local_account(self):
        cloud=CloudPaper('owner/private-journal','A',self.root)
        with patch.object(cloud,'call',side_effect=CloudError('offline')):
            with self.assertRaises(CloudError): cloud.snapshot()
        cloud.dir.mkdir(parents=True)
        write(cloud.dir/'view.json',{'market':'A','cloud_updated_at':'2026-11-09'})
        with patch.object(cloud,'call',side_effect=CloudError('offline')):
            self.assertTrue(cloud.snapshot()['cloud_stale'])
            with self.assertRaises(CloudError): cloud.dispatch('start')

    def test_watchlist_only_exports_names_and_matching_codes(self):
        self.assertEqual(watch_items([{'symbol':'00700','name':'腾讯','note':'private'}],'HK'), [{'symbol':'00700','name':'腾讯'}])
        with self.assertRaises(ValueError):watch_items([{'symbol':'00700','name':'wrong'}],'A')

    def test_separate_sessions_and_persistent_pause(self):
        now = datetime(2026,11,10,10,30,tzinfo=HK)
        self.assertEqual(session_window(now,'HK','morning')[1].astimezone(HK).hour, 12)
        for market, window, expected in (
            ('A', 'morning', ('09:00', '12:00')), ('A', 'afternoon', ('13:00', '15:30')),
            ('HK', 'morning', ('09:30', '12:00')), ('HK', 'afternoon', ('13:00', '16:00')),
        ):
            bounds = session_window(now, market, window)
            self.assertEqual(tuple(t.astimezone(HK).strftime('%H:%M') for t in bounds), expected)
        half_day = datetime(2026,12,24,10,tzinfo=HK)
        self.assertIsNone(session_window(half_day,'HK','afternoon'))
        self.assertEqual(session_window(half_day,'HK','morning')[1].astimezone(HK).hour,12)
        self.assertEqual(session_window(now,'HK','afternoon')[0].astimezone(HK).hour, 13)
        self.assertIsNone(session_window(now.replace(day=15),'A','morning'))
        controls = {'enabled':True,'watchlist':[{'symbol':'000001','name':'样本'}]}
        apply_control(self.paper,controls)
        generation = self.paper.snapshot()['generation']
        apply_control(self.paper,controls)
        self.assertEqual(self.paper.snapshot()['generation'],generation)
        apply_control(self.paper,{**controls,'enabled':False})
        self.assertFalse(self.paper.snapshot()['enabled'])

    def test_delayed_cron_uses_current_watch_window(self):
        now = datetime(2026, 11, 12, 15, 0, tzinfo=HK)  # Yesterday's morning cron arrived at 15:00.
        for market in ('A', 'HK'):
            self.assertEqual(scheduled_window(now, market), 'afternoon')
            self.assertEqual(scheduled_window(now.replace(hour=8), market), 'morning')
            self.assertEqual(scheduled_window(now.replace(hour=12), market), 'afternoon')
            self.assertIsNone(scheduled_window(now.replace(hour=2), market))
            self.assertIsNone(scheduled_window(now.replace(day=14), market))
        self.assertIsNone(scheduled_window(now.replace(minute=30), 'A'))
        self.assertEqual(scheduled_window(now.replace(minute=30), 'HK'), 'afternoon')
        self.assertIsNone(scheduled_window(now.replace(month=12, day=24), 'HK'))

    def test_a_observation_outside_trading_leaves_the_account_untouched(self):
        from backend.market import market_context
        for hour, minute, label in [(9, 15, '盘前'), (11, 45, '午间'), (15, 15, '盘后')]:
            now = datetime(2026, 11, 10, hour, minute, tzinfo=HK)
            self.paper.clock = lambda: now
            before = self.paper.snapshot()
            with patch('backend.cloud_sessions.fetch_quotes', return_value={}) as fetch:
                self.assertIn(label, observe_outside_trading(self.paper, market_context(now, 'A')))
                fetch.assert_called_once()
            self.assertEqual(self.paper.snapshot(), before)

    def test_live_quote_must_match_symbol_and_have_a_recent_timestamp(self):
        now=datetime(2026,11,10,10,0,30,tzinfo=HK)
        fields=['0']*78;fields[75]='HKD'
        for i,v in {1:'样本',2:'00700',3:'101',4:'100',6:'5000',30:'2026/11/10 10:00:12'}.items(): fields[i]=v
        keys={'r_hk00700':'00700'}
        def parse(): return parse_quotes('v_r_hk00700="'+'~'.join(fields)+'";',keys,now)
        self.assertEqual(parse()['00700']['price'],101)
        fields[30]='2026/11/10 09:45:12';self.assertFalse(parse())
        fields[30]='2026/11/10 10:00:40';self.assertFalse(parse())
        fields[30]='2026/11/10 10:00:10';fields[2]='00001';self.assertFalse(parse())

    def test_live_decision_needs_later_quote_and_new_volume_and_does_not_run_at_lunch(self):
        p=self.paper
        now=[datetime(2026,11,10,10,0,tzinfo=HK)]
        p.clock=lambda:now[0]
        sym='000001';p.research.items[sym]={'name':'样本','symbol':sym}
        p.store.put_snapshot(sym,{'data_date':'2026-11-09','complete':True},now[0].isoformat())
        p.enable(True,start_worker=False)
        calls=[]
        def agent(context,evidence,model):
            calls.append(context)
            return {'model':model,'trace':[],'plan':{'summary':'合成报价用于验证模拟撮合', 'actions':[{'symbol':sym,'side':'buy','quantity':100,
                'reason':'仅用于验证交易决策和后续报价的时间顺序','invalidation':'后续报价偏离则放弃本次模拟'}]}}
        p.agent=agent
        volume=[1000];at=[now[0]]
        change=[1]
        def quotes(*args):return {sym:{'name':'样本','price':10,'at':at[0].isoformat(),'volume':volume[0],'change_pct':change[0]}}
        def tick():return live_tick(p,lots={sym:100},checked={sym},quote_fetch=quotes)
        self.assertEqual(tick(),'observed');self.assertFalse(calls)
        change[0]=2.5
        self.assertEqual(tick(),'decided');self.assertEqual(p.snapshot()['orders'][0]['status'],'pending')
        now[0]+=timedelta(minutes=1);tick();self.assertEqual(p.snapshot()['orders'][0]['status'],'pending')
        at[0]=now[0];tick();self.assertEqual(p.snapshot()['orders'][0]['status'],'pending')
        volume[0]=1100;tick();self.assertEqual(p.snapshot()['orders'][0]['status'],'filled')
        self.assertEqual(len(calls),1)
        now[0]=now[0].replace(hour=12,minute=20);self.assertEqual(tick(),'closed')

    def test_failed_plan_preserves_quota_and_baseline_and_still_cools_down(self):
        p = self.paper
        now = [datetime(2026,11,10,9,35,tzinfo=HK)]
        p.clock = lambda: now[0]
        p.enable(True, start_worker=False)
        symbol = '000001'
        p.research.items[symbol] = {'name':'样本', 'symbol':symbol}
        p.store.put_snapshot(symbol, {'data_date':'2026-11-09','complete':True}, now[0].isoformat())
        price = [10.]
        def quotes(*_):
            return {symbol: {'name':'样本','price':price[0],'volume':1000,'at':now[0].isoformat(),'change_pct':3}}
        def tick():
            return live_tick(p, lots={symbol:100}, checked={symbol}, quote_fetch=quotes)
        hold = {'model':'test','trace':[], 'plan':{'summary':'继续观察盘中行情','actions':[]}}
        with patch.object(p, 'agent', side_effect=[hold, ValueError('test'), hold, hold]) as agent:
            self.assertEqual(tick(), 'decided')
            now[0] += timedelta(minutes=30)
            price[0] = 11
            self.assertEqual(tick(), 'model-error')
            live = p.snapshot()['live']
            self.assertEqual((live['attempts'], live['baseline']), (1, {symbol:10.}))
            now[0] += timedelta(minutes=1)
            self.assertEqual(tick(), 'observed')
            now[0] += timedelta(minutes=29)
            self.assertEqual(tick(), 'decided')
            now[0] += timedelta(minutes=30)
            price[0] = 12
            self.assertEqual(tick(), 'decided')
            now[0] = now[0].replace(hour=13)
            price[0] = 13
            self.assertEqual(tick(), 'observed')
            self.assertEqual(agent.call_count, 4)
            self.assertEqual(p.snapshot()['live']['attempts'], 3)
