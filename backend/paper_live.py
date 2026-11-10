"""A market-session tick for the paper experiment. Order intent and fill observations are distinct."""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import uuid

from backend.live_quotes import fetch_quotes
from backend.market import market_context
from backend.paper import policy
from backend.paper_agent import Plan, propose
from backend.store import encode
from backend.prices import normalize_frame


class LiveSessions:
    """Owned by a single paper worker. Refresh daily reference data once, poll only while open."""
    def __init__(self, paper):
        self.paper=paper
        self.ready=None
        self.checked=set()
        self.lots={}

    def raw(self,symbols):
        data={}
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(self.paper.fetcher,s,'paper',''):s for s in symbols}
            for job in as_completed(jobs):
                try:
                    result=job.result()
                    if result['currency'] != self.paper.research.info['currency']:continue
                    normalize_frame(result['rows'])
                    data[jobs[job]]=result
                except Exception:continue
        return data

    def tick(self,ctx):
        p=self.paper
        snapshot=p.snapshot()
        if snapshot['blocked']:return 'blocked'
        symbols=set(p.research.watch_items())|set(snapshot['positions'])|{o['symbol'] for o in snapshot['orders'] if o['status']=='pending'}
        if ctx['phase']=='closing':
            if snapshot['last_session']==ctx['expected_session']:return 'already-complete'
            return 'settled' if p.settle(self.raw(symbols),ctx['expected_session']) else 'incomplete'
        if ctx['phase']!='intraday' or not snapshot['enabled']:return 'closed'
        key=(ctx['today'],tuple(sorted(symbols)))
        if self.ready!=key:
            raw=self.raw(symbols)
            if not p.settle(raw,ctx['expected_session']):return 'incomplete'
            self.checked=set()
            for s,v in raw.items():
                previous=[r for r in v['rows'] if r['date']<=ctx['expected_session']]
                today=next((r for r in v['rows'] if r['date']==ctx['today']),None)
                if len(previous)<30 or previous[-1]['date']!=ctx['expected_session']:continue
                if today and (today.get('split') or today.get('dividend')):continue
                if s in snapshot['positions'] and not today:continue
                self.checked.add(s)
            self.lots=p.fetcher('','lots','')['lots'] if p.market=='HK' else {s:(200 if s.startswith('688') else 100) for s in symbols}
            if self.checked and set(snapshot['positions'])<=self.checked:self.ready=key
        # Slow reference fetches may cross lunch or the close; live_tick checks the clock anew.
        return live_tick(p,lots=self.lots,checked=self.checked)


