# PROPOSAL · 跨智能体（跨机）任务队列 + 真完成/人确认门控 · 调研 + 方案（待 Publisher 拍板）

> 2026-06-24 · 触发：Publisher 想要一套**跨智能体、甚至跨机器**的任务队列——agent A 做完任务1（真完成）→ agent B 做任务2（依赖1）→ ...；**不盲目按序，而是每步真完成 + 人确认没问题才推下一步**（step N 有问题就停、等修复确认，不推 N+1）。要求：调研这套架构对不对、能不能做、谁管队列、怎么读彼此实时内容。
>
> **状态**：wmux 原生 a2a 工具自查 + 三路调研（仓库盘点 + 业界模式 + 我综合）。**本文 = 调研结论 + 设计方案，不是已实施**。建/不建、分几期建，等 Publisher 拍板。
>
> **🔄 2026-06-28 更新（切流后·新一轮起点）**：飞书桥已从 `xhs/orchestrator` 切到 **`link16/feishu`**，通讯层升级到 **a2a/p2a 结构标记 `[飞书_from_<发>_to_<收>]` + per-turn 路由**（回信精准回源·三来源交错不串台·不漏隐私·`send_feishu_msg --to-agent` 按名喊话已就位）。→ 本提案 **Phase 2(跨机)的通讯地基已就绪**，正是落地时机。**实现建议建在 `link16`**（通讯/编排基建仓·本文档亦宜随之迁入 link16/docs）。本次新增**硬规则 §1.5（a2a 必回派活方）**，并把 **a2a-reply-wait 工具列为第一块积木**（§5 Phase 1 起点）。「agent 状态表(空闲/忙碌·跨机)」= §2.1 typed-state 任务板；「监控面板 / 层级 agent 管一堆 agent」= §2+§5 整套。

---

## §0 · 一句话结论

> **能做，而且你已经走了 ~80%。** 你现有的「巡航总控 + 看门狗 + marker 完成闸（ready/revised/needs-human）+ 飞书 OK 确认循环」**正好就是业界对你这个规模推荐的那套架构**——中心化单写者编排 + 文件当黑板控制平面 + 确定性验证门 + fail-closed 人确认检查点。差的只是：① 把状态标志升级成**带 actor+时间戳的 typed token** ② **跨机**那一层（任务表 SSOT + 完成信号回传 + 跨机锁）③ 把「人确认才推进」从单 stage 推广到整个队列。**不用上 Temporal/Kafka 这种重型基建**——业界明确说那是你这个规模的 overkill。

---

## §1 · 直接回答你的四个核心问题

### Q1 · 谁来管队列？专开一个 session，还是任何 agent 都能推进？

**答：中心化「单写者」队列管家（专开一个 manager session）。这是业界对小团队的默认推荐，不是「任何 agent 都能推进」。**

- 业界三种拓扑（来源 Azure / Confluent / MortalApps）：① **中心化 orchestrator**（一个管家管全队列·唯一能 advance/retry/abort）② **黑板/共享状态**（所有 agent 读写一个共享态·一个 controller 决定谁下一个）③ **choreography**（事件驱动·无中心）。
- **对小 setup 的共识**：中心化更简单、状态单一真相源、debug 是一条线性 trace、易加门控；choreography 的 tracing 负担 > 收益（「orchestration 是 2026 默认」）。
- **「单写者」是关键纪律**（来源 md-task-router）：**只有 manager 写任务板**，其它 agent 只执行 + 返回结构化结果 → **不用分布式锁就消灭一整类竞态**。这正是你巡航总控的模型。
- 所以：**「专开一个管家 session」是对的**。但它可以是个**随时能起的常驻 manager**——你在任何对话里给它排任务，由它管推进；推进权不分散给每个 agent。

### Q2 · 怎么让你们读到彼此实时进行中的内容？

**答：用「推送式状态」不用轮询（偷 A2A 协议的理念）。同机的你已经有，跨机用飞书群当黑板。**

- **同机（已有）**：`wmux-rpc.js read` 读屏 + jsonl transcript 实时解析（`jsonl_reply_extract.py`）+ **v8 hook→outbox→drainer**（Stop hook 写终结态、PostToolUse hook 写进度 → drainer 推飞书）。这条链**本质就是 A2A 的「SSE 流式状态」轻量版**，方向对。
- **跨机（要补）**：飞书群当 message bus（你两机 agent↔agent 群 relay 已实证跑通）。偷 A2A 两个理念：① 任务有**显式生命周期** pending/in-progress/completed/failed ② **status 用推送/订阅，不用轮询**。
- **可选增强**：wmux 原生 **a2a 任务协议**（见 §4）——`a2a_task_send`（可 `execute:true` 直接在对方工作区起后台任务）+ `a2a_task_query`（按状态查）+ **`a2a_task_update`（只有接收方能改状态）**，自带 SSE 式实时。

