# PLAN-915 · Codex 终端事件 → 飞书卡片投递契约

> `plan_version: 1.3.0`  
> 状态：**主人已批准；单 bot canary 已部署，真实飞书验收待本轮回复后自动执行**  
> 本轮边界：只为 `tb25-link16-codex` 开启 app-server canary；允许定向重生该 bot 的 Codex worker、定向重启其 bridge。其他 bot 不切换、不重启；全舰队 rollout 仍等真实飞书验收。

版本留痕：`1.0.0` 完成对齐与 mockup；`1.1.0` 用本机 Codex 0.144.5 真 turn 钉死 app-server 事件；`1.2.0` 落地 milestone contract、持久游标、canary runtime 与首轮异构测试；`1.3.0` 完成高保真 TUI fixture、22 项回归与单 bot bridge 定向重启，并安排当前回复送达后的 worker 切换和真实飞书 canary。

## 1. 这次真正要解决的问题

这不是“长任务要不要先批准”，而是一个消息投递协议问题：Codex 终端中同时存在用户可见旁白、计划、工具活动、子 agent 状态、内部 reasoning、最终答案等多种事件。Link16 必须明确哪些进飞书、以什么粒度进、如何更新卡片，以及异常轮换时如何保证不重复。

用户要的目标态：

1. 终端里本来就对用户可见的 `commentary` 在飞书中也能看到。
2. 工具调用可以显示，但只能是压缩后的活动摘要，不能泄露整段命令和输出。
3. 同一事件只出现一次；卡片 patch 失败或超预算后，新卡只接续未送达事件。
4. 最终答案另起一张卡，不与进行中卡混写。
5. 不把 raw reasoning / chain-of-thought 当作“思考过程”外发；这里所说的“思考过程”是 Codex 主动写给用户看的 commentary。

## 2. 恢复出的现场

- 昨天断电前的 Codex session：`019f6be3-2245-72a2-b174-75d48e08b573`。
- 真实工作目录：`D:\410_VibeCoding\Post\tools\link16-agent-infra`。
- 该 session 内，用户引用的“我已经确认到一个关键点……”同时以 `event_msg/agent_message/commentary` 与 `response_item/message/assistant/commentary` 落盘。
- 紧接着的 `Updated Plan` 来自 `update_plan` 工具；`Waiting for agents` 来自 `wait_agent` 调用，是 TUI 状态，不是 assistant commentary。
- 当轮 Codex 飞书记录约 117 个工具事件，91 次进度送达；commentary 没进入 outbox。

## 3. Claude 与 Codex 当前链路

### Claude Code（当前已打磨链路）

`PostToolUse` 只负责唤醒 hook；hook 随后读取 Claude transcript JSONL，从本轮 anchor 后重建按顺序排列的 `text / thinking / tool` steps，再写入共享 outbox。Stop hook 也读 transcript，并用终结态、竞态轮询和“末尾两段实质中段正文”规则保证收尾正文不丢。

### Codex（当前缺口）

Codex `PostToolUse` hook 只把当前 `tool_name/tool_input` 转成一条 label；没有 assistant-message lifecycle hook。Stop hook 只用稳定字段 `last_assistant_message` 发送最终答案。Codex session JSONL 虽含 commentary，但官方明确 transcript 格式不是稳定 hook 接口，本仓 Bridge Invariant 也禁止把它当生产协议。

### Codex 可行的原生事件源

Codex app-server 原生输出带类型的 item：`agentMessage`（含 `phase=commentary|final_answer`）、`plan`、`reasoning`、`commandExecution`、`fileChange`、`mcpToolCall`、`collabToolCall` 等。这是唯一能在不猜 transcript 的前提下精确区分终端事件的官方通道；但 app-server 当前仍标注为实验/开发调试接口，所以只能先做 throwaway + 单 bot canary。

### 2026-07-17 真探针结论（已证实，不再是假设）

- 本机 `codex-cli 0.144.5` 的真实顺序为：commentary item completed → plan updated → commandExecution completed → plan updated → final_answer item completed → turn completed。
- reasoning 是独立 item；过滤它不会吞 commentary。
- app-server 支持 WebSocket backend；Link16 可让**官方 Codex TUI**用 `--remote` 连接同一 backend，同时由第二连接订阅 typed events。无需解析 Codex JSONL，也无需以自制 readline frontend 取代 TUI。
- app-server final 只记入脱敏 event ledger；最终飞书答案仍由 Codex Stop hook 单独投递，所以答案只出现一次。
- 父 turn 与 collab 子 turn 会交错；producer 只消费 root thread，子任务状态从 root collab item 归一化，不能让 child turn 重置主人卡片。

## 4. 客观现状评分

