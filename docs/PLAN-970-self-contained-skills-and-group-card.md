---
doc_type: PLAN
doc_id: PLAN-970
title: Link16 自带飞书能力、隔离 Profile 与真人群卡片
status: active
purpose: 让新用户只凭 Link16 完成隔离账号、飞书 skill、桥与守护服务部署，并让真人群聊最终答案以无重复、可分片的互动卡片投递。
owns:
  - Link16 必备 skill、profile、常驻服务与验收能力的自足改造
  - AGENTS.md 与 CLAUDE.md 的对等 runtime 入口方案
  - p2a-ext 真人群聊卡片、防双发与完整历史改造
  - 本次改造的分阶段验证和人工确认闸
does_not_own:
  - anysearch、align、push 等个人 workflow 的内容设计
  - 飞书应用权限 capability 的长期合同
  - 其它内容仓的业务提示词
read_when:
  - 将 Link16 交给不使用主人 claude-config 的新同事
  - 从零创建隔离的 Claude Code 或 Codex 工作账号
  - 群聊回复出现 Markdown 纯文字、双发或进度泄漏
  - 调整 AGENTS.md 与 CLAUDE.md 的职责边界
last_reviewed: 2026-08-27
---

# PLAN-970 · Link16 自带飞书能力、隔离 Profile 与真人群卡片

> Plan version: **6** · 2026-08-26
>
> 当前状态：**S4.5 生产热修完成；DM 积压、真人群短卡和 provider 重试已真实验收。长群答与 live a2a 仍留在 S5.2，不伪装成全项完成。**
> 开工后按 living-plan 分 Step 推进；任何会改变本机开机启动项的动作都先展示计划并等用户确认。

## 0. 本轮已经拍板

1. Link16 第一版只自带 **1 个核心 skill：`feishu`**。anysearch、align、push、pull、commit、govctl、gstack、个人 memory 等继续留在每个用户自己的配置仓。
2. 新建 Claude/Codex 账号必须使用用户明确选择的隔离目录，例如 `~/.claude-work`、`~/.claude-personal`、`~/.codex-work`、`~/.codex-work2`；新建流程拒绝直接使用 `~/.claude` 或 `~/.codex`。
3. 群消息不靠模型猜“对面是真人还是 bot”，直接使用桥已有的 route 身份：`p2a-ext` 是真人发起，最终答案按卡片容量发 1 张或多张互动卡片；`a2a` 是 bot 发起，发纯文字；`p2a` 私聊继续发卡片。
4. 真人群不投递隐藏推理；是否展示正常进度由既有产品配置决定。无论最终答案分成几张，每个内容分片只能投递一次，默认机械阻止“agent 手动投群一次 + 桥自动回群一次”和重试双发。
5. 安装器必须把“桥、cron、看门狗、注册回调监督、收发、历史”都纳入验收，不以“脚本文件存在”冒充“服务正在工作”。

## 1. 目标态

1. **Link16 单仓自足**：新用户 clone Link16 后，可以安装飞书桥、注册器、回传链、历史工具、看门狗和 Link16 自带的 `feishu` skill；不依赖主人的 `.claude-personal`、govctl 或个人 skills。
2. **隔离 Profile 由用户命名**：用户明确选择 profile ID、runtime 和 home；本机只加载一份有效 profile registry，launcher、worker 和 bot 名册都从它解析，不能从 cwd、alias 或环境猜账号。
3. **安装是可审阅的闭环**：先 dry-run 列出会安装的软件、profile、skill、hooks、名册和开机启动项；用户确认后 apply；最后用机器信号验证，而不是让用户自己判断日志。
4. **常驻服务只有一个生命周期入口**：wmux 登录自启；Windows 计划任务 `FeishuBridge-Autostart` 负责整体起桥；桥再拉起 cron 和 `bridge_watchdog.py`。不得再注册第二个独立看门狗任务。
5. **两个 runtime 入口职责对等**：Codex 读 `AGENTS.md`，Claude Code 读 `CLAUDE.md`。两者都是“Link16 部署工程师 JD / system prompt”，共享职责落到 ROLE/ARCH/SOP/SPEC，入口只保留各 runtime 的执行翻译。
6. **按发起者身份选择群格式**：真人群最终回复是 1 张或按容量顺序拆分的多张互动卡片；bot 群是一条可机器读取的文字；私聊仍是卡片。
7. **所有收发可解释**：自动回信与主动发送都写入统一账本，能按秒还原“谁发给谁、用了什么 route、最终飞书 message_id 是什么”。