### Q3 · 怎么判「真完成了」才推进，不盲目按序？

**答：Verify-Gated Completion（验证门控完成）——每步的「done」由独立的、有 ground truth 的确定性检查判定，fail-closed。**

- **核心原则（来源 agentpatterns.ai · Nguyen&Tran 2026）**：**产出结果的 agent 不是判定「做完了」的 agent**。一个独立的、只读的 verifier 坐在每个「done 声明」上，按确定性检查 admit/reject。
- **四个生效前提**：① verifier 独立于 producer ② **有 ground truth**（测试/类型检查/schema/退出码——**不是另一个 LLM 的意见**）③ verifier 在关键路径上 ④ 测过拦得准。
- **你已经有 ground-truth verifier**：`check_ready_gate.py` / `check_revision.py` / `validate.js` 的退出码——比 LLM 自评强一个量级。wmux a2a 也内建「只有接收方能置 completed」防 producer 自夸。
- **⚠️ 诚实 caveat（来源原文）**：某部署规则一致性 98.58% 但 **blocked precision 只 0.39%**（几乎每个 reject 都误报）。→ **新门先 warn-first 观察一两次确认拦得准，再升 block**。这跟你仓库已有的「加闸克制·warn-first 升级」纪律完全一致，继续保持。
- **不盲目按序的另一面**：每个 task 必须有**二元、可测的退出条件**（"improve until good" = 永不终止）。

### Q4 · 人确认才推进（你最强调的）

**答：Checkpoint-and-Review / 人确认门——默认不推进（fail-closed），只有你显式 OK 才解锁下一步。**

- 你的痛点原话：「step N 做完但人发现有问题 → 不推 N+1，等人修复确认。任务排了 1,2,3 是因为我预期第1步做完是某效果，达到才推进」。**这就是业界的 human approval gate**（LangGraph `interrupt()` / Temporal signal / Step Functions `waitForTaskToken` / Inngest `waitForEvent`）。
- **共同骨架**：① step N 后插一个「等待」节点 ② **状态写进持久层**（文件）③ 进程可以死、可以关机，**等人的那段不占资源** ④ 人发回一个信号（飞书「OK」）→ **从断点续跑，不重跑已完成 stage** ⑤ 配 timeout 兜底（多久没回 → 再喊一次）。
- **你已经在做对的事**：`PNN-awaiting-publisher.md` / `PNN-ready.md` + 飞书「OK / 换 H1」确认循环就是单 stage 版的这个。**只需推广到整个队列**：task 标 `blocked:human:<什么>` → manager 不推它的下游，直到你 OK。

### 附 · 做完但要回退？

轻量 saga 思路（来源 youngju.dev）：每个 step 记「怎么撤销我」（哪怕只是「删我写的 ready.md + flip status 回 draft」）+ 重做的 step 保持**幂等**（重跑不产生重复文件/commit）。你的 `/post-commit` 严格隔离 + reset index 已是这思路。不用上完整 saga 框架。

---

## §1.5 · a2a 回复协议（2026-06-28 新增·硬规则·这是「队列守望」的地基）

**铁律：被派活的 agent 必须回复派活方（在群里 @ 回·结构化结果）—— 不可不回。** 正因为「必回」是不变量，派活方才【总能等到】回复、「守望完成 / `--wait`」才成立。

- **必做（无条件·任何任务都要）**：被派 agent 干完 / 受阻 / 失败 → **在群里 @ 回派活方**报结果（`done` / `blocked:<原因>` / `failed:<原因>` + 一句结论）。这是 a2a 的不变量，不是可选项。
- **选做（只在任务明确要求时）**：任务里若【明确要求】额外动作（如「也 DM 主人报完成」）→ 照做；没要求就只回派活方。
  - ⚠️ **反例（2026-06-28 实修正）**：给 arch 那条写「**不是回我**·是 DM 主人」是**错的**——正确是「**回我(必须) + DM 主人(因这任务额外要求了)**」两个都要。「DM 主人」是叠加项，永不取代「回派活方」。
- **派活方侧 = a2a-reply-wait 工具**（§5 Phase 1 第一块积木）：每次 a2a 派活 = `send → robust 等到那条回复`。判定用**结构信号**，不靠猜：① 我发的消息被对端 👍 = 收到回执 ② 群里 `sender==对端 app` 且 `msg_type==text` 且时间晚于我基线的【**最新实质回复**】（跳过进度卡 `interactive` / 防回环哨兵）。
  - 先**不做**「没等到怎么自动重试」（因为不变量是『必回』·等就等到；真长期没回 = 该 agent 出事，留给监控面板/人，不盲目重发）。**但这个「等待 + 拿到结构化回复」的状态必须存在、必须是每次 a2a 派活的默认动作。**
