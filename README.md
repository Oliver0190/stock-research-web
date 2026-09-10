# 港股观察

一个本机运行的港股研究工作区：首页先看关注列表和重点变化，进入个股后查看研究时间线、走势图、财报新闻和观察笔记。

## 启动

需要 Python 3.12。已经有 `.venv` 时，在项目目录运行：

```powershell
.venv/Scripts/python.exe -m backend
```

或运行 `./start.ps1`。打开 **http://127.0.0.1:8765/**。端口被其他程序占用时可用 `--port 8766`。

新环境安装：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
```

已有 `.env` 时不要覆盖。`DEEPSEEK_API_KEY` 可选；缺少或接口失败时仍保存并展示规则解读。

程序运行期间才能更新数据。首次启动自动采集；此后交易时段每 10 分钟检查一次，盘前 08:00 起采集、收盘后的数据校验通过后生成复盘。周末、假日不做盘中检查。关闭终端或电脑后更新停止，已保存的报告不会丢失。`STOCK_AGENT_AUTO_REFRESH=0` 可关闭自动采集，改为页面手动更新。没有安装系统计划任务、开机服务，也不会发送飞书消息。

## 日常使用

- **今日总览**：看自选股价格、真实数据日期、重点事件；可筛选有变化或未查看的股票。
- **个股档案**：看近 30 / 60 / 120 个交易日价格、技术状态、研究时间线和财报新闻。
- **历史日期**：查看该日的报告和不晚于该日的最近一次快照。没有保存过的数据不会假装存在。
- **观察笔记**：填写后点击保存，存入本机数据库；不会发送给 AI。
- **更新失败**：保留上次成功的数据；进入个股点击“更新这只股票”可补跑。

自选股、名称、价格阈值集中在 `config.yaml`，修改后重启程序。报告从网站版首次运行开始积累；旧飞书正文没有历史存储，未自动导入。

## 代码放在哪里

```text
backend/               唯一的业务后端
  app.py               HTTP 接口、本机访问边界、页面托管
  service.py           数据采集、报告流水线、后台更新
  store.py             SQLite 存储
  market.py            港股交易日历、数据日期校验
  data.py              行情源适配（复用旧版）
  fundamentals.py      财报新闻（复用旧版）
  analyzer.py          技术计算（复用并修正旧版）
  reports.py           事件判断和可离线运行的规则解读
  llm.py               DeepSeek 文案接口
  fetch_worker.py      数据源超时隔离
frontend/              无需单独安装或构建的页面
  app.js               路由和交互状态
  views.js             页面视图
  api.js               后端接口
  format.js, chart.js   文本与图表
  styles.css           布局和组件样式
  tokens.css           颜色和字体变量
tests/                 关键回归检查，使用独立的合成测试数据
docs/                  架构与数据口径
data/                  本机数据库；不提交 Git
```

一个 Python 进程同时提供页面、接口和调度，只需维护一套应用。前端没有另一份股票分析逻辑，也没有浏览器中的第二份业务数据库。不要使用多个 Uvicorn worker，否则会重复启动调度。

`requirements.txt` 记录直接依赖范围，`requirements.lock.txt` 固定本次验证的依赖版本。升级后重新检查再更新锁定文件。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node tests/frontend.test.mjs
```

Python 检查不调用外部行情/AI，不接触实际业务数据库。Node 仅用于开发检查，日常运行网站不需要 Node。

## 数据与备份

默认存储 `data/stock-agent.sqlite3`。关闭程序后备份该文件即可；运行中不要只复制主文件，因为 SQLite 可能还有 WAL 文件。`.env` 保存私密配置，不在网页中提供，也不提交 Git。可使用 `STOCK_AGENT_DB` 指向其他持久目录。

当前版只监听 `127.0.0.1`，仅供本机试用。远程部署时再统一增加认证和部署配置；不要直接将这个本机接口开放到公网。

## 旧版

旧飞书系统保留在 Git 历史的 `759faa2` 及之前版本。旧推送入口已经从网站版源代码移除，GitHub 上的三个旧工作流保持停用。本次网站改造在 `codex/stock-dashboard` 分支开发。

详见 [架构与数据口径](docs/architecture.md)。