def live_tick(paper, *, lots, checked, quote_fetch=fetch_quotes, monitor_only=False):
    now, market = paper.clock(), paper.market
    ctx = market_context(now,market)
    if ctx['phase'] != 'intraday':
        return 'closed'
    state = paper.snapshot()
    if not state['enabled'] or state['blocked']:
        return 'paused'
    items = paper.research.watch_items()
    symbols = set(items) | set(state['positions'])
    quotes = quote_fetch(sorted(symbols),market,now)
    day = ctx['today']
    with paper.transaction() as (s,db):
        if not s['enabled'] or s['generation'] != state['generation']:
            return 'paused'
        live = s.setdefault('live',{})
        if live.get('day') != day:
            live.clear();live.update(day=day, attempts=0, baseline={}, last_attempt=None)
        live.update(observed_at=now.isoformat(), fresh=len(quotes), requested=len(symbols))
        # Refuse to update any existing position or trade against stale/missing holdings.
        if any(x not in checked or x not in quotes for x in s['positions']):
            s['message']='持仓行情或公司行动未核验，盘中估值与交易暂停。'
            return 'missing-holding-data'
        for symbol,p in s['positions'].items():
            p.update(mark_price=quotes[symbol]['price'],mark_date=day)
        for order in s['orders']:
            if order['status']!='pending' or order.get('execution')!='intraday':
                continue
            q=quotes.get(order['symbol'])
            if now >= datetime.fromisoformat(order['expires_at']) or order['eligible_session']!=day:
                order.update(status='expired',result='五分钟内未取得可用的新报价')
            elif (not monitor_only and q and order['symbol'] in checked and
                  datetime.fromisoformat(q['at']) > datetime.fromisoformat(order['created_at'])):
                if abs(q['price']/order['reference']-1) > .03 or q.get('at_limit'):
                    order.update(status='expired',result='报价偏离或触及涨跌停，未成交')
                elif q['volume'] > order.get('reference_volume',0):
                    paper.fill(s,order,{'open':q['price']},day)
                    if order['status']=='filled':
                        order.update(fill_at=q['at'],result='后续新报价加固定滑点模拟成交')
        if monitor_only or not quotes:
            s['message']=f'盘中监测：{len(quotes)} / {len(symbols)} 只有新报价。'
            return 'observed' if quotes else 'no-quotes'
        generation=s['generation']
        eligible={sym:q for sym,q in quotes.items() if sym in checked and sym in lots and lots[sym]>0}
        if not eligible:
            s['message']='暂无通过行情与交易单位校验的股票。';return 'no-tradable'
        trigger=not live['last_attempt'] or any(abs(q['price']/live['baseline'].get(sym,q['price'])-1)>=.02 for sym,q in eligible.items())
        if (not trigger or live['attempts']>=3 or (live['last_attempt'] and
                (now-datetime.fromisoformat(live['last_attempt'])).total_seconds()<1800)):
            return 'observed'
        if paper.agent is propose and not os.environ.get('DEEPSEEK_API_KEY'):
            s['message']='盘中监测已运行，缺少模型密钥，无法生成交易方案。';return 'missing-model-secret'
        live['attempts']+=1;live['last_attempt']=now.isoformat();live['baseline']={sym:q['price'] for sym,q in eligible.items()}
        data={}
        for sym,q in eligible.items():
            saved=paper.store.snapshot(sym,ctx['expected_session']) or {}
            if saved.get('data_date')!=ctx['expected_session'] or not saved.get('complete'):
                continue
            if market=='A' and ('ST' in q['name'].upper() or sym.startswith('689')):
                continue
            data[sym]={'symbol':sym,'name':q['name'],'tradable':True,'raw_close':q['price'],'lot':lots[sym],
                'quote_at':q['at'],'quote_volume':q['volume'],'quote_change_pct':q['change_pct'],'price_source':'腾讯证券 · 盘中报价',
                'research_date':saved['data_date'],'analysis':saved.get('analysis'),'news':((saved.get('fundamentals') or {}).get('news') or [])[:6]}
        if not data:
            live['attempts']-=1;live['last_attempt']=None
            s['message']='盘中报价已更新，等待上一交易日的已完成研究资料。';return 'no-research'
        record={'id':uuid.uuid4().hex,'session':day,'created_at':now.isoformat(),'version':'paper-live-v1',
                'execution':'intraday','trace':[],'evidence':data,'plan':None}
        db.execute('INSERT INTO paper_decisions VALUES(?,?,?,?)',(record['id'],day,'running',encode(record)))
    try:
        current=paper.snapshot()
        rules={**policy(market),'execution':'决策完成后五分钟内，按后续有新增成交量的新报价加滑点模拟成交。'}
        context={'market':market,'currency':current['currency'],'session':day,'cutoff':now.isoformat(),
                'cash':current['cash_cents']/100,'positions':current['positions'],'policy':rules,'recent_reviews':current['reviews'][-5:],
                'stocks':[{k:v[k] for k in ('symbol','name','tradable','raw_close','lot','quote_change_pct')} for v in data.values()]}
        answer=paper.agent(context,data,paper.research.config['llm']['model'])
        plan=Plan.model_validate(answer['plan']).model_dump()
        if len(set(a['symbol'] for a in plan['actions']))!=len(plan['actions']):raise ValueError('重复股票')
        record.update(plan=plan,trace=answer['trace'],model=answer['model'])
        paper.commit_plan(record,data,generation,live=True)
        return 'decided'
    except Exception:
        record['error']='本轮盘中模型方案未完成，没有生成订单。'
        with paper.transaction() as (s,db):
            db.execute("UPDATE paper_decisions SET status='error',payload=? WHERE id=? AND status='running'",(encode(record),record['id']))
            s['message']=record['error']
        return 'model-error'
