import test from 'node:test';
import assert from 'node:assert/strict';
import { escape, markdown, safeUrl } from '../frontend/format.js';
import { chart } from '../frontend/chart.js';
import { overview, detail } from '../frontend/views.js';
import { indicatorPlot, indicatorPanels } from '../frontend/indicators.js';

test('remote content stays text; active URLs are rejected', () => {
  assert.equal(escape('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('data:text/html,hello'), null);
  assert.equal(markdown('**标题**\n<img src=x onerror=alert(1)>'), '<strong>标题</strong><br>&lt;img src=x onerror=alert(1)&gt;');
});
test('flat prices and missing chart data remain valid', () => {
  assert.ok(!chart([{date:'2026-01-01',close:5},{date:'2026-01-02',close:5}]).includes('NaN'));
  assert.ok(chart([]).includes('暂无走势数据'));
});
test('empty initial workspace and empty stock file render without demo values', () => {
  const stock = {symbol:'00700',name:'测试股票',snapshot:null,profile:{note:''},reports:[],unread:0};
  const data = {stocks:[stock],reports:[],dates:[],market:{today:'2026-09-10',expected_session:'2026-09-09',label:'盘中'},selected_date:'2026-09-10',update:{running:false}};
  const state = {route:'overview',day:'',search:'',filter:'all',drafts:{},range:30,kind:'all',openReports:new Set(),closedReports:new Set()};
  assert.ok(overview(data,state).includes('等待首次更新'));
  assert.ok(detail(data,stock,state).includes('暂无研究记录'));
});

test('MACD bars start at zero and extend in the correct direction', () => {
  const html = indicatorPlot([{date:'2026-09-09',dif:1,dea:0,macd_bar:2}, {date:'2026-09-10',dif:-1,dea:0,macd_bar:-2}], 'macd');
  const zero = Number(/indicator-guide zero[^>]*y1="([^"]+)"/.exec(html)[1]);
  const up = /indicator-bar up[^>]*y="([^"]+)"[^>]*height="([^"]+)"/.exec(html);
  const down = /indicator-bar down[^>]*y="([^"]+)"[^>]*height="([^"]+)"/.exec(html);
  assert.ok(Number(up[1]) < zero);
  assert.ok(Math.abs(Number(up[1]) + Number(up[2]) - zero) < .000001);
  assert.equal(Number(down[1]), zero);
  assert.ok(Number(down[2]) > 0);
});

test('KDJ scale contains values outside 0–100 without clipping', () => {
  const html = indicatorPlot([{date:'2026-09-09',k:90,d:50,j:170}, {date:'2026-09-10',k:0,d:60,j:-120}], 'kdj');
  assert.ok(html.includes('J 170.000'));
  assert.ok(html.includes('J -120.000'));
  const path = /indicator-line series-3" d="([^"]+)"/.exec(html)[1];
  const ys = [...path.matchAll(/[ML][\d.]+,([\d.]+)/g)].map(m => Number(m[1]));
  assert.equal(ys.length, 2);
  assert.ok(ys.every(y => y >= 16 && y <= 160));
  assert.ok(ys[0] < ys[1]);
});

test('indicator ranges keep the last reading and missing archives stay explicit', () => {
  const series = Array.from({length:120}, (_,i) => ({date:`day-${i}`,dif:i,dea:i-1,macd_bar:2,k:60,d:50,j:80}));
  const snapshot = {data_date:'2026-09-10',source:'Test',complete:true,indicator_chart:{series,macd:{summary:'<img src=x>',detail:'当前状态'},kdj:{summary:'K 高于 D',detail:'当前状态'}}};
  for (const range of [30,60,120]) {
    const html = indicatorPanels(snapshot, range);
    assert.ok(html.includes(`day-${120-range}`));
    assert.ok(html.includes('119.000'));
    assert.ok(html.includes('&lt;img src=x&gt;'));
    assert.ok(!/NaN|undefined/.test(html));
  }
  assert.ok(indicatorPanels({data_date:'2026-09-09'},60).includes('这份存档尚未保存指标序列'));
  assert.ok(indicatorPanels(null,60).includes('等待首次行情更新'));
});
