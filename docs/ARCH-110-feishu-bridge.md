# ARCH-101 · 飞书智能体桥（xhs总控桥 · owned-session 多智能体）

> **职责**：Publisher 手机/飞书 ↔ 电脑上 Claude 会话的**双向对话**。每个智能体（bot）= 飞书群里一个**常驻身份** + 桥**自己托管的一个 wmux 会话**。你在手机上 @ 它，它就把活交给它在电脑上专属的那个 Claude。
> **取代**：[`ARCH-100-orchestrator.md`](ARCH-100-orchestrator.md)（Telegram · 需代理 · superseded）。
> **本文档涵盖**（飞书相关问题先来这里找）：① bot 怎么收你的话、怎么把活交给电脑上的 Claude（§2）② **回复怎么回到飞书（v8 · hook→outbox→drainer · 2026-06-16 重构）**（§2.5）③ **回复用什么格式发给你**——飞书互动卡片 / 进度合并 / 长文分条 / 必达兜底（§2.6）④ **怎么监控、出问题去哪查日志**（§2.7）⑤ 自愈/配置/验收（§3-§5）⑥ bot 的自助能力（§7）⑦ **多媒体通道：你发图/文件 ↔ 我发图（§2.9）** ⑧ **在线查看：本地 md/HTML → 飞书云文档链接（§2.11）**。
> **状态（v8 · 2026-06-16 已部署 + 真机验证）**：回传从「轮询 jsonl + 单线程发送」重构为 **hook→outbox→drainer 事件驱动 push + doctor 机械自愈**（§2.5）——根治长 turn 队头阻塞 / 10min 卡片死 / autopilot 永不结束 / background-shell 唤醒轮丢失 / 补发淹没。早前（2026-06-15 P140）：**owned-session 多 bot 已实现跑通**（每 bot 一进程一飞书长连接 · 会话死自动重生 · 桥重启自恢复）。本轮（P140）补齐并上线三件生产级能力：**① 回复用飞书「互动卡片」流式发**（实时进度 + 最终答案 + 过程小结·§2.6）**② jsonl 钉死防串台**（多会话同目录不再读错文件·§2.5）**③ 必达发送 + trace 日志监控**（四级降级绝不丢 + 每道闸有痕迹·§2.6/§2.7）。
> **启动**：`python feishu/feishu_bridge.py`（裸跑即把所有 bot 各起一隐藏进程后台常驻 · `stop`/`status` 管理）。
>
> ⚠️ **2026-06-28 切流后更新（详见 CHANGELOG v0.2.0）**：① 注入标记格式已改为 `[飞书_from_<发>_to_<收>]`（下文多处仍写旧 `[飞书-<bot>]`·语义同·hook 兼容两者）② 回信路由改 **per-turn**——a2a(群)消息回【群】发**纯文字+@**(非互动卡片)、p2a/DM 才发卡片 ③ 本机(zhenz/D:)现 **17 bot**、跑 `link16/feishu`、开机自启已配。本文标题/「6 bot」/「不开机自启」/「互动卡片发回」等为**抽离前旧述·待整体校订**。

---

## § 1 · 两个发信身份（喇叭 + 智能体 · 永不合并）

```
你的飞书群
├ 机器人「xhs-card」  = scripts/notify.py(webhook) · 单向播报 · 纯脚本「又笨又稳」
│    瘦身后只发: 真告警(卡死/限流自愈/桥挂/escape) + 关键里程碑(ready/发布页铺好/已入库)
│    砍掉: 每小时巡检 + 逐条进度  →  想知道进度就「@ 智能体问」
└ 智能体「xhs总控桥」(可 N 个) = feishu/feishu_bridge.py · 飞书 SDK · 双向对话
     每个 bot 托管一个「桥自己开的」wmux 会话(下面 §2)
```

- **为什么两条线不合并**：喇叭必须**独立于智能体**。智能体是 LLM——会卡死、会限流；喇叭是纯脚本兜底——**智能体病了，喇叭还能喊你**。看门狗的告警也走喇叭（见 ARCH-310）。**智能体 = 主交互入口；喇叭 = 兜底播报。**
- **瘦身原则**：喇叭只发「你必须立刻知道」的（出事了 / 该你下场了）。所有「进度类」都不再主动推——你想知道，@ 智能体问一句即可。

---

## § 2 · 智能体核心机制（owned-session · 桥自养会话）

每个 bot 的生命周期，桥全包：

| 时机 | 桥自动做什么 |
|---|---|
| **首次 @它 / 它没有会话** | `workspace.new {name}` 建一个**专属 workspace**（自带 bash 终端）→ **分行发 `bash` → `cd "<cwd>"` → claude 启动命令·每行读屏探就绪再发下一行**（§2.12）→ `surfaces` 找到这个新面板的 pty → 记进会话注册表。桥先回一句「正在为你起会话…稍等」 |
| **之后 @它** | 把你的话注入那个会话（**你的输入打头·末尾缀 `[飞书-<bot>]` 标记**·标记移末尾是为了不挡你直接发 slash command）→ 会话 hook(Stop/PostToolUse) 写 outbox → drainer 读 outbox 用飞书互动卡片发回（§2.5/2.6） |
| **会话死了**（你手关 workspace/面板 · 或**关机重开/重启 wmux 致 claude 死但 pty 被恢复**） | 下次消息 `_reuse_check` 三道闸判：pty 没了 / **daemon 重启过(指纹变)** / agentName 死壳 → **自动关掉死壳重生**，不用你管（§2.14） |
| **桥进程被你关、重开** | 单实例锁顶替旧的 + 重连飞书 + 读注册表：daemon 没动则 pty 活就复用（指纹匹配）、daemon 重启过 / 死壳就重生 |

- **会话注册表（每 bot 一个文件 · 多进程无 race）**：`_autopilot/bridge-session-<bot>.json` = `{workspace_id, pty, jsonl}`（桥自己写自己读 · `jsonl` = 这个会话钉死的 transcript 路径·见 §2.5）。另有 `_autopilot/bridge-owner-<bot>.json` 记 owner。**这取代了旧的 `supervisor.pty` 单指针**——bot 绑的是「桥自己造的会话」，不是「猜哪个总控」，所以永远指得对、不会指到忙总控。
- **彻底删掉**：`supervisor.pty` 认领、`register_supervisor.py`、wsid8 pty 文件、「🛌 没人认领总控不在岗」那套——owned-session 下不需要。
- **斜杠命令**：桥**只拦截下面这 6 个自己的命令**；**其余任何 `/xxx` 一律 verbatim 透传进会话**（当 Claude Code 自己的 slash command·见末条）：
  - `/clear` → 给会话发 `/clear` 清空上下文（面板留着）
  - `/cd` → **像文件浏览器一样在目录树里走**（2026-06-18 重构 · 配置 `feishu/bridge-cd-bookmarks.json`）：
    - **无参 `/cd`** = 列【bot 当前所在目录】的全部直接子目录 + 编号 → **你回一个数字就【选中】那个目录**（手机零打字 · 不过滤 · dotfolder/归档全列）。**⚠️ 懒启动（2026-06-26）：回数字只是【选中目录·不立刻起会话】**（`_do_cd` 关旧会话 + 把目录暂存进注册表 `cwd`、清掉 runtime 字段、**不 spawn**），真正起会话推迟到你发【下一条正式消息】时由 `on_message→ensure_session` 在该目录冷启。「当前目录」= 会话注册表 `bridge-session-<bot>.json` 的 `cwd`（每次 spawn / `/cd` 都写）· 没有则 bot 默认 cwd（仓库类 bot = 本仓库根 · config 类 = 其配的 cwd）。回数字的消费在 `on_message`：上条 `/cd` 把「编号→路径」存进 `bridge-cd-pending-<bot>.json`（15min 有效），下条纯数字命中即【选中暂存】；回非数字 = 改主意，清待选照常处理。
    - **`/cd ..`** = 回上一级。
    - **`/cd <名字 或 路径>`** = 跳转。解析优先级：当前目录的直接子目录（`/cd compass` 从 Yoach 直进 `Yoach/compass`）→ 书签（`post`/`xhs`/`yoach`/`lab`/`personal` · 大小写不敏感）→ `search_roots` 全局模糊搜（唯一命中直进 · 多个列编号回数字）→ 绝对路径。
    - **路径跨机 token**（绝不写死盘符/用户名 · 遵 CLAUDE.md 跨机铁律）：`$PROJECT`=本仓库根 · `$VIBECODING_ROOT`=各机 VibeCoding 根 · `~`=各机 home · 加载时展开。`bookmarks`/`search_roots` 只服务「跳到别处」（无参列当前目录用不到它们）。
    - **自愈重生保留当前目录**（2026-06-24 · 与 `/account` 临时号自愈保留对称）：会话死掉自动重生时 `ensure_session` 在**注册表 `cwd`**（`/cd` 过的目录）起，不再无脑回名册默认；注册表无 `cwd`（首次 / `/close` 清过）才回默认。
  - `/screen` → 读屏看现场
  - `/stop` → `ctrl+c` 打断当前 turn
  - `/close` → `workspace.close` 干净撤掉这个 bot 的会话 + **清注册表（含 `/cd` 过的 `cwd`）+ 账号切回名册默认** → **下次 @ 用名册默认账号 + 默认目录重建**。起会话/关会话/自愈重生的飞书提示都打印「账号 + 目录」并各自标注「（默认）/（已切·默认 X）」，让你一眼看出用哪个号、在哪个目录起的。
  - `/help` → 列全部命令 + `/cd` 书签清单
  - **其余任何 `/xxx`**（`/resume <name>` / `/rename` / `/model` / `/compact` …）→ **原样转发进 ccp 会话**（verbatim·**绝不缀 `[飞书]` 标记**·否则行首不是 `/` → CC 不认成 slash command）。桥回一句「⏎ 已转发」。需会话已存在（先发句话起会话再发 slash）。

---

## § 2.4 · 桥起会话的 spawn 命令 = 给 bot「戴装备」（解码 · 2026-06-17）

> 2026-06-18 更新：桥已支持 **runtime registry**（`feishu/agent_runtime.py`）作为 CLI 差异的单一真相源。bot 配置 `agent` / `runtime` 省略时默认 `claude`，加 `"agent":"codex"` 则起 Codex；以后 Kimi/Gemini/Antigravity 也应按同一 registry + outbox 合约接入，不在桥主流程里散落硬编码。

> 一句话：桥起一个 bot 会话**不是裸 `ccp`**，而是 `cd <cwd>` 后敲一条「带 env + flag」的长命令（`_worker_cmd`，在 `feishu_bridge.py`）——给这个 claude 会话戴上「记录仪 + 对讲机」（hook 回传）并告诉它「中转文件放哪」。**看到终端飞一长串 = 正常，不是 bug。**

**完整命令**（以 config 为例）：
```
FEISHU_BRIDGE_SESSION=config FEISHU_BRIDGE_OUTBOX_DIR=".../_autopilot" CLAUDE_CONFIG_DIR="$HOME/.claude-personal" claude --dangerously-skip-permissions --settings ".../_autopilot/bridge-hooks.json"
```

**5 段逐段解码**：

| 段 | 干嘛 | 为什么需要 |
|---|---|---|
| `FEISHU_BRIDGE_SESSION=<bot>` | 标记「这是桥起的会话」 | hook 靠它判断该不该动——**只对桥会话动**，绝不泄漏到你日常 ccp（env 不命中即 `exit 0`） |
| `FEISHU_BRIDGE_OUTBOX_DIR=<repo>/_autopilot` | 告诉 hook 回传中转文件写哪 | drainer 从那读 → 发飞书（见下「为什么 _autopilot」） |
| `CLAUDE_CONFIG_DIR=$HOME/.claude-personal` | 用哪套 claude 配置 | 桥会话统一走 personal 配置（**按用户/配置归类·与工作目录无关**） |
| `claude --dangerously-skip-permissions` | 跳过「允许用此工具吗」弹窗 | bot 无人值守、没人在键盘点「允许」→ 必须跳。**开机那句权限警告 = 这个 flag 的提示**，正常 |
| `--settings "<repo>/_autopilot/bridge-hooks.json"` | 给【这个会话】挂 Stop + PostToolUse 两个 hook | v8 回传的核心，见下 |