## 2. 已核实事实

| 维度 | 当前事实 | 判断 |
|---|---|---|
| 仓库自带 skills | 仓内没有 `SKILL.md`；现有 `feishu` 真源是 `~/.claude-personal/skills/feishu/SKILL.md`，只有 1 个 76 行薄路由文件 | **内容不大，但归属错了** |
| 现有 skill 内容 | 核心工具索引可复用；metadata 仍写 `user-general`，且“主人↔bot 走 DM”已不符合真人群聊新合同 | **不能字节级原封复制，需要一次语义迁移** |
| Codex skill 位置 | OpenAI 官方支持仓库 `.agents/skills` 与用户 `$HOME/.agents/skills`；用户级目录不跟 `CODEX_HOME` 走 | **feishu 可全 Codex profile 共用，但不能按 `.codex-work*` 各装一份** |
| profile registry | committed `agent-profiles.json` 固定了 9 个 owner profile，其中包含 `~/.claude`、`~/.codex` 和 ccp/ccp2/cxp 等路径 | **不能直接作为新同事的个人名册** |
| profile bootstrap | 目前写死创建 ccp/ccp2/cxp，只建 home、launcher 与 Shell 函数；安装 skill 数为 0 | **需改为用户显式输入和本机有效 registry** |
| 私人与核心说明 | README 已说 anysearch/push/pull/align 属个人增强；SOP-100 又把 `feishu` 混入个人 workflow | **边界文字冲突** |
| 私人治理依赖 | SOP-120 仍把 `$agent-profile-governance` 写成新 profile/bot 的统一入口 | **新同事部署仍被私人 skill 卡住** |
| 常驻服务代码 | `feishu_bridge.py start` 会随整体桥启动 cron 和 `bridge_watchdog.py`，整体 stop 会一起停止 | **代码是单生命周期** |
| 常驻服务文档 | SOP-100 §9.0 表格仍要求第二个 `AutopilotWatchdog-Autostart`；§9.5 和 TOOLS 又明确它已作废 | **同一 SOP 自相矛盾，容易装出双看门狗** |
| 注册回调监督 | `register_feishu_app.py` 会 arm 并拉起独立 `registration_monitor.py` worker，OAuth/权限/认主/入群可机械回调 | **能力已存在，应纳入安装验收** |
| runtime 入口 | AGENTS.md 承担共享正文 + Codex 适配，CLAUDE.md 只是薄适配器 | **不符合两个 runtime 对等 JD 的目标** |
| 卡片权限 | 同一 bot 的 DM 出站回执多次为 `via=card`，飞书 API 为 `msg_type=interactive` | **卡片能力已开，不是权限缺失** |
| 群消息实现 | `_new_card()` 对所有 `oc_` 群目标统一调用 `_send_group_text()`，没有区分 `a2a` 与 `p2a-ext` | **仓库设计导致真人群也发纯文字** |
| 13:26 双发实例 | 同一群提问先在 13:27:24 被手动投 text，约 12 秒后桥在 13:27:36 自动投 text | **确实双发，不是视觉错觉** |
| 路由状态 | 自动 answer 已带 `route=p2a-ext` 并成功回群 | **UTF-8 路由修复已生效；手动补投来自过时判断** |
| 历史完整性 | `bridge_history.py` 能看到自动 answer，却漏掉 `send_feishu_msg.py` 手动投群 | **“所有收发都可查”仍有缺口** |

## 3. 边界与架构合同

### 3.1 Link16 必须自带什么

- 飞书桥、wmux RPC、注册器与 registration monitor、hooks、outbox/drainer、cron、watchdog、历史和审计工具。
- 唯一 repo-owned `feishu` skill：只负责发现和路由，工具实现仍以仓内 `TOOLS.md` 及脚本为真源。
- 桥自己的 runtime commands，例如 `/status`、`/new`、`/close`、`/account`、`/cd`、`/screen`、`/handoff`。
- profile 建立器、本机 profile registry、bridge roster 模板、hooks 安装器、preflight 和 service doctor。
- 两个对等部署入口：Codex 的 `AGENTS.md`、Claude Code 的 `CLAUDE.md`。

