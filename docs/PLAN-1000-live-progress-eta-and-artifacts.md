---
doc_type: PLAN
doc_id: PLAN-1000
title: Link16 实时进度、绝对 ETA 与中间产物交付
status: active
purpose: 用最小规则让主人持续看见正在做什么、何时完成，以及每个可审阅产物。
owns:
  - 进度卡信息层级与更新节奏
  - Plan、Stage、Step 的优先级与绝对完成时间表达
  - 中间产物的飞书与本地路径交付
does_not_own:
  - 飞书 final answer 的分片容量
  - 生产桥重启窗口
  - 具体业务仓的任务拆解
read_when:
  - 修改 align、living-plan 或 Link16 进行中卡
  - 调整飞书中间产物交付方式
last_reviewed: 2026-09-01
---

# PLAN-1000 · 实时进度、绝对 ETA 与中间产物交付

## 目标与边界

目标不是增加一套字段或任务管理系统，而是让长任务的执行过程持续可见：主人随时能知道当前在做什么、下一份可审阅交付是什么、预计几点完成。

- 只修改 `~/.claude-personal` 与 Link16 两个仓库，不修改 baseball 仓库。
- Stage 的排列顺序就是优先级：越靠前越优先；Step 默认不再重复写优先级字段。
- 独立 Step 可以重排或并行；真实依赖优先于显示顺序。
- 不设 `16k + 4k` 或 `20k` 字符硬上限；按专题边界和可读性自然续开下一份 PLAN。
- 不新增必填 JSON、artifact 工具或复杂卡片协议；自然语言进度是主交互，工具明细只保留在本地审计记录。

## 进度与绝对 ETA 合同

非 trivial 多步任务开工时必须建立 runtime 原生计划投影；Codex 使用 `update_plan`，Claude 使用当前 task/todo surface。Step 状态或 Plan 变化时同步更新。每个计划项都显示时间：完成项写 `（实际完成 HH:mm）`，进行中和未开始项写 `（预计 HH:mm 完成）`，跨天写 `MM-DD HH:mm`。只在 commentary 里说“计划已更新”不算，桥也不从 prose 猜出一份计划。

进行中卡有三层，不得混写：

1. `📋 当前计划`：来自真实 plan event，显示已完成、当前进行和未开始项。
2. 普通 commentary：解释收到什么、准备怎么查、为什么这样做，保持无色。
3. 结果回执：`🟡` 表示方向已锁定或阶段结论有证据，`🟢` 表示 Step/产物完成且验证通过，`🔴` 表示真实 blocker、验收失败或必须优先处理的风险。renderer 只保留 agent 的显式状态，不自动给思考着色。

`📋 当前计划` 使用渐进展开，而不是“只列 Stage”或“复制全部 Step”二选一：

- 所有 Stage 都显示一条顶层项，包含状态、短目标/主要产物和实际完成时间或绝对 ETA，供主人判断优先级与整体排程。
- 只有当前 Stage 在同一顶层项下缩进显示短 Step；每条只保留状态、短产物和实际时间/绝对 ETA。完整动作、验收和证据仍在 PLAN 真源。
- Step 切换后收起旧 Stage、展开新 Stage；尚未进入执行时展开当前决策闸。renderer 只保真显示 runtime plan event，不从 prose 猜层级。

每次开工、切换 Step、产出可审阅成果、发现阻塞或预计完工时间明显变化时，更新同一张进行中卡。Step 变化还要同步 runtime plan，并另发一条结果回执；只输出思考和纯时长不算 Step 更新。连续执行 10 分钟仍无上述事件时，补一条短心跳；汇报不中断后续执行。

每次更新同时给出四级绝对完成点：

1. 总计划或当前全部 P0 的预计完成时间。
2. 当前 P0 的预计完成时间。
3. 当前 Stage 的预计完成时间。
4. 当前 Step 的预计完成时间，以及下一 Step 的预计时间段。

计算前先读取当前 `Asia/Shanghai` 时间。用户可见文字以 `ETA HH:mm（预计 HH:mm 完成）` 为主；“约 8–12 分钟”只能放在括号里辅助，禁止只写纯时长。进入新 Step 或出现新证据就重算；ETA 偏移至少 5 分钟，或剩余工时变化约 20% 时立即说明原因。Step 完成后记录实际完成时间，并用“预估 vs 实际”校准后续 ETA。用户明确纠正视为未通过；用户继续推进且未纠正只能记为暂时通过；没有反馈保持未知，不伪造验收。

