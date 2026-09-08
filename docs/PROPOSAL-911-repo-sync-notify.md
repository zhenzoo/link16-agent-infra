# PROPOSAL · 跨机「推完即通知对端拉取」+ agent 状态表要不要做 · 调研 + 方案（待主人拍板）

> 2026-07-03 · 触发：主人观察到——两台机之间之所以要通讯，**主因是几个共享仓要互相同步更新**（`xhs-card-gen` / `link16-agent-infra` / `.claude-personal`）。想法：本机 commit+push 完 → 触发一个（手动或 agent 自触发的）动作 → **通知另一台机管这个仓的 agent**，它空闲就 `git pull` 拉齐。
> 附带第二问：PROPOSAL-910 里提的「状态表 / 管家 / 实时空闲」要不要建？做成实时状态表（甚至前端）有没有必要？
>
> **状态**：本文 = 调研结论 + 设计方案 + 明确推荐。是 PROPOSAL-910（跨 agent 任务队列愿景）的**第一个具体、需求驱动的窄切片**。
>
> **✅ 2026-07-04 已落地第一层（主人拍板 · 边聊边建）**：① 名册抽成独立机器可读 SSOT `feishu/agent-registry.json`（27 agent · 本机 19 verified / 对面 8 待 tb24 核）② **查名册 tool** `feishu/registry.py`（`list`/`peers`/`whois`/`resolve`/`shared-repos`/`is-shared` · 登记进 `TOOLS.md`）③ 顺带修掉 open_id 戳 bug（桥 `a2a_from_name` 拿 open_id 查名册换回名字）④ **接线 skill**：`/pull` 写进接收约定、`/push` 加「共享仓才半自动提议通知对面拉」步。**决策落定**：名册格式 = JSON（桥要读、免依赖）；接收约定 = 写进 `/pull` skill 本身（非 CLAUDE.md）；不做单独脚本（agent 直接调 `registry.py` + `send_feishu_msg`）；共享仓 = `shared_repos` 白名单（就 3 个）。下方原始设计正文保留作背景。

---

## §0 · 一句话结论

> **两块拆开看。**
> ① **「推完通知对端拉取」值得现在做，而且 90% 的零件已经在了**——通知通道 = 现成的 a2a（`send_feishu_msg --to-agent`），拉取安全性 = 现成的 `/pull` 纪律。缺的只有一个**跨机共享的「仓↔agent」小名册**（谁在哪台机管哪个仓）+ 一层薄封装。
> ② **「实时空闲状态表 / 前端」现在【不必】做。** 因为 a2a 是**推送式**的：你发「有空拉一下 link16」给对端 agent，它**忙就排队、闲就立刻做**——**接收方自己的「回合边界」就是天然的空闲闸**，根本不需要我们去测「它现在空不空」。真要的不是「实时看板」，而是一张**很少变的静态名册**（目录性质，不是监控性质）。实时看板留到真有「多机流水线卡住要看谁堵着」的具体痛点再做。

---

## §1 · 需求拆两件事（别混）

| # | 主人说的 | 本质 | 现在做？ |
|---|---|---|---|
| A | 推完通知对端拉取 | **事件触发的一次性通知**（push → notify → pull） | ✅ 值得做（需求真、零件全） |
| B | 状态表 / 管家 / 实时空闲（甚至前端） | **持续维护的状态视图** | 拆两半：静态名册✅做 · 实时看板⏸缓 |

---

## §2 · A：推完通知对端拉取 —— 设计（最简能 work）

### 2.1 全链路（四步 · 大半是现成的）

```
本机 agent               对端 agent（另一台机）
  │ 1. /push 推共享仓成功
  │ 2. 查名册：这个仓在别的机上是谁管的
  │ 3. send_feishu_msg --to-agent <对端> "pushed <仓> @<commit>, 有空拉一下"
  │──────────── 飞书群 a2a ──────────────▶│ 4. 桥把消息注入对端会话
  │                                       │    对端：先 commit/stash 本地未提交 → git pull → 有冲突就解
  │◀────── send_feishu_msg 回执 ──────────│    → send 回「已拉到 @<commit>」
```

- **第 3 步「通知」= 现成的 a2a**：v0.6 协议里 `send_feishu_msg --to-agent B` 就是「发 peer 的唯一路」（自盖 `[飞书_from_A_to_B]` 戳、进共享群、@对方）。**不用造新通道。**
- **第 4 步「拉取」= 现成的 `/pull` 纪律**：先看有没有未提交 → 有就先 commit/stash → 再 fetch/pull → 有冲突我解不硬来。routing 走对端 agent（而不是从外部 SSH 盲拉）**正是因为**只有 agent 知道自己有没有未提交的活、能安全地选时机、能解冲突。

### 2.2 关键洞察：「空闲」是【涌现】出来的，不用去测