### 3.2 什么继续属于用户私人配置

- anysearch、align、push、pull、commit、living-plan、pressroom 等 workflow。
- 个人 commands、memory、taste、模型预设、MCP、通知音、社媒账号工具和长期偏好。
- govctl、`$agent-profile-governance`、gstack、Jina 轮换器等维护者增强。
- 所有认证、token、session、history；不得复制整份 `.claude-personal` 或 `.codex-personal` 给新用户。

### 3.3 repo-owned skill 的唯一真源与安装位置

- 推荐把当前 76 行薄路由**提升**为 `.agents/skills/feishu/SKILL.md`：这是仓内唯一源码，同时也是 Codex 官方可发现的仓库 skill 位置。
- 提升时保留工具路由逻辑，但把个人 metadata、固定两台机描述和“主人↔bot 只走 DM”等过时规则改成 Link16 通用合同。因此是“迁移现有 skill”，不是长期保留两份相同文件。
- Claude：安装器把该真源 materialize 到用户选择的每个 `$CLAUDE_CONFIG_DIR/skills/feishu/`。
- Codex：按 OpenAI 官方规则安装到 `$HOME/.agents/skills/feishu/`，由该 Windows 用户的所有 `CODEX_HOME` profile 共用。`CODEX_HOME` 仍隔离认证、会话和配置，skill 本身用户级共享。
- 每个已安装副本记录 source path + hash；相同版本跳过，用户自己改过则报告 drift，不静默覆盖。

### 3.4 Profile registry 与隔离目录

- 新增 gitignored 的 `feishu/agent-profiles.local.json` 作为**本机唯一有效 registry**；运行时通过一个 `profile_registry_path()` 解析器只选这一份，不做 base+overlay 合并。
- committed 文件降为脱敏 example/schema，不再携带主人 ccp/ccp2/cxp 的个人名册语义。
- profile wizard 必须显式接收 `profile ID + runtime + home + launcher`；新建时拒绝 home 恰好等于 `~/.claude` 或 `~/.codex`，校验 home 唯一且只允许 HOME-relative 路径。
- 老机器先把当前 registry 无损迁入 local，再切 resolver；现有 bot 的 ccp/ccp2/cxp 不改名、不换账号、不搬认证。默认目录条目只允许作为被审计的 legacy 输入，不再由新建流程产生。

### 3.5 开机启动与人工确认

- 安装器先显示将要修改的准确对象：wmux `HKCU Run` 项、`FeishuBridge-Autostart` 计划任务、目标 repo/Python/Windows 用户、预计启动的 bot 数。
- 只有用户明确确认后才 apply；已有任务先读回并显示差异，不盲目覆盖。
- 正确拓扑是：`登录 → wmux → FeishuBridge-Autostart → bridge bots + cron + watchdog`。
- `registration_monitor` 是每次注册时由注册器按 job 拉起的临时监督 worker，不是第二个全局开机任务。
- 验收必须同时检查计划任务状态、bridge 进程、cron、唯一 watchdog、日志心跳、profile doctor 和至少一次真实收发。

## 4. 执行计划

### S1 · 固化 skill 与隔离 Profile

- [x] **S1.1 把现有 `feishu` skill 提升为 repo-owned 真源**
  - 迁移当前薄路由到 `.agents/skills/feishu/SKILL.md`，去掉个人 metadata/两台机假设，更新 p2a-ext/a2a 与防手动补投规则。
  - 保证 skill 只指向 `TOOLS.md`、ARCH、SOP 和已有脚本，不复制工具实现，不带 anysearch/align/push 等私人能力。
  - 验证：断开 `.claude-personal` 后，在 Link16 仓内仍能发现并正确使用 `$feishu`。
  - 进展：已新增唯一真源 `.agents/skills/feishu/SKILL.md`；所有引用目标存在，禁止的私人路径/metadata/旧 DM 与 a2a 规则扫描为 0。调查同时确认旧个人 compat adapter 会与新用户级 skill 同名，S1.2 必须先报冲突并提供显式迁移，不能静默并存。