## 中间产物合同

RESEARCH、PLAN、PRD、ARCH、SOP、SPEC、ROLE、TASTE、LOG、BAN 等受治理前缀文档，以及图片、视频、音频和 HTML，只要形成可审阅版本就立即交付，不等 Stage 结束：

- 飞书 `p2a`：始终显示本机绝对路径；先查询 Link16 本机全局产物交付策略，默认 off 时不创建在线副本、不显示 HTTPS 行，on 或用户本轮明确要求在线稿时才发布飞书在线文档/媒体。默认不发送本地原文件附件；只有用户明确要求“附件/原文件”才可发送。在线链失败也不得自动降级成附件。
- 本地绝对路径使用普通可选中文字，不放代码块。只有客户端真实支持时才显示可点击本地入口；否则明确使用“复制路径”，不伪装成链接。
- 本地打开是每份产物交付的一部分，不是最终收尾：验证后按“更新 plan → 发带状态回执与入口 → 立即用系统默认应用打开 → 进入下一 Step”逐份执行，禁止攒到最终批量补。有人直接参与的本机会话默认打开；owner `p2a` 在当前任务明确要求自动打开时，也构成对该任务内已核对目标文件的逐份 GUI 授权。后台、cron、a2a 或无人值守任务没有这条明确授权时不弹 GUI，只登记并发送链接/路径。
- 每完成一份产物就交付一条带状态的回执，列出产物、验收状态和访问入口，随后继续执行；不把十份成果压到最终回复一次性发送。

## 全盘职责图

| 层 | 唯一职责 | 本轮承重位置 | 不负责 |
|---|---|---|---|
| 立项 | 初始 Plan/Stage/Step、初始逐项时间、关键 Step 与交付契约 | `~/.claude-personal/skills/align/SKILL.md` | 运行中回填、飞书发送、卡片渲染 |
| 执行 | runtime plan 同步、实际/预计时间回填、黄绿红回执、逐产物交付与打开 | `~/.claude-personal/skills/living-plan/SKILL.md` | runtime 私有事件格式、发送 API |
| 全局入口 | 让所有新 Claude/Codex 受管 Session 都读到最小硬规则 | `~/.claude-personal/CLAUDE.md`、`AGENTS.codex.template.md` | 复制完整 skill；生成后的各 profile 入口不可手改 |
| 飞书选择 | 本机全局在线副本开关、单次明确覆盖与原文件附件授权 | `feishu/artifact_delivery.py`、`.agents/skills/feishu/SKILL.md`、`TOOLS.md` | 业务 Step 何时完成、GUI 是否获授权 |
| 长期架构/协议 | plan 事件、状态语义、时间与附件授权边界 | `ARCH-110`、`SPEC-210` | 替 agent 猜计划、状态或工时 |
| 计划事件 adapter | 把 Codex `turn/plan/updated` 变成 `📋 当前计划`，保留 Step 文本和状态图标 | `feishu/bridge_events.py` | 读钟、计算完成时间 |
| 进度 renderer | 原位渲染 plan/commentary，过滤工具正文，保留显式状态 | `feishu/bridge_outbox.py` | 从 prose 合成计划或给思考自动着色 |
| 在线文档发送 | 两条在线链与失败 receipt；在线失败不再自动发原文件 | `feishu/feishu_bridge.py` | 显式附件发送（另由 `send_feishu_file.py`） |
| 回归 | 锁定逐项时间透传、状态分层、无自动附件、分片与安全 | `tests/test_bridge_events.py`、`test_bridge_outbox.py`、`test_outbound_delivery.py` | 产品决策 |
| 跨 Session 恢复 | 全 profile resolver、Claude/Codex adapter、resume/xray | `PLAN-1010`，后续实施到 `find-session` / `session-xray` | 本 PLAN 的进度卡呈现 |

