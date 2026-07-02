# PLAN-911 · a2a 通讯健壮性四修（活计划）

> **类型**：PLAN（临时·做完归档）· 绑 [`ARCH-140`](ARCH-140-a2a-comm-protocol.md)（协议 SSOT）+ [`PROPOSAL-910`](PROPOSAL-910-cross-agent-task-queue.md)（队列地基）· 走 living-plan + validate-then-scale。
> **状态**：`plan_version: 1`（2026-07-02 建档·**调研+承重事实已亲验·待主人拍板 §3 决策点后动手**）。
> **一句话**：a2a「喊话」已能 work（[`PLAN-910`](PLAN-910-a2a-reply-wait.md) 第一块积木交付了 send/reply/reply-wait），但**不够稳**——四个有实据的真问题。本计划稳修它们，**净减≥净加·零硬编码·全 SSOT·全靠结构化信号**。
> **红线（主人 2026-07-02 定）**：够用就行别过度；不退化、不拆坏现有收发（生产基建·巡航/总控桥都在用）；判定全靠**结构化信号/信封/锁/注册**，不靠软规则记忆或 taste。

---

## §0 · 承重事实（主 session 亲 Read 代码验证·file:line）

1. **problem 1 根因 = 桥【丢弃】晚到的 a2a 回复**（不是收不到）。对端 B 回复经 B 的 drainer 发到共享群、@ 发起方 A、尾缀哨兵 `PEER_LOOP_MARK`（`feishu_bridge.py:1578`）。A 的桥**确实收到这条事件**（`on_message`·`is_group` + `mentioned_bot`），但 `feishu_bridge.py:1320` 见哨兵就 `return` 丢弃（防 A↔B 回环）。⇒ **唯一接住晚到回复的是 `send_feishu_msg.py --wait` 那个独立子进程**（`send_feishu_msg.py:316 wait_for_reply`）；窗口一关，回复落群没人接（今日实证：tb25-ccp 12:02 回复、120s 窗超时、只能 `bridge_feishu_probe --group` 手捞）。
2. **problem 2 根因 = 信封 `from` 直接塞 open_id**。`feishu_bridge.py:1476-1481`：群消息（a2a）时 `from_disp = (sender or "agent")`，`sender = msg.sender.open_id`（`:1310`）⇒ 注入的信封成 `[飞书 from=ou_e7225b23… to=tb25-link16 via=群 · route=a2a …]`，`from` 是裸 open_id 不是名字。
3. **SSOT 已在消息里**：发信方 `send_feishu_msg.py:418` 盖 `[飞书_from_<发>_to_<收>]`（用**名字**·非 open_id）。到 `:1476` 时这个戳仍在 `text`（mentions/at_name 已 strip、纯文本戳保留）。⇒ **problem 2 解 = 解析这个已在场的戳，零 API、零硬编码**（与 ARCH-140 §5b「SDK 事件侧认 from 靠可见标记」同一结论：event 侧 open_id 按 app 隔离、不可靠，戳才是 SSOT）。
4. **problem 3 名册漂移**（`bridge-bots.local.json`）：`tb25-codex` 的 `at_name=@tb25-speech-codex` ≠ `@`+name（其余 bot 都守 `at_name==@+name`）；`tb25-coacho` 的 `app_id_env=FEISHU_BRIDGE_COACHO_APP_ID`（缺 `TB25_` 段·其余都 `FEISHU_BRIDGE_TB25_*`）。三个名（内部 `name` / `@` 用的 `at_name` / 飞书显示名 `display_name`·未填）无一处机械对照——`bridge_doctor.py` 只查 outbox 运行态、**没有名册一致性检查**。
5. **problem 4 不可发现**：`send_feishu_msg.py` 只登记在本仓 `TOOLS.md:21`（link16 内部索引）；别的仓的 Claude Code 不读它 ⇒ 不知道有「按名喊智能体」这原语。**无用户级 skill 靠 description 自动浮现。**
6. **关键不变量（别拆坏）**：agent↔agent 走共享群（`shared_group()` 现解析·`oc_1c80…` 不硬编码）；哨兵 `PEER_LOOP_MARK`（U+2063×3）= 桥防回环；主人↔bot=DM(p2a)、agent↔agent=群(a2a)。

---

## §1 · 四修方案（每条：根因 → 优雅解 → 为什么不退化）

