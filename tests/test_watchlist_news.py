import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.news import fetch_news, rank_news
from backend.service import ResearchService
from backend.store import Store
from backend.watchlist import normalize_code, resolve_stock


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'data' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.config = {'watchlist':[{'symbol':'00700','name':'腾讯控股','alert_low':100}], 'llm':{'model':'test'}}
        self.store = Store(Path(self.temp.name) / 'watch.sqlite3')
        self.fetch = Mock(side_effect=lambda symbol, mode, arg: {'symbol':symbol,'name':'测试公司'})
        self.service = ResearchService(self.store, self.config, self.fetch)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_add_normalizes_code_and_duplicate_does_not_fetch_or_queue_twice(self):
        with patch.object(self.service, 'start') as start, TestClient(create_app(self.service, schedule=False)) as client:
            response = client.post('/api/watchlist', json={'symbol':' 5 '})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['stock']['symbol'], '00005')
            self.assertTrue(response.json()['added'])
            self.assertFalse(client.post('/api/watchlist', json={'symbol':'00005'}).json()['added'])
            self.fetch.assert_called_once_with('00005','lookup','')
            start.assert_called_once_with(['00005'], queue=True)
            self.assertEqual(client.post('/api/watchlist', json={'symbol':'002594'}).status_code, 422)
            self.assertEqual(client.post('/api/watchlist', json={'symbol':'5'}, headers={'Origin':'https://elsewhere.example'}).status_code, 403)

    def test_remove_all_stays_empty_after_restart_and_restore_preserves_history(self):
        self.store.save_note('00700','原有观察笔记','2026-09-13T10:00:00+08:00')
        report = {'id':'saved','symbol':'00700','report_date':'2026-09-11','data_date':'2026-09-11',
                  'kind':'closing','title':'test','body':'保存的正文','summary':'test','importance':'important',
                  'engine':'rules','created_at':'2026-09-11T18:00:00+08:00'}
        self.store.put_report(report)
        with TestClient(create_app(self.service, schedule=False)) as client:
            self.assertEqual(client.request('DELETE','/api/watchlist/00700',json={}).status_code, 200)
            data = client.get('/api/overview?day=2026-09-11').json()
            self.assertEqual((data['stocks'],data['reports'],data['dates']), ([],[],[]))
            self.assertEqual(client.get('/api/stocks/00700').status_code,404)
        restarted = ResearchService(Store(self.store.path), self.config, self.fetch)
        self.assertEqual(restarted.watch_items(), {})
        with patch.object(restarted,'start'):
            restored = restarted.add_stock('700')['stock']
        self.assertEqual(restored['alert_low'],100)
        self.assertEqual(restarted.detail('00700')['profile']['note'],'原有观察笔记')
        self.assertEqual(restarted.detail('00700')['reports'][0]['body'],'保存的正文')
        restarted.close()

    def test_removal_during_collection_drops_pending_stock(self):
        self.service.progress['running'] = True
        result = self.service.add_stock('5')
        self.assertTrue(result['added'])
        self.assertEqual(self.service.pending, {'00005'})
        self.service.remove_stock('00005')
        self.assertEqual(self.service.pending,set())
        with patch.object(self.service,'refresh_one') as refresh:
            self.service._run(['00005'])
            refresh.assert_not_called()

    def test_lookup_failure_does_not_persist_and_market_codes_are_strict(self):
        self.fetch.return_value = None
        self.fetch.side_effect = RuntimeError('核验服务暂时不可用')
        with TestClient(create_app(self.service,schedule=False)) as client:
            self.assertEqual(client.post('/api/watchlist',json={'symbol':'5'}).status_code,503)
        self.assertEqual(list(self.service.watch_items()),['00700'])
        self.assertEqual(normalize_code('000001','A'),'000001')
        for invalid in ['700','00700','６００５１９','../../x','600519.SS']:
            with self.assertRaises(ValueError): normalize_code(invalid,'A')

    def test_quote_lookup_rejects_mismatched_response_symbol(self):
        response = Mock(text='v_hk00005="100~汇丰控股~00006~10";')
        with patch('backend.watchlist.requests.get',return_value=response):
            with self.assertRaises(ValueError): resolve_stock('00005','HK')


def news(title, day='2026-09-11', url=None):
    return {'title':title,'time':day+' 10:00:00','url':url or 'https://example.com/'+title,'source':'测试来源'}


class NewsTests(unittest.TestCase):
    def test_financials_outrank_new_buybacks_and_generic_market_headlines(self):
        rows = [news('腾讯控股连续20日回购'),news('腾讯控股连续19日回购','2026-09-10'),
                news('腾讯第二季度财报：营收增长','2026-08-12'),news('港股公司净利润排行榜'),
                news('腾讯拟提高回购计划金额'),news('腾讯游戏付费用户数增加'),news('腾讯Q2财报增长，软件ETF大涨')]
        ranked = rank_news(rows,'00700','腾讯控股','2026-09-13')
        self.assertIn('财报',ranked[0]['title'])
        buybacks = [n for n in ranked if n['category']=='buyback']
        self.assertEqual(len(buybacks),1)
        self.assertEqual(buybacks[0]['related_count'],2)
        self.assertEqual(buybacks[0]['importance'],'normal')
        self.assertTrue(any(n['category']=='material' and n['importance']=='important' for n in ranked))
        self.assertEqual(next(n for n in ranked if '排行榜' in n['title'])['importance'],'normal')
        self.assertEqual(next(n for n in ranked if 'ETF' in n['title'])['importance'],'normal')
        self.assertEqual(rank_news(ranked,'00700','腾讯控股','2026-09-13'),ranked)

    def test_deduplication_window_and_company_relevance(self):
        rows = [news('比亚迪8月销量增长'),news('比亚迪8月销量增长'),
                news('宁德时代净利润增长'),news('比亚迪年报','2025-12-01'),news('比亚迪业绩','2026-10-01')]
        ranked=rank_news(rows,'002594','比亚迪','2026-09-13')
        self.assertEqual(len(ranked),2)
        self.assertEqual(ranked[0]['category'],'operations')
        self.assertEqual(ranked[1]['importance'],'normal')

    def test_multiple_queries_keep_partial_results_and_total_failure_is_explicit(self):
        def search(query):
            if query=='00700': raise RuntimeError('source error')
            return [news('腾讯Q2财报',date.today().isoformat())]
        with patch('backend.news.search_news',side_effect=search):
            data=fetch_news('00700','腾讯控股')
        self.assertEqual(len(data['news']),1)
        self.assertIsNotNone(data['news_issue'])
        with patch('backend.news.search_news',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): fetch_news('00700','腾讯控股')


if __name__ == '__main__':
    unittest.main()