`align`、`living-plan` 和用户入口都出现同一目标，不是三份 SSOT：align 管“第一次怎么写”，living-plan 管“执行中怎么变”，用户入口只保留跨 Session 不能漏的最低保证。具体飞书降级与卡片行为只认 Link16 skill/ARCH/SPEC/Python。

### 工具选择与调用链

最简答案：**规则写在入口文档和 workflow skill；当前 agent 读取规则与上下文后决定此刻是否触发；skill 把目标路由到一个确定工具；Python 只执行并返回真假，不制定政策。**

调用链固定为四层：

1. **规则在哪里**：当前用户消息拥有最高的本轮意图；`CLAUDE.md` / `AGENTS.md` 放每个 Session 都不能漏的底线；`align` / `living-plan` 放长任务流程；`feishu` / `open-local` 各自放工具边界。
2. **谁决定何时执行**：当前 agent 是判断者。它根据用户消息、Step 状态和已读规则判断“现在该交付、打开还是只继续工作”。skill 不是后台守护进程，Python 也不会自己决定时机。
3. **调用什么**：agent 先选择 skill。涉及飞书用 `$feishu`；涉及本地预览用 `$open-local`。skill 再把动作路由到唯一脚本。
4. **怎样执行并验真**：Python 只做确定动作并返回成功/失败；agent 依据真实返回值更新 plan、发 🟢/🔴 回执并进入下一 Step。在线发送成功不等于本地已打开，打开成功也不等于获得附件授权。

| 用户要的结果 | 触发规则所在 | 具体工具 | 代码归属 |
|---|---|---|---|
| Markdown / HTML 在线阅读 | 全局在线开关 on，或用户本轮明确要求在线稿 | `$feishu` → `artifact_delivery.py` → `feishu_bridge.py send --doc` | Link16 |
| 图片 / 视频 / 音频等在线查看 | 全局在线开关 on，或用户本轮明确要求在线稿 | `$feishu` → `artifact_delivery.py` → `send_feishu_media.py` | Link16 |
| 本地系统默认打开 | Step 有可审阅产物，或用户说“打开” | `$open-local` → `scripts/open_local.py` | 用户级 `.claude-personal` workflow；Codex 读生成 adapter |
| 原文件附件 | **只有**用户明确说“附件/原文件” | `$feishu` → `send_feishu_file.py` | Link16 |

### 五个仓/配置区的真实边界

- `~/.claude-personal`：**共享 workflow 真源**。维护 `align`、`living-plan`、`open-local`、Claude 用户入口源和 Codex 入口模板。
- `~/.agents/skills`：**Codex 可发现的 skill 安装区**。`claude-compat-*` 是薄 adapter，指回 `.claude-personal` 真源；这里不再写第二套业务规则。
- `~/.codex-personal`：**cxp 运行环境**。拥有认证、会话、hooks、配置和生成后的 `AGENTS.md`；不拥有 workflow 真源，生成文件也不手改。
- Link16 仓：**飞书基础设施**。拥有 `$feishu`、在线文档/媒体/附件脚本、事件 adapter、卡片 renderer、ARCH/SPEC 与回归测试；不拥有通用本地打开习惯和业务 PRD。
- 业务仓（例如 baseball）：**业务产物**。拥有自己的 PRD、PLAN、HTML、图片、视频和业务测试；调用用户级 `$open-local` 与 Link16 `$feishu`，不复制它们。

### 一个完整实例：baseball 的 PRD Step 完成

假设 baseball 当前 Step 是“把 8 种部署状态接入 PRD”，文件为 `C:\...\baseball\docs\PRD-320.md`：

1. agent 完成修改和验证。`living-plan` 规定此刻不能继续埋头做下一步，必须先交付这份阶段产物。
2. agent 调用 Codex 原生 plan 更新：该项变成 `✅（实际完成 10:32）`，下一项仍是 `🔄（预计 10:42 完成）`。Link16 的 `bridge_events.py` 只把这个真实 plan event 渲染成 `📋 当前计划`，不会从普通思考里猜计划。
3. agent 发结果回执：验证完成用 🟢；若只是方向锁定用 🟡；若验收失败或阻塞才用 🔴。`bridge_outbox.py` 原样显示这个显式状态，不给普通思考自动着色。
4. 因为当前是飞书 `p2a`，agent 选择 `$feishu` 并查询 Link16 全局策略。开关 off 时只在回执显示 PRD 绝对路径；on 时才由 `feishu_bridge.py send --doc` 生成并追加一行飞书在线文档 URL。用户没说“附件/原文件”，所以任何状态下都不能发送本地 `.md` 附件。
5. 回执发出后，agent 选择 `$open-local`，把已经核对的精确 PRD 路径交给 `scripts/open_local.py`。Windows 用 `os.startfile` 按系统默认关联打开；脚本不搜索文件，也不挑编辑器。
6. 各动作验真后，agent 才进入下一 Step。开关 off 时用户看到有完成时间的当前计划、🟢 阶段回执、本机绝对路径和“已打开”；开关 on 时只额外多一行真实在线 URL。

