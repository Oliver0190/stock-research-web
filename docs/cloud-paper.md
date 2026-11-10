# 关机后运行模拟账户

网站代码在 `Oliver0190/stock-research-web`。不要将 `.env`、数据库或 `data/` 提交到公开仓库。这里有两种不同的运行方式；同一账户只选一个执行端。

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
