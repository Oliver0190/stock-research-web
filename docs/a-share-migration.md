# A 股接入记录

## 旧项目与停用状态

- 复制来源目录：`Stock Agent(A)/`，原仓库 [Oliver0190/A-Stock-Agent](https://github.com/Oliver0190/A-Stock-Agent)。复制目录最初与远端 `7fab680` 一致，未发现已跟踪代码的本地修改。
- 2026-09-10 检查时，GitHub 没有正在运行或排队的任务，最近可见运行发生在 2026-08-03。
- 已通过 [15ec2e2](https://github.com/Oliver0190/A-Stock-Agent/commit/15ec2e28250ce559aa2cd79dd52d4662f725349c) 移除盘前、盘中、盘后三份工作流的定时触发，并为作业加上恒为 false 的条件，防止误点手动运行继续推送。复制目录已同步此提交。
- 未删除原仓库、代码历史或本地原工程。删除电脑目录不会停止 GitHub 定时任务；上述停用是在远端独立完成的。

## 接入方式

- 16 只沪深 A 股关注对象导入 `config.a.yaml`。港股仍使用 `config.yaml`。
- 同一 Python 进程、同一前端、同一分析和报告代码；按市场选择数据源、代码格式、币种和交易日历。
- 港股继续使用 `data/stock-agent.sqlite3`；A 股写入 `data/stock-agent-a.sqlite3`。原港股数据在接入前另存了 `data/backups/before-a-market-20260910-173745.sqlite3`。
- 根目录的 `.env` 是网站唯一的私密配置入口。没有将旧 A 股 `.env` 复制进代码或提交到 Git，也没有恢复飞书发送逻辑。两地市场共享网站当前配置的 DeepSeek 密钥。
- 网站不导入、不执行 `Stock Agent(A)/` 内的代码；该目录已加入 `.gitignore`，只作本机迁移参考。
- GitHub 上仍只有旧 A 股停用变更。新双市场网站属于主项目的本地 `codex/stock-dashboard` 分支。

## 目录是否可以删除

不需要为了“停止运行”删除本体或复制目录。新网站运行验证后，可以将旧工程整体归档；确认其中被 Git 忽略的 `.env`、`state.json` 及其他个人文件已经妥善保留，再删除重复的本机目录。删除前应核对实际路径，避免误删当前网站根目录或 `data/`。本次没有执行目录删除。

## 行情修正

接入时，部分 Yahoo A 股日线出现开盘价高于最高价的异常。校验已前移至每个数据源返回后：异常数据会触发备用源，不修改数值使其“通过”。A 股增加腾讯日线回退，保留来源标签；旧 AKShare 腾讯接口的 `amount` 实际是成交量，主板/创业板手数换算为股，科创板已为股。已用相同已完成交易日与 Yahoo 对照核实。

- [AKShare 日线接口与单位](https://akshare.akfamily.xyz/data/stock/stock.html)
- [上交所交易规则（2026 年修订）](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)