## S7 目标态验收预览（已实施）

2026-09-01 的新反馈把“产物怎么出现”收敛为三个彼此独立的动作。现状没有全局在线交付开关；`p2a` 仍被用户级入口、`living-plan`、`feishu` Skill 和本 PLAN 同时规定为默认生成在线链接。目标态不是再加一个附件模式，而是把选择权收进一个开关：

1. **本地路径**：每份可审阅产物都显示一行完整绝对路径，不放代码块，也不伪装成 Markdown / `file:///` 链接。现有 Link16 sanitizer 和真实客户端探针只证明它可以复制，尚未证明飞书卡片能通过 Ctrl/右键直接打开；因此目标态明确写“本地路径”，不承诺可点击。
2. **在线入口**：由本机 Link16 的一个全局开关决定。默认关闭时不调用在线发布工具，卡片里完全没有 `https://...` 一行；开启时才调用既有在线文档/媒体工具，并且只多出一行真实 URL。原文件附件仍只有用户明确要求时才允许。
3. **本地预览**：有人直接参与的本机会话立即调用 `$open-local`；本轮 owner `p2a` 已明确授权当前任务逐份自动打开，因而每份产物验证并回执后立即打开。后台、cron、a2a 或其他无法确认 GUI 授权的任务不据 `p2a` 或时间猜测人在电脑前。

开关只负责“要不要生成在线 URL”，不放入 profile registry，也不改变 bot/profile 身份。实际单一真源为 gitignored 的 `feishu/artifact-delivery.local.json`，配一份 committed 样例和 `status/set-online/decide` CLI；所有 Link16 profile 与 bot 在这台机器读取同一个值。`feishu_bridge.py send --doc` 与 `send_feishu_media.py` 在网络前调用该策略闸，`$open-local` 仍只负责本地打开。

### 目标卡片 · 在线开关关闭（默认）

```text
🟢 Step 4.3 已完成（实际 16:42）

产物：LOG-1030 · Baseball 原会话白盒复盘
验收：Codex 时间线、Link16 投递账本与原会话指针已交叉核对。
本地路径：C:\410_VibeCoding\Post\link16-agent-infra\docs\LOG-1030-baseball-session-xray.md
已用系统默认应用打开。

🔄 下一 Step：发布报告并更新 Plan（预计 16:55 完成）
```

### 目标卡片 · 在线开关开启

```text
🟢 Step 4.3 已完成（实际 16:42）

产物：LOG-1030 · Baseball 原会话白盒复盘
验收：Codex 时间线、Link16 投递账本与原会话指针已交叉核对。
本地路径：C:\410_VibeCoding\Post\link16-agent-infra\docs\LOG-1030-baseball-session-xray.md
在线文档：https://YOUR_TENANT.feishu.cn/docx/真实文档标识
已用系统默认应用打开。

🔄 下一 Step：发布报告并更新 Plan（预计 16:55 完成）
```

Step 完成时必须先更新 runtime plan，再单独发上述结果回执；没有产物的 Step 也要发完成时间、验证结果和下一 Step ETA。工具调用、修改路径和原始命令继续留在本地审计，不回到卡片正文。单个 Step 执行超过 10 分钟且没有新结论时，仍发一条短心跳，避免再次出现 15:45–16:11 这种 26 分钟无可见事件的空窗；心跳不是完成回执，也不刷工具流水。

## Living Plan

Stage 顺序即优先级；表内时间是绝对完成点，执行中按实际证据更新。

### S1 · 合同与最小 PLAN（22:57–23:18）