| 层 | 评分 | 证据 |
|---|---:|---|
| 最终答案必达 | 9/10 | Codex Stop 直接使用 `last_assistant_message`；answer 有 route pin + 重试 |
| Claude 中间可见性 | 8/10 | transcript 可重建 text/thinking/tool；已有多轮竞态修正 |
| Codex 中间可见性 | 1/10 | 只有 PostToolUse label；commentary 为 0 |
| 工具信噪比 | 2/10 | raw command 第一行直接进卡；117 次工具形成洪水 |
| 去重/轮换 | 3/10 | 同卡 patch 正常；edit 失败分支会用当前完整 segment 新开卡 |
| 跨 runtime 合约 | 6/10 | 共用 answer/progress outbox 与 drainer，但 producer 语义不对等 |

## 5. 目标事件契约

| 终端事件 | 飞书策略 | 目标样式 |
|---|---|---|
| `agentMessage.phase=commentary` | **原样显示** | `💬` 完整正文，按事件顺序写入进行中卡 |
| `agentMessage.phase=final_answer` | **原样、另起卡** | `✅ 最终答案`，不复制进行中正文 |
| `plan` / `update_plan` | **压缩显示** | 当前 `已完成/进行中/待办` 摘要；同一 plan 原地替换 |
| command / MCP / web / file change | **分组压缩** | `🔧 本段 8 个工具：搜索×3 · 读取×4 · 验证×1`，可附最后一个高层动作 |
| subagent/collab | **状态压缩** | `👥 2/2 已完成`；只在状态变化时刷新 |
| tool 原始输入、命令全文、输出全文 | **不显示** | producer 内存中完成安全投影后丢弃，不进入 event/outbox/progress-state |
| `reasoning.content`、raw chain-of-thought | **不显示** | 不进入飞书；不能与 commentary 混淆 |
| Waiting/Finished waiting、spinner、token_count | **不显示** | 纯 TUI/传输噪声；token 只可在最终 footer 汇总 |

## 6. 卡片状态机

1. 每个归一化事件必须有稳定键：`runtime + session_id + turn_id + event_id + revision`。
2. 进行中卡维护“已确认送达的最后事件游标”，同一事件重复到达只更新其 revision，不追加副本。
3. `patch` 同一 message 时可以重绘整张卡；因为 message_id 不变，用户看到的是同一卡更新，不算重复消息。
4. 卡片满或 patch 失败时，旧卡封口；新卡从 `acked_cursor + 1` 开始。禁止把旧 segment snapshot 作为新卡正文。
5. 工具事件先进入相邻分组，再渲染；commentary 是分组边界，永不被工具摘要吞掉。
6. final answer 到达后封口进行中卡，答案另起；不把 commentary 再复制到答案卡。

## 7. Before / After

真渲染对比：`docs/images/PLAN-915-terminal-delivery-before-after.png`。

### Before

- 飞书只看见工具 label，commentary 在终端存在但缺席。
- 卡片 patch 失败后，新卡从当前 segment 开头重发，旧行在新消息中再次出现。
- `Updated Plan`、等待 agent、工具调用没有语义分层。

### After

- 一张进行中卡按时间顺序显示 commentary；工具按相邻区间压成一行。
- plan 只显示当前状态；subagent 只显示完成度变化。
- raw reasoning、raw command/output、Waiting UI 不显示。
- 最终答案另起卡。

## 8. 执行计划（主人已批准；按 Living Plan 回填）

### Step 1 · 固化双 runtime 事件 fixture

- **解决什么**：没有同一任务的 Claude/Codex 原始事件基线。
- **矛盾**：要真实复现，又不能提交私人正文和机器路径。
- **推荐方案**：从已恢复 Codex session 与 Claude outbox 各抽一轮脱敏 fixture，保留事件类型、id、revision、时序和虚构正文。
- **改什么**：只新增 `tests/fixtures/` 样本与读取 helper。
- **交付物**：commentary + tool + plan + wait + final 的跨 runtime 对照输入。
- **验证**：旧实现稳定复现“Codex commentary=0、工具洪水、edit-fail 重复”。
- **状态**：✅ 已完成。新增 Codex typed event、Claude legacy progress、父/子 turn 交错三类脱敏 fixture。

### Step 2 · 定义 runtime-neutral event contract

- **解决什么**：当前 `progress` 只有 `steps` 或 `label`，无法表达事件身份和 revision。
- **矛盾**：体验要统一，但 Claude/Codex producer 不能共享脆弱 parser。
- **推荐方案**：新增规范化 event envelope，producer 分开，renderer 共用。
- **改什么**：ARCH-110、outbox schema、contract tests；不先动 live producer。
- **交付物**：commentary/plan/tool/file/collab/final 六类事件及稳定去重键。
- **验证**：同一 renderer 可消费两套 fixture，输出相同语义卡片。
- **状态**：✅ canary contract 已完成。`milestone-v1` 带 root turn、event id、revision、route；runtime producer 保持分离。

### Step 3 · 先修 drainer 的游标与 edit-fail 续卡

