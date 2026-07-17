# PLAN-914 · Codex 飞书进度降噪 + 长任务 align 前置闸

> `plan_version: 1.0.0`  
> 状态：**等待主人确认，未实施**  
> 交付契约：本计划只定义目标、前后效果、实现顺序和验证；收到明确“按计划执行”前，不改生产桥、不重启、不切运行时。

## 1. 我理解的问题

这不是单纯“消息太多”，而是两层协议同时错位：

1. **进度内容错位**：飞书收到的是 shell/patch/MCP 的原始调用摘要，而不是对主人有用的阶段判断、证据、风险和下一步。
2. **进度载体错位**：drainer 用当前轮完整 steps 反复 patch 卡片；patch 失败时又拿完整 snapshot 开新卡，导致旧行重复出现。
3. **Codex 能力边界**：当前稳定 hooks 有 PostToolUse/Stop 等，但没有 assistant commentary hook；所以 commentary 没有进入 outbox。官方 app-server 才提供 `item/agentMessage/delta`。
4. **工作流程错位**：上一个长任务应该先 align、展示目标态并等确认，却被直接按实施任务执行。

## 2. 客观现状评分

| 层 | 评分 | 已验证证据 |
|---|---:|---|
| 最终答案必达 | 9/10 | 本轮最终答案正常送达 |
| 进度信噪比 | 1/10 | 01:21–01:34 的本地时间线几乎全是工具摘要 |
| 进度去重 | 2/10 | 同窗口 receipts 为 91 条送达，其中 card×74、edit×17；edit 失败分支会用完整 snapshot 新开卡 |
| 阶段判断可见性 | 0/10 | Codex hook 只写 tool label；commentary 没有对应 hook 进入 outbox |
| 长任务先 align | 2/10 | 用户要“看怎么同步”，实际直接完成了跨仓改动和全量 apply |

## 3. Before / After 目标效果

对比 mockup：`docs/images/PLAN-914-progress-before-after.png`。

### Before（当前）

- 顶部只有 `🔧117 💭0`。
- 正文逐行暴露 PowerShell、Python、文件路径。
- 卡片更新/轮换会重复带上此前命令。
- 用户不知道：现在确认了什么、为何这样做、下一个决定是什么。

### After（目标）

执行前先发一张 **align 卡**，只包含：

- 我理解的目标与当前证据。
- 推荐做成什么样。
- 明确不做什么。
- 等主人回复“按推荐方案执行”后才开始。

执行获批后只维护一张 **阶段卡**，只包含：

- 当前阶段与进度（例如 `2/4 · 方案设计`）。
- 已确认的事实。
- 当前判断/风险。
- 下一步。
- 底部可写“原始工具明细已折叠，本地日志可查”，不展示命令正文。

## 4. 推荐架构

采用“两条进度通道”，不直接押注一次大迁移：

1. **活动心跳（机械层）**：PostToolUse 只证明“仍在工作”，不发送 command/path；同一张卡限频更新。
2. **语义 milestone（智能层）**：发送用户可读的阶段判断。MVP 用显式、结构化 milestone 记录；同时做 app-server throwaway 实验，确认能否稳定接收 `item/agentMessage/delta` 后再决定是否迁运行时。

这样可以先立刻消除工具洪水，又不靠解析不稳定 Codex transcript 假装拿到了 commentary。

## 5. 执行 Steps（每步均待批准）

### Step 1 · 固化本次事故回放 fixture

- **解决什么**：没有可重复的真实输入，后续容易“看起来改善”但复发。
- **矛盾**：真实 outbox 有价值，但不能把运行态/私人正文提交进测试。
- **推荐方案**：从本轮提取脱敏的 117-tool/patch-fail 结构，只保留 kind、长度、时序和虚构 label。
- **改什么**：新增 `tests/fixtures/codex-progress-noise.jsonl` 与回放 helper；不动生产状态文件。
- **交付物**：可一键重放“长任务 + edit 失败 + 最终答案”的固定样本。
- **验证**：旧 renderer 必须稳定复现多卡、重复 snapshot 和高工具噪声。