- [x] **S1.2 实现多 runtime skill 安装与 drift 保护**
  - 扩展 bootstrap 的 dry-run/apply/doctor：Claude 装到每个已选 config home，Codex 装到用户级 `.agents/skills`。
  - 同名同 hash 跳过；同名不同 hash 报冲突；绝不触碰 auth/session/history。
  - 验证：空 home、重复 apply、用户改写冲突、多个 Claude profile、多个 Codex home 五组 fixture。
  - 进展：安装器已按 Claude profile home 与 Codex 用户级 `.agents/skills` 部署；具备 source/tree hash、manifest、幂等升级、用户 drift/同名冲突 fail-closed。当前机旧 Codex compat adapter 已安全摘除，repo-owned skill 的 Claude/Codex doctor 全绿；69 个其它私人 adapters 未改。

- [x] **S1.3 把 profile 建立器改为用户显式命名（两阶段迁移的第一阶段）**
  - 引入本机唯一 effective registry 和原子迁移；CLI 先展示 profile ID/runtime/home/launcher，再 apply。
  - 新建拒绝 `~/.claude`/`~/.codex`、重复 home、非法绝对路径；launcher 只引用 registry，不复制映射。
  - 验证：新用户可创建 `.claude-work`、`.codex-work2`；老机器 ccp/ccp2/cxp migration 前后解析完全一致。
  - 进展：新增 local registry resolver、`init/register/migrate` 三条 dry-run/apply 路径与脱敏 example；新建拒绝默认目录/重复 home，local 损坏不回退。当前机 9 个 profile 已原子迁移且前后完全相等，ccp/ccp2/cxp doctor 全绿。为避免其它尚未迁移机器拉取即断桥，committed legacy fallback 暂保留；preflight 将要求新部署必须先建 local，待各旧机迁移后再单独摘除 fallback。

- [x] **S1.4 清理 active 文档中的私人依赖与漂移**
  - 修 README、TOOLS、SOP-100/120/121/160；历史 PLAN 不批量追改。
  - 基线流程只引用仓内脚本；私人的 governance/Jina/workflow 只可出现在 optional maintainer 段。
  - 验证：active docs 扫描中不再把私人路径或私人 skill 写成安装前置。
  - 进展：README/TOOLS/SOP-100/120/121/160 与 personal compatibility README 已按 repo-owned skill、local registry、用户显式 profile 和单 watchdog lifecycle 对齐；私人 governance/Jina/workflow 只保留在明确 opt-in 区。额外修复了“可跳过 provider”与初始化器强制双 runtime 的矛盾，单 Claude 或单 Codex registry 已有测试。

### S2 · 两个 runtime 入口成为对等部署工程师 JD

- [x] **S2.1 新建共享 `ROLE` SSOT**
  - 写清部署工程师的 mandate、输入、输出、决策权、人工停点和完成闸，不放 Claude/Codex 专属工具名。
  - 按文档 taxonomy 使用 ROLE，而不是另造 JD/GUIDE 前缀。
  - 进展：新增 `ROLE-010-link16-deployment-engineer.md`，集中 owns mandate、输入/输出、决策权、10 类人工停点、跨 turn 回调与 Core/Feishu/Group 三层完成闸。

- [x] **S2.2 重构 AGENTS.md 为 Codex 入口**
  - Codex 从一次阅读即可进入 ROLE + SOP-100 + TOOLS，并知道 Codex hooks/event、skills 和执行适配。
  - 不引用 Claude 私人 home，不承担 Claude 专属机制。
  - 进展：AGENTS 直接路由 ROLE/SOP/TOOLS，只保留 `$feishu`、typed hook/event、`UserPromptSubmit`、`spawn_agent` 与 effective profile 继承。

- [x] **S2.3 重构 CLAUDE.md 为 Claude 入口**
  - 与 AGENTS 使用同一职责/人工闸/验收骨架，只替换 Claude hooks、background monitor、transcript 与 skill 发现方式。
  - Claude 不再把 AGENTS 当作自己的 runtime adapter，但两者都路由同一共享 SSOT。
  - 进展：CLAUDE 直接路由同一 ROLE/SOP/TOOLS，只保留 `/feishu`、transcript JSONL、Claude hooks、child-session 清理与 Agent tool 继承。