主人担心的「它忙的时候别打扰、它空了再拉」——**a2a 的推送模型天然解决，一行检测代码都不用写**：

- 桥收到「有空拉一下」→ 注入对端 Claude 会话。
- 对端**空闲**（没在跑）→ 立刻起一个 turn 处理这条 → 拉 → 回执。
- 对端**正忙**（turn 中）→ 这条消息**排队**，等它当前这段干完、到回合边界，才看到「拉一下」→ 再拉。
- 对端**有未提交的活** → 它（作为有脑子的 agent）先 commit/stash 再拉、有冲突自己解。

→ **接收方自己的回合节奏就是空闲闸。** 这也是为什么「路由给 agent」比「外部 SSH 盲拉」强：盲拉会把对端正编辑的工作树搅乱，agent 拉不会。

> 唯一缺口：对端会话**根本没起**（机器关了 / 那个 bot 没 spawn）→ 消息躺群里没人注入。这不是 bug，是**最终一致**：等对端下次开工前 `/pull` 一次就补齐。共享仓「开工前先 pull」本来就该是默认动作，兜底足够。

### 2.3 缺的那一块：跨机「仓↔agent」名册

现在的 bot 名册 `feishu/bridge-bots.local.json` 是**每台机各自的、gitignore 的**——本机看不到对端有哪些 agent。要路由「push 完通知谁」，得有一张**两台机都看得到、进 Git 的**小表。按仓聚合最省事：

```yaml
# link16/agent-registry.yaml（committed·跨机 SSOT·只登【共享仓】）
xhs-card-gen:
  - { agent: tb25-xhs-card-gen, machine: machine-a,   cwd: D:/410_VibeCoding/Post/tools/xhs-card-gen }
  - { agent: tb24-xhs-card-gen, machine: machine-b,  cwd: E:/410_VibeCoding/Post/tools/xhs-card-gen }
link16-agent-infra:
  - { agent: tb25-link16, machine: machine-a,  cwd: D:/410_VibeCoding/Post/tools/link16-agent-infra }
  - { agent: tb24-link16, machine: machine-b, cwd: E:/410_VibeCoding/Post/tools/link16-agent-infra }
claude-personal:
  - { agent: tb25-ccp, machine: machine-a,  cwd: C:/Users/<user>/.claude-personal }
  - { agent: tb24-ccp, machine: machine-b, cwd: E:/... }
```

- 路由 = 「我 push 的仓 R → 名册里 R 名下、**机器 ≠ 本机**的 agent，逐个 send 通知」。
- **命名已经帮上忙**：本机全 `tb25-*`、对端全 `tb24-*`、后缀一致（`tb25-link16` ↔ `tb24-link16`）。所以「前缀换机器、后缀不变」是个可靠的**兜底启发式**；名册只是把它**显式化**（对不上前缀 heuristic 的特例能覆盖）。
- 只登**共享仓**（3 个）。单机仓、非仓文件夹（`Lab` 是故意不建仓的）**不进**——它们不跨机同步，没有对端可通知。

---

## §3 · B：状态表 / 实时空闲 / 前端 —— 拆成两半

### 3.1 建：静态名册（就是 §2.3 那张）—— 目录，不是监控

主人说的「状态表」，**真正有用的那半是这张静态名册**：谁在哪台机管哪个仓、CWD、职责。它**很少变**（只在加/挪 bot 时改）、**进 Git**（两机无共享盘，Git 就是跨机 SSOT）、成本极低、直接解决「push 完该通知谁」的查表。**这就是 PROPOSAL-910 §2.1 任务板的种子**，也是主人记忆里「状态表·种子=名册·走 Git」那条。

### 3.2 缓：实时空闲看板 / 前端 —— 现在【不必】，三条理由

1. **a2a 推送已经替代了它的核心用途**：你要「它空了再拉」→ 发过去、它自己按回合节奏消化（§2.2）。**根本不需要先知道它空不空**再决定发不发。看板要回答的问题，通知模型已经不问了。
2. **实时空闲 = 持续心跳 = 轮询**：要在看板上实时显示「闲/忙」，每个 agent 得**不停上报**状态到某处。这正是你不喜欢的轮询（`prefers-event-callbacks-over-polling`）；而且「闲」是**极易过期**的信号——你读到「闲」的那一刻它可能已经忙了。
3. **投入产出比现在是负的**：做一个跨机实时前端（心跳采集 + 存储 + 前端渲染 + 刷新）是**重活**，而当前唯一的具体需求（同步仓）根本用不上它。

> **但没堵死后路**：真要建看板，**原始信号已经在了**——桥的 v8 hook 里，`Stop` hook 在**每个 turn 结束时触发**（= 会话转空闲的确切事件，非轮询）。将来真出现「手动编排多机流水线、需要一眼看谁卡着」的痛点，就用 Stop hook 喂一张状态表。**在 demand 出现前不预建**（对齐 PROPOSAL-910「管家 tb25-ncs / 任务板 = 需求驱动、别空转预建」）。