> 为什么不再是裸 `ccp`：v7 回传靠轮询（不挂 hook）；v8 改「hook 主动推」→ hook 必须在**会话 spawn 那一刻**用 `--settings` 挂上 → 命令就长了。是 v8 的必要代价。

**`_autopilot/bridge-hooks.json` 是什么**：给 claude 的一份**只对桥会话生效的额外「规矩单」**（`--settings` 临时挂，**绝不进**全局 `~/.claude/settings.json` 或项目 `.claude/settings.json`）。`write_hooks_settings()` 运行时生成，就 2 条 hook：
- **Stop**（matcher `*`，每轮触发）→ 跑 `hooks/bridge_stop.py` → 把这轮最终回复写进 outbox。
- **PostToolUse**（matcher = 一串工具名 `Bash|PowerShell|Edit|…|Agent|…`）→ 跑 `hooks/bridge_posttool.py` → 把进度写进 outbox（实时进度卡）。matcher 含哪些工具 = 决定「哪些动作会刷新进度卡」（2026-06-17 已含 `Agent`/`PowerShell`）。
- 都 `async:true`（不阻塞会话）+ `timeout`（最多等几秒）。

**为什么 outbox 放 `_autopilot/`**：`_autopilot/` 是本项目「**后台系统的运行时状态 + 草稿**」总目录——巡航(autopilot)、看门狗、桥 都把临时状态 / 中转文件丢这（`_` 前缀 = scratch，不进正式产物、不是源码也不是成品帖）。桥的回传中转(outbox)正属此类。典型住户：`bridge-outbox-<bot>.jsonl`（回传中转·hook 写 / drainer 读）· `bridge-outbox-hwm-<bot>.json`（读到哪的高水位）· `bridge-session-<bot>.json`（会话登记）· `bridge-hooks.json`（本规矩单）· watchdog 状态。

> ⚠️ hook 找它依赖的解析脚本 `jsonl_reply_extract.py` 用**自身相对路径**（`Path(__file__).parents[1]` = `feishu/`），**不靠 `CLAUDE_PROJECT_DIR`/cwd**——否则 cwd≠xhs 仓库的 bot（如 config，cwd=`~/.claude-personal`）会 import 失败 → 进度退 `🔧Bash`、Stop hook 静默 return 致该 bot **失声**（2026-06-17 实证修复 commit `3ee02ed`）。

---

## § 2.4.1 · 多 Runtime 适配（Claude / Codex / future CLI · 2026-06-18）

**原则**：桥只管「飞书收发 + wmux 注入 + outbox/drainer」。CLI 差异全部收束到 runtime adapter，不允许在桥主流程里到处写 `if codex`。

| 层 | SSOT | 说明 |
|---|---|---|
| bot 选择哪个 CLI | `feishu/bridge-bots*.json` 的 `agent` / `runtime` 字段 | 默认 `claude`；Codex 写 `"agent":"codex"`；本机私有 bot 仍放 `bridge-bots.local.json` |
| CLI 启动命令 / ready / live / transcript 策略 | `feishu/agent_runtime.py` | Claude、Codex、future/custom 的唯一分叉点 |
| 回传格式 | `_autopilot/bridge-outbox-<bot>.jsonl` | `answer/progress/ask` 记录是跨 CLI 合约；飞书发送层不关心来源 |
| Claude hook | `_autopilot/bridge-hooks.json` + `feishu/hooks/bridge_*.py` | Claude 支持 per-session `--settings`，所以桥 spawn 时临时挂 hook |
| Codex hook | `CODEX_HOME/hooks.json` + `feishu/hooks/codex_bridge_*.py` | Codex 从 CODEX_HOME / project `.codex` 发现 hook；用 `install_codex_bridge_hooks.py --write` 合并安装 |

**Codex 启动命令**（由 `agent_runtime.py` 生成，不手写到多处）：

```bash
FEISHU_BRIDGE_SESSION=<bot> FEISHU_BRIDGE_OUTBOX_DIR="<repo>/_autopilot" CODEX_HOME="<home>" codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --no-alt-screen -C "<cwd>"
```

注意：`--dangerously-bypass-approvals-and-sandbox` **不要**再叠 `-a never` / `-s danger-full-access`，Codex CLI 会拒绝这种组合。`--dangerously-bypass-hook-trust` 只跳过 hook trust，不跳过项目目录 trust；首次进新目录若出现 `Do you trust the contents of this directory?`，桥会自动按一次 Enter 继续。

**Codex 回复解析**：不要复刻 Claude JSONL parser。Codex 官方 hook Stop payload 有 `last_assistant_message`，`codex_bridge_stop.py` 直接以它为最终回复；PostToolUse 用 `tool_name/tool_input` 写 compact progress。这样不会依赖 Codex transcript JSONL 的非稳定格式，也不会影响 Claude 的 race-guard JSONL 解析。

**安装 Codex hooks**（默认 dry-run，`--write` 才写全局 Codex 配置）：

```bash
python feishu/install_codex_bridge_hooks.py
python feishu/install_codex_bridge_hooks.py --write
```

该脚本合并到 `CODEX_HOME/hooks.json`，按 command 去重并保留已有 hook（例如个人 Stop 声音提醒）。Codex hook 脚本自身仍用 `FEISHU_BRIDGE_SESSION` 守门，所以即使全局安装，也只对桥 spawn 的 Codex 会话写 outbox；普通 Codex 会话 env 不命中即 no-op。

**当前边界**：AskUserQuestion 结构化检测仍是 Claude PreToolUse 路径；Codex 的交互问题样式需等真实 payload 后再补 adapter。桥的 `/screen`、`/stop`、`/close`、`/cd`、附件下载、飞书发送路径已 runtime-neutral。

详细 rollout 见 [`docs/_plans/PLAN-2026-06-18-bridge-multi-agent-runtime.md`](_plans/PLAN-2026-06-18-bridge-multi-agent-runtime.md)。

---

## § 2.5 · 回复怎么回到你飞书（v8 · hook→outbox→drainer · 2026-06-16 回传重构）

> 一句话：**不再轮询 jsonl 猜「这轮答完没」**，改成用 **Claude Code 自己的 hook 主动 push**——worker 会话每结束一轮（Stop hook）、每调一个实质工具（PostToolUse hook），就把内容写进该 bot 的 **outbox 文件**；桥的 **drainer** 读 outbox 发飞书。事件驱动、不堵塞、不挑触发源。

- **为什么换掉旧轮询**（v7 的 `mirror_tailer` 轮询已退役·实证根因见 `_autopilot/_BRIDGE-HARDENING-LOG.md`）：旧法靠后台 tailer 轮询钉死的 jsonl + 等「带文字的 end_turn」+ **单线程发送**，三个结构性病：① 单发送引擎被一个长 turn **队头阻塞**（`REPLY_TIMEOUT=24h`）② 流式进度卡飞书侧 ~10min 强关后**不再刷** ③ 持续运行的 autopilot turn **永不干净 end_turn** → 永远等不到。且**只认 `promptSource=typed` 的真人键入当锚点** → **background-shell 完成唤醒的那一轮**（系统注入·非 typed）**结构上必丢**。
- **v8 怎么做（4 件）**：
  1. **作用域**：桥 spawn worker 时带 `--settings <_autopilot/bridge-hooks.json>`（运行时生成·绝对路径·跨 repo 安全）+ env `FEISHU_BRIDGE_SESSION=<bot>` / `FEISHU_BRIDGE_OUTBOX_DIR`。hook **只作用桥起的会话**（env 不命中即 `exit 0`）→ **永不进你日常 ccp / 不写项目 `.claude/settings.json` / 零额外开销**。
  2. **Stop hook**（`feishu/hooks/bridge_stop.py`）：一轮结束 → 读 transcript，**只取 anchor(末条真用户消息)之后【终结态消息】(`stop_reason ∈ {end_turn, max_tokens, stop_sequence, refusal}`)的 assistant 文本拼接**（结构上排除 `tool_use`/`pause_turn` 的过渡话）→ 追 `{"kind":"answer",…}` 到 `_autopilot/bridge-outbox-<bot>.jsonl`。⚠️ **竞态防护（2026-06-18 · 详见 `_BRIDGE-HARDENING-LOG.md §9`）**：hook 开火与「最终答案落盘」几乎同刻，为防读在写前、抓到上一块过渡文本（实证：tb25-speech 把调工具前的「Now let me publish…」当答案发），**在 15s timeout 内短轮询（~200ms/次）直到终结态文本出现再写**；到点仍空 → 不发（宁缺勿错）。**每轮都触发**：首轮 / autopilot 每轮 / **background-shell 唤醒轮** —— 全覆盖（旧轮询漏掉的就是这些）。
  3. **PostToolUse hook**（`bridge_posttool.py`·matcher 收窄到实质动作 Bash/Edit/Write/Task/… 跳过高频 Read/Glob）：每个工具完成 → 追 `{"kind":"progress","label":…}`。**取代会过期的流式进度卡**（每条独立·永不 10min 死）。
  4. **drainer**（`bridge_outbox.outbox_drainer`·桥进程后台 task·**唯一发送引擎**）：byte-offset HWM 增量读 outbox（重启不重放）→ answer 立即 `card_send` / progress 限流合并（`coalesce_sec`）发 → 去重集防双发。**不再等 turn、不被任何长 turn 阻塞**。
- **SSOT / 隔离**：hook 只写 outbox 文件（**不碰飞书凭据**）；唯一持飞书 WS + 凭据的是桥进程；outbox **单写（hook）单读（drainer）**。
- **⚠️ 过渡铁律**：hook 只在会话 **spawn 那一刻**（`--settings`）挂上 → **重启桥不会给已在跑的旧会话补 hook**。新会话自动带 v8；已在跑的会话（旧桥裸 ccp 起的）要 **respawn**（`/close`+re-@ 或自然重启）才获 v8 auto-mirror（总控下次巡航自动获得）。
- _(jsonl 钉死 / 唤醒判别那套是 v7 防串台机制·v8 outbound 已不依赖 jsonl·现仅 `/screen`、`cmd_doctor` 显示用·`on_message` 里 re-pin 循环标 vestigial·下轮清理删)_

### § 2.5.1 · 回信送哪（路由元数据信封 · 2026-06-29 根治「旧便签串台」）

> **一句话**：「这条回复该回 DM 还是回某群+@谁」= 焊在【消息本体】的结构化信封里、跟消息绑死；hook 直接从本条消息解析，**不再靠会过期的旁路便签**。

- **信封格式**（桥 `on_message` 注入时缀在消息末尾·人读 + 机读合一）：
  - DM（p2a）：`[飞书 from=host to=<bot> via=DM · route=p2a]`
  - 群 a2a：`[飞书 from=<发信open_id> to=<bot> via=群 · route=a2a dest=<群chat_id> at=<发信open_id>]`
- **hook**（`bridge_userprompt.py`）每轮用正则从本条 prompt 解析 `route=/dest=/at=` → 写 `bridge-turn-route-<bot>.json`（schema 不变 `{kind,dest?,at?}`）→ drainer / `bridge_stop` 照旧读它发。回复 = 把信封「倒过来」（from↔to、原 via）。
- **根因（实证 2026-06-29）**：旧机制把「回哪」写在**单独的 `bridge-next-route-<bot>.json` 便签**（per-bot 旁路文件），靠「下一轮 hook 消费即删」。但群消息那轮若没干净跑 hook（回信失败 / 会话冷重启 / env 丢），**便签不被消费就成地雷**——一张 23:18 tb25-ccp 在群 @arch 写的便签躺了 ~21h，被次日 20:46 主人的「注册 bot」DM 踩中 → arch 的 DM 回复漏进群 + @错 bot（哨兵挡住没成回环）。信封把回址跟消息绑死 → **按消息原子化，跨会话 / 交错 / 冷重启都不串、不过期**。
- **兼容窗口**：hook 解析不到信封（桥重启前的旧标记 / 旧 `send` 路径）→ 退回旧 `is_feishu + 便签` 兜底；桥重启后每条注入都带信封 → 便签转 vestigial（`_write_next_route` 暂留兜底）。隔离测试 8 场景全过（含「旧群便签 + DM 信封 → 仍回 DM」「撞名 + 信封 → 仍回 DM」）。
- **自查身份**：bot 不确定「我是谁」→ `python feishu/whoami.py`（读 `FEISHU_BRIDGE_SESSION` env + 名册 + 会话记录）。信封里的 `to=<bot>` 就是桥按这个身份钉的。

