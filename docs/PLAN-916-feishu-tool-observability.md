# PLAN-916 · 飞书进度卡工具可观测性

> `plan_version: 0.10.0`  
> 状态：**Step 1–5 与 Step 7 已完成；remote TUI readiness 已红→绿修复，单 bot session 已恢复，Step 6 准备重新 canary**  
> 交付边界：先只切换 `tb25-link16-codex` 并完成闭环 canary；其余 Codex bot 本轮只读审计，不重启、不改名册，待小样证实后再决定分批 rollout。

## 1. 这轮真正要解决的问题

PLAN-915 已解决两件核心问题：Codex 可见 commentary 能进入飞书，原始命令与 reasoning 不再形成工具洪水。真实 canary 也证明新链路已经生效。

现在的新缺口是：工具被压缩成“里程碑 7 / 工具段 3”“本段 5 个工具：验证×5”后，安全性够了，但可审计性不足。主人能看到 agent 说了什么，却看不到它用了 `rg`、读取工具、Python、Git 或浏览器，也看不到访问或修改了哪些仓库路径。

目标态不是恢复逐条命令，而是在每个相邻工具段中集总展示：

1. 工具总次数。
2. 动作类别与代表性工具/运行时。
3. 去重后的仓库相对路径。
4. 访问、修改、新增分开表达。
5. 原始命令、参数、输出、绝对用户路径、环境值和 reasoning 仍不外发。

## 2. 客观现状评分

| 层 | 评分 | 事实依据 |
|---|---:|---|
| commentary 可见性 | 9/10 | canary 与当前真实 turn 均按顺序送达 commentary |
| 工具防泄漏 | 9/10 | canary `raw_leaks=0`，command/output 不进 milestone outbox |
| 工具类型可读性 | 3/10 | `commandExecution` 统一归类为“验证”，看不出 `rg` / 读取 / Python / Git |
| 路径可审计性 | 0/10 | milestone payload 当前不保留任何安全路径元数据 |
| 写入辨识度 | 4/10 | `fileChange` 可归类为“修改”，但未列目标路径；command 内发生的读写也不可见 |
| 去重与卡片续写 | 8/10 | event id + revision 与 dirty delta 已在 canary 生效 |

## 3. ABCD 对齐

### A · 保留已经好的部分

- commentary 原样、按事件顺序显示。
- plan 只显示当前状态。
- subagent 只在状态变化时显示。
- final answer 独立成卡。
- 相邻工具继续聚合，不恢复逐条日志。

### B · 补足工具类型

- 把笼统的“验证”细分为：搜索、读取、Python、Git、测试、浏览器、Shell、外部工具等安全类别。
- 类别后可以显示代表性程序名，例如 `搜索 rg ×2`、`读取 Get-Content ×3`、`Python 验证 ×1`。
- 不显示参数、管道、表达式或命令全文。

### C · 补足路径

- 只显示工作区内的仓库相对路径。
- 按“访问 / 修改 / 新增”分组，分别去重。
- 每段每组默认最多显示 5 个路径，剩余显示“另有 N 个”。
- 工作区外路径、用户目录、环境文件和疑似 secret 文件只显示安全类别，不显示具体路径。

### D · 控制噪声和泄漏

- 路径在 producer 内从 typed item 临时解析，立即规范化为安全元数据；原始 command/output 不写入 event ledger 或 progress outbox。
- commentary 是工具段边界；每段只保留一条会随 revision 更新的聚合摘要。
- header 不再显示难懂的“里程碑 N / 工具段 N”，改为“计划完成度 + 实际工具次数”。

## 4. 本轮新增需求映射

| # | 主人新需求 | 归属 |
|---:|---|---|
| 1 | 保留现在这些文字和经过思考后的 commentary | A |
| 2 | 能看见用了 `grep/rg`、read/write、Python 等什么工具 | B |
| 3 | 工具集总列出，不要每条都展示 | B、D |
| 4 | 能看见查看了哪些文件夹或文件路径 | C |
| 5 | 先 align、给方案，再看 Before / After mockup | 本 PLAN 与 mockup 交付 |

## 5. 推荐展示契约

每个相邻工具段渲染为一个可 revision 更新的摘要块：