- [x] **S2.4 增加入口语义防漂移检查**
  - 机械检查两份入口共同包含 ROLE、SOP、TOOLS、人工停点和完成闸；允许 runtime adapter 不同。
  - 禁止私人 workflow 再混入 Link16 必备能力。
  - 进展：新增 `tests/test_entry_documents.py`，机械验证 front matter、共同三入口、runtime marker/禁项、ROLE 人工闸、链接存在、长段不互拷与 legacy watchdog 不得重建；S1/S2 focused suite 74 passed + 2 subtests。

### S3 · 安装清单、常驻服务与健康验收

- [x] **S3.1 把安装器输出改成用户可读清单**
  - 软件层：Git/Git Bash、GitHub CLI、Python、Node、wmux、Claude Code、Codex。
  - Link16 层：隔离 profiles、唯一 feishu skill、hooks、local roster、飞书凭据、桥、cron、watchdog、注册 monitor、历史账本。
  - 输出每项“将安装 / 已存在跳过 / 需要登录 / 需要人工确认 / 已验证”，不让用户读技术堆栈猜进度。
  - 进展：`windows_bootstrap.py` 现在稳定输出软件 7 项 + Link16 10 项；保留 raw technical evidence，但人读层只使用固定状态词。当前机能直接看到 5 bot、桥、cron、watchdog、注册回调与 history 的验收状态。

- [x] **S3.2 实现常驻服务 dry-run / apply / rollback**
  - 检查或建立 wmux 登录自启和唯一 `FeishuBridge-Autostart`；不建独立 watchdog task。
  - 修改已存在启动项前显示 before/after；用户确认后才 apply，并保留可恢复信息。
  - 清理 SOP-100 §9.0 与 §9.5 的矛盾，把旧方案只留作明确的历史说明。
  - 进展：新增 `service_installer.py`；plan 精确列 before/after 并生成 digest，apply 绑定已审阅 digest，receipt 支持 compare-and-swap rollback。它不启停生产进程；legacy watchdog 只允许 Disable。当前机唯一差异是 wmux Run 从版本目录换为稳定 shim，已展示给主人，仍等待明确批准，所以尚未 apply。

- [x] **S3.3 扩展 service doctor 与 preflight**
  - 一次检查计划任务、wmux、bridge bot 数、cron、watchdog 唯一实例/心跳/源码新旧、profile 继承、hooks、registration monitor 可启动性和日志可写。
  - 状态分为“文件存在 / 已配置 / 正在运行 / 已真实收发”，防止假绿灯。
  - 进展：新增只读 `service_doctor.py`，preflight 导出同源 `run_checks()`。当前实测 profile/skill、Claude hooks/Codex typed transport、5 个 bridge、唯一 cron/watchdog、wmux RPC、注册 monitor 回调和 history roundtrip 均有机械证据；整体 blocked 只来自尚未批准的 wmux 启动项 drift。另修复 Windows detached PID 不适用 `os.kill(pid, 0)` 导致的 monitor 假死误报。

- [ ] **S3.4 跑空白用户安装验收**
  - 在临时 HOME 先验证全套 dry-run/apply/idempotency，不接触真实认证。
  - 在用户确认的真实账号完成登录、名册、桥和服务安装后，验 DM 收发、注册回调、watchdog 心跳与 history 回读。
  - 进展：临时 HOME 已覆盖 registry dry-run/apply、双 runtime skill、第二次幂等、零 auth/credentials 复制、内存 service apply/rollback；当前真实账号的 DM 往返、注册 ready callback、唯一 watchdog 新鲜心跳与 history 回读均已通过。仅 wmux 持久启动路径 apply 等主人批准，故本 Step 暂不勾完成。
  - 补漏：Codex profile bootstrap 现同时无损合并三类 bridge hooks，保留用户 hook；坏 JSON fail closed。service doctor 改为检查所选隔离 home 的真实 `hooks.json`，不再只凭 worker 文件存在假绿。

### S4 · 真人群最终卡片与机械防双发

- [x] **S4.1 按 route kind 分开发送格式**
  - `p2a-ext → interactive card + @真人`；`a2a → text + @peer bot`；`p2a → DM card`。
  - 卡片真实发送失败时自动降级文字并在 receipt 标明原因，不丢消息。
  - 进展：发送器已按 kind 路由；p2a/p2a-ext interactive、a2a text。progress 用显式 purpose，空 message_id 不再误报成功；fallback receipt 记录 requested/via/degraded/reason。

