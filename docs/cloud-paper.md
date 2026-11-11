# 关机后运行模拟账户

网站代码在 `Oliver0190/stock-research-web`。一个账户使用一个执行端，模型密钥和用户账本各自保存在部署环境的 Secret 和私有存储中。

## GitHub 盘中模拟实验

公开网站仓库的 `paper-sessions.yml` 在交易日按北京时间盯盘。港股为 09:30–12:00、13:00–16:00，半日市只跑上午。A 股为 09:00–12:00、13:00–15:30，其中 09:00–09:30、11:30–12:00 和 15:00–15:30 只观察新报价。两个市场各按交易所日历跳过休市日，开市后才按信号调用模型和模拟成交。

GitHub 每周一至五于 08:07、12:07 提前启动标准 Linux 运行器，准备好后等到窗口开始，每市场约每分钟检查一次。08:47、09:43 和 12:47、13:43 分别补启对应时段。所有任务共用一个写账锁，排到时已错过该时段就退出。16:47 另有收盘估值和次日研究准备任务。GitHub 可能延迟或漏发定时事件，错过的报价不会补做成交。

首轮信号取相对昨收的涨跌幅，阈值为 2%。后续相对上一轮决策时的报价比较，同样以 2% 为阈值。每市场每天最多三轮，相隔至少 30 分钟。只有较决策更新、成交量增加且符合时效和仓位约束的报价才能模拟成交。

账户记录在私有 `stock-paper-runtime`，以只对这个私库有效的 SSH 部署密钥读写。模型密钥与部署密钥放在网站仓库的 Actions Secrets。关注列表、原始决策和资金记录只写入私库。每次订单变化及大约五分钟一次的状态会提交到账本。午休、收市和节假日由两地日历判断。

首次安装在本机执行 `python -m ops.paper_admin install-sessions`，配置部署密钥与模型 Secret，然后启用仓库变量 `PAPER_SESSIONS_ENABLED=true`。手动运行一次 `check`，确认两个账本都成功更新后执行 `python -m ops.paper_admin link-sessions` 并重启本机网站。网站的启动、暂停和自选同步写入私库 `control/A.json` 或 `control/HK.json`，运行中的任务在下一次检查控制文件时生效。两市场共用一个任务锁。私库的 `state/A` 和 `state/HK` 可直接在手机上查看。

## 盘中：常驻进程

`compose.yaml` 在持续在线的 Linux 主机上运行一份原网站，设置 `STOCK_AGENT_PAPER_MODE=intraday`，用一个持久 Docker 卷保存 A 股与港股的独立 SQLite。公开仓库的 `Verify paper trading container` 工作流只构建镜像和用合成数据测试，不代表生产交易已启动。

1. 两个本机账户先暂停，检查是否有待处理的订单。执行 `python -m backend.cloud_job seed data/paper-seed`，仅导出虚拟账本、决策和自选名称/代码，没有笔记或密钥。目标目录必须为空或不存在。保留本机数据库备份。
2. 在已有主机克隆网站仓库，用 SSH 等私有通道将 `paper-seed` 放入主机上的 `data/paper-seed`。在主机单独设置 `DEEPSEEK_API_KEY`，不要在 Git 或聊天里传密钥。
3. 有 Docker Compose 的主机，在项目目录执行：

   ```sh
   docker compose build
   docker compose run --rm -v "$PWD/data/paper-seed:/seed:ro" research python -m backend.cloud_job import-seed /seed
   docker compose up -d
   ```

   导入只接受空数据库卷。不要执行 `docker compose down -v`，`-v` 会删持久账本。
4. 网站仅绑定服务器的 `127.0.0.1:8765`。在自己的电脑执行 `ssh -N -L 8766:127.0.0.1:8765 用户名@主机`，浏览器打开 `http://127.0.0.1:8766/#A/paper`。确认两个账户金额与自选无误，分别点击“启动模拟”。电脑关闭后主机继续运行；随时可在同一页面暂停。不要再启动旧本机账本。无登录保护的接口不应直接暴露公网。

交易时段按 XSHG / XHKG 日历（包含午休、休市和各交易所的提前收市）每分钟检查一次；不是交易所逐笔数据。A 股与港币柜台的报价都校验代码、币种和时间，超过 90 秒就跳过。首次有合格资料，或相对上一轮决策报价变化达到 2%，才考虑调用模型。每市场每天最多 3 轮，至少间隔 30 分钟。需有上一交易日研究快照；行情、交易单位或公司行动未核实，则不交易。

决策完成后等待一笔时间更晚、成交量增加的有效报价，再按原滑点与费用记账；五分钟内无合格报价则订单到期。A 股当日加仓也会计入锁定数量。每日收盘再估值与复盘，不把盘中遗漏订单追记为开盘成交。网络、行情源和模型处理时间都会影响实际频率。

## 收盘批处理：私有 GitHub Actions 账本

`ops/paper-workflow.yml` 是安装模板，**不在公开仓库自行定时运行**。安装时，在独立私有运行仓库中固定应用提交号，只给该私库的任务写入自己账本的权限。每个工作日北京时间 17:23 / 20:23 做收盘实验和补跑；已完成交易日幂等。GitHub 的定时启动可能延迟，不能提供盘中实时保证。不要用长时间空转的 hosted runner 充当常驻主机。

安装：从已推送、验证过的应用提交执行 `python -m ops.paper_admin install`。这会创建私库并导入**暂停状态**的账户；已有同名仓库时拒绝覆盖。在私库 Actions Secrets 中添加 `DEEPSEEK_API_KEY`，运行 `python -m ops.paper_admin verify`，检查该轮成功后再运行 `python -m ops.paper_admin link` 并重启本机网站。之后网站从私库读取账本，操作提交 GitHub 队列；断网只读标有日期的缓存，绝不退回本机下单。修改本机自选后可点“同步关注列表”。也可以在 GitHub 私库 Actions 手动选择 `start/pause/run`，或直接在私库 `state/A` 和 `state/HK` 阅读简报。已排队的暂停以实际执行时间为准。

私库上限和配额取决于账户计划，不会自动开通付费服务。参考 [Actions 定时任务](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[托管运行器和用量](https://docs.github.com/en/actions/concepts/billing-and-usage)。