```text
🔧 工具活动 · 5 次
类型：搜索 rg ×2 · 读取 Get-Content ×2 · Python 验证 ×1
访问：feishu/bridge_events.py · feishu/bridge_outbox.py · docs/PLAN-915-…md
修改：无
```

发生写入时改为：

```text
🔧 工具活动 · 3 次
类型：读取 ×1 · 文件修改 ×1 · 浏览器渲染 ×1
访问：docs/mockups/PLAN-916-…html
新增：docs/PLAN-916-…md · docs/images/PLAN-916-…png
```

推荐 header：

```text
🤖 进行中 · 计划 2/3 · 工具 8 次
```

不再把“工具段数”当作工具数量展示。工具段仍是内部状态机概念，只是不放到用户 header。

## 6. Before / After

真渲染对比：`docs/images/PLAN-916-tool-observability-before-after.png`。

### Before

- commentary 已经清楚。
- 工具只显示“验证×N”，无法判断用了什么程序。
- 没有访问路径，也无法明确这一段是否写了文件。
- header 的“里程碑 / 工具段”是内部实现概念，主人难以解释。

### After

- commentary 与 plan 保持现状。
- 每个工具段显示实际调用次数、类别与代表性程序名。
- 访问、修改、新增路径分开显示；路径仓库相对化、去重、限量。
- header 显示计划完成度与实际工具次数。
- 原始命令、参数、输出、绝对路径和 reasoning 继续隐藏。

## 7. 执行计划（待主人批准后交给 living-plan）

### Step 1 · 定义安全工具元数据 schema

- **解决什么问题**：当前 tool event 只有笼统 `category/status`，没有工具名与安全路径。
- **矛盾/冲突点**：要增加可观测性，但 raw command 不能进入持久层。
- **推荐方案**：给规范化 tool event 增加 `tool_family`、`program`、`read_paths`、`write_paths`、`create_paths`；只允许脱敏后的短字段。
- **改什么·改哪里**：`feishu/bridge_events.py` 的 event payload 与 fixture schema；ARCH-110 增加公开元数据白名单。
- **交付物**：一个明确的 allowlist schema，无法承载原始命令和输出。
- **验证**：序列化 event ledger/outbox 后搜索 fixture secret marker 为 0。
- **状态**：✅ 已完成。ARCH-110 已定义 event/聚合 step 白名单，并把 ledger、outbox、progress-state、卡片统一纳入防泄漏边界；同时纠正 PLAN-915 中“raw 留本地 outbox”的过期表述。主 session 已复核 worker 的 `**event` ledger 写入、progress snapshot 与 restart-state 三条持久化路径。

### Step 2 · 实现命令类型与路径的安全提取器

- **解决什么问题**：`commandExecution` 目前全部显示为“验证”。
- **矛盾/冲突点**：PowerShell、Python、Git 和 shell 组合命令形态复杂；不能为了完整解析而把敏感参数外发。
- **推荐方案**：只做保守识别：首个安全程序名 + 明确文件参数/字面量路径；解析失败就退回 `Shell`，绝不猜测或输出整串 command。
- **改什么·改哪里**：新增纯函数模块或放入 `bridge_events.py`；支持 `rg`、`Get-Content`、`git`、`python`、测试命令和浏览器工具的最小分类集。
- **交付物**：`commandExecution item -> safe public metadata` 转换器。
- **验证**：Windows 绝对路径转仓库相对路径；工作区外路径、`.env`、变量展开、密钥样字符串全部隐藏。
- **状态**：✅ 已完成。主 session 用本机 Codex 0.144.5 的 `app-server generate-json-schema --experimental` 钉死官方字段：`commandExecution.commandActions/cwd` 与 `fileChange.changes[{path,kind}]`。实现因此优先消费 typed action/path，只对 `rg/Get-Content/Python/Git/浏览器` 做固定显示名识别；解析失败退回 `Shell`。权威 workspace 从 worker 的 resolved cwd 注入，raw command/query/diff/output 不进入返回 event。6 个事件契约测试已通过。

### Step 3 · 聚合工具类型和路径

