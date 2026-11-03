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
last_reviewed: 2026-11-04
---
# ARCH-150 · 给智能体排定时任务（CRON）—— 闹钟 vs 大脑 · 每 bot 专属 · 零硬编码

> **一句话**：到点把**一句触发词**注入某个 bot 的当前会话（按它此刻的 profile：Claude Code / Codex / Kimi Code）（= 定时替你 @ 它派活）。机制（几点·派给谁·发哪句）在 `feishu/bridge_cron.py`；**真正的活（大脑）在那个 bot 自己仓里的一份 SOP**，触发词只是把它引过去。
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

### 4.1 Codex：原生接收回执才算投递成功

Codex 的输入框出现文字、Wmux 返回按键成功、读屏暂时为空，都不能证明任务被接受。
`cron_delivery.py` 在粘贴前将任务、原文摘要和目标 thread 写入现有 `bridge_inbox`；
成功只认同一 thread、同一完整原文的原生回执：已有 `UserPromptSubmit` hook，或
TUI 网关收到官方 `turn/start` 成功响应及 turn ID。两者都更新同一记录，不另建发送队列。
官方接口合同见 [Codex App Server](https://learn.chatgpt.com/docs/app-server)。

没有回执时保持待确认。当前原生线程必须明确 idle，且 Wmux 读到输入框仍是完整原稿，才经
受工作区保护的 `input.sendKey` 补 Enter；未知结果不重新粘贴，不清稿、不新建会话。首次投递与恢复共用
终端互斥锁，已确认记录重复恢复零发送。确认窗口为 20 秒，登记的提交尝试最多 3 次，
次数跨 cron 重启保留；超过上限明确记录未确认，不能当成功。首次底层投递仍沿用桥的有界按键处理。

驱动抛出类型明确的 `RetryableHandoffError`，才证明本次零写入或已清理未提交；普通异常文本不构成证明。
Inbox将该证明单独持久化，后续诊断不能覆盖它。等待至少20秒、原线程明确空闲、绑定不变且读屏能识别
**空输入框**后，才经原受保护注入入口重新投递；空读屏不是空输入框，其他草稿不覆盖。
每次重投前原子消耗证明并累计次数，防止提交后进程崩溃导致盲目重贴；再次取得明确未提交证明才可再试。
总共最多3次投递，最后一次仍明确未提交则记`failed`并停止恢复；未知结果继续待确认。窗口持续不可读
时保留未提交证明和具体阻塞，不绕过Wmux的文件夹信任检查、不擅自显示窗口。

用户直接接管并完成同一业务后，先保存公开结果/验收证据，再用 `Inbox.scheduled_cancel(精确cron记录ID, 原因)`
取消该条过期投递；记录仍保留原文与失败原因，不伪造`UserPromptSubmit`回执，不批量取消其他消息。

待确认记录由后续 tick 续查，不受“本分钟已触发”挡住。开关关闭、用户 `/stop` 取消、
账号/会话变化、内容变化或原生状态未知时不补交。接口缺失、异常或历史为空不是未接收证明；
本机 0.166.0 的历史分页接口实测不支持，因此本链不依赖历史查询，更不解析终端排版来确认成功。
Claude/Kimi 仍走原有受保护提交分支；由守护进程登记的到点任务也等待既有 `UserPromptSubmit` hook 确认完整原文，按键成功不能提前记完成。两平台不套用 Codex 的读屏补交规则，缺回执只保留待确认。

`idle-fresh` 只在确认旧会话空闲并彻底关闭后创建新会话。排队或准备中的消息始终阻塞关闭；Codex 已提交但未确认的消息按原生当前 thread 核归属：当前或未知绑定仍阻塞，明确属于别的旧 thread 的记录保留原状态与正文，但不阻塞当前空闲会话。该判断不等于旧消息成功，也不允许重投旧消息；原生空闲和旧进程退出仍须另外确认。Claude/Kimi 保留原有保守检查。
新会话的提交未确认时，后续恢复在原会话
核对回执和草稿，不再执行关闭/重建；执行记录见 [PLAN-1200](PLAN-1200-cron-native-receipt.md)。

### 4.2 到点先登记，受阻后继续恢复

守护进程先把本轮所有到点任务写入各 bot 的原 Inbox，再开始慢启动。唯一键仍是 bot 库内的
`cron:<任务名>:<原定分钟>`；跨分钟、进程重启不换键，任务信封也带原定分钟，避免旧回执误认新一笔。`cron-last-fired.json` 只表示已登记，
采用 bot+任务名索引，不能作为接收成功凭证。旧键兼容读取，升级不追补没有登记过的历史分钟。

尚未触及提交的记录为 `scheduled`，普通消息消费者不会领取。原生忙碌、等待选择、锁被占用时
保留原任务，每20秒重查；确认可执行后才持久累计启动次数，最多3次。空闲关闭、进程退出、账号及
绑定校验沿原合同执行。一次启动失败但尚未派活可以延期再试；若启动器尚未提交并已保存本次工作区绑定，沿原绑定续启，不重新关闭它。启动事务中进程崩溃而结果未知时，
保留现场并告警，不盲目重建。独立会话内的launcher恢复另见 ARCH-110 §2.12。

一旦进入投递边界，固定原会话和原到点分钟，后续只查原生回执或执行4.1允许的有证补交，
不得再跑关闭/新建。前一笔同任务未确认时阻止下一笔重复派活。配置、账号变更、停用、删除和用户
`/stop` 取消旧待办；取消不伪造成功。达到启动/投递上限或延期24小时，保存原文及错误并向 owner
告警一次；发送失败继续用同一去重键投递告警，自动派活已停止。迟到的原生接收证据仍可确认已提交记录。

真实业务验收为2026-11-04 LAB-2已有巡航接收；延期、重启、失败、取消、跨bot同名及三平台分支
采用隔离故障注入回归，未声称下一次自然零点已验收。执行与上线记录见 [PLAN-1210](PLAN-1210-cron-and-startup-recovery.md)。

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
bridge_cron.py recover <name> --bot X             # 核原生回执，必要时补交同一草稿；已确认不重发
bridge_cron.py recover <name> --bot X --adopt-draft # 显式接管旧版没有登记的完整遗留草稿
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