---

### § 2.5.2 · 回信送达保证（at-least-once · 2026-06-30 根治网络抽风丢消息）

> **一句话**：网络/DNS 抽一下时，回信不再被【丢】——发不出去就【留着、网络回来自动补发】，且不重复。

- **病（实证 2026-06-29 夜）**：DNS 抽了约 2 分钟，drainer 发卡撞 `getaddrinfo failed`；但旧逻辑「读一条 → 发一条 → **不管成没成都推 HWM（书签）+ 标 sent**」→ 那几条（含注册链接）被**跳过、再不回头**，网络恢复也不补。WS（入站）本就自动重连、**你发的我始终收得到**；丢的只在**出站**。
- **修**：`drain_batch` 发 **answer / ask** 时，逐块发、记已发数（`state["partial"]`）；任一块发不出 → 抛 `RetrySend`，`outbox_drainer` **不推 HWM、不标 `sent`** → 下一轮重发，直到成功。已发的靠 `sent` + `partial` **去重不重复**。
- **防永堵**：同一条卡超 `GIVE_UP_SEC`（默认 600s）还发不出（多为**永久错**·如无目标 / 被拒，非网络）→ 放弃推进解堵，不无限 hold。
- **`_send_plain` 返回送达布尔**（True=发出 / False=失败），drainer 据此判要不要重试。**progress** 仍 best-effort（临时进度·可丢，不 hold 队列）。
- **验**：隔离测试（注入断网通断 + 假时钟驱动真 `outbox_drainer`）4 场景全过——断网 2 轮→恢复补发恰 1 次 / 一直在线正常发 / 恢复后多轮不重复 / 永久错到点放弃解堵。

---

## § 2.6 · 回复用什么格式发给你（统一卡片流 · v8.1 · 2026-06-16）

> **一条铁律贯穿进度 + 回复**：内容都走飞书**互动卡片**；一张卡 `update_card` **原地长大** → 满 ~2800 字（`CARD_BUDGET`）**或 `update_card` 失败** 就冻结、开新卡接着写（**不截断·不重发**）。`guaranteed_send`(markdown) 降为「发卡彻底失败」的最终兜底（几乎不触发）。

- **进度卡（原地长大 + 满则轮换）**：PostToolUse hook 把【当前轮结构化 steps】写 outbox；drainer 维护「当前卡」的 message_id，每来新进度就 `update_card` **原地刷新这张卡**——你看到的是**同一张卡在长大**（实时显示 💭思考 / 📝文字 / ✏️📖🔧 全工具 + 头部 🔧/💭/🪙 计数）。卡满 ~2800 字 **或 update_card 失败（撞飞书改卡上限）→ 冻结当前卡、开新卡接着写**。**关键：`update_card` = `im.message.patch` 普通消息编辑·不是流式卡·无 10min 死**；卡数随【信息量】有界增长，**不随时间线性刷屏**。
- **答案卡（同款·超长拆连续多卡）**：Stop hook 把该轮最终回复 + 过程小结 footer 写 outbox；drainer 发答案卡，**>2800 字按行拆成连续多卡**（card1 满→card2 接着写·**不再退 markdown**）。
- **最终兜底**：只有 `new_card`/`update_card` **彻底失败**才退 `guaranteed_send`（互动卡→markdown→text→webhook·每级验真送达）。
- **裸 URL 自动 `_linkify`** 成可点链接。
- **drainer deps**（注入·见 `feishu_bridge.run()`）：`new_card(text)->message_id`（`_ensure_card_snapshot` 发卡）· `edit_card(mid,text)->bool`（`update_card`）· `send_plain(text)`（`card_send` 兜底）。`coalesce_sec` 只作「相邻 update 最小间隔（批量化）」，**轮换靠字数/失败·不靠时间**。
- **裸 URL 自动 `_linkify`** 成 `[url](url)` 可点（飞书卡片不自动 linkify 裸网址）。
- **🔒 机械闸 `_seal_bare_urls`（2026-06-24）**：卡片路径走 `_linkify` 已包链接·**但 `guaranteed_send`(markdown/text 必达兜底/镜像直发) 不经 `_linkify`** → 裸 URL 紧贴 CJK/全角时飞书**原生 autolink 贪婪**把后续中文整段吞进 href（实证：`https://x.com/…872（中文…)` 渲成一整条超链接·href 里 `%EF%BC%88…`）。修：在**最低发送收口 `_send_checked`**（覆盖 guaranteed_send 的 markdown+text）+ `_send_group_text`（a2a 群）对 payload 跑 `_seal_bare_urls`——裸 URL 紧跟非 ASCII 时插一个空格强制 autolink 在 URL 真末尾终止（只在该精确危险态触发·8 例单测过·URL 本身不改·已 `[](){}` 包的靠负 lookbehind 跳过）。软规则（链接单独成行）只是兜底·这道闸才是确定性保证。**改桥代码需重启桥才生效**（别在活会话中途重启）。

---

## § 2.7 · 怎么监控 / 出问题去哪查（每道闸都有痕迹）

> 目标：任何一条消息从「飞书进」到「回复出」，每一道关卡都留痕，出问题能回溯卡在哪一环。

- **日志在哪**：每个 bot 一个文件 `feishu/_logs/bridge-<bot>.log`（**追加不覆盖**·重启不清空·能查历史·每次重启有 `===== restart 时间 =====` 分隔）。
- **trace id 贯穿全链路**：每条消息分一个 6 位短码，一条消息从进到出按顺序打这几行——

  | 日志行 | 含义（这道闸过了） |
  |---|---|
  | `[id] 收到 <谁>: <前80字>` | 消息进来 + 通过鉴权（owner/白名单） |
  | `[id] 已注入 pty=… pinned=有/无` | 已把话发进 bot 会话；`pinned` = 这会话的 jsonl 是否已钉死 |
  | `[id] 📌 钉定 transcript …xxx.jsonl` | （首轮/换会话）探测并钉死了它自己的 jsonl |
  | `[id] 回复 <谁> via=card/markdown/text/webhook stream_ok=… len=… 耗时` | 最终怎么发出去、走第几级、多少字、花多久 |

- **怎么一眼判断健不健康**：
  - 正常 = 看到「收到 → 已注入 →（必要时📌钉定）→ 回复 via=…」闭环。
  - `via=card stream_ok=True` = 流式卡片正常送达 ✅
  - `via=markdown` / `via=text` = 卡片那级出问题但**已降级送达**（你手机仍收到）⚠️ 可观察
  - `via=webhook` = 前三级全失败、走了喇叭兜底（该查飞书应用权限 / 网络）🔴
  - 只看到「收到 + 已注入」却**迟迟没有「回复」那行** = 这轮还没答完（长任务正常）或卡在解析（配合 `status` + `/screen` 看现场）。
- **进程 / 会话活性**：`python feishu/feishu_bridge.py status`（看每 bot 进程在不在、会话活没活）。
- **outbox 投递健康（v8）**：`python feishu/bridge_doctor.py`（每 bot outbox backlog / 是否 stuck·doctor_loop 已在桥内自愈·这是手动巡检版）。
- 🔑 **飞书 API 探针（调试别只看日志！）**：`python feishu/bridge_feishu_probe.py --all --recent 3` 直读**各 bot 聊天真实消息历史**（飞书官方记录·非 SDK 自报 success）→ 核对「桥说发了用户到底收没收到」。`--bot arch --verify "片段"` 验某条是否真落到 DM。**怀疑「发了不回 / 卡到没到」时，日志 + 这个 API 探针双管齐下**（注：流式卡片正文 API 读到的是「请升级客户端」占位·只能确认到没到+时机·正文在 App 看）。详见 `docs/TOOLS.md §14`。

---

## § 2.8 · 两边同步（terminal ⇄ 飞书）· ⚠️ v8 已重构 · 机制见 §2.5 · 下面 v7 细节仅备查

> 一句话：不管你在**手机飞书 @/私聊** bot，还是**直接在电脑 terminal 里打字**，你的每条话 + Claude 的每条回复，**两边都看得到完整对话**。

**为什么需要**：§2 的 @-驱动路径只搬运「桥自己注入的那一轮」（`on_message` 触发）。你**直接在终端敲字**时 `on_message` 不触发 → 那一轮飞书侧完全看不到。本节补上**镜像器**，把终端原生的轮次也搬到飞书。

**架构（v8 · 2026-06-16）**：不再有独立 `mirror_tailer`。每个 bot 进程在 `runner()` 起 **`outbox_drainer` = 唯一发送引擎** + `doctor_loop` 自愈。terminal 轮、@ 轮、**bg-shell 唤醒轮**的回复**统一由 worker 会话的 Stop/PostToolUse hook 写 outbox → drainer 发**（§2.5）。`on_message` 只「收→👍→注入」。**v7「轮询 jsonl + 等 end_turn + 单线程发送」整套已删**（队头阻塞/10min 卡死/漏 bg 轮的病根见 §2.5）。⚠️ 下面 v7.14 细节**仅备查·不代表现状**：
- 盯**钉死的那个 jsonl**（`bridge-session-<bot>.json` 的 `jsonl` · 和 §2.5 同一个文件 · 零额外猜测），用 **byte-offset 高水位（HWM）** 增量读新行，**绝不重放历史**（重启 / 换会话从文件末尾 EOF 接续）。
- 每条新记录分类（复用 `jsonl_reply_extract` 的 `_is_real_user_message` / `_user_text` / `_assistant_texts`）：
  - **真用户消息**：带 `[飞书-<bot>]` 标记 = 飞书来的（你已在飞书里打过）→ **丢弃不回搬**；无标记 = 终端敲的 → 搬到飞书 DM（`🧑 你（终端）`）。
  - **assistant 终答**（`end_turn` 且有文本）：归属「飞书来的轮」→ **跳过**（@-路径已发 · 防重发）；归属「终端轮」→ 搬到飞书 DM（`🖥 [终端会话]` · 长文 SDK 自动分条）。
- **搬到哪**：bot 的 **owner open_id（私聊 DM）**（`bridge-owner-<bot>.json` · 首个 @ 它的人即 owner · 开机即有 ·「优先私聊」满足）。
- **用什么发**：镜像 / 主动推送 / 短回复一律走 **`card_send`** = 飞书**互动卡片**（CardKit `channel.stream` · 和 @-路径同款好看可交互排版）· 卡片装不下（>`CARD_SAFE_CHARS`）或失败才退 `guaranteed_send`（markdown→text→webhook）。**不发裸文字消息。**
- **进度卡 与 答案 分家 `_deliver_turn`**（v7.16 · 2026-06-15）：① 一张**流式进度卡**实时刷 💭思考 / 🔧工具 / 🪙token（`channel.stream` + emit·答完定格「✅ 完成·见下↓」·**绝不放答案**）；② 答案**永远只走 `card_send` 发一张卡**（一次性·瞬间·任何时长可靠·≤容量单卡 / 超容量分条）。卡片不碰答案 → **结构上不可能双发**，又**保留实时进度**。每轮 2 张卡（进度卡 + 答案卡）。**裸 URL 自动 `_linkify` 成 `[url](url)` 可点**（飞书卡片不自动 linkify 裸网址）。（v7.15 曾砍掉进度卡=过度化简·v7.16 撤回）

**防回环 + 防重发（两道独立闸 · 原则「宁漏不重」）**：
1. 飞书来的用户行带标记 → 镜像器直接 `continue`，**永不回搬**；而且「搬出去」是一次 outbound `send`，不产生新 inbound、不写 jsonl → 无反馈边、数学上转不起圈。
2. 每一轮 assistant 终答按「最近的前一条真用户消息有没有标记」归属：有标记 = @-路径负责，无标记 = 镜像器负责，两路按**轮次来源**划分互不重叠。HWM 未知来源时默认按「飞书来的」跳过（安全侧）。