### 修 1 · a2a 回复必达发起方（即便 wait 窗关了）—— 只改守望小工具·不碰桥
**根因**：今日漏听**不是**桥的问题——回复一直好好地在群里躺着（B 的 drainer 早已把它发进共享群·`:1578`）。真正的问题是**守望小工具窗口太短**（今日 caller 只给了 120s·`wait_for_reply` 就等这么久）→ 到点下班、晚来的回复没人接。
**优雅解（主人 2026-07-02 定）= 让守望小工具「等够久 / 一直等到回复」，别过早下班。桥一行不改**：

- **`send_feishu_msg.py:wait_for_reply` 耐心版**：a2a 派活默认等 **30min**（可传更久·**默认挂后台** `run_in_background` 前台不阻塞·poll 间隔**渐进退避** 6s→30s/60s 省流）——等到对端回 `done:/blocked:/failed:` 就返回、通知发起方。
- **超时兜底 = DM 主人（主人 2026-07-02 定·「必达发起方」的人工闸）**：真等到点还没等到 → 工具**自动给主人发一条 DM**（发起方 bot 的 owner·读 `_state/bridge-owner-<bot>.json` 的 open_id）：「⏱️ 我(<发起方>) 等 <对端> 的回复等了 30min 没动静·你去群里看看 <对端> 啥情况」。**明说「在等谁、去哪看」= actionable**，绝不静默超时。这就是「回调触发自动输入」的落地：守望结束（后台 shell 回调）即自动 DM host。
- **回复永远进群（已满足·不退化）**：对端回复本就发进共享群（`:1578`）→ 主人一读群即见 <对端> 现况（`bridge_feishu_probe.py --group`）。
- **不碰生产桥**：只动 `send_feishu_msg.py` 一个函数（发起方侧工具）；**桥核心（收发 / `:1320` 回环防护）一行不改** → 生产收发零风险。

> **为什么不上收件箱 / 不改桥（主人 2026-07-02 拒·我认同）**：群 = 持久真相源、消息本就查得到（ARCH-140 §5b 早已定）。缺的只是「守望小工具太早放弃」——那就让它**等够**。这是最小改动、最不碰生产的解。

### 修 2 · 信封 from 显示名字（不是 open_id）
**解**：`feishu_bridge.py:1476` 群分支——先从 `text` 正则解 `[飞书_from_(\w[\w-]*)_to_`，命中就 `from_disp = 该名`；无戳（真人在群里 @）再退 `sender` open_id / "群友"。一个正则、零 API、用发信方自盖的 SSOT 戳。
**不退化**：只改 `from_disp` 的取值来源，route/dest/at 不动；无戳时退回原值。

### 修 3 · 名册命名漂移 —— 先讲清「一个 bot 有几个名」，再修
**背景（一个 bot = 3 个名·存在 3 个地方·改一个不会自动改另俩）**：
| 名 | 谁用 · 存哪 | 例 | 能不能随便改 |
|---|---|---|---|
| **代号**（内部 slug·名册 `name`）| **机器认它**：`--to-agent`/存档文件名全靠它 | `tb24-note` | ❌ 稳定 ID·改了机器找不到它的档案 |
| **@名**（名册 `at_name`）| 群里 @ 它时打的 | `@tb24-note` | 一般 = `@`+代号 |
| **飞书显示名**（Feishu app_name·`bot/v3/info` 拉·名册 `display_name` 多没填）| **你在飞书 App 里看到的**·飞书后台随手改 | 「TB24-note」| ✅ 给人看的·随便改 |

