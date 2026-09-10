"""Cross-market boundaries and source selection, with no live provider calls."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend import data
from backend.a_fundamentals import financial_records
from backend.app import create_app
from backend.market import HK, instrument, market_context, yahoo_symbol
from backend.prices import normalize_frame
from backend.service import ResearchService
from backend.store import Store


class MarketRulesTests(unittest.TestCase):
    def test_ah_symbols_and_currencies_are_distinct(self):
        self.assertEqual(yahoo_symbol('01211', 'HK'), '1211.HK')
        self.assertEqual(yahoo_symbol('002594', 'A'), '002594.SZ')
        self.assertEqual(yahoo_symbol('688008', 'A'), '688008.SS')
        self.assertEqual(instrument('000977', 'A')['currency'], 'CNY')
        with self.assertRaises(ValueError):
            instrument('01211', 'A')
        with self.assertRaises(ValueError):
            instrument('002594', 'HK')

    def test_lunch_and_close_follow_each_exchange(self):
        now = datetime(2026,9,10,11,45,tzinfo=HK)
        self.assertEqual(market_context(now, 'A')['phase'], 'break')
        self.assertEqual(market_context(now, 'HK')['phase'], 'intraday')
        self.assertEqual(market_context(datetime(2026,9,10,15,20,tzinfo=HK), 'A')['phase'], 'settling')
        now = datetime(2026,9,10,15,50,tzinfo=HK)
        self.assertEqual(market_context(now, 'A')['phase'], 'closing')
        self.assertEqual(market_context(now, 'A')['expected_session'], '2026-09-10')
        self.assertEqual(market_context(now, 'HK')['phase'], 'intraday')
        self.assertEqual(market_context(now, 'HK')['expected_session'], '2026-09-09')
        self.assertEqual(market_context(datetime(2026,10,1,12,tzinfo=HK),'A')['phase'], 'holiday')
        self.assertEqual(market_context(datetime(2026,9,12,12,tzinfo=HK),'A')['phase'], 'holiday')

    def test_invalid_primary_ohlc_falls_back_without_repairing_prices(self):
        good = pd.DataFrame([{'date':'2026-09-10','open':10,'high':11,'low':9,'close':10.5,'volume':1000}])
        bad = good.copy()
        bad.loc[0,'open'] = 12
        def first(*args): return bad
        def second(*args): return good
        def last(*args): raise AssertionError('Unexpected third source')
        first.__name__ = '_fetch_yfinance'
        second.__name__ = '_fetch_tencent'
        with patch.dict('os.environ',{'DATA_SOURCE_A':'yfinance'}), patch.object(data,'_fetch_yfinance',first), patch.object(data,'_fetch_tencent',second), patch.object(data,'_fetch_akshare',last):
            result = data.fetch_daily('002594',market='A')
        self.assertEqual(result.attrs['source'],'腾讯证券 / AKShare')
        self.assertEqual(result.iloc[0]['open'],10)
        with self.assertRaises(ValueError):
            normalize_frame(bad)

    def test_legacy_tencent_volume_normalizes_main_and_star_shares(self):
        raw = pd.DataFrame([{'date':'2026-09-10','open':10,'high':11,'low':9,'close':10.5,'amount':1000}])
        with patch('akshare.stock_zh_a_hist_tx',return_value=raw):
            self.assertEqual(data._fetch_tencent('002594',30).iloc[0]['volume'],100000)
            self.assertEqual(data._fetch_tencent('000977',30).iloc[0]['volume'],100000)
            self.assertEqual(data._fetch_tencent('688008',30).iloc[0]['volume'],1000)

    def test_a_financial_values_keep_base_units_and_cumulative_period(self):
        records = financial_records(pd.DataFrame([{'REPORT_DATE':'2026-06-30','TOTAL_OPERATE_INCOME':120000000,'PARENT_NETPROFIT':15000000}]))
        self.assertEqual(records['currency'],'CNY')
        self.assertEqual(records['periods'][0]['revenue'],120000000)
        self.assertIn('累计',records['periods'][0]['report_period_type'])
        self.assertEqual(records['periods'][0]['net_profit_label'],'归母净利润')


class MarketIsolationTests(unittest.TestCase):
    def test_config_cannot_share_one_database_file_across_markets(self):
        with patch('backend.app.database_path',return_value=Path('same.sqlite3')):
            with self.assertRaisesRegex(ValueError,'不同文件'):
                create_app()

    def test_read_write_refresh_and_history_stay_inside_selected_market(self):
        root = Path(__file__).resolve().parents[1] / 'data' / 'tests'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            services = {}
            for market,symbol in [('HK','01211'),('A','002594')]:
                config = {'market':market,'watchlist':[{'symbol':symbol,'name':'比亚迪'}],'llm':{'model':'test'}}
                services[market] = ResearchService(Store(Path(folder)/(market+'.sqlite3')),config,lambda *args: None)
                services[market].store.put_report({'id':symbol+':test','symbol':symbol,'report_date':'2026-09-10','data_date':'2026-09-10','kind':'closing','title':'test','summary':'test','body':'test','importance':'important','engine':'rules','created_at':'2026-09-10T17:00:00+08:00','metadata':{}})
            with TestClient(create_app(services=services,schedule=False)) as client:
                for market,symbol,wrong in [('HK','01211','002594'),('A','002594','01211')]:
                    overview = client.get('/api/overview',params={'market':market}).json()
                    self.assertEqual([s['symbol'] for s in overview['stocks']],[symbol])
                    self.assertEqual(overview['market']['id'],market)
                    self.assertEqual(client.get('/api/stocks/'+wrong,params={'market':market}).status_code,404)
                    response = client.put('/api/stocks/'+symbol+'/note',params={'market':market},json={'note':market+' note'})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(client.put('/api/stocks/'+wrong+'/note',params={'market':market},json={'note':'wrong'}).status_code,404)
                with patch.object(services['A'],'start',return_value=True) as a_start, patch.object(services['HK'],'start') as hk_start:
                    self.assertEqual(client.post('/api/refresh?market=A',json={}).status_code,202)
                    a_start.assert_called_once_with(None)
                    hk_start.assert_not_called()
                self.assertEqual(client.get('/api/overview?market=US').status_code,422)
                client.post('/api/stocks/002594/read?market=A',json={'through':'2026-09-10T18:00:00+08:00','report_ids':['002594:test','01211:test']})
                self.assertIsNotNone(services['A'].store.report('002594:test')['read_at'])
                self.assertIsNone(services['HK'].store.report('01211:test')['read_at'])
                self.assertEqual(services['A'].store.profile('002594')['note'],'A note')
                self.assertEqual(services['HK'].store.profile('01211')['note'],'HK note')
                self.assertEqual(client.get('/api/stocks/01211').status_code,200)


if __name__ == '__main__':
    unittest.main()