**新增持久化字段**（`bridge-session-<bot>.json` · `_merge_session` 读改写 · 不覆盖 `pty/jsonl`）：`chat_id` / `open_id`（每条 inbound 刷新 · 给主动推送用）+ `mirror:{jsonl, offset}`（镜像器 HWM）。

**配套 · 主动推送 CLI**（解决「我在终端让你发到飞书」· 走 DM 不走群喇叭）：
```
python feishu/feishu_bridge.py send --bot <name> --file reply.md [--to <chat_id/open_id>] --json
```
→ 独立短进程重建 `FeishuChannel`（REST · 不依赖常驻桥）→ 推到持久化的 `chat_id` / owner open_id → 复用 `guaranteed_send` 四级兜底 → 打印 `{delivered, via, to}`。**这是 Claude 在终端会话里主动发飞书的唯一正道**（`scripts/notify.py` 是群喇叭 · 只用于机械告警 · 绝不用于对话回复）。

**配套 · 送达回执**（解决「我只知道写了不知道发没发」）：桥每发一条往 `_autopilot/bridge-receipts-<bot>.jsonl` 追加一行 `{tid, ts, kind, delivered, via, len, …}`。终端会话 `Read` 这文件尾巴即可确认「我上一条到底送达没、走第几级」（事后确认 · 非同轮）。

**已知边界**：① tailer 只发**钉死的那个会话**——从没 @ 过的独立终端会话需先 @ bot 一次建立钉定（`doctor` 会显示 `jsonl❌未钉`）。② 没人 @ 过该 bot 则无 DM 目标 → tailer 空转（首个 @ 后即激活）。③ @ 轮回复统一发 **owner DM**（优先私聊·群里 @ 也回 DM）。④ 重启正在跑的轮：tailer 从 HWM 幂等续 → **不丢回复**（B 主赢）。**监控**：`feishu_bridge.py doctor` 一眼每 bot 健康（进程/会话/jsonl 钉没钉/DM/最近 receipt）；每次发送落 `_autopilot/bridge-receipts-<bot>.jsonl`（机械闸·单一真相源）。

**激活**：改完需 `python feishu/feishu_bridge.py stop && python feishu/feishu_bridge.py start` 重启桥（常驻进程不会热加载新代码）。

---

## § 2.9 · 多媒体通道（你发图/文件 → 我 · 我发图 → 你 · 2026-06-16）

> 一句话：**你在飞书发图/文件**，桥真把字节下载到本地、注入【本地路径】给会话（不再是没用的占位）；**会话也能主动发图到你手机 DM**（`send --image`）。两端全走 `lark-channel-sdk` 一等公民 API，不手搓 token/multipart。

**入站（你发图/文件 → 桥下载 → 注入本地路径）**
- **根因（修的是什么）**：飞书 SDK 把图/文件消息渲成 `content_text` 占位 `![image](<飞书资源key>)` / `<file key=.. name=../>`。资源 key **不是路径、不是 URL**，旧桥把它**原样注入**会话 → ① 会话拿不到真图 ② 占位**以 `!` 开头**，Claude Code TUI 把行首 `!` 当 **bash 命令模式** → 拿 `[image](..)` 喂 bash 报 `syntax error near '('`（2026-06-16 twitter 桥日志原样坐实）。
- **怎么修**：`on_message` 捕获 `msg.resources`（SDK 给的附件描述符 `file_key`+`type`）→ 用 SDK `channel.download_resource_to_file(file_key, resource_type, message_id=msg.id, dest_dir=…)` **真下载字节**。**带 `message_id` → 走 `im/v1/messages/{id}/resources` 入站正确端点**（不是只能下机器人自己上传图的 `image.get`/`file.get`·nanobot PR#986 坑）。
- **落地哪**：`_autopilot/inbox/<bot>/<日期>/`（`INBOX_ROOT` 派生自 `PROJECT`·不硬编码盘符/用户名）。文件名取 `file_name` 或 `<file_key><自动后缀>`（SDK 按 content-type 补 `.png`/`.jpg`）。
- **注入什么**：`_strip_media_markup` 剥掉 `![image](key)` / `<file .../>` 噪声 → 注入「📎 收到 N 个附件（已存本地·可直接 Read·按需移到目标资产目录）+ 绝对路径」。**既消 `!` 开头的 bash 坑，又让会话能直接 `Read`。** 纯文本路径占位剥离是 no-op·行为零变化。
- **会话拿到后**：路径在手 → `Read` 看图 / 移到目标资产目录（如某 note 的 assets）/ 继续处理。桥只负责「收下 + 给路径」，**不猜该归到哪篇**（归属是会话的 taste 决策）。

**出站（会话主动发图 → 你手机 DM）**
- `python feishu/feishu_bridge.py send --bot <name> --image <图路径> [--text "说明"]`（`--image` 可与 `--text` 同用）。
- 走 SDK `OutboundImage(source=MediaSource(kind="file", path=…))` + `ch.send`（底层 `im/v1/images` 出站上传·与 `scripts/send_card_feishu.py` 同款能力）。封面 / 截图 / 图表 / 架构图直达手机。
- 实测：`send --bot explore --image …` → `{"delivered": true, "image_ok": true}`。

**出站（会话主动发语音 → 你手机 DM · 可拖动进度条 · 2026-06-18）**
- `python feishu/send_feishu_voice.py --bot <name> --audio <音频> [--to oc_/ou_] [--text "说明"]`（任意格式·默认 ffmpeg 转 Ogg/Opus）。
- 🚨 **根因（为什么不能直接用 SDK `OutboundAudio`）**：飞书语音进度条要能拖动，**上传 opus 时必须带 `duration`（毫秒·与实际一致）**——飞书官方 Audio 文档：「指定音频时长，否则**播放进度展示不准确**」；Upload File API：「duration… **If this field is not specified, no specific duration is displayed.**」。而 lark-channel SDK 的 `driver.upload_file()` 只传 `file_type`/`file_name`、**从不传 duration**（`parse_opus_duration` 被导出却从没被调用）→ 飞书拿不到时长 → **点播放直接跳结尾、进度条不可拖动**（2026-06-18 [飞书-explore] 实证·**这不是飞书组件 bug，是 SDK 漏了 duration**）。
- **怎么修**：`send_feishu_voice.py` 绕开 SDK、直接走 REST：ffmpeg 转单声道 48k Ogg/Opus（飞书语音唯一认的编码）→ **读末页 OggS granule 算真实 duration（零依赖·本机无 ffprobe·granule/48 = ms）** → `im/v1/files` 上传带 `file_type=opus + duration` → `msg_type=audio` 发。复用 `send_card_feishu.api/tenant_token/send_msg`（REST SSOT）。
- 实测：`--bot explore --audio …` → `{"voice_ok": true, "duration_ms": 6006}`·进度条可拖动显示 0:06。

**激活**：入站在常驻桥 `on_message` 里 → 改完需 `stop`+`start` 重启桥（**只重启桥进程·wmux 会话原样不动**·2026-06-16 实测 6 会话 daemon-id 全不变）。出站 `send` 是独立短进程·即改即用。

**SSOT / 边界**：入站 + 出站图全用 SDK 一等公民 API（`download_resource_to_file` / `OutboundImage`）· 不手搓 HTTP。**出站语音例外走 REST**（`send_feishu_voice.py`·因 SDK `OutboundAudio` 漏 duration·见上「出站语音」根因）。当前覆盖 image/file/voice。**在线查看 md/代码/HTML**（不走「发文件」）= 见 **§2.11**（已选飞书云文档路线·Tailscale 已否）。**在线查看图片/视频/任意媒体**（不发到手机·只给链接）= **§2.11b 已建**（`send_feishu_media.py`·图嵌 docx + 视频/文件嵌 docx → 发文档链接）。

---

## § 2.10 · 交互弹窗转发（AskUserQuestion → 你回答 → 桥驱动 · 2026-06-17 检测端升级 PreToolUse 结构化）

> 🚫 **本节整体已停用（2026-06-22 用户决议 · AskUserQuestion 在飞书桥【硬禁用】）**。
> **怎么禁**：`write_hooks_settings`（`bridge_outbox.py`）生成的 `bridge-hooks.json` 里加 `"permissions": {"deny": ["AskUserQuestion"]}` —— 裸工具名 deny = 把它从模型上下文整个拿掉，模型压根看不到、不会调 → **自动改用「普通文字 + 编号选项」**（你回数字/文字即可）。**仅作用桥 spawn 的会话**（`--settings` 指它）·你日常 ccp/终端的 AskUserQuestion 不受影响。实测（v2.1.183 throwaway）：模型回「这个环境里没有 AskUserQuestion 工具可用」+ 优雅降级成文字选项·无 picker。
> **为什么禁**：picker 在飞书纯文字 chat 里得不偿失，且有三个结构性坑根治不掉/不值得修——① **问前分析/收尾无法先于 ask 卡送达**（平台硬限·根因在此）② **ask 挂着时你打字被答题模式打成「选项N」mangling** ③ **死会话残留 picker 劫持下一条**（曾用 Y1 补·见下）。全砍 picker = 三坑无源可生。
> **① 的根因（jsonl 落盘时机·2026-06-22 live 实测 v2.1.183 双态钉死）**：模型在调 AskUserQuestion 前写的那段分析/收尾 prose，**只渲染到终端屏幕、不写进 jsonl 文件**——直到你【答完那道题】才落盘。实测：picker 挂着未答时 jsonl 里 **assistant 记录 = 0 条**（prose 只在屏上）；一旦答题 →prose+ask 才进文件（assistant 记录 4 条）。桥读的是 **jsonl 文件**不是屏幕 → 你答之前桥根本拿不到那段 prose → **没法把它发在 ask 卡之前**，只能等你答完随收尾补发。这就是用户反复遇到「只看到问题卡、看不到前面那条收尾」的根。读屏抓 prose 这条退役已久（正文含编号/ASCII 会串台）。**∴ 平台层无解 → 改走「普通文字消息带编号选项」(整条消息先到、顺序天然正确)。**
> **想恢复**：删那行 `deny` 重启桥即可——下面整套检测/答题机制（PreToolUse 写 `kind:ask` + drainer 渲卡 + `_drive_picker` + Y1 死会话闸）**原样保留只是 dormant**，删 deny 即复活。**下面内容仅作 dormant 参考 / 恢复时的实现真相。**

> 一句话：会话调 **AskUserQuestion**（终端弹选择题）时，**PreToolUse hook 在弹窗前开火、直接拿结构化 `tool_input`** → 写 `kind:"ask"` 进 outbox → drainer 渲卡发你飞书 → 你回数字/文字 → 桥驱动按键替你选。

**关键事实（2026-06-17 实测纠正 v1 假设）**：pending AskUserQuestion 的整条 assistant 消息（分析正文+问题+选项）在你回答前**不落盘 jsonl**、Stop/PostToolUse 也不开火——但 **PreToolUse 对 AskUserQuestion 会开火，且 payload 带完整结构化 `tool_input`**：`{questions:[{header,question,options:[{label,description}],multiSelect}]}`。这是**零读屏、零正则**的真源。（v1 的「读屏 + find_ask_picker 正则」检测已退役——正文含编号列表/ASCII 时会把正文当选项串台，见 git 史 2026-06-17 修。）