### Step 2 · 写死飞书进度内容契约

- **解决什么**：现在“什么值得发”没有 SSOT。
- **矛盾**：用户要知道正在做什么，但不需要命令流水账或隐藏思维链。
- **推荐方案**：进度只允许 `phase / confirmed / judgment / risk / next`；禁止 raw command、绝对路径、测试逐条结果。
- **改什么**：更新 `ARCH-110` 的进度卡规范和对应 renderer schema。
- **交付物**：机器可校验的 milestone schema 与文案预算。
- **验证**：contract tests 拒绝 command/path 字段；单卡正文保持在预算内。

### Step 3 · 增加“长任务必须先 align”硬行为契约

- **解决什么**：上轮把“看方案”误执行成“直接落地”。
- **矛盾**：自动推进效率高，但架构/多仓/长程任务未经拍板会偏离意图。
- **推荐方案**：命中任一条件即先 align 并停：多仓、架构/规则改动、预计超过 3 个文件、预计超过 15 分钟、用户说 plan/align/先梳理。
- **改什么**：后续更新 Codex Personal AGENTS、align skill 与桥 session 启动规则；明确只有“开始/按计划执行/go”解除闸。
- **交付物**：一张 align 卡 + PLAN 文档；未批准前零生产写入。
- **验证**：用“看看怎么同步”“直接修一行 typo”“按现有 plan 执行”三类 prompt 测分流。

### Step 4 · 先关闭 raw tool 文本外发

- **解决什么**：shell/python/path 直接淹没飞书。
- **矛盾**：完全关闭 PostToolUse 会让长任务看起来失联。
- **推荐方案**：hook 仍计数和更新时间，但 label 只保留高层类别；命令正文只留本地 outbox/debug log，不进卡片。
- **改什么**：`codex_bridge_posttool.py` 和 progress record schema。
- **交付物**：低噪活动心跳。
- **验证**：117 次工具调用在飞书正文中出现 0 条 command/path；活动状态仍持续更新。

### Step 5 · 修复 edit 失败导致的重复 snapshot

- **解决什么**：patch 失败后新卡重复此前全部内容。
- **矛盾**：新卡要保留当前状态，但不能重发历史流水。
- **推荐方案**：进度卡改成“当前状态 snapshot”，内容天然固定短；edit 失败新开卡时只发最新 snapshot，并封存旧 message_id。
- **改什么**：`bridge_outbox.drain_batch` 的 progress state/rotation 分支。
- **交付物**：同一阶段最多一张活卡；失败轮换不重复历史。
- **验证**：注入连续 edit-fail、限频和超预算三种逆境，均无重复 label。

### Step 6 · 建结构化 milestone outbox 类型

- **解决什么**：当前 outbox 只有 raw progress 与 final answer，中间没有语义层。
- **矛盾**：需要传阶段判断，又不能解析不稳定 transcript。
- **推荐方案**：新增 `kind: milestone`，字段固定为 phase/confirmed/judgment/risk/next；drainer 原地更新同一张阶段卡。
- **改什么**：outbox schema、renderer、dedupe key 和 bridge-history 展示。
- **交付物**：与 CLI/runtime 无关的语义进度协议。
- **验证**：同 milestone 幂等；跨 turn 不串 route；answer 到来后阶段卡封口。

### Step 7 · 做显式 milestone producer MVP

- **解决什么**：Codex lifecycle hook 没有 commentary 事件。
- **矛盾**：需要近期可用，同时不能靠 transcript 私有格式。
- **推荐方案**：先提供 Link16 原生 progress tool/MCP，桥启动规则要求长任务只在阶段变化时调用；PostToolUse raw 明细不外发。
- **改什么**：新增最小 progress tool、注册到桥 worker、补 AGENTS 调用规则。
- **交付物**：可控、结构化、低频的人类可读 milestone。
- **验证**：60–90 秒长任务能看到阶段判断；20 个连续工具调用不增加飞书消息数。

