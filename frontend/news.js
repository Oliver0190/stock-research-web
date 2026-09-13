import { escape as e, safeUrl } from './format.js';

export function newsPanel(fund, filter = 'important', expanded = false) {
  const all = fund?.news || [];
  const items = all.filter(n => filter === 'all' || (filter === 'earnings' ? n.category === 'earnings' && n.importance === 'important' : n.importance === 'important'));
  const visible = expanded ? items : items.slice(0, 4);
  return `<div class="news-section"><div class="news-heading"><h3>新闻精选</h3><span class="subtle">重要度优先</span></div>
    <div class="news-filters" aria-label="新闻重要度筛选">${[['important','重要'],['earnings','财报'],['all','全部']].map(([value,label]) => `<button class="filter ${filter === value ? 'selected' : ''}" data-action="news-filter" data-value="${value}" aria-pressed="${filter === value}">${label}</button>`).join('')}</div>
    <p class="news-method">近 180 天 · 按标题归类，优先财报、经营数据与重大事项；日常回购合并展示。</p>
    ${fund?.news_issue ? `<p class="news-method news-warning">${e(fund.news_issue)}</p>` : ''}
    ${visible.length ? `<div class="news-list">${visible.map(n => `<article class="news-item"><div class="news-category ${n.importance === 'important' ? 'important' : ''}">${e(n.category_label || '公司动态')}${n.related_count > 1 ? `<span>已合并 ${n.related_count} 条</span>` : ''}</div><small>${e(n.time?.slice(0, 10) || '日期未提供')} · ${e(n.source || '来源未标注')}</small>${safeUrl(n.url) ? `<a href="${e(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${e(n.title)} ↗</a>` : `<p>${e(n.title)}</p>`}</article>`).join('')}</div>` : `<p class="news-empty">${all.length ? '当前候选中暂无匹配的重要新闻，可切换「全部」查看。' : '暂无新闻记录'}</p>`}
    ${items.length > 4 ? `<button class="news-more" data-action="news-expand">${expanded ? '收起' : `展开其余 ${items.length - 4} 条`}</button>` : ''}
    ${fund?.news_fetched_date ? `<p class="news-updated">检索于 ${e(fund.news_fetched_date)}</p>` : ''}</div>`;
}