- [x] **S4.2 保持现有进度策略，最终答案按容量无损分卡**
  - 不新增隐藏推理或工具流水账投递；保留当前已经生效的群 progress 产品策略，不把“无重复”误实现成“一轮只能发一张”。
  - 最终 answer 超过单卡安全容量时按稳定顺序拆成多张，每张带 `part/total` 和稳定 fragment ID；短答案仍只发一张。
  - 同一次 answer 的各分片内容互不重复、缺片可检测，重试时只补缺失分片。
  - 进展：answer/fragment ID 改为跨进程稳定 SHA；2800 字容量内无损切分并显示 part/total。每片成功即 durable ACK，重启只补缺片；删除 600 秒超时推进 HWM 的静默丢失分支。

- [x] **S4.3 机械阻止同轮手动补投**
  - `send_feishu_msg.py` 检测当前 bot active turn 的回址；目标等于本轮自动回址时默认拒绝。
  - 保留显式 proactive override，用于真正的主动通知、跨群和 a2a，不把发送能力一刀切掉。
  - 进展：三 runtime final 路径共享 active turn lifecycle 与 compare-and-clear；发送 CLI 在取 token 前拦截同目标，`--proactive` 显式放行并留痕。

- [x] **S4.4 把主动发送写进统一历史**
  - 成功发送后记录 origin、route、目标、正文、message_id、timestamp；不写 secret/token。
  - `bridge_history.py` 按 message_id 合并去重，自动 answer 与主动发送都能按秒还原。
  - 进展：新增加锁的 `link16-outbound-v1` JSONL；自动片段与主动发送都记录 message_id。history 只按非空 message_id 去重，并抑制已有送达片段对应的 legacy outbox 摘要。

- [x] **S4.5 分离本地 fragment ID 与飞书 provider UUID（生产热修）**
  - 保留 S4.1～S4.4：不回退真人群卡片、稳定分片、逐片 durable ACK、active-turn 防双发和统一历史。
  - `fragment_id` 继续作为 64 位本地账本主键；飞书卡片、a2a 文字和 fallback 文字统一使用从完整 fragment ID 派生的标准 36 字符 UUID。
  - 旧积压原地续送：不清 outbox/HWM/answer-state；未 ACK 片自动改用合法 UUID，已 ACK 片不得重发。
  - 预期文件：`docs/SPEC-210-outbound-delivery.md`、本 PLAN、`feishu/feishu_bridge.py`、`tests/test_outbound_delivery.py`、`tests/eval_plan_970.py`。净改目标：只增加一个 provider 映射 helper 和承重测试，不改本地分片算法。
  - 验证档位：cheap（helper/route 单测）→ stage（49 项投递 focused tests + evaluator mutation）→ e2e（只重启飞书桥，回读 receipt/history/outbox）。
  - 量化判据：本地 ID `64/64` 字符保留；provider UUID `36/36` 字符且三种发送出口 `3/3` 一致；三个积压 outbox `3/3` 重新推进；旧已 ACK 片新增重复 `0`。
  - 结果：只新增一个确定性 UUIDv5 映射；三个旧 outbox 原地从 stuck 降为 `0B`，共 21 个未 ACK fragment 全部取得真实 message_id，未清任何 HWM/answer-state。对同一真人群 fragment 再投一次，飞书返回同一个 message_id，客户端可见消息没有新增第二条。

### S5 · 自动验证、维护窗口与发布

- [x] **S5.1 分层自动验证**
  - focused tests → full pytest → py_compile → preflight/service doctor → docs/skill/profile validator。
  - 只用 fake channel/fixture，不把生产桥重启当自动测试。
  - 进展：全仓 `380 passed + 30 subtests`；承重模块 py_compile 全绿；评分器基线 21/21，重复 fragment 变异降至 19/21，raw provider UUID 变异降至 20/21。当前机 profile/skill/hooks、5 bot、cron、唯一 watchdog、注册回调与 history 真往返已验证。

