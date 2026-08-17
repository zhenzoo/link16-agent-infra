---
doc_type: ARCH
doc_id: ARCH-150
title: 给智能体排定时任务：闹钟与大脑分离
status: active
purpose: 解释定时派活的机制（几点、派给谁、发哪句）为何与任务内容（大脑）分离，以及多机不撞的实现方式。
owns:
  - cron 守护进程的触发模型与热读
  - 每 bot 一个 cron-jobs/<bot>.yaml 的划分约定
  - 守护进程只触发本机名册内 bot 的多机隔离
does_not_own:
  - 任务本身要干什么（在各 bot 自己仓的 SOP 里）
  - 桥的注入机制（见 ARCH-110）
  - 主人开关任务的操作（见 TOOLS.md 的 feishu/cron.py）
read_when:
  - 要给某个 bot 加/改/停定时任务
  - 定时任务没触发或触发到了错误的机器
  - 改动 bridge_cron.py
last_reviewed: 2026-08-17
---
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

### 5.1 主人自己开关：`python feishu/cron.py`（复选菜单 · 不用喊 agent）
开 / 关一个定时任务是**纯确定性动作**，本不该每次找 agent 代跑 `enable`/`disable` → 给主人一个复选框：

```
python feishu/cron.py            # 列全部任务 · ↑↓/jk 选 · 空格 开/关 · a 全开 · n 全关
                                 # f 立刻跑一次(要确认) · 回车 保存退出 · q 放弃退出
python feishu/cron.py board      # 带参数 = 原样透传给 bridge_cron.py（board/status/add/fire/start…）
```

- **`✓`=开着**（到点自动派活）· **`*`=本次改动还没保存**；保存才落盘（`q` / Ctrl-C 一律不写）。
- 写回仍走 `_save_bot_file` → `cron-jobs/<bot>.yaml`，**守护进程热读、免重启**；每笔改动进 `_logs/bridge-cron.log` 留痕。
- **`disabled_reason` 自动维护**：关 → 写「主人手动关（时间）· 非故障 / 非跑挂了 + 恢复命令」；开 → **清掉**旧原因（否则留着会误导下一个人以为是崩了）。
- 保存后若「有任务开着但守护进程没跑」→ 当场问一句要不要 `start`（开了却没闹钟 = 白开）。
- **两种输入模式自动选**：真控制台（Windows Terminal / PowerShell / cmd）走单键；MinTTY / git-bash 那种 stdin 是管道、读不了单键 → 退回**行输入模式**（敲序号 `1 3` + 回车），功能一样。
- **为什么单独一个 `cron.py`**：`bridge_cron.py` 裸跑必须保持 `status`（agent / 脚本常这么调，不能把它们卡进交互 TUI）→ 人用短门面、机器用原名。菜单本体是 `bridge_cron.py menu`。

### 5.2 完整 CLI（agent / 脚本 / 高级操作）
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