**A · 发现 + 转发（PreToolUse 结构化 · 检测端）**：`hooks/bridge_pretool.py`（PreToolUse · matcher `AskUserQuestion` · `FEISHU_BRIDGE_SESSION` env 守门 · import 走 `parents[1]` 自身 feishu/）→ 读 `tool_input.questions` → 写 `{kind:"ask", questions:[...], context}` 进 outbox（**弹窗瞬间·无延迟**）。drainer 的 `kind:"ask"` 分支用 `render_ask_card(questions, context)` 从结构化数据渲卡（题面 + 编号选项 1..N + 每选项 description + 多 question 都渲）→ 发飞书；`_ask_key` 指纹去抖。**结构化数据天生免疫**：正文里的有序/无序列表、ASCII 画图、emoji、奇符号、多段长文——一概不污染选项（它是工具原始入参·不是猜屏）。2026-06-17 高保真 fixture 验过：乱正文 + 多 question + emoji/箭头 → 抓到的结构化全干净。
> _**ask 卡只发问题本身（`context` 恒空）**：问之前的「实时思考 / 自言自语旁白」属【进度卡】(PostToolUse·实时刷)，不混进问题卡（Publisher 2026-06-18 实证：有 Ask 时卡上带一堆英文自言自语旁白，没 Ask 的普通回复反而干净——根因 = `bridge_pretool` 旧版抓 `progress().texts` = 本轮【全部】文本含旁白）。真正的「收尾结论」(紧贴问题前那段·**回答前不落盘 jsonl**·实证 9188 字/10 段、AskUserQuestion 那条消息无 text 兄弟块·PreToolUse 结构上抓不到) 由 **Stop hook 在 turn 结束时干净补发**（`_final_turn_reply` 的 `_asst_has_ask` 只取「问前结论 + 终结 wrap-up」·排除「文本→普通工具」的中途旁白）。2026-06-18 演进：先删旧 `[-1800:]` 截断 + `_clean_screen_text`（毁 markdown 表格），再去掉 `progress().texts` 抓取本身（旁白污染根治）。_

**B · 你回答 → 桥驱动（结构化状态机 + 开环确定性驱动 · 零读屏 · 2026-06-18 根治中 · living plan `feishu/_PLAN-askq-answer-robust.md`）**

> ⚠️ **病根（2026-06-18 explore 实证）**：旧 B 段「`on_message` 注入前读屏 → `find_ask_picker` 认 picker → `_answer_picker` 移光标后**再读屏回读确认 `cursor_num==target`**」对**高(多问题 / 长描述 / 上方有大表)的 picker 失效**——光标移到末项「Type something」时，页脚特征串(`Enter to select / to navigate / Esc to cancel`)被挤出读窗(实测 tail=250 都没有)→ 回读返 None → 在按 enter/打字前就放弃 → 回「没确认选到你要的项」。**读屏回读这条链是最脆的，整段退役。**

新设计 = 答题侧也以结构化信号为唯一真相，键盘只做确定性开环驱动：

- **B-1 答题模式判定（删读屏）**：drainer 发 ask 卡时，把每问算好的选项映射写进 `bridge-picker-<bot>.json`（真选项 `1..K` + `K+1=✍️自己打字` + `K+2=💬换个聊法` + `multiSelect` + session/ts）；收到下一条 `progress`/`answer`/新 `ask`（= 回合恢复）就清掉。`on_message` **不再读屏**——读这文件：在且新鲜 → 答题模式；否则普通注入。
- **B-1.5 死会话残留 picker 闸（Y1 · 2026-06-22 catvpn 实证）**：会话停在 AskUserQuestion 等你回答时，若 **wmux daemon / 桥重启把会话整个杀死**（关机重开 / 装 wmux / 手动重启桥 / daemon 崩 …），picker 文件**不会随会话一起清**。下一条消息进来时 `ensure_session` 已判旧会话失效并**重生新会话**（`created=True`，且已回你「🆕 上个会话已失效·起新的」）——此时**任何残留 picker 必属那个已死会话**（新会话刚生、啥都没跑过、不可能写过 picker）。`on_message` 在驱动前加闸：**`created=True` → 作废残留 picker（`picker_clear`）、不驱动、把这条当普通新消息处理**。不加闸的后果（catvpn 实证）：你回的内容被当成对【废题】的答案、被打成「选项 K+1·自己打字」**劫持注入新会话**（日志 `🅰️ picker 驱动 ✓ nums:[K+1]`）。会话活着复用（`created=False`）时此闸不触发 → 正常答题路径一行不变（零回归）。⚠️ 注：会话已死 → **那一轮的「收尾结论」物理上找不回**（prose 只活在死掉的会话内存里、从没落盘 jsonl·见 §2.10 A 与 `bridge_pretool` 注释 F3）；此闸只做到「失败干净、不劫持下一句话」，不（也无法）补发收尾。
- **B-2 编号无硬编码**：内置项号 `K+1`/`K+2` 一律按 `len(options)` 算（Q 有 3 真选项→自己打字=4；只有 2 个→自己打字=3）。**绝不写死 4/5**。卡片(`render_ask_card`)逐问把「几号=什么」明列 + 回复说明。
- **B-3 回复协议**：纯数字 `N` → 选第 N 项提交；`N 文本` → 选第 N 项（若 N=自己打字号则把文本打进去）；纯文字（无前导数字）→ 默认选「自己打字」把文本打进去。**多问**：逐行（第 i 行=第 i 问）或按序；缺的问用默认。`parse_picker_reply(text, picker_meta)` 解析。
- **B-4 数字直选驱动 + 闭环校验提交（2026-06-19 改：选项用结构信号·提交加读屏闭环）**：每问 select（数字直选·自动进下一问）→ 自由项 select+送字+enter → 末问后**自动跳到「Review your answers / ❯ 1. Submit answers」确认屏** → 末尾 `key enter` 提交。`_drive_picker(pty, ws, answers, picker_meta)`。
  - ⚠️ **生产偶发卡 Submit 根因（用户实证「卡 Submit answers 自己去点」）= 那一 enter 在高负载/渲染竞态下被丢**（干净 throwaway 复现不出·零延迟 enter 都能提交=TUI 缓冲键队列；只在生产丢键）。→ **闭环 verify-retry**：发完末 enter 读屏，只要还有确认屏标记(`Submit answers`/`Ready to submit`·新问题屏没有·不误触)就**重按 enter**(≤6 次)直到它消失。返回 **True=确认屏消失(已真提交) / False=重按多次仍卡**。
- **B-5 确认走【屏幕校验】(2026-06-19 取代 `_await_picker_resolved`·已删)**：`_drive_picker` 闭环校验提交成功→回「✓ 已替你选并提交」；校验没通过(仍卡确认屏)→**诚实**回「⚠️ 没能确认提交成功·去终端看一眼或重发」（**不再谎报「已提交·它接着在跑」**——旧软化话术把真卡也说成功·误导你·issue②c）。最终回复照常 Stop hook→outbox→drainer 发回。

**D · 终端键序（throwaway 会话实测钉死 · 2026-06-19 复验 · Claude Code v2.1.181）**：
- **布局/编号真相 = PreToolUse `kind:ask` 结构化 questions**（全新会话实测**真开火**·验过地基）。屏上每问编号 = `1..K`（结构化 `options`）+ `K+1 = Type something`（自由文本）+ `K+2 = Chat about this`。**号按 `len(options)` 算**（K=3→自由文本=4；K=2→=3），不写死。
- **选普通项 N** → `send "N"`（**按编号直选**·实测光标在别处也精确选 N）→ **自动进下一问**。
- **自由文本（`K+1`）** → `send "K+1"`（高亮 Type something·不前进）→ `send "<文本>"`（**行内输入·替换 label**）→ `key enter`（提交该文本·进下一问）。⚠️ **空文本直接 Enter = decline**（必须先打字）。实测自由文本居中(Q2)·下一问数字(Q3)不被吞。
- **提交（2026-06-19 实测钉死两层真相）** → **末问 send 数字后【自动跳到】「Review your answers / ❯ 1. Submit answers / 2. Cancel」确认屏**（光标已在 1.Submit answers·不是先到 ✔ Submit tab）→ **`key enter` 提交全部**。单问无此确认屏(直接提交)。⚠️ 那一 enter 生产偶发被丢→卡这屏=用户实证 bug → `_drive_picker` **闭环 verify-retry**：读屏还见 `Submit answers`/`Ready to submit` 就重按 enter(≤6 次·B-4)。
- **不可靠 → 不用**：`↑` 到顶**回绕**(1→K)非钳住 → **永不靠方向键归零/计数**，一律数字直选。`Tab` 只前进且在 Submit 钳住；`←` 回退。
- **确认走【屏幕校验】**：提交成功 = 读屏确认屏标记消失(`_drive_picker` 闭环判 ✓·取代旧 `bridge-picker` 清/`_await_picker_resolved`)。

**C · 看门狗安全闸**：看门狗（纯兜底）`nudge_pane` 前若处于 picker（优先查 `bridge-picker-<bot>.json`；兜底 `find_ask_picker(屏)`）→ 跳过 nudge（那是「在等你回答」不是卡死·回车会乱选）。

**SSOT / 无硬编码**：**检测 + 答题布局 = PreToolUse 结构化 `tool_input`**（零解析·`bridge_pretool.py` → `bridge-picker-<bot>.json`）；内置项号按 `len(options)` 算（不写死）；键序常量一处集中；pty/chat_id 读 `bridge-session-<bot>.json`。`find_ask_picker`/`PICKER_SIGNATURES` **退居看门狗兜底闸**——答题侧不再依赖读屏。

**激活**：① 桥 `stop`+`start`（`write_hooks_settings` 重生 `bridge-hooks.json` 含 PreToolUse）② **PreToolUse hook 在会话 spawn 那刻挂上 → 已在跑的旧会话要 respawn（`/close`+re-@ 或自然重启）才获得**（explore 22:56 起、hook 23:24 才 commit `3c4d5e7` → 没加载 → 7 bot `kind:ask` 记录全 0·全新会话才有）；新会话自动带。看门狗重跑生效。

---

## § 2.11 · 在线查看（本地 md/HTML → 飞书云文档链接 · 2026-06-16 · ROADMAP §11 路线 2）

> 一句话：会话把本地 `.md`/`.html` 文件**转成飞书在线云文档**，发一条**文档链接**到你 DM——你在飞书 App 里直接看（格式完整、可滚动、**可复制、可编辑保存**），不用回电脑、不用隧道、不用 Tailscale。最 Feishu-native 的「在线查看」。

**入口**：`python feishu/feishu_bridge.py send --bot <name> --doc <file.md|.html> [--text "说明"] [--name "文档标题"]`。

**链路（全 `tenant_access_token` · bot 身份 · 封装在旁挂小工具 `feishu/feishu_docs.py`·不塞桥主回路）**：
1. **上传素材** `POST /drive/v1/medias/upload_all`（multipart · `parent_type=ccm_import_open` · `extra={"obj_type":"docx","file_extension":ext}`）→ `data.file_token`。
2. **建导入任务** `POST /drive/v1/import_tasks`（`type=docx` · `point={mount_type:1, mount_key:""}`=bot 云空间根目录）→ `data.ticket`。
3. **轮询** `GET /drive/v1/import_tasks/{ticket}`（间隔 2s·上限 ~30 次）→ **成功判据 = `job_status==0` 且 `token` 非空**（坑：status=0 但 token 空 = 仍处理中·别当成功）→ 取 `data.result.{token,url}`。
4. **授权 owner（关键坑·否则你点链接「无权限」）** `POST /drive/v1/permissions/{token}/members?type=docx`（body `{member_type:"openid", member_id:<owner open_id>, perm:"view", type:"user"}`）。⚠️ body 的 `type:"user"`(成员类别) ≠ query 的 `type=docx`(资源类别)·两个都要传。
5. **发链接**：把 `data.result.url` 用 `card_send` 发到 DM（裸 URL 自动 `_linkify` 可点）。

**支持**：`.md`/`.markdown`/`.mark` 和 `.html` 都导成 docx（文档类只能导成 docx）。≤20MB 走单次上传。