- [x] S1.1 读取两仓现状与承重合同。计划 22:57–23:08；实际 22:57–23:01。
- [x] S1.2 写入本 PLAN，锁定最小规则。计划 23:01–23:10；实际 23:01–23:05。
- [x] S1.3 发布飞书在线文档与本地绝对路径。计划 23:10–23:18；实际 23:05–23:06。

### S2 · 用户级规则简化（23:06–23:37）

- [x] S2.1 简化 `align`：取消固定 20–30 步、每步六字段和全文重复。计划 23:06–23:22；实际 23:06–23:10。
- [x] S2.2 简化 `living-plan`：Stage 顺序承载优先级，机读 schema 仅在确有机械检查需要时启用。计划 23:10–23:27；实际 23:10–23:14。
- [x] S2.3 将公共行为分别落到 Claude 与 Codex 用户级源，并通过治理 renderer 验真。计划 23:14–23:37；实际 23:14–23:19。

### S3 · Link16 卡片与产物呈现（23:19–23:37）

- [x] S3.1 更新出站 SSOT：工具明细折叠、路径能力不误导、四级 ETA 由自然语言承载。计划 23:19–23:33；实际 23:19–23:23。
- [x] S3.2 调整进行中卡 renderer 与回归测试，只保留工具总数。计划 23:23–23:41；实际 23:23–23:26。
- [x] S3.3 定稿当时版本的 mockup：曾误用错误缩写；产物入口改为非代码块且提供诚实 fallback。计划 23:26–23:45（23:30 因补丁重试重算）；实际 23:26–23:37。S5 已统一纠正为 `ETA`。

### S4 · 验收、桥重启与真实交付（23:37–23:58）

- [x] S4.1 跑 Link16 单元、分片、安全与 mockup 渲染检查。计划 23:37–23:58；实际 23:37–23:40。
- [x] S4.2 跑 profile governance dry-run/apply/idempotence/doctor，并同步 `align`/`living-plan` adapters。计划 23:40–23:52；实际 23:40–23:46。受管 docs/wrappers 与两项 adapter 均幂等；全局 doctor 仅保留既有 `cck` 缺 `launch.sh`，未越界重建。
- [x] S4.3 审计两仓最终 diff、回填验收与实际时间。计划 23:46–23:58；实际 23:46–23:51。两仓 `diff --check` 通过；Link16 两个 Python 模块编译通过；本地链接降级结果可复制且重复处理不漂移。
- [x] S4.4 只重启 Link16 飞书桥，不关闭、重建或注入任何 wmux/session；验证进程、bot 健康与会话连续性。计划 23:52–00:02；实际 23:52–23:53。
- [x] S4.5 发布最终在线 PLAN 与验收结论，并等待最后一批增量消息清空。计划 00:02–00:10；实际 23:53–23:58。

### S5 · 新 Session 的计划、状态与阶段交付补闸（09:23–10:35）

2026-09-01 baseball 新 Session 暴露了上一轮没有覆盖的语义缺口：卡片外壳已统一，但 plan、状态圆点和阶段产物仍完全依赖 agent 主动发出。旧 Session `01a0558f-30e1-7c90-bca1-c2668ab03228` 有真实 plan event 和显式 🟡/🟢 回执；新 Session `01a05a8e-0ae1-7d80-b777-bc823a4f3c40` 连续发出多条思考与 ETA，却没有 plan event，也没有在 PLAN/PRD/HTML 形成时即时交付。桥没有丢事件，而是上游未产生事件。