- [ ] **S5.2 维护窗口做三类真人 E2E**
  - 用户批准后只重启目标 bot/必要服务。
  - 真人群短答案：恰好 1 张 interactive；长答案：按容量得到 N 张有序 interactive，N 个 fragment ID 唯一、正文拼回原文、0 重复。
  - 重试验：同一 answer/fragment 再投一次不新增消息；若中途缺一片，只补缺片。
  - bot 群验：恰好 1 条 text、对端能读并回；DM 卡片、history 和 watchdog 同时复核。
  - 进展：DM 旧积压 21 个 fragment 全部成功且 0 重复；`tb26-baseball-zhen` 在 Sport 业务产品团队成功发送 1 张真人群 interactive，重复同一 provider UUID 得到同一个 message_id。群长答和 live a2a 尚未跑。另发现原 `tb26-baseball` 已不在该群，真实 API 返回 `230002 Bot/User can NOT be out of the chat`；这是成员关系变化，不是本次 UUID 代码回归。

- [ ] **S5.3 晋升真源、提交和版本**
  - 把最终规则晋升到 ROLE/ARCH/SOP/SPEC/TOOLS，回填本 PLAN 和 CHANGELOG。
  - 按概念拆 commit/push；真人 E2E 通过后再决定版本标签，不提前宣称完成。

## 5. 量化交付契约

| 量 | 预期 | 怎么来的 |
|---|---:|---|
| 改造主线 | 4 | skill/profile；runtime 入口；部署常驻服务；群 UX/历史 |
| Stage | 5 | 固化、入口、安装、群聊、验证发布 |
| Step | 19 | 4 + 4 + 4 + 4 + 3 |
| repo-owned skills | 1 | 只保留 `feishu` |
| 新增长期资产 | 约 6 | feishu skill 真源、local registry/example、profile wizard、ROLE、service doctor、outbound ledger |
| 重点修改文件组 | 约 14–20 | profile/bootstrap、skill、入口、SOP/TOOLS、bridge/send/history、doctor、tests |
| 真人验收路径 | 5 | DM、真人群短答案、真人群长答案、重复重试、a2a bot 文字 |
| 常驻启动项 | 2 个入口 | wmux HKCU Run + 1 个 FeishuBridge 计划任务；watchdog/cron 是桥子进程 |
| 不进入仓库的个人 workflows | 至少 8 类 | anysearch/align/push/pull/commit/govctl/gstack/个人 memory 等 |

## 6. 验收账本

### 6.1 判据与天花板

| 维度 | 数据来源 | 满分条件 | 天花板 |
|---|---|---|---|
| 自足完成度 | `tests/eval_plan_970.py` + 临时 HOME | repo skill、local registry、双 runtime 安装、service doctor 全部可达 | 4/4 |
| 路由完成度 | fake channel receipts + outbound ledger | DM card、p2a-ext card、a2a text、三路 provider UUID 全对 | 4/4 |
| 去重与分卡质量 | answer/fragment ledger fixtures | 短答 1 片、长答 N 片可还原、本地/远端 ID 分离、重试 0 新重复、缺片只补缺片 | 5/5 |
| 常驻服务质量 | service doctor 结构化输出 | wmux、bridge task、bridge、cron、唯一 watchdog 均给出真实状态 | 5/5 |
| 文档边界质量 | docs validator | 0 个私人必备依赖、0 个第二 watchdog 现行指令、入口共同职责全覆盖 | 3/3 |

### 6.2 逐版记录