**🚨 前置（一次性·每个要用此功能的 bot 应用·只有 Publisher 能在开发者后台做）**：开**【应用身份/tenant】** scope（桥用 tenant_access_token·**不是用户身份**——2026-06-17 实证：只开用户身份仍 `99991672 Access denied`）。
> - **采用 `drive:drive` + `docx:document`(:create)**（SSOT = `feishu_docs.CLOUD_DOC_SCOPES`）。⚠️ **2026-06-21 修正**：`drive:drive` 只覆盖 upload/import/query/授权，但**创建 docx**（`POST /docx/v1/documents`·§2.11b 媒体在线查看的第 1 步）**另需 `docx:document` 或 `docx:document:create`**——只给 `drive:drive` 会在创建文档处报 `99991672 One of [docx:document, docx:document:create] is required`。老 bot 一直能发是因为注册预置带了 docx 家族（实测 tb25-cartoonMV 有 `docx:document:create`）；漏了 docx 的新 bot（tb25-cartoonMV-3）才暴露此坑。现 `register_feishu_app.py` 末尾**一条链一次开齐**（`APP_IDENTITY_MANUAL_SCOPES`）。
> - 等价可选（更细粒度·explore 验过）：`docs:document.media:upload` + `docs:document:import` + `docs:permission.member:create` 三个。改用哪组 = 改 `CLOUD_DOC_SCOPES` 一处。
> - **为什么必须单独开**：一键创建 SDK（`lark_oapi.register_app`）的 `app_preset` **只支持 `name`/`avatar`/`desc`**（源码 `scene/registration/__init__._apply_app_preset` + 单测确认）、archetype 硬编码 `PersonalAgent`——**无法在创建时预置 scope**；且没有「应用给自己授权」的 API（安全红线）→ scope 只能管理员后台开。
> - **怎么开**：`register_feishu_app.py` 建完会打印**一键开通链**（`feishu_docs.auth_url(app_id)`），**Claude 把它发给 Publisher** → 点开 → 开通（**务必选「应用身份/tenant_access_token」·不是用户身份**！2026-06-17 podcast/social_media 实证：只开用户身份仍全拒）→ **创建版本并发布**才生效。铺老 bot 同理（各 app_id 一条链）。
> - 判定够没够：`python feishu/_tmp/_probe_scopes.py <bot>`（4 步都不报 `99991672` = 通）。一键预置建的 app **不含**这权限·必走此步。

**边界（诚实）**：① 你在飞书里改了文档，**改动留在飞书云那篇·不会自动回灌本地 `.md`**——回灌要再加一步（`GET /docs/v1/content` 把文档拉回 markdown 覆盖本地）·是 v2。② 它**会在飞书云存一份文档**（导入到 bot 云空间根目录·可后续归到专用文件夹/定期清）——Publisher 已知此 tradeoff 并接受。③ `type=docx` 与 public 分享 enum 有「不确定」项·首篇先测（见 plan）。

**SSOT / 不硬编码**：复用 `scripts/send_card_feishu.py` 的 `api`/`tenant_token`（stdlib·绕代理·不重写 token 逻辑）；owner open_id 取桥已持久化的 `bridge-owner-<bot>.json` / 会话 open_id；不硬编码 folder/盘符/用户名。**`send` 是独立短进程·即改即用·不需重启桥。**

## § 2.11b · 在线查看媒体（本地【图片 / 视频 / 任意文件】→ 嵌进 docx → 发链接 · 2026-06-19）

> 一句话：会话把本地**图片 / 视频 / pdf / 任意媒体**塞进一篇飞书在线文档，发**一条文档链接**到你 DM——你点链接在飞书里**看图、放视频、预览文件**，**不点就不下载、不占手机内存**。「图片在线 / 视频在线」都走它。

**入口**：`python feishu/send_feishu_media.py --bot <name> --media <图/视频/文件> [--media <更多> …] [--caption "说明"] [--title "标题"] [--text "前言"] [--to oc_/ou_]`（`--media` 可重复 → 一篇里混排多个）。引擎 = `feishu_docs.publish_media_as_doc`。

**为什么不能照搬 §2.11 的 import**：import 只吃 md/html/txt/docx、**吃不下图/视频**；也不能「图当独立网盘文件传上去拿链接」——**bot 是应用身份、没有个人「我的空间」根目录**（`drive/v1/files/root_folder_meta` 对 tenant token 返 **404**·2026-06-18 实证）。唯一通路 = **docx 块 API**。

**链路（全 `tenant_access_token` · bot 身份 · 需 `drive:drive` + `docx:document`(:create)·见 §2.11 修正）**：
1. **建空文档** `POST /docx/v1/documents`（bot 无需 folder_token·建在 bot 云空间）→ `data.document.document_id`。⚠️ **此步需 `docx:document` / `docx:document:create` scope**（光 `drive:drive` 不够·会报 `99991672`）。（**它同时是根页面块 block_id**·作后续 append 的 parent）。
2. **逐个媒体**（顺序 append）：
   - **图片**（jpg/png/gif/webp/bmp/heic…）→ 建空 `block_type:27 image:{}` 块 → 上传 `medias/upload_all`（`parent_type=docx_image` · **`parent_node=图片块 block_id`** · multipart 带 `size`）→ `PATCH …/blocks/{id}` `{"replace_image":{"token":file_token}}`。
   - **其他**（视频/音频/pdf/任意）→ 建空 `block_type:23 file:{}` 块（飞书生成**两层**：外 `33` View · 内 `23` file·取**内层 block_id**）→ 上传（`parent_type=docx_file` · **`parent_node=内层文件块 block_id`**·⚠️ **不是 document_id**·fan-sun 参考实现用 doc_id 会撞 `1770013 relation mismatch`·2026-06-19 实证修正）→ `PATCH` `{"replace_file":{"token":file_token}}`。视频/音频/pdf 在文档里**自带内联播放器/预览**。
3. **取链接** `POST /drive/v1/metas/batch_query`（`request_docs:[{doc_token,doc_type:"docx"}]` · `with_url:true`）→ `data.metas[0].url`（如 `https://my.feishu.cn/docx/…`）。
4. **授权 owner**（同 §2.11·`permissions/{token}/members?type=docx`·`perm` 默认 `view`）→ **发裸 URL**（单独一行·不进代码块·才可点）。

**坑备忘**：① 建空块 `image:{}`/`file:{}` **必须为空**（带 name/token → `1770001`）。② `docx_file` 上传点 `parent_node` = **内层文件块 block_id**（与图片对称·非 document_id）。③ docx 块写接口限频 3/秒·多媒体批量注意。④ 媒体存进 bot 云空间那篇 docx（不在你个人盘·不乱你视野·可定期清）。

---

## § 2.12 · 起会话怎么把命令喂进终端（分行发 + 读屏探就绪 · 2026-06-18 根治）

> 一句话：桥起会话 = `workspace.new` → **三行各自独立发**：`bash` → `cd "<cwd>"` → claude 启动命令。每发一行**读屏轮询到 shell 提示符回来再发下一行**（取代固定 `sleep(0.4)` 盲等），探不到则超时回退原盲等。根治「冷机/新 shell 没就绪 → 下一行被吞」。

**根因（2026-06-18 实测 · 两个症状同一病根）**：`wmux_session.spawn` 旧版每发一行只 `time.sleep(0.4)` 就发下一行。冷机上嵌套 git-bash 这 0.4s 还没起到可接收输入，下一行糊到没就绪的终端 → 被吞。两个看似无关的症状其实同根：

- **进错目录**：`cd` 被吞、claude 那行侥幸落 → claude 在默认目录起（实测 tb25-speech 落到 xhs-card-gen 而非 solo-podcast-video）。
- **起会话空等 1 分钟**：整行（含 claude）被吞 → claude 压根没起 → `_wait_claude_ready` 干等满写死的 60s 才「补发」一次，补发（此时 shell 早就绪）才真起 claude（日志铁证：4 次冷起「收到→未就绪」全是 66s，补发后 +11s 才出 ❯）。**所以你看到 +1min 才出现的那行命令，其实是补发，不是首发。**

**不是传输慢**（曾怀疑 · 实测排除）：单次 `node wmux-rpc.js` RPC ~40–70ms、命名管道瞬连成功（`wmux-daemon-<user>` 与 `wmux-<user>` 两个管道都在 → SETUP §64「管道名失配走 TCP」已过时）、TCP 回退代价 ≈ 0、30s subprocess 超时从没被触及。

**修法（SSOT 在 `wmux_session.py`）**：

- `spawn` 的 `send_line` 改「发一行 → `_wait_shell_ready` 轮询读屏到提示符回来 → 再发下一行」。提示符判定 `_PROMPT_TAIL_RE`（行尾 `$`/`>`/`❯`）+ 超时/间隔常量（`SHELL_READY_TIMEOUT=8` / `SHELL_POLL_SEC=0.3` / `SEND_SETTLE_SEC`）集中一处。
- **cd 行的落地铁证**：git-bash 提示符含 cwd → 探就绪时要求新提示符**含目标目录尾段**，cd 真落进对的目录才算就绪。
- **分行不合并**（曾试过 `cd "X" && claude` 合并一行 · 已撤回）：要每行清清楚楚、顶层 shell 本身停在对目录。`_worker_cmd` 只回 launch 命令（不含 cd），cwd 交给 `spawn` 单独发 cd 行。
- 配套 `feishu_bridge._wait_claude_ready`：`READY_TIMEOUT_SEC` 60→30、**先读后睡**（首轮不空等）。首发不再被吞后 claude ~10–15s 出 ❯，补发基本用不上。
- **超时兜底**：探不到提示符 → 回退原 `sleep(0.4)` 照发，最坏不比旧版差、绝不卡死 / 少发。

**向后兼容**：`spawn(name, cmd, cwd, shell_init)` 签名 / 返回值不变 → autopilot（走 `spawn_worker.py` 的 split-here · 不经 `wmux_session.spawn`）零影响。

**验证（2026-06-18）**：开新 workspace 真 claude 端到端 —— `cd "D:/410_VibeCoding/Yoach"` 独立一行、顶层 shell 与 claude 都落在 Yoach、就绪 **5.76s**（旧 bug 起步空等 66s）。

---

## § 2.13 · 注入投递保证（撞 auto-compact 不再静默黑洞 · 2026-06-18 根治）

> 一句话：桥注入一条消息后**记一笔 pending**；若那一轮被 **auto-compact 吃掉**（上下文满时提交触发压缩、消息没被当成 turn 处理、会话回 idle、零回复），doctor 会**检测到并必达重投 + 通知你**——不再像以前那样无声丢失、你干等不到回复。

**病根（twitter 实证 · 2026-06-18）**：注入是 fire-and-forget·假设 hook→outbox→drainer 必回。但消息撞 auto-compact → 被吃 → turn 没发生 → 零 outbox 活动 → 无 answer → **静默黑洞**（桥日志还写「已注入·回复走 drainer」当作交付）。以前没会话在「桥等回复时」撞过 compact·这条路从没走过。

**结构化信号 · 纯桥侧（无新 hook · 只需重启桥 · 免 respawn）**：
- **记 pending**：`on_message` 正常注入后 → `bridge-pending-<bot>.json{ts, size0=注入时 outbox 字节数, attempts}`（`bridge_outbox.pending_write`）。
- **活动 = outbox 字节增长**：每 bot 独立 outbox 文件 → 只有它自己的 hook(progress/answer)写入才让它变大 → `getsize` 涨 = 那一轮真发生了（O(1)·精确·无同秒 ts 歧义）。
- **检测**（`bridge_outbox.pending_status`·doctor 每 30s per-bot 调）：`size 涨`=**active**(turn 发生/进行中 → 信任 drainer → 清 pending) / `零活动 + 超 PENDING_TIMEOUT_SEC(120s)`=**stuck**(疑被吃/卡) / 否则 **waiting**。
- 🚨 **重投前【结构闸】`_pending_reinject_blocked`（2026-06-19 根治频繁误报·用户最烦）**：「outbox 零活动 + 超时」**≠ 真撞 compact**——**长思考开场 / 纯文字回答 / 等你答题** 都零 outbox 却在跑（实证 cartoonMV 长思考 127s 被冤判重投）。判 stuck 后**两道结构信号确认真没在处理才放行重投**：① **picker 待答**(`bridge-picker` 在)=正常暂停等答→不重投；② **wmux `agentStatus != 'idle'`**(`running`/`waiting`/`working`)=会话还活着在跑→不重投（实证：活跃 turn 全程非 idle·哪怕长思考 115s 也不翻 idle；**只有真回空闲提示符/死壳才 `idle`**）。只有 picker 无【且】`agentStatus=='idle'`/读不到 → 才算真撞 compact·放行重投。
- **必达恢复**（`_recover_pending` closure）：过闸后 stuck 且 attempts<1 → **重投**那条消息(compact 后上下文已空·必成) + DM「已为你自动重投」+ 重置计时(attempts=1)；重投后仍 stuck → DM「重投仍无响应·请手动重发 / `/close` 重开」+ 清 pending（**给 guard·不循环**）。