---

## §4 · 归属：做成 skill 还是写进 CLAUDE.md？—— 两个都要，按角色分

按 CLAUDE.md 的 toolify 原则「**确定性活→脚本 / 判断活→playbook**」拆：

| 角色 | 内容 | 形态 | 为什么 |
|---|---|---|---|
| **发送方** | push 成功 → 查名册 → send 通知对端 | **脚本 / 薄 skill** | 纯确定性（查表 + 发一条），无判断 → 固化成 `feishu/repo_sync_notify.py <仓>`，`/push` 后一步调用（或做成 `/sync-repos` 薄 skill 串起来） |
| **接收方** | 收到「拉一下 X」→ commit/stash → pull → 解冲突 → 回执 | **CLAUDE.md 行为约定**（或 ARCH-140 挂一个 a2a 用例） | 有判断（怎么处理未提交、怎么解冲突）→ 是「常驻行为」不是「可调用流程」，写进 CLAUDE.md 让每个 agent 收到就照做，复用 `/pull` |

- **不 fork `/push` `/pull`**：它俩是 vendored 的通用 skill，为这个跨机小场景改它们会污染通用工具。发送方做**独立薄 skill / 脚本**，内部**调用**现成 push 逻辑再加一步通知。
- 最小可用版其实很小：发送方就一条 `send_feishu_msg`，接收方就一段约定。可以先**纯约定 + 一个 helper 脚本**跑通，用顺手了再决定要不要包成正式 `/sync-repos`。

---

## §5 · 复用什么 / 补什么（净加克制）

| 能力 | 现有 | 复用/补 |
|---|---|---|
| 跨机通知通道 | a2a `send_feishu_msg --to-agent`（v0.6） | ✅ 直接用，零改动 |
| 安全拉取 | `/pull` skill（commit/stash→fetch→解冲突不硬来） | ✅ 接收方复用 |
| 每台机 agent 清单 | `bridge-bots.local.json`（本机·gitignore） | 种子，抽出共享仓部分 |
| 空闲判定 | 无（也**不需要**·§2.2 涌现） | ✅ 不补 |
| 空闲原始信号（备将来） | 桥 v8 `Stop` hook（turn 结束事件） | 现成·建看板时才接 |
| **跨机仓↔agent 名册** | 无 | ➕ **新建：`agent-registry.yaml`（committed·只登 3 个共享仓）** |
| **发送方封装** | 无 | ➕ **新建：`feishu/repo_sync_notify.py`（查名册 + send）** |
| **接收方约定** | 无 | ➕ **写进 CLAUDE.md：收到「拉 X」怎么做** |

**净结果**：核心通道（a2a）和拉取（/pull）不动，只加「一张名册 + 一个发送脚本 + 一段接收约定」。

---

## §6 · 分期落地（validate-then-scale · 先小样证）

- **Phase 1（先纯手动证配方）**：手写 `agent-registry.yaml`（3 仓）。本机 push link16 后**手动** `send_feishu_msg --to-agent tb24-link16`「拉一下」→ 看对端是否收到、是否安全 pull、是否回执。**一条链证通**再固化。
- **Phase 2（固化发送方）**：把「查名册 + send」写成 `repo_sync_notify.py`，`/push` 共享仓后调它（或包 `/sync-repos`）。接收方约定写进 CLAUDE.md。
- **Phase 3（按需·可能永不做）**：真出现多机流水线痛点，才用 `Stop` hook 建实时状态表 / 看板。

---

## §7 · 待主人拍板的决策点

1. **A 做不做、按此方案做**？（推荐：做，需求真、净加小）
2. **名册放哪**：`link16/agent-registry.yaml`（committed）对吗？还是想放 `.claude-personal`（跨所有仓更中立）？
3. **发送方触发**：`/push` 里自动加一步（推完自动通知），还是**手动**触发（你/agent 说一声才通知）？主人原话是「只要我触发它，或者它 use one 就触发」——倾向**半自动**：push 完 agent 提议「要不要通知对端拉」，你点头才发？还是全自动？
4. **B（实时看板）**：同意**现在只建静态名册、实时看板缓到有痛点**吗？
5. **接收方遇到本地未提交/冲突**：默认「先 commit 再 pull」还是「先 stash 再 pull」？（倾向 commit——留痕、不丢）

---

## §8 · 与 PROPOSAL-910 的关系

本文 = PROPOSAL-910（跨 agent 任务队列愿景）的**第一个需求驱动窄切片**：只做「push→通知→pull」这一条最实的链，**不上**管家（tb25-ncs）/ typed-state 任务板 / verify-gate 那整套重的。名册（§2.3）正是 910 §2.1 任务板的种子——将来真要队列，从这张名册长出去。**先把这条窄链跑顺，再看要不要长成 910。**