- **解决什么问题**：同一工具段需要一条摘要，而不是多条 event label。
- **矛盾/冲突点**：聚合后的 event id 必须稳定，同时新工具到达后摘要要原地更新。
- **推荐方案**：延续现有 `tools:<first_event_id>` 稳定键；revision 增长时重算 counts 与去重路径集。
- **改什么·改哪里**：`MilestoneAccumulator._steps()`；增加 actual tool count、类别 Counter 与有序路径集合。
- **交付物**：每个相邻工具段一条结构化 summary step。
- **验证**：连续 20 个工具只形成一条工具摘要；commentary 前后形成两个独立工具段；重复 event 不增加计数。
- **受 Step 2 影响**：聚合器直接消费 allowlist payload，不再解析中文 label；路径字段统一为 `access_paths/write_paths/create_paths`，程序名来自固定显示名。
- **状态**：✅ 已完成。相邻工具段现在输出 actual `tool_count`、有序 `tool_types`、访问/修改/新增的有序去重路径与稳定 `source_event_ids`；同 event 的安全 payload 更新只增 revision、不增加调用数。每组持久化路径上限 50，卡片 fallback label 只显示前 5 个并精确给出“另有 N 个”。9 个事件契约测试通过，覆盖 20-tool 单段、commentary 分段、重复 event 与 payload revision。

### Step 4 · 更新卡片 renderer 与 header

- **解决什么问题**：现有 renderer 只能显示单行 label，header 把工具段数当成工具指标。
- **矛盾/冲突点**：路径增加后卡片更长，仍需控制 2800 字预算与手机扫读体验。
- **推荐方案**：工具摘要最多四行；每组路径默认 5 个；header 显示实际工具次数，不展示内部工具段数。
- **改什么·改哪里**：`feishu/bridge_outbox.py` 的 milestone renderer/header；保持 legacy progress 路径兼容。
- **交付物**：与 mockup 一致的进行中卡。
- **验证**：无路径、只读、读写混合、超过 5 个路径、超卡预算五类 snapshot 测试。
- **状态**：✅ 已完成。milestone-v1 header 改为“计划完成度 + 整轮实际工具次数”，不再显示内部里程碑/工具段计数；正文继续消费 Step 3 已脱敏的 multiline label。预算轮换与 edit-failure 的新卡正文保持 dirty-only，但 header 使用完整 snapshot 总计。8 个 drainer 测试通过，并确认 Claude legacy `🔧N 💭N` header 不变；同时移除一个无调用的 `_v2_steps()` helper。

### Step 5 · 建立防泄漏和跨 runtime 回归

- **解决什么问题**：新功能主动接触 command 字段，泄漏风险高于 PLAN-915 当前实现。
- **矛盾/冲突点**：需要真实命令形态覆盖，但不能把本机私人路径或 secret 写入 fixture。
- **推荐方案**：使用虚构路径与 canary marker 建脱敏 fixture；测试 ledger、outbox 与最终卡片三层均不含 raw 字符串。
- **改什么·改哪里**：`tests/fixtures/plan915/` 或新 `tests/fixtures/plan916/`、`tests/test_bridge_events.py`、`tests/test_bridge_outbox.py`。
- **交付物**：工具分类、路径归一化、limit、dedup、secret redaction 的自动化测试。
- **验证**：全量单测通过；raw command/output/absolute home path 命中数为 0。
- **状态**：✅ 已完成。新增 hostile typed fixture，把 command/action/query/stdout/stderr/arguments、file diff、workspace 外路径、`.env`、credentials/private-key 和自定义 dynamic tool 名依次穿过 ledger、progress outbox、restart progress-state 与最终卡片；四层 marker 和绝对路径均为 0。路径前 5 + 精确 overflow、只读“修改：无”、edit-failure dirty-only 也已覆盖。全仓 36 项测试通过；首次全量回归暴露旧 hook 测试继承 live canary 环境变量，确认是测试隔离 bug 后修正，生产 hook 逻辑未改。

### Step 6 · 单 bot canary 与 rollout 决策