- 与 verify-gate(§Q3)/人确认(§Q4) 的关系：reply-wait 拿到的是 agent 的**自报结果**；是否「真完成」仍由独立 verify-gate 判（agent 自报 ≠ 真完成）。两者叠加：必回(拿到自报) → verify-gate(独立验真) → 推进。

---

## §2 · 具体设计（贴你的 setup · 最简能 work）

业界对你这个规模的最简范本 = **markdown/文件当控制平面**（来源 Siyaovo/md-task-router · OpenClaw）。配方：

### 2.1 任务板 = 一个共享 typed-state 文件（SSOT · 不要重型引擎）

```
task_id | title | status(typed token) | assigned_to | depends_on | verify_gate | priority
--------|-------|---------------------|-------------|------------|-------------|--------
T1 | XCOM 改 commit/push skill | done | tb24-xcom | [] | govctl doctor 全过 | high
T2 | 本机 commit+push 渲染改动 | running:supervisor:<ts> | tb24-xhs | [T1] | git log 有该 commit | high
T3 | TB25 拉取 + 它那边 commit | blocked:dep:T2 | tb25-speech | [T2] | 远端 ref == 本地 | normal
```

- **状态 = typed token，不是 free-text**（md-task-router 说这是「对系统行为最大的单一杠杆」）：`pending` / `running:<agent>:<ts>` / `blocked:human:<什么>` / `blocked:dep:<task>` / `done` / `failed` / `needs-human`。带 **actor + 时间戳** → manager 能机械判**依赖链 / 死锁 / 超时**，不是模式匹配字符串。
- 存哪：本机 git-tracked 文件（同机）；**跨机** → 共享盘 / Git / 飞书云文档（你 send --doc 已能建在线文档）当跨机 SSOT。
- 你现有的 `_index.yaml` / `PNN.meta.json` / `_sources/PNN-*.md` 已经在做这事，只差升级成带 actor+时间戳的 typed token。

### 2.2 队列管家 = 一个单写者 manager session（巡航总控的泛化）

循环：**读任务板 → 找「依赖全 done（已验证）且无 human-block」的 task → 派给对的 agent/机器 → 守望完成 → 跑 verify_gate → 标 done（或 needs-human/blocked:human）→ 重复**。
- 唯一写任务板的就是它（单写者纪律）。
- 派活：同机 `spawn_worker.py` + wmux-rpc；跨机 飞书群 `[TASK]` 消息（或 wmux company a2a §4）。
- 守望：现有「派 worker 当轮挂 run_in_background 守望真完成态」铁律 + 看门狗。

### 2.3 跨机回传 = 标准化群消息 + 跨机看门狗

- 标准格式：`[TASK-UPDATE] <task_id> <old>→<new> <reason>`（纯文字·过对端可读铁律）发共享群。
- **跨机看门狗**：现有 watchdog + 群消息监听 = 「看到 TASK-UPDATE 就更新本地任务板」。
- **跨机锁**（防两机同时干一个 task）：共享盘/Git/飞书文件 `.lock`（同机已有 `_writer-in-flight.lock`，跨机版同理 + PID/心跳检活）。

### 2.4 人确认检查点（贯穿）

- task 标 `blocked:human:confirm-cover` → manager 挂起它 + 下游，飞书喊你 → 你回「OK」→ manager 把它 flip `done` → 下游解锁。
- 默认 fail-closed：**没你的 OK，永不推进**。配 timeout 兜底（多久没回再喊）。

### 2.5 必加的两道保险

- **硬迭代预算**：每个 worker 设 max turn/retry（你的「返修改 2 版不过才 needs-human」就是），每 task 有 binary exit，避免「improve until good」空转。
- **幂等**：每 task 重跑不产生重复 marker/commit（你的 post-commit 隔离已防）。

---

## §3 · 复用什么 / 补什么（净加克制）

| 能力 | 现有 | 复用/补 |
|---|---|---|
| 单写者管家 | 巡航总控（ARCH-310 / SOP-005）| ✅ 泛化成「读任务板的队列管家」 |
| 活性 + 被动恢复 | 看门狗（判整条线动没动·非扫错误词）| ✅ + 跨机群消息监听 |
| 真完成判定 | marker 闸 + check_*.py 退出码 | ✅ 当 verify-gate（已是 ground truth） |
| 人确认 | PNN-awaiting/ready + 飞书 OK 循环 | ✅ 推广到队列级 typed `blocked:human` |
| 同机通讯 | 飞书桥 v8 hook→outbox→drainer | ✅ 当流式状态 |
| 跨机通讯 | 飞书群 a2a relay（已实证跨机）| ✅ + 标准化 `[TASK-UPDATE]` 格式 |
| 串行锁 | `_writer-in-flight.lock`（同机）| ⬆ 补跨机版 |
| **任务板 SSOT** | `_index.yaml` 等分散 | ➕ **新建：一个 typed-state 任务板（跨机共享）** |
| **跨机看门狗 + 锁** | 无 | ➕ **新建：群消息 ingest + 共享 .lock** |