**实现**：`bridge_outbox.py`(pending_path/write/load/clear/status 纯函数) + `feishu_bridge.py`(on_message 记 pending · `_recover_pending` · `PENDING_TIMEOUT_SEC=120`) + `bridge_doctor.py`(doctor_loop 加 `recover_pending` dep·每轮 per-bot 调·异常不拖垮)。验证：pending_status 六态单测 + doctor 调用/抗异常 + 真会话挂真 hooks e2e（正常→active 不误投 / 打断模拟被吃→stuck / 重投真答出来）。

**已知边界（诚实·v2 · 2026-06-19）**：① ~~纯思考 >120s 误判重投一次~~ **已被 `_pending_reinject_blocked` 结构闸根治**（agentStatus 非 idle → 不重投）② 进度流过后 mid-turn 才 compact 卡死（size 已涨→判 active）→ 本机制不覆盖·由 §2.5 drainer 发卡超时 + §3 outbox backlog 自愈部分兜底 ③ 真撞 compact 那一刻 agentStatus 恰好还没回 idle（极短窗）→ 下一轮 doctor(30s 后)再判·最终仍会重投(不漏)。

---

## § 2.14 · 会话死活判别（daemon 重启指纹 + agentName 软闸 · 2026-06-19 根治「关机重开后注进死壳」）

> 一句话：判一条会话能不能复用，**不再只看「pty 还在不在 `workspace.list`」**（这条被 wmux 重启恢复骗过）——而是 ① 先比 **daemon 实例指纹**（变了 = 这会话的 claude 已随旧 daemon 全死）② 再看 wmux 自报的 **agentName**（空 = 裸壳无 agent）。两道结构信号·不靠读屏。SSOT：`feishu_bridge._reuse_check` + `wmux_session.{pty_state,daemon_fingerprint}`。

**病根（2026-06-18 实证）**：你**关掉 wmux → 关机 → 开机 → 重开 wmux → 重启桥**后 @ bot，消息被**注进一个裸 bash 死壳**（飞书侧看到 shell 报 `command not found`·桥日志「已注入 pty=daemon-36ef7d9b」复用了重启前的老 pty）。根因 = `ensure_session` 的两道旧闸全被骗过：
- **`pty_alive`**：wmux 把 workspace **连同同一个 pty id + 屏幕 buffer 一起从 `~/.wmux/sessions.json` 恢复** → 老 pty 仍在 `workspace.list` → 返回「活」。**pty id 重启后被原样复活，结构上分不出死活。**
- **`_agent_live`(读屏)**：恢复的 pane 显示**冻结的旧 claude 屏幕 buffer**（含 `❯`）→ 读屏判「活」。读屏本就是最脆一环。
- **开机时间为什么救不了**：Windows **快速启动(Fast Startup)** 走混合休眠，关机→开机**不刷新** `LastBootUpTime`/`GetTickCount64`（实测停在 4 天前）→ 开机时间判死不可靠。

**修法 = `_reuse_check` 三道闸，全过才复用（任一不过即关掉重生）**：
1. **pty 仍在 `workspace.list`**（`wmux_session.pty_state` 一次 list 同时拿 alive + agentName）。
2. **daemon 指纹未变（主闸）**：spawn 时把 `~/.wmux/daemon.pid` 的 `pid:mtime` 盖进会话记录 `daemon_fp`；复用前比当前值。daemon **每次启动**（真重启 / 关机重开 / 快速启动开机 / 关掉 wmux 重开）都重写 `daemon.pid`（实测 mtime 钉在启动那刻、3h 不动 = 启动写一次·非心跳）→ 指纹一变 = 旧 daemon 底下的 claude 全死 → 作废重生。**指纹缺失（读不到 / 老记录无 `daemon_fp`）→ 跳过该闸·零回归**，绝不因读不到就误杀活会话。
3. **agentName 非空（软闸·辅）**：`workspace.list` 每 workspace `metadata.agentName` = wmux 自报在跑的 agent（`Claude Code`/`Codex CLI` = 活；`''` = 裸/死壳）。**只在 agentName 空 + 读屏也判裸 shell 时才判死壳**（双印证）——agentName 标签偶抖（实测会把 Claude 误标 Codex CLI、活会话偶尔闪空），故绝不靠它单独误杀。

**只重启桥不会误杀活会话**：单纯重启 python 桥**不重启 wmux daemon** → `daemon.pid` 不变 → 指纹匹配 → 正在跑的会话照常复用（保住 ARCH-101 §2.8「桥重启不丢正在跑的轮」）。**只有真·daemon 重启**才作废，正合「wmux 没了→会话也没了→帮我重连」的心智模型。

**过渡补强**：本修复前建的老记录无 `daemon_fp` → 首次「确认存活复用」时惰性补盖当前指纹（`ensure_session` 里·只在判定可复用=确认活时盖·绝不把死壳误盖成当前 daemon）→ 不必等它被重新 @ 才有指纹保护。

**激活**：改的是常驻桥代码 → `stop`+`start` 重启桥生效（**只重启桥进程·wmux 会话原样不动**）。新老会话都立即受保护（老会话靠过渡补强）。

---

## § 3 · robustness（你最关心的 · 四类情况全自愈）

| 情况 | 桥的自动处理 |
|---|---|
| @ 一个还没会话的 bot | 自动 `workspace.new` + ccp（首次 ~10-20s 起会话，桥先回「正在起…」） |
| 手关了某 bot 的 workspace/面板 | 下次 @它 → 检测 pty 没了 → **自动重生**，不用来电脑收拾 |
| 关了桥的 python 进程 | 重开 `start` → 单实例锁顶替 + 重连飞书 + 读注册表恢复；wmux daemon 没动 → 会话照常复用（指纹匹配·§2.14） |
| 重启电脑 / 关机重开 / 重开 wmux | wmux 持久化恢复 workspace（**连同同 pty id + 屏幕 buffer**）→ 但里面的 claude 已死。桥据 **daemon 重启指纹**（`daemon.pid` 变）判这些会话失效 → 下次 @ 自动关掉死壳重生（**不再注进裸 shell**·§2.14 · 2026-06-19 根治） |
| **wmux GUI 没开** | 桥回「wmux 没开，请先打开 wmux」（没 wmux 无法 spawn）→ 你开了 wmux 再 @ 即自动起会话 |
| **outbox 投递卡 / drainer 死（v8）** | `doctor_loop` 检出（backlog 久不推进）→ **自动重启 drainer**；连续多轮仍卡才 `notify` 喊人（保守自愈 · `bridge_doctor.py`） |

> **铁律（PTY 单向）**：桥能托管的会话**必须在 wmux 里**（要有 pty）。这正是 owned-session 的好处——会话由桥在 wmux 里亲手造，天然满足。
>
> **⚠️ v8 hook 过渡铁律**：hook 只在会话 **spawn 那一刻**（`--settings`）挂上 → **重启桥不会给已在跑的旧会话补 hook**。新会话自动带 v8 mirror；已在跑的会话要 respawn（`/close`+re-@ 或自然重启）才获得（§2.5）。

---

## § 4 · 配置与凭证（多 bot = 多飞书应用）

- **配置表** `feishu/bridge-bots.json`：每个 bot 一行 `{name, app_id_env, app_secret_env, at_name, cwd?}`（密钥不入表，只存 `.env` 键名；**不再有 pty_file**——pty 由桥 spawn 得到）。
- **每个 bot = 一个飞书自建应用**（一条长连接）：`python feishu/register_feishu_app.py --name "xhs-botN" --bot botN`（OAuth 扫码 · 预置权限+事件+长连接 · 凭据写 `.env` 各自键名）。**扫码只有你能做。**
- **白名单**：`.env` `FEISHU_BRIDGE_ALLOWED_OPEN_IDS`（你的 open_id · 全 bot 共享）。
- N 个 bot 都加进同一个群，群里 `@对应 bot` 说话。

---

## § 4.1 · 跨机可移植（多台电脑各跑各的桥 · 2026-06-16 · 方案 A）

> 📖 **装到新机器的完整 runbook**（requirements.txt / 怎么拿 wmux handler / 为什么之前拿不到 / 排错速查）→ [`feishu/SETUP-new-machine.md`](../feishu/SETUP-new-machine.md)。下面只讲可移植机制本身。

> 一句话：桥本来把 `.env` 路径和 bot 名册都写死成**一台机**的绝对路径（`E:\…\.env` + committed `bridge-bots.json` 里全是 `E:`/`zhuzhen`）。换台机（盘符/用户名不同）就读不到凭据、改名册又跟另一台机 git 打架。现在两处都改成「**不写死盘符 + 机器本地优先**」，每台电脑能各跑各的桥、各管各的 bot，互不冲突。

**① `.env` 路径跨机解析**（`feishu/bridge_env.py:resolve_env_path` · 桥/register/probe 三个入口共用）：
优先级 `XHS_ENV_FILE`（显式全路径）→ `VIBECODING_ROOT/.env`（每台机一次性设·权威位置）→ 从仓库逐级上溯找到的第一个 `.env`（无需任何 env var·兼容 `Post/xhs-card-gen` 与 `Post/tools/xhs-card-gen` 两种布局）→ legacy `E:\410_VibeCoding\.env` 兜底（绝不破坏老机器）。**不再写死盘符**（符合用户 CLAUDE.md 跨机铁律）。

**② 机器本地 bot 名册**（`feishu/bridge-bots.local.json` · gitignore · `bridge_env.bots_config_path`）：
- 该文件**存在 = 整盘接管**——桥**只跑**它列的 bot，整盘覆盖 committed `bridge-bots.json`（**不合并**）。理由：同一飞书应用两台机各连一条 WS 会撞，所以本机必须只连自己的 bot；且本机不碰入了 git 的共享文件 → **跨机零冲突**。
- 该文件**不存在 = 行为零变化**——用 committed `bridge-bots.json`（另一台机/CI 永远走这条·不受影响）。
- 模板：`feishu/bridge-bots.local.example.json`（committed）。

**这台机挂个本地 bot 的完整步骤**（不需要把桥拆成独立仓库）：
1. `cp feishu/bridge-bots.local.example.json feishu/bridge-bots.local.json`
2. `python feishu/register_feishu_app.py --name 本机助手 --bot local1` → **你扫码**（凭据写进本机 `.env` 的 `FEISHU_BRIDGE_LOCAL1_APP_ID/SECRET`）
3. 编辑 `bridge-bots.local.json`：`cwd` 指向本机要驱动的仓库绝对路径（驱动哪个本地仓库就填哪个·不限 xhs）
4. `python feishu/feishu_bridge.py stop && python feishu/feishu_bridge.py start` 重启桥
5. 群里 `@本机助手` 说话 → 桥在本机 `workspace.new` + 起 ccp 落在该 cwd → 驱动本机仓库

> **注**：`/cd` 书签（`bridge-cd-bookmarks.json`）暂仍 committed 指另一台机；本机要本地化它，同样放 `bridge-cd-bookmarks.local.json`（已 gitignore·目前桥未读 local 版·需要时再补一行解析）。挂本地 bot 不依赖 `/cd` 书签——cwd 在 bot 名册里直接指定。

---

## § 4.2 · bot 默认登录账号（`claude_config_dir` · 多 Claude 账号切换 · 2026-06-24）

> 一句话：一个 bot 默认用哪个 Claude 登录账号（`~/.claude-personal` / `~/.claude-work2` …），由它**名册条目里的 `claude_config_dir` 字段**决定；不写 = 默认 `~/.claude-personal`。**改默认账号 = 改这一个字段 + 重启桥**，就这一处。

**机制链（spawn 时账号怎么定）**：
名册 `bridge-bots.local.json` 的 bot 条目 `claude_config_dir`
→ `feishu_bridge.load_bots()` 把该字段拷进内存 bot dict
→ `agent_runtime._claude_config_dir(bot)`（取不到则回退 `~/.claude-personal`）
→ spawn 命令注入 `CLAUDE_CONFIG_DIR=<dir>`（见 §2.4）
→ 该会话所有 Claude 操作（登录态 / 凭据 / transcript）落在那个 config dir。