- [x] S5.1 对照 Codex rollout、Link16 event ledger 与 progress state，定位为“runtime plan 未调用 + 思考/里程碑/产物无分层”，不是新 Session 丢历史或 drainer 吞 plan。计划 09:23–09:50；实际 09:23–09:51。
- [x] S5.2 小修 `align` 的关键 Step、详细状态层交接、机械一致性检查和过期引用。计划 09:35–09:55；实际 09:35–09:48。
- [x] S5.3 更新 `living-plan` 与 Claude/Codex 用户级源：强制原生计划投影；普通思考无色；🟡/🟢/🔴 只用于阶段结论、已验证交付与 blocker；Step 状态变化必须单独发结果回执，产物即时交付。计划 09:48–10:05；实际 09:48–09:59。
- [x] S5.4 更新 ARCH/SPEC 和 renderer 回归：显式状态原样保留，普通思考不自动补黄；确认 plan 只来自真实 plan event；每份产物在线交付后立即本地打开，不等最终阶段。计划 09:59–10:15；实际 09:59–10:04，63 项聚焦回归通过。
- [x] S5.5 全盘梳理职责并定位附件根因：HTML 在线 import 因权限失败后，`send --doc` 的 Python 会自动发送原文件；这不是 agent 单纯重复调用。计划 10:04–10:18；实际 10:04–10:08。
- [x] S5.6 落地每项实际/预计绝对时间、取消在线失败自动附件、同步 repo-owned feishu skill 与受管入口。计划 10:08–10:22；实际 10:08–10:28，含 PyYAML 安装与 `$open-local` 官方校验。
- [x] S5.7 跑用户级治理、Link16 聚焦/全量回归，更新在线 PLAN 并逐份本地打开。`send --doc` 是独立短进程，附件修复无需重启生产桥；常驻 progress renderer 未在活 Session 中自动重启。计划 10:22–10:45；实际 10:28–10:35。

### S6 · 计划卡渐进展开（14:32–14:45）

PLAN-240 暴露了第二个投影缺口：完整 PLAN 已有 6 个 Stage、26 个 Step 和绝对时间，但飞书回复把它压成 5 句无时间摘要。用户既不能调整 Stage 优先级，也看不到当前 Stage 的近期交付；反过来复制 26 条长 Step 又会淹没重点。

- [x] S6.1 读取 PLAN-240 的真实第五章，确认缺口在“飞书回复投影”，不是 PLAN 真源。计划 14:32–14:38；实际 14:32–14:34。
- [x] S6.2 冻结“Stage 全显、当前 Stage 展开”的渐进计划卡，并用 PLAN-240 做真实消息样稿。计划 14:34–14:41；实际 14:34–14:38。
- [x] S6.3 将合同写入 align、living-plan、Claude/Codex 用户入口和 SPEC-210，补 runtime plan 透传回归并完成治理同步。计划 14:38–14:45；实际 14:38–14:39。13 项计划事件测试通过；6 个受管入口二次运行零漂移；Align/Living Plan 均通过官方 Skill 校验。
- [x] S6.4 按用户复核补齐有序编号与“对象 + 可验收产物”的自足文案合同；评估 baseball Stage 3 并行，形成独立 wmux worker 的 CLI/Skill 方案。计划 14:43–15:00；实际 14:43–14:48。方案进入 `PLAN-1020`，没有在规则未冻结前直接创建面板。

### S7 · 本地优先产物回执与逐 Step 可见性（16:22–18:00）

本 Stage 是 16:21 新反馈对本 PLAN 的补充，不另建重复 PLAN。Stage 顺序仍代表优先级；S7.1 只冻结目标形态，后续 Step 待用户确认后执行。

- [x] S7.1 核对现有在线交付规则、客户端本地路径能力和真实进度时间线，形成上述两张目标卡片与实施边界。计划 16:22–16:35；实际 16:22–16:29。结论：当前没有全局在线开关；本地路径只能诚实显示为可复制文字；15:45–16:11 的空窗来自上游未产生 Step/心跳事件，不是 renderer 丢消息。
- [x] S7.2 建立 Link16 本机全局在线交付开关的单一真源、样例、查询/切换 CLI 与网络前失败闸。计划 17:28–17:50；实际 17:28–17:37。默认关闭，5 项策略测试、三个 Python 编译与 diff 检查通过。
- [x] S7.3 同步 Claude/Codex 用户级源、`align`、`living-plan`、`feishu`、`open-local` 与过期项目记忆：默认本地路径 + 授权后 `$open-local`，只有开关开启才生成在线 URL。计划 17:37–18:00；实际 17:37–17:44。四个 Skill 通过官方校验；`cc/ccp/ccp2/cck/cx/cxp` 受管入口生成后第二次运行零漂移，优先 profile 的 Feishu Skill 副本与仓库真源 hash 一致。
- [x] S7.4 把“每个 Step 完成必须有 runtime plan 更新 + 结果回执；10 分钟无事件必须心跳”写入 ARCH/SPEC，并新增一次真实渲染序列回归：完成态 plan 与绿色回执进入同一进度卡，工具路径不进入正文。计划 17:44–18:15；实际 17:44–17:47。69 项聚焦回归通过。
- [x] S7.5 分别验收开关关闭/开启两条端到端路径并恢复默认关闭。计划 17:47–18:05；实际 17:47–18:00。off 态真实 `send --doc` 在网络前以 exit 3 拒绝，零 URL/附件；on 态通过原生 docx 链真实创建并送达 `PLAN-1000 · 本地优先产物回执验收`（`https://YOUR_TENANT.feishu.cn/docx/TR0wdY19xoivIhx5yWqcgVdjnae`），receipt 为 `delivered=true`、`doc=true`、`attachment=null`。外层执行器 30 秒切断使同 shell 的 `finally` 未运行，实时复核发现策略仍 on 后已单独恢复 off；因此新增硬规则：单次在线交付只用 `--explicit-online`，不得临时切全局值并依赖 shell 恢复。最终全量 391 项回归和两仓 diff 检查通过；受管入口二次运行零漂移。