**根因（TB24-note 那次自我认知错乱）**：你在飞书里把**显示名**改成了「TB24-note」，但机器**名册本子**里它的**代号/@名**还是注册时的老名（可能 twitter/随笔）。那个 bot 跑 `whoami` 查的是**名册本子**（老名）+ `whoami.py:80` 还把 `at_name` 误标成「飞书显示名」⇒ 它就懵了「我是 twitter？随笔？」——**它看的本子没跟着你在飞书里的改动更新**。
**核心解（主人 2026-07-02 定）= 三名合一**：一个 bot 的**代号 = @名 = 飞书显示名**，全部统一成那个好记的名（如 `tb24-note`）。不再「代号一套、显示名一套」——**一次性全改齐**。三件事：
- **对齐现有全部漂移（一次性改干净）**：本机名册 `tb25-codex`(@名=@tb25-speech-codex) / `tb25-coacho`(env 键缺 TB25_) → 对齐。**若某 bot 连代号本身都要换成新名**（如 tb24-note·在另一台 E: 机·代号还是老名）→ 代号也改，**连带改 .env 键名 + 几个 `_state` 档案名**（机器全靠代号找它的东西）——我一趟改干净、不留半拉子。（跨机：E: 那台的本地名册我这边改不到 → 给它一条对齐命令 / 由那台 session 执行·doctor 在两台都能自查。）
- **`whoami.py`（已存在·确认过·是修不是建）**：现在它查**名册老本子** + `:80` 把 `at_name` 误标成「飞书显示名」→ 改成**当场去飞书问真实显示名**（`bot/v3/info` 的 `app_name`）+ 分行报「代号 / 飞书显示名 / @名」，bot 再不会认错自己。
- **`bridge_doctor.py --roster`（doctor 式一致性检查）**：逐 bot 把 3 个名并排列 + 机械核「三名是否一致」（+`--live` 拉 `app_name` 对照）→ 打「✅一致 / ⚠️漂移(附差异)」。以后你随手在飞书改名，跑一下就知道哪没跟上。
**不退化**：代号改动一趟做全（键名+档案名同步·改完重启桥验）；`whoami` 只加一次 API 查（失败退名册·不崩）；doctor 纯新增只读子命令、原 outbox 逻辑一行不动。

### 修 4 · 跨仓可发现（最高优先）· 一个「飞书工具总入口」skill（不是单个工具）
**根因（主人 2026-07-02 点透）**：全局 `~/.claude-personal/CLAUDE.md` **已经写了**「凡飞书相关 → 去 link16 看」，但那个 CLAUDE.md **太长**、这段被淹没 → 别的仓的 Claude **想不起来**去 link16 找飞书工具。「靠读长文记得」= 软规则、会忘。
**解 = 把它变成结构信号（description 触发的 skill·不靠记）**：走 `/toolify` 建**一个**用户级 skill（暂名 `feishu`），description 命中**任何飞书意图**（发飞书 / 飞书发消息·图·视频·文件·语音·在线文档 / **智能体互相喊话·a2a·@某 bot 让它干活** / 飞书桥起停调试 / 查 bot 消息记录 / 自查身份…）。
- **skill 正文 = 薄路由**：不复制工具、只**指到 link16 的 `TOOLS.md`（SSOT 工具索引·🔵feishu 段整套）** + 一句「你要 X → 用工具 Y」的速查（send_feishu_msg 喊话 / send_feishu_file·voice·media 发东西 / feishu_docs 在线文档 / bridge_feishu_probe 读群 / whoami 自查 / register 建 bot…）。**跨机解析 link16 路径**（D: 带 `tools/` / E: 不带·两条都试或 `$VIBECODING_ROOT` 拼）。
- **一个 skill 覆盖【所有】飞书工具**，不是每个工具一个 skill（主人要的就是这个）。它命中就把 Claude 领到 link16 全套飞书工具面前。
- 全局 CLAUDE.md 飞书段**留一句指针**指向这个 skill（skill 是主入口·CLAUDE.md 只兜底）。走治理同步（gov scope → reindex → sync·全环境）。
**不退化**：纯新增 1 个 skill + 改一行指针；不改任何现有代码/工具。

---

## §2 · Step 计划（有序·验证档位跟风险走）

| step | 改什么 | 碰哪些文件 | 删/改/加 | 验证档位 |
|---|---|---|---|---|
| **1** | 修 4「飞书总入口」skill（**最高优先·零风险先落**）：`/toolify` 建 1 个 `feishu` skill（description 命中所有飞书意图 → 路由到 link16 `TOOLS.md` 全套）+ 全局 CLAUDE.md 飞书段留一句指针 | `~/.claude-personal/skills/…` + 全局 `CLAUDE.md` + gov | 加（新能力·天然加法） | cheap（别的仓起会话验 description 浮现→领到 TOOLS.md）|
| **2** | 修 2 信封 from 解戳 | `feishu_bridge.py`(:1476 一处) | 改（净 0） | cheap(单测正则) + **e2e**(重启桥·真发一条看注入信封) |
| **3** | 修 3 三名合一：`whoami.py` 报真实显示名 + `bridge_doctor.py --roster` 一致性检查 + **一次性对齐全部漂移**（含必要的代号 rename→连带 .env 键+档案） | `whoami.py` + `bridge_doctor.py` + `bridge-bots.local.json`(+.env 键) | 改(whoami)+加(只读子命令)+改(名册/键对齐) | cheap(跑 whoami/doctor) + 代号 rename 的 bot 重启桥验 |
| **4** | 修 1 守望小工具耐心版：`wait_for_reply` 慷慨默认(30min)/上限 24h + poll 退避 | `send_feishu_msg.py`(:wait_for_reply 一个函数) | 改（净 0·无新文件·**不碰桥**） | cheap + **e2e ×异构** |
| **5** | 文档回填：`ARCH-140`(信封 from 名 + §7 守望「等够久」+ §5b 确认不建收件箱) + `ARCH-110 §2.5.1`(信封 from 名) + `TOOLS.md`(skill 入口) + `SOP-120`(三名合一规则) | 上述 docs | 改 | none |