| Version | 完成度 | 质量度 | 证据 | Note |
|---|---:|---:|---|---|
| v2-before | 待基线 | 待基线 | 首次运行 `tests/eval_plan_970.py` 后回填 | 已锁定多卡不重复合同，代码尚未改 |
| v4-s1 | 3/4 | 67 focused tests + 2 subtests | skill 双 runtime doctor、registry 等价回读、profile doctor | S1.1–S1.3 完成；active docs 尚待 S1.4 |
| v4-s2 | 4/4 | 74 focused tests + 2 subtests | active docs scan、单 runtime registry、ROLE/AGENTS/CLAUDE 语义闸 | S1/S2 完成；常驻服务与群投递待实现 |
| v4-s3 | 3/4 | 36 focused tests | 空 HOME、service plan/apply/rollback、四层 doctor、live 5-bot/cron/watchdog/registration/history | S3.1–S3.3 完成；S3.4 只待已展示的 wmux 启动项人工批准 |
| v4-s4 | 19/19 | 61 focused tests + 21 subtests；bootstrap/hooks 补漏 29 passed + 6 subtests | route fake receipts、稳定分片/跨重启 ACK、active-turn guard、unified outbound ledger、PLAN evaluator mutation | S4 完成；`eval_plan_970.py --self-test` 基线 19/19，故意复制一个 fragment 后降为 17/19，证明评分器能抓重复 |
| v4-s5-static | 19/19 | 369 passed + 30 subtests | full pytest、py_compile、profile doctor、preflight、service doctor | 静态/fixture 验收完成；生产桥未重启，真人群短/长/a2a E2E 与 wmux 启动项仍等人工维护窗口 |
| v5-before | **19/19（假绿）** | 生产 `3/3` 活跃 bot 最终回复 HTTP 400 | history 中答案存在但 message_id 为空；三个 outbox stuck | 评分器只测本地 fragment 稳定性，未约束 provider UUID，必须先修尺子再修代码 |
| v6-hotfix | **21/21** | 380 passed + 30 subtests；51 focused + 11 subtests | 三个积压 outbox `3/3` 清零；21 个 fragment 全 ACK；DM/真人群卡片真实 message_id；同 UUID 重试返回同一 ID | 只重启 5 个飞书桥；6 个 wmux PID、8 个 workspace 与 workspace ID 均未变化；21:05 后新增 UUID 相关 HTTP 400 为 0 |

### 6.3 尺子审计

- `tool_fixes`：v5-before 的评分器假绿已修：现在显式检查 64 位本地 ID、36 位标准 provider UUID、三出口共用与 raw UUID 变异；故意恢复旧行为会从 21/21 降为 20/21。
- `blind_spots`：飞书客户端视觉布局仍需真实群回读/截图辅助；API receipt 只能证明 `interactive`，不能证明阅读体验。
- `rejected`：禁止把“去重”偷换成“一轮只允许一条消息”。
- `blocked`：只有飞书权限/真实账号/维护窗口等外部条件才可进入；不阻塞的步骤继续执行。
- `blocked` 当前项：其它旧机器尚未逐台生成 local registry，因此 committed legacy registry 的最终摘除延后；不阻塞新用户显式 init 和本机运行。
- `delivered`：全部自动判据满分且三类真实 route 验收后填写。

## 7. 已识别风险与推荐处理

1. **不是简单复制一个文件就结束**：skill 本体只有 76 行，迁移很小；主要工作在 profile 本地化、安装幂等、常驻服务验收和旧机器无损迁移。整体是中等规模改造，不需要人工逐个筛查整个 claude-config。
2. **Codex skill 不能按每个 `CODEX_HOME` 隔离安装**：OpenAI 官方用户级位置是 `$HOME/.agents/skills`。由于 `feishu` 是 Link16 核心公共能力，让同一 Windows 用户的所有 Codex profiles 共用正合适；认证/会话仍由 `.codex-work*` 隔离。
3. **不能字节级搬现有 skill**：现有 metadata 和“主人↔bot 只走 DM”已经过时。应保留工具路由，迁移为 repo-owned 版本，再把私人那份降为受管安装副本。
4. **不能建第二个看门狗计划任务**：实际代码已经把 watchdog 绑在整体 bridge 生命周期；第二任务会引入双实例和重复注入风险。安装界面仍单独展示“看门狗已启用/已验证”，但底层只建一个桥任务。
5. **老机器迁移是最高风险步骤**：必须先生成 local registry、证明所有现有 bot/profile 解析不变，再切 runtime resolver；失败就不改任何运行进程。

## 8. 执行授权

主人已于 2026-08-26 批准按以下默认值执行：

1. Codex 的 `feishu` skill 按官方方式放 `$HOME/.agents/skills`，同一 Windows 用户的多个 Codex profile 共用。
2. 开机启动采用“wmux 登录自启 + 一个 FeishuBridge 计划任务”，cron/watchdog 由桥带起，不建第二个 watchdog 任务。
3. 本机现有 ccp/ccp2/cxp 先原样迁移；只对**新建** profile 禁止 `~/.claude`/`~/.codex`。

补充合同：真人群最终答案允许因卡片容量拆成多张；验收对象是“没有内容重复、没有重复投递、没有缺片”，不是“永远只有一张卡”。