## 验收

基线：进行中卡正文被工具活动占据；Plan/Stage/Step 缺少统一绝对完成点；中间产物常到最终阶段才集中出现；本地链接显示与真实能力不一致；HTML 在线导入失败会自动把本地原文件作为附件发出。

目标：四级绝对完成点在每个进度节点同时可见；runtime plan 每项都有实际/预计时间；连续 10 分钟无事件仍有短心跳；一份可审阅产物完成即显示本机绝对路径并在已授权时本地打开；只有全局开关 on 或本轮明确要求时才创建在线副本；在线交付失败绝不自动发送本地原文件；工具明细不再抢正文；Stage 顺序直接表达优先级；两仓回归测试与治理检查通过。

新增验收：新长任务 Session 必须出现真实 `📋 当前计划`；计划卡全部 Stage 可见且只有当前 Stage 展开短 Step，每个可见项都带实际时间或绝对 ETA；普通思考不带状态色；方向锁定/阶段结论、已验收完成、真实 blocker 分别使用 🟡/🟢/🔴；每个产生 PLAN、PRD、图片、HTML、视频或音频的 Step 在产物可审阅时当场给出带入口的结果回执并立即用默认应用打开，不得拖到最终阶段。

已观察：

- S5 聚焦回归共 96 项通过；最终全量回归 485 项、35 个子测试通过，只有第三方 Lark SDK 的 2 条弃用警告。
- S1–S4 的桌面与窄屏 mockup 已真实渲染；S5 根据用户复核把缩写统一为 `ETA`，并固定为绝对完成点优先、分钟范围只作补充。
- profile governance 的 docs、wrappers 以及 `align`/`living-plan` adapter 同步均通过二次运行幂等检查。
- `$open-local` 的官方 `quick_validate.py` 通过；PyYAML 6.0.3 已安装；Codex adapter 二次同步为 `unchanged`；三个相关 Python 文件编译与两仓 `diff --check` 通过。
- 目标 PLAN、ARCH、SPEC、mockup、用户入口和 workflow skills 中没有残留用户可见 `EST`，统一使用绝对 `ETA`。
- profile doctor 仅报告既有且不在本次两仓范围内的 `cck` launcher 缺失；没有把该外部问题误算成本次回归，也没有越界重建。
- 飞书桥已重启；8/8 bot、cron、watchdog 与真实 I/O 正常，23:58 最终文档交付后 Link16 outbox 积压回到 0。重启前后 Link16 会话 `daemon-9383dd5d`、workspace `ea8bc25d` 以及 baseball 的 5 个会话和全部 9 个 workspace ID 均未变化。
- 重启后的进行中消息已由新 bridge 写入出站台账，证明当前 turn 无需重建 session 即可继续增量更新飞书。
- S5 没有重启生产桥；在线文档与取消附件降级由每次独立启动的 `send --doc` 进程立即生效。用户入口、workflow 和常驻 renderer 的新语义由后续新 Session/下次受控桥启动完整读取。
- 观察结果与证据随各 Stage 完成后就地回填，不另造重复状态文件。