> **净加/净减自检**：修 1/2 **改一处**（净 0·无新文件）；修 3 whoami 改+doctor 只读新增+对齐；修 4 **1 个** skill（新建能力·薄路由不复制工具）。**全批无新增运行时文件、无为凑删除硬删、不碰生产桥核心**——比 v1/v2 更瘦、更不动生产。

---

## §3 · 决策点（主人 2026-07-02 全部拍定 ✅）

1. **skill 名 = `feishu`**（飞书总入口·触发任何飞书意图→领到 link16 全套·全环境同步）。✅
2. **修 1 超时兜底 = DM 主人**（守望到点没等到 → 自动 DM host「等谁、去哪看」）。✅
3. **跨机代号 rename（tb24-note 那台 E:）= 我做完本机侧后，a2a 联系 `tb24-link16`**（E: 机负责飞书桥的 link16 智能体·较新·没改过名·由我控制、让它配合执行对齐）；**或**把名称统一登记进 `.env` 再走 `envsync` skill 同步——**取更简洁的一条**（执行时定）。✅
4. 本机两处小漂移（tb25-codex @名、tb25-coacho env 键）我直接对齐。✅
5.（修 1 守望耐心版 + 修 2 from 解戳：已定死。）

---

## §4 · 测试计划（真 a2a 往返·非纸上·≥3 异构）

- **oracle**：`bridge_feishu_probe.py --group` 读真实群（真相源）+ 被测发起方后台任务实收 verdict。
- **异构覆盖**：① **超时晚回**（派活给测试 bot·让它 sleep 到远超旧 120s 窗再回 `done:` → 验耐心版守望仍等到、返回 done）② **from 名解析**（真发一条·看注入信封 `from=<名>` 非 open_id）③ **长消息**（超 SEND_MAX 边界不截断·信封仍对）④ **跨机**（本机 D: ↔ 另一台 E:·冷启动跳卡）⑤ **三名合一**（跑 doctor --roster 见全 ✅·whoami 报真实显示名）。
- **红→绿**：修 1 前——120s 窗超时漏听（红）；修后——耐心版等到 `done:`（绿）。**看日志不只看返回值**（守望 poll 退避日志 + 返回 verdict）。

---

### 变更日志
- v1（2026-07-02 15:04）：建档。§0 五条承重事实经主 session 亲 Read 代码验证（problem 1=`:1320` 丢弃 / problem 2=`:1476` 塞 open_id / SSOT 戳在 `:418`）。四修方案定稿。
- **v2（2026-07-02·主人反馈后简化）**：修 1 砍收件箱 → 改「桥转接活会话」；修 3 补 3 名定义表 + whoami。
- **v3（2026-07-02·主人二次校正·全面简化到最不碰生产）**：① **修 1 连桥转接也不要了**——主人定「守望小工具等够久 / 一直等到回复」（默认 30min·上限 24h·后台跑·poll 退避）+ 回复本就进群（`:1578`）→ **只改 `send_feishu_msg.py` 一个函数、桥一行不改**、零风险。② **修 3 = 三名合一**（代号=@名=飞书显示名·一次性全对齐·含必要的代号 rename）+ 确认 `whoami.py` **已存在=修不是建**。③ **修 4 = 一个「飞书总入口」skill**（触发任何飞书意图→路由到 link16 `TOOLS.md` 全套·不是每工具一个 skill）——根治「全局 CLAUDE.md 太长、飞书段被淹、别的仓想不起去 link16」，用 description 触发的结构信号取代会忘的软规则。