### Step 8 · 独立验证 Codex app-server 原生 agent-message stream

- **解决什么**：判断长期能否自动拿到 commentary，而不是让模型额外调 progress tool。
- **矛盾**：app-server 有官方 `item/agentMessage/delta`，但切换可能影响 wmux、resume、注入和 hooks。
- **推荐方案**：只起 throwaway app-server fixture，验证 initialize、thread resume、turn steer、agentMessage delta、tool event、interrupt 和 final completion。
- **改什么**：新增实验 harness 与结果文档；不接生产 bot。
- **交付物**：go/no-go 证据表。
- **验证**：至少三条异构 turn；逐条比对 TUI+hook 与 app-server 的事件完整性和恢复能力。

### Step 9 · 在证据后选择长期路径

- **解决什么**：避免凭感觉直接迁运行时。
- **矛盾**：原生 stream 体验更好，现有 wmux/TUI 链路成熟且已承载会话。
- **推荐方案**：若 Step 8 全部满足才立单独迁移 PLAN；否则保留显式 milestone tool，绝不读取 Codex transcript 猜 commentary。
- **改什么**：只更新 ADR/ARCH 决策，不在本计划里偷渡运行时迁移。
- **交付物**：明确的长期架构裁决。
- **验证**：裁决逐项引用实验结果，而非主观偏好。

### Step 10 · 建进度 UX 回归与压力测试

- **解决什么**：避免只测 happy path。
- **矛盾**：消息系统的重复、限频和 route 串台只在逆境暴露。
- **推荐方案**：覆盖 117-tool replay、edit 连续失败、两 turn 交错、final answer 插入、桥重启续 drain。
- **改什么**：`tests/` 的 renderer/drainer/hook 集成测试。
- **交付物**：低噪、无重复、答案必达的自动 gate。
- **验证**：五类场景看结果也看 receipts/outbox；零 raw command 泄漏。

### Step 11 · 只在 tb25-link16-codex 做 canary

- **解决什么**：不把未验证的新卡片体验一次铺到全舰队。
- **矛盾**：要真实飞书效果，又不能影响其他 bot。
- **推荐方案**：单 bot 开关，主人发一个 10 分钟长任务；先看 align 卡，批准后看 milestone 卡和最终答案。
- **改什么**：仅 tb25-link16-codex 本地配置；切换/重启前再次请主人确认。
- **交付物**：真实 DM before/after 截图、时间线与 receipts 对照。
- **验证**：主人肉眼验收 + bridge-history 闭环；消息数、重复数、raw command 数量可量化。

### Step 12 · 验收后再扩全舰队

- **解决什么**：把通过的行为变成统一协议。
- **矛盾**：各 runtime 能力不同，不能假设 Claude/Codex 事件相同。
- **推荐方案**：共享 milestone outbox/render contract，runtime producer 分开实现；逐 bot 分批 rollout。
- **改什么**：installer、SOP、ARCH 与 bot roster feature flag。
- **交付物**：Claude/Codex 一致的用户体验，不共享脆弱 parser。
- **验证**：每 runtime 各收一条 align、milestone、answer；全舰队 bridge-history 无工具洪水。

## 6. 需要主人拍板的 3 个问题

1. **推荐**：先做 Step 1–7 的低风险降噪 + 显式 milestone，app-server 只实验、不迁生产。是否按这个顺序？
2. **推荐**：长任务 align 闸采用严格模式——没有明确“开始/按计划执行/go”就停在 plan。是否确认？
3. **推荐**：执行中只更新一张当前阶段卡，最终答案另起；不再逐工具发消息。是否确认？

## 7. 明确不在本轮偷偷做的事

- 不立即改 hooks、drainer 或 Codex runtime。
- 不重启/切换 live bridge。
- 不解析 Codex transcript 充当稳定协议。
- 不把工具洪水从 117 条简单改成另一种 117 条文案。
- 不把本计划视为执行授权。

