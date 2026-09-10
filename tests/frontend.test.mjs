import test from 'node:test';
import assert from 'node:assert/strict';
import { escape, markdown, safeUrl } from '../frontend/format.js';
import { chart } from '../frontend/chart.js';
import { overview, detail } from '../frontend/views.js';

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
