# ARCH-150 · 给智能体排定时任务（CRON）—— 闹钟 vs 大脑 · 每 bot 专属 · 零硬编码

> **一句话**：到点把**一句触发词**注入某个 bot 的 Claude Code 会话（= 定时替你 @ 它派活）。机制（几点·派给谁·发哪句）在 `feishu/bridge_cron.py`；**真正的活（大脑）在那个 bot 自己仓里的一份 SOP**，触发词只是把它引过去。
>
> 实现：`feishu/bridge_cron.py`（守护进程 + CLI）· 载体：`feishu/cron-jobs/<bot>.yaml`。用法速查在 `TOOLS.md`🔵 / `feishu` skill。

## 1. 核心原则：闹钟 vs 大脑
- **闹钟**（确定性·在 Python）：几点触发（cron 5 段）、派给哪个 bot、注入哪句**触发词**。
- **大脑**（判断活·在 agent）：到底干啥 = 那个 bot **自己仓里的一份 markdown SOP**（如 `notes/docs/SOP-020-reply-to-comments`）。触发词只说"去执行你仓的 SOP-xxx"。
- **为什么**：定时逻辑不僵在 Python 里；真正的活跟着 agent、跟着仓走。加一个定时任务 ≈ 定一个闹钟，不是写一段程序。

## 2. 载体：每个 bot 一个 YAML（专属划分·别混）
```
feishu/cron-jobs/
  tb24-notes-2.yaml        ← notes-2 的定时任务全在这
  tb24-tennis-post.yaml
  <bot>.yaml …
```
- **一 bot 一文件·`bot` 名 = 文件名**（文件里不重复写 bot）→ `ls cron-jobs/` 就知道"谁有定时任务"，打开 `<bot>.yaml` 就看到它排了啥。**解决"全混一起、记不清谁有"。**
- **为什么 YAML**：prompt 常多行 → YAML `|` 块干净、可注释；JSON 多行要转义丑；markdown 机器解析脆。
- **向后兼容**：旧扁平 `cron-jobs.json` 仍读（`load_jobs` 合并两源、`(bot,name)` 去重、per-bot 优先）；新任务一律进 per-bot yaml。

一条 job 的字段：`name`（唯一）· `cron`（5 段：分 时 日 月 周·周0=日）· `tz`（默认 Asia/Shanghai）· `enabled` · `prompt`（触发词·`--sop` 会自动生成"执行本仓 SOP-xxx"）· `desc`（人读·可选）。

## 3. 多机：只跑本机的 bot（零硬编码 host）
守护进程每 tick **只【真触发】`job.bot ∈ 本机名册(bridge-bots.local.json)` 的任务**。
→ `cron-jobs/` 可 committed 共享，两台机同读一份、各自只跑自己那些 bot 的活、**不重复触发、不必手写 hostname**。（旧 `host` 字段已弃用。）

## 4. 注入 = 一条 p2a 消息（回复恒回主人）
触发时注入的 marker 复刻 on_message 信封：
```
<触发词> [飞书 from=cron:<job> to=<bot> via=定时 · route=p2a]
```
`route=p2a` → 那个 bot 干完**回复恒回主人 DM**（不漏进群·不串台·ARCH-140 §3）。

## 5. 工具（CLI · 不用手改文件）
```
bridge_cron.py board                              # ⭐ 全舰队总览：每个 agent 排了啥·下次/上次·本机●/别机○
bridge_cron.py add --bot X --name N --cron "0 9 * * *" --sop docs/SOP-xxx [--tz .. --desc ..]
bridge_cron.py add --bot X --name N --cron "…" --prompt "自定义触发词"   # 不走 SOP 时
bridge_cron.py rm | enable | disable --bot X --name N
bridge_cron.py list [--bot X]                     # json
bridge_cron.py fire <name> --dry-run              # 测：只打印 marker 不注入
bridge_cron.py start | stop | status              # 守护进程（随 feishu_bridge 整体 start/stop 起停·起停它不重启任何 bot 桥）
bridge_cron.py check "0 9 * * *" [--n 5]          # 调试 cron 表达式未来 N 次
```
- `add` 的 **bot 名对着本机名册校验**（不存在直接报错·列出可用）= 零硬编码。
- **热读免重启**：守护进程每 tick 重读 `cron-jobs/`，加/改/停 job 立即生效（唯独**改 `bridge_cron.py` 代码本身**要 `stop && start` 守护进程）。

## 6. 加一个定时任务（典型流程）
1. 在那个 bot 的**仓里**把任务写成一份 SOP（大脑）；
2. `bridge_cron.py add --bot <该bot> --name <任务名> --cron "<几点>" --sop <该SOP路径>`（闹钟）；
3. `bridge_cron.py board` 确认排上了。到点它自动执行、`route=p2a` 把结果回你 DM。

> 相关：路由（p2a/a2a/p2a-ext）= `ARCH-140 §3`；桥总体 = `ARCH-110`；bot 名册/改名 = `SOP-120`/`SOP-125`。