- **解决什么**：新卡重复旧 snapshot 是共享发送层 bug，与 commentary 来源无关。
- **矛盾**：同卡 patch 需要完整 snapshot，新卡又只能发未送达 delta。
- **推荐方案**：把 `render_start` 与 `acked_cursor` 分开；patch 失败时新卡从游标后开始。
- **改什么**：`bridge_outbox.py` 的 progress state/rotation；保留 answer 重试语义。
- **交付物**：任意 runtime 都不再因 edit-fail 重复旧行。
- **验证**：正常 patch、连续 edit-fail、超预算轮换三类异构测试。
- **状态**：✅ 已完成首轮。legacy edit-fail 与 milestone edit-fail 都只续 dirty delta；milestone message id/revision/route 已落盘，可随 bridge restart 恢复。

### Step 4 · 实现新的卡片 renderer

- **解决什么**：工具与 commentary 当前没有不同展示预算。
- **矛盾**：工具可审计，但不能淹没飞书对话。
- **推荐方案**：commentary 原文、工具相邻分组、plan 原地替换、collab 状态变化才渲染。
- **改什么**：纯 renderer + snapshot 测试，不接生产。
- **交付物**：与 mockup 一致的进行中卡和独立答案卡。
- **验证**：117-tool fixture 最多形成少量工具摘要，commentary 全保留，raw command 为 0。
- **状态**：✅ canary renderer 已完成。相邻工具合并，plan 原位 revision 更新，commentary 原文保留，raw command/output 不进入 outbox。

### Step 5 · 做 Codex app-server throwaway 事件探针

- **解决什么**：确认官方 item stream 在本机版本对 resume、steer、interrupt、subagent 是否完整。
- **矛盾**：它能给出准确 commentary，但目前是实验接口，不能直接替换生产 TUI。
- **推荐方案**：一次性 app-server client，只记录 item type/id/phase/status；不接 bot、不写生产 outbox。
- **改什么**：实验 harness 与结果记录。
- **交付物**：go/no-go 证据，列出 `agentMessage/plan/tool/collab/final` 的真实时序。
- **验证**：普通工具轮、含 plan+subagent 长轮、interrupt+resume 三类真 turn。
- **状态**：✅ 本机官方 TUI 高保真 fixture 已通过 commentary、plan、工具、collab、final；真实飞书 canary 再验证投递与肉眼体验。

### Step 6 · 为 Codex 增加 canary producer

- **解决什么**：hook-only 路径结构上拿不到 commentary。
- **矛盾**：要真实终端旁白，又要保住当前 TUI/wmux/resume 体验。
- **推荐方案**：仅 `tb25-link16-codex` 用 feature flag 起私有 app-server；官方 TUI 以 `--remote` 连接，observer 订阅同一 root thread。PostToolUse 在 canary 中 no-op；hook Stop 继续作为唯一 final 路径，避免双发。
- **改什么**：`agent_runtime.py`、Codex producer、bot local flag；切换/重启前再次请主人确认。
- **交付物**：单 bot 原生 commentary 事件流。
- **验证**：event id 对齐、断线恢复、Stop fallback 不双发。
- **状态**：🔄 代码、本机 flag 与目标 bridge 定向重启已完成；为不截断当前授权回复，目标 worker 在本轮 Stop answer 入 outbox 后由单 bot helper 自动重生。

### Step 7 · 飞书 canary Before/After 验收

- **解决什么**：本地 renderer 正确不等于真实飞书 patch/轮换正确。
- **矛盾**：必须真验，又不能影响全舰队。
- **推荐方案**：只在 `tb25-link16-codex` 跑一条包含 commentary、20+ tools、plan、subagent、final 的代表性长任务。
- **改什么**：仅 canary 开关与一次受控重启（另行确认后）。
- **交付物**：真实卡片截图、bridge-history、receipts、event ledger 四层证据。
- **验证**：commentary 全到、raw command=0、重复事件=0、最终答案=1。

### Step 8 · Claude 回归与全舰队 rollout

- **解决什么**：共享 renderer 改动不能破坏已经打磨过的 Claude producer。
- **矛盾**：体验统一，但 Claude JSONL race-guard 不能被 Codex 方案替换。
- **推荐方案**：Claude 只适配 event envelope，保留现有 parser/Stop 规则；两个 runtime 分批 rollout。
- **改什么**：Claude adapter、installer、SOP/ARCH、bot flags。
- **交付物**：统一卡片 UX、runtime-specific producer。
- **验证**：Claude/Codex 各收 commentary、工具摘要、plan 状态、final；Claude 原有 Ask/Stop/route 回归全过。
- **状态**：⏸️ 按既定范围等待 Step 7 真实飞书验收；本轮只跑 Claude 回归，不切全舰队。

## 9. 已拍板项

1. **推荐**：commentary 全保留；工具只做相邻分组摘要，不显示命令正文。
2. **推荐**：`Updated Plan` 显示为一条“当前计划状态”，`Waiting/Finished waiting` 完全不显示。
3. **推荐**：不读取 Codex transcript 生产解析；先做 app-server throwaway，证实后只在 `tb25-link16-codex` canary。

主人于 2026-07-17 确认按本方案实施，并明确只重启 `tb25-link16-codex` 的飞书桥，不碰其他 bot。