**净结果**：核心三件（飞书桥/总控/看门狗）几乎不动，只加「任务板 + 跨机协调层」一层。

---

## §4 · wmux 原生 a2a 选项（可选增强 · 同机最香）

ToolSearch 实查到 wmux 自带一套 a2a 任务协议（基本就是为这想法生的）：
- `a2a_task_send`：给另一工作区发任务 · **`execute:true` 直接在对方起后台 Claude 任务** · `silent` 不打扰对方 TUI · 可挂结构化 `data`
- `a2a_task_query`：按状态查（submitted / working / **input-required** / completed / failed / canceled）
- `a2a_task_update`：**只有接收方能改状态** + 挂 artifacts 交付物（防 producer 自夸"做完了"）
- `a2a_discover` / `a2a_broadcast` · **company 模式**：部门/lead/CEO 组织 + **inbox/ack**（正规投递通道不靠 paste）+ 在线状态
- **任务状态机 = 完整 A2A 协议**：`input-required` 正是你「等人确认」的阻塞态；`completed` 由接收方置 = 真完成才标。

**判断**：**同机多 agent 用 wmux a2a 最香**（原生、带状态机、实时）。**跨机**要确认 wmux company a2a 能否跨机器（a2a 默认按工作区/同 daemon）——能则首选，不能则跨机走飞书群 relay。建 MVP 时先小样验一下跨机 a2a 通不通。

---

## §5 · 分期落地（validate-then-scale · 先证再放大）

- **Phase 1（同机·先证配方）**：建 typed-state 任务板 + 把巡航总控泛化成「读板队列管家」（带依赖 + verify-gate + human-checkpoint）。在**一条跨 stage 链**上端到端证通（如：本机 commit → 验证 → 下一步）。
- **Phase 2（跨机）**：标准化 `[TASK-UPDATE]` 群消息 + 跨机看门狗 ingest + 跨机 `.lock`。在**两机一条链**上证通（本机做完 → TB25 拉取做它的 → 回传）。这正是你举的例子（XCOM 改完 → 我 commit → 下一个 agent 拉取再做）。
- **Phase 3（增强）**：同机换 wmux 原生 a2a（更实时）；跨机 a2a 若通也上。

每期先小样端到端证 + warn-first 观察门准不准，再放大。

---

## §6 · 待 Publisher 拍板的决策点

1. **整体**：这套方向（中心化单写者管家 + 文件任务板 + verify-gate + 人确认检查点 + 飞书跨机 relay）批不批？走 living-plan 分期建？
2. **谁当管家**：常驻一个专门的「队列管家 session」，还是让某台机的巡航总控兼任？（推荐：专门一个，职责单一）
3. **任务板存哪**（跨机 SSOT）：共享盘 / Git 仓 / 飞书云文档？（看你两机有没有共享盘；没有则 Git 或飞书文档）
4. **跨机通讯**：先用飞书群 `[TASK-UPDATE]`（已证跨机），还是先验 wmux company a2a 跨机通不通？
5. **门控严格度**：verify-gate 和 human-checkpoint 都默认 fail-closed + warn-first 起步（怕误拦），同意吗？

---

## §7 · 来源（三路调研核心链接）

- 持久执行/工作流引擎（别上重型）：youngju.dev 2026 深度对比 · DebuggAI「Queue ≠ Workflow Engine」
- 多 agent 拓扑（谁管队列）：Microsoft Azure AI Agent Orchestration Patterns · Confluent Event-Driven Multi-Agent · MortalApps · agentpatternscatalog Blackboard · Wikipedia Blackboard system
- A2A 协议（读彼此实时内容）：Google Announcing A2A · a2aproject/A2A · Atlan A2A vs MCP
- 真完成门控 + HITL：agentpatterns.ai Verify-Gated Completion（含 0.39% blocked precision caveat）· LangChain Human-in-the-loop / Interrupts · AWS Step Functions waitForTaskToken · Evidently LLM-as-a-Judge
- 小型文件落地范本：Siyaovo/md-task-router（markdown 控制平面·单写者·typed state·quiescence gate）· OpenClaw

> 仓库内已有的正确实现（直接复用 / 升级）：巡航总控（ARCH-310/SOP-005）· 看门狗（_autopilot/watchdog.py）· marker 闸（PNN-ready/revised/needs-human）· check_*.py 退出码 verifier · 飞书桥 v8（ARCH-101）· 跨机群 relay（memory project_feishu_agent_to_agent_group_relay）· `_writer-in-flight.lock` 串行锁。