- **解决什么问题**：本地 snapshot 正确不等于真实飞书手机端可读。
- **矛盾/冲突点**：要看真实效果，但不能再次影响全舰队。
- **推荐方案**：只对 `tb25-link16-codex` 跑一轮含搜索、读取、Python 验证、文件新增与 8+ 路径的受控 canary；主人肉眼确认后再决定 rollout。
- **改什么·改哪里**：仅 canary bot 的 feature flag/worker；任何重启在执行前单独确认。
- **交付物**：真实飞书卡、bridge-history、receipt、event ledger 四层证据。
- **验证**：类型和相对路径可见；每段最多 5 个路径；raw leak=0；final 恰好 1 条；其他 bot 未切换。
- **当前状态**：✅ **已完成并于 2026-07-23 转正为默认路**（主人拍板：新建 Codex bot 一律 typed-event，老「标准路径」弃用）。落地 = `agent_runtime.codex_transport()` 默认返回 `app-server-canary`，只有名册显式写 `cli-legacy` 才回退；SOP-121 改写为「默认 canary」+ 存量 bot 切换的 `/new` 补步，SOP-160 的 fleet-wide 闸同步翻牌。转正当时的实证：tb25 三只 Codex bot 全带标记、2 只 worker 在跑（`tb25-link16-codex` / `tb25-cartoonMV-codex`），后者 event ledger 691 条 tool 事件、label 带命令原文的 = 0。以下为原始 canary 记录：主人已确认只切换 `tb25-link16-codex`。首次 helper 已正确等到授权 answer 的精确回执，但新 app-server remote TUI 没有普通 CLI 的 `OpenAI Codex` banner；旧 ready 判据因此连续误报未就绪，把 worker 命令投进已运行的 composer，并在第二次超时后关闭活 workspace。现已按真实 screen oracle 修正为：仅 `app-server-canary` 接受 composer marker，普通 Codex CLI 仍要求 banner + composer，trust prompt 仍不放行。6 项 runtime 定向测试与 40 项全仓测试通过；当前 workspace/PTY 已钉回 session，单 bot bridge 已加载修复，准备重新执行 canary。首次失败未产生 canary marker且 raw leak=0；其他 bot 未重启。

### Step 7 · 全体 Codex 智能体接入审计

- **解决什么问题**：共享代码更新不代表每个 Codex bot 都已进入 typed-event producer 架构。
- **客观范围**：当前 committed registry 与本机 roster 一致登记 3 个 Codex Personal bot：`tb25-link16-codex`、`tb25-speech-codex`、`tb25-cartoonMV-codex`；历史 `tb25-codex` 只有日志残留，不是现役 bot。
- **已证事实**：三者都使用 `~/.codex-personal`，但只有 `tb25-link16-codex` 配置 `codex_transport=app-server-canary` 与 `delivery_contract=milestone-v1`。Speech 与 Cartoon MV 仍走普通 Codex CLI，因此即使只重启 bridge，也不会产生 PLAN-916 的结构化工具摘要。
- **本轮动作**：只读核对 registry、machine-local roster、runtime 分支、当前 bridge/session/worker；不改 Speech/Cartoon 名册，不启动其会话，不发测试消息。
- **rollout 前提**：Step 6 的真实卡、ledger、progress-state、receipt 与唯一 final 全绿后，才能把同一 transport flag 分批推广到另外两个 bot；每个 bot 仍需自己的单 bot restart 与 live canary。
- **验证**：输出逐 bot 矩阵，明确“已接入 / 共享代码但未接入 / 非现役痕迹”，避免把代码兼容性误报成生产已生效。
- **状态**：✅ 已完成。现役 Codex Personal bot 恰好 3 个；`tb25-link16-codex` 已接 typed app-server，Speech/Cartoon MV 仅共享 Codex Personal 配置、尚未接入 typed producer。两者 bridge 在跑但当前无 session，本轮未改名册、未重启、未发测试消息。历史 `tb25-codex` 仅有日志/归档痕迹，不在 registry 或本机 roster。

## 8. 已确认的展示决策

1. **路径上限**：推荐每个工具段的“访问 / 修改 / 新增”各最多 5 个，超过显示“另有 N 个”。
2. **程序名粒度**：推荐显示 `rg`、`Get-Content`、`Python`、`Git`、`浏览器`；不显示参数与子命令全文。
3. **无写入提示**：推荐只读段固定显示“修改：无”，让主人无需猜测。
4. **header**：推荐改为“计划 2/3 · 工具 8 次”，不再显示内部“里程碑 / 工具段”计数。

以上四项与 mockup 已获主人确认，Step 1–5 已按此实现并完成离线回归。