**账号别名**（`agent_runtime.ACCOUNT_ALIASES` · 既是 `/account` 运行时切换的参数 · 也是 `claude_config_dir` 该填的值）：

| alias | runtime | config dir | 用途 |
|---|---|---|---|
| `cc` | claude | `~/.claude` | 默认号 |
| `ccp` | claude | `~/.claude-personal` | 个人（不写字段时的兜底） |
| `ccw` / `ccw2` / `ccw3` | claude | `~/.claude-work{,2,3}` | 公司号 |
| `cx` / `cxp` | codex | `~/.codex{,-personal}` | Codex（改默认用 `codex_home` 字段） |

**改一个 bot 的默认账号**（例：让 `tb25-yoach` 永远走公司号 work2）：
1. 编辑 `feishu/bridge-bots.local.json`，给该 bot 条目加一行（路径用 `~` · 绝不写死盘符/用户名 · 跨机铁律）：
   ```json
   { "name": "tb25-yoach", "...": "...", "cwd": "...", "claude_config_dir": "~/.claude-work2" }
   ```
2. `python feishu/feishu_bridge.py stop && python feishu/feishu_bridge.py start` 重启桥（**必须**——名册只在桥启动时载入内存，不热加载）。
3. 之后 @ 该 bot 起的每个会话默认就走新账号，**无需再敲 `/account`**。

**`/account` 是运行时临时覆盖**（≠ 改默认）：群里 `/account ccw2` 会**就地关旧会话、把账号【选好暂存】**（懒启动·见下条），只对当前会话有效，`/close` 或桥重启后切回名册默认。它通过 `apply_account()` 直接硬写内存 dict、**绕过名册** —— 所以名册字段失效时它仍好使（见下方坑）。

**懒启动（lazy launch）：`/cd` `/account` 都【只选·不起会话】，发第一条正式消息才冷启（2026-06-26 改）**。
- **动机**：旧模型里 `/cd` 选目录、`/account` 切账号都【各自立刻起一个会话】，且 `/account` 还会把 `/cd` 的选择清掉 → 想「又切账号、又走 `/cd` 交互选目录」时，先输哪个哪个就先起会话、另一个被冲掉，根本没法叠加。
- **新模型（与「没有任何命令、直接发第一条消息就自动起会话」对齐）**：`/cd <选中>` / `/account <号>` 只做**暂存**——关旧会话 + 把 `cwd`（`/cd`）/ `account`（`apply_account` 改内存 dict + 注册表标记）写进会话注册表、并清掉 runtime 字段（`pty/workspace_id/jsonl/daemon_fp` 置空），**都不 `spawn`**。真正起会话**推迟到你发下一条正式消息**：`on_message → ensure_session` 读 `current_cwd(bot)`（=暂存的 `cwd`）+ bot dict 的账号，一次冷启。
- **互不清除**：两个选择落在注册表**不同字段**（`cwd` vs `account`），`_merge_session` 只覆盖给定键 → 先 `/cd 74` 选目录、再 `/account cc` 切号，**74 的目录被保留**（这正是用户要的）。`/cd` 列了编号还没回数字时 `/account` 也不清菜单：账号暂存到那份 `bridge-cd-pending` 待选上，回数字再 `_do_cd` 暂存目录，下一条正式消息用新账号在选中目录冷启。
- **不挑就直接发消息** → `ensure_session` 在「当前（暂存的或默认）目录 + 当前账号」冷启，零额外步骤。`/close` 仍清空全部暂存 + 账号回名册默认。
- **代价**：~15s 冷启从「`/account` 后台预热」挪到「发消息之后」；换来「切目录 + 切账号任意叠加、互不抢起空会话」。没有任何后台 watchdog/doctor 会主动 spawn 无会话的 bot（实证：`spawn`/`ensure_session` 只在 `on_message` 触发），故暂存态不会被提前点火。

**注册新 bot 不会自动带账号**：`register_feishu_app.py` 只写凭据 + 提示往名册加 `{name/app_id_env/app_secret_env/at_name/cwd}`，**不写 `claude_config_dir`**。要新 bot 默认走非个人号，建完**手动**往它名册条目补这一行再重启桥（见 ARCH-102 §登记表）。

> ⚠️ **2026-06-24 修过的坑（防 regress）**：`load_bots()`（`feishu_bridge.py`）用白名单逐 key 重建 bot dict，**曾漏拷 `claude_config_dir`** → 名册配的默认账号永远到不了 spawn、每次回退 `~/.claude-personal`，只有 `/account ccwN`（硬写内存）才生效（`tb25-yoach` / `tb25-lab-3` 双双中招）。已补 `"claude_config_dir": s.get("claude_config_dir")` 透传。**以后给 `load_bots` 的 bot dict 加任何账号 / spawn 相关字段，记得在这白名单里也加一行，否则名册配了也是哑的。**

---

## § 5 · 验收测试（owned-session 端到端）

- **T1 冷启动自动起会话**：啥都不开 → 建第 2 bot → 群里 @它发一句 → 桥自动 `workspace.new` + ccp → claude 处理 → 回飞书。
- **T2 自动重生**：T1 通后，手动 `workspace.close` 那个 bot 的 workspace → 再 @它 → 自动重建 + 回话。
- **T3 桥重启**：kill 桥 python → `start` → @它 → 仍能用（复用或重生会话）。
- **T4 多 bot 隔离**：两个 bot 各自独立会话，同群分别 @ 各下指令，互不串台。

---

## § 6 · 实现状态（v8 回传重构 · 2026-06-16 已部署 + 真机验证）

全部 ✅ 已实现并跑通（v8 三层测试全绿 + 真飞书 live 验证 · 详 `_autopilot/_BRIDGE-HARDENING-LOG.md` / `_BRIDGE-V8-PLAN.md`）：

- **owned-session 多 bot**：`feishu_bridge.py` 接 `wmux_session`，每 bot 一进程一飞书长连接（lark_channel 单进程只能跑一条 WS，故每 bot 各起一个隐藏进程）+ 会话死自动重生 + 桥重启自恢复。
- **已删旧物**：`supervisor.pty` 单指针 / `register_supervisor.py` / 认领 / wsid8 全删；`bridge-bots.json` 去 pty_file 加 cwd。**v8 又删**：`mirror_tailer`/`_deliver_turn`/`_drive_turn`/进度卡渲染 + 5 常量（−190 行·feishu_bridge.py 1132→942）。
- **斜杠命令**：桥自己认 `/clear /cd /screen /stop /close /help`；**其余 `/xxx` verbatim 透传进 ccp**（`/resume`/`/rename`/`/model`…·不缀 `[飞书]` 标记）。普通消息的 `[飞书-<bot>]` 标记移到**末尾**（不挡 slash·v8 回传不依赖它·仅人读 + 总控 notify 抑制 + vestigial pin 子串匹配）。
- **回复管线（v8）**：**Stop hook 写 outbox（答案）+ PostToolUse hook 写 outbox（进度）→ `outbox_drainer` 唯一发送引擎读 outbox 发**（§2.5）+ **飞书互动卡片**（§2.6）+ **必达四级降级**（§2.6）+ **`doctor_loop` 机械自愈**（§3）+ trace id 日志（§2.7）。覆盖首轮 / 长 turn / autopilot 永不结束 / **background-shell 唤醒轮** / 补发不淹没（旧轮询全挂的 5 场景·v8 测试台实证）。
- **新增文件**：`feishu/hooks/{bridge_stop,bridge_posttool}.py` · `feishu/bridge_outbox.py`（drainer）· `feishu/bridge_doctor.py`（自愈）· `feishu/bridge_feishu_probe.py`（验真送达 tool）· `jsonl_reply_extract.last_turn_reply()`。运行时生成 `_autopilot/bridge-hooks.json`。
- **消息串行锁** + **owner 自动信任**（§7）。
- **多媒体通道（§2.9 · 2026-06-16）**：入站 `on_message` 用 SDK `download_resource_to_file`（带 `message_id` 走 message-resource 端点）真下载你发的图/文件到 `_autopilot/inbox/<bot>/<日期>/` → 注入【本地路径】（修「`!` 占位触发 bash 模式」bug）；出站 `send --image` 用 `OutboundImage` 把本地图直达手机 DM（实测 delivered/image_ok 双绿）。全 SDK 原生 API。
- **配套**：喇叭瘦身（`scripts/notify.py` 只发告警+里程碑）+ 看门狗 v0.10（ARCH-310）+ SOP-005（起总控流程不再手写 supervisor.pty）。

---

## § 7 · bot 的自助能力（被飞书桥 spawn 的 Claude 该知道的）

> 你（`feishu_bridge.py` 在 wmux 里 spawn 出来的 Claude）= 某个飞书 bot 的后端会话。除了干活，你天生还能：

- **自查「我是谁 / 在哪个群 / 谁是 owner」**：用 `.env` 里本 bot 的 `FEISHU_BRIDGE_<BOT>_APP_ID/SECRET` 调飞书 open-apis（`https://open.feishu.cn/open-apis`，绕代理 `NO_PROXY=feishu.cn`）—— 拿 tenant_access_token → 查 bot 显示名 / 所在群(chat list) / owner。**这是你本来就能做的，别绕半天**（2026-06-15 实证：default bot 被问「在哪个群叫什么」时绕了半天才想到调 API）。封装见 ROADMAP「飞书 API 工具」。
- **自助建 bot**：`python feishu/register_feishu_app.py --name X --bot Y`（扫码建新智能体应用）。删 / 改名 / 改头像 = 待 ROADMAP 调研（飞书 API 或手机端）。
- **消息串行**：同一 bot 同时收多条消息 → per-bot `asyncio.Lock` 串行处理（Zara 式「运行中消息排队下一轮」· 防并发注入交错丢回复 · 2026-06-15 修）。
- **owner 自动信任**：每个 bot **首个 @ 它的人自动成 owner**（之后只认它 · 免手维护白名单 · open_id 是 per-app 的故必须如此 · 保留 `.env` 全局白名单兼容）。
- **消息样式**：回复走**飞书互动卡片流式发**（实时进度 + 最终答案 + 过程小结），超长自动转分条普通消息，详见 §2.6——你（bot 会话）不用自己管发送格式，桥统一处理；你只管把答案写好（markdown 写法即可）。
- **🔑 主动发 DM + 自查送达（档1 自助协议 · v7.14）**：你看到对话里 `[飞书-<bot>]` 标记 → 你就是那个 bot 的后端（不确定就看最近的 `[飞书-X]`，X 即 bot 名）。① **默认 tailer 已自动把你的回复回传 DM**，你不用管；**只在用户显式说「走 DM 发给我」或你怀疑没送达时**手动发：`python feishu/feishu_bridge.py send --bot <bot> --file reply.md --json`（独立 REST · 绕卡片超时 · 走 bot 自己的 DM 通道 · **绝不用 `scripts/notify.py` 群喇叭**）。② **自查上一条送没送**：Read `_autopilot/bridge-receipts-<bot>.jsonl` 尾部（`delivered`/`via`/`timed_out`），或跑 `python feishu/feishu_bridge.py doctor`。

---

*ARCH-101 · owned-session · 2026-06-15 P140（多 bot 每进程一 WS + 串行锁 + owner 自动信任 + bot 自助能力 + **互动卡片流式回复 §2.6 + jsonl 钉死防串台 §2.5 + 必达四级降级 + trace 日志监控 §2.7**）· 2026-06-16 加 **多媒体通道 §2.9（入站收图真下载+注入本地路径 / 出站 send --image · SDK 原生 API）** + **交互弹窗转发 §2.10（AskUserQuestion 读屏发现→飞书列选项→你回数字/文字→桥驱动按键·不盲选·看门狗 picker 安全闸）**· 取代 inject-supervisor.pty。配套 ARCH-310（看门狗 v0.10）/ ARCH-320 / SOP-005 / ROADMAP-remote-agent-control。*
