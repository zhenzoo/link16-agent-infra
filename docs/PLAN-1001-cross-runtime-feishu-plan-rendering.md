---
doc_type: PLAN
doc_id: PLAN-1001
title: Claude、Codex、Kimi 飞书计划卡统一与新 runtime 接入治理
status: complete
purpose: 用最小改动让三种 runtime 在飞书显示同一套 Stage、Step、ETA 计划样式，并建立后续接入千问与 GLM 时的可验证边界。
owns:
  - 本轮跨 runtime 计划卡 renderer 修复与验收
  - 本轮 runtime 接入 fail-closed 治理与历史遗留收口
  - 本轮真实 owner DM 验收证据
does_not_own:
  - 用户级共享沟通规则正文
  - 模型私有思维链的采集或展示
  - 未经本轮验收的新 runtime 正式实现
read_when:
  - 统一 Claude、Codex、Kimi 的飞书当前计划样式
  - 为 Link16 增加千问、GLM 或其他 runtime adapter
last_reviewed: 2026-09-22
related:
  - PLAN-1000
  - ARCH-110
  - ARCH-120
  - SPEC-210
---

# Claude、Codex、Kimi 飞书计划卡统一与新 runtime 接入治理

## 0. 目标、交付对象、用户输入

1. 保留用户已经定稿的 `CLAUDE.md`、`AGENTS.md`、skills 与原生 runtime 规则；renderer 只消费这些规则产生的公开事件，不反向重写用户配置。
2. 让 Claude、Codex、Kimi 的 `📋 当前计划` 在飞书严格使用同一套 `Stage N｜对象与成果`、`N.M` Step、状态图标和绝对 ETA 样式，并修复计划块与后续 commentary 黏连。
3. Kimi 继续使用原生 `UserPromptSubmit` hook 注入工作行，使用 Wire observer 收集公开 text、todo、工具安全摘要和 final；不得采集或展示私有 `think`、工具参数、工具结果或错误原文。
4. 用最小的 runtime adapter 合同和 fail-closed 检查收口历史遗留，明确以后接入千问或 GLM 时需要登记的入口文档、skills、hook/event、启动器与测试，不把未知 runtime 静默当成 Codex 或 Kimi。
5. 完成定向回归、全仓回归和 Claude／Codex／Kimi 的真实 owner DM 验收；测试期间如临时切换 bot profile，验收后恢复原 profile 并留下机械证据。
6. Link16 仓由当前主 session 单写本 PLAN、renderer、runtime adapter、ARCH/SPEC 与测试；用户级 governance 仓只在明确需要修复 renderer/fail-closed 真源时局部修改，保留两仓其他 session 的未提交内容。

## 1. Stage / Step

1. ✅ Stage 1｜锁定跨 runtime 计划卡基线、活动 PLAN 与现有未提交改动（实际 00:34）
　1.1 ✅ 读取文档分类合同并确认本任务接续 PLAN-1000 为 PLAN-1001（实际 00:30）
　1.2 ✅ 对照 Claude、Codex、Kimi 的 plan 事件、卡片拼装和现有测试，标出只需局部修改的边界（实际 00:34）
2. ✅ Stage 2｜让共享飞书 renderer 原样呈现 Stage、Step、ETA 并分隔 commentary（实际 00:39）
　2.1 ✅ 修改计划 label：去除旧的额外有序编号，保留 `Stage N` 与 `N.M`，统一全角缩进和无空行合同（实际 00:36）
　2.2 ✅ 修改卡片 block 拼装，让计划块与 commentary/final 有明确段落边界且不影响分片预算（实际 00:37）
　2.3 ✅ 用同一逻辑计划事件核对 Claude、Codex、Kimi 三条 adapter 输出逐字一致（实际 00:39）
3. ✅ Stage 3｜建立千问、GLM 等新 runtime 的最小接入合同并消除未知 runtime 静默回退（实际 00:50）
　3.1 ✅ 增加小型 runtime adapter 目录，声明入口文档、skill、启动器、hook/event 与公开事件能力（实际 00:44）
　3.2 ✅ 将现有硬编码分支改为显式 Claude/Codex/Kimi 分派，未知 runtime 一律 fail closed（实际 00:47）
　3.3 ✅ 补齐 Kimi 遗漏的 profile/context 文档边界，并写明 Qwen、GLM/ZCode 的两种接入路径（实际 00:50）
4. ✅ Stage 4｜更新 ARCH-110、ARCH-120、SPEC-210 与跨 runtime 黄金测试（实际 00:55）
　4.1 ✅ 更新架构与出站合同，删除仍宣称 `1./2./3.` 和“两个 runtime”的旧描述（实际 00:53）
　4.2 ✅ 新增三 runtime 逐字同构、计划块分隔、隐私过滤与未知 runtime 拒绝测试（实际 00:55）
5. ✅ Stage 5｜通过定向、治理 doctor 与 Link16 全仓回归（实际 00:58）
　5.1 ✅ 运行 renderer、Claude、Kimi、profile 与 session-work 定向回归并修复真实失败（实际 00:54）
　5.2 ✅ 运行 profile governance docs/doctor 幂等检查，确认没有改写用户已定稿规则（实际 00:56）
　5.3 ✅ 运行 Link16 正式 tests 全量回归并审阅最终 diff（实际 00:58）
6. ✅ Stage 6｜在 owner DM 完成 Claude、Codex、Kimi 真实卡片与原生计划能力验收（实际 01:19）
　6.1 ✅ 核对测试 bot 空闲、原 profile=kp 与桥健康，确定 Claude→Codex→Kimi 且最终恢复 kp 的实测顺序（实际 01:05）
　6.2 ✅ 逐个 runtime 发起“公开进度→原生计划→只读工具→final”任务，记录当前 CLI 真实能力而不伪造成功（实际 01:17）
　6.3 ✅ 恢复测试 bot 的 kp profile，交叉核对 outbox、9 条卡片创建/编辑回执、DM 历史与私有 think 零泄漏（实际 01:19）
7. ✅ **⭐ Stage 7｜交付：PLAN-1001、统一 renderer、ARCH/SPEC 与三 runtime DM 验收证据（实际 01:25）**
　7.1 ✅ 回填实际时间、原生工具边界与后续新增 Qwen/GLM 的操作入口（实际 01:22）
　7.2 ✅ 按飞书产物策略交付本机文档入口并给出最终结论（实际 01:25）

## 2. 回执

- 2026-09-22 00:30：确认用户级共享沟通真源已采用 `Stage N` / `N.M` 格式，但 Link16 计划 label 仍额外生成 `1./2./3.`，卡片正文还会把下一条 commentary 直接黏在计划 Step 末尾；当前问题是展示层合同滞后，不是 Kimi 未加载 AGENTS.md。
- 2026-09-22 00:30：确认现有未提交改动包含 Kimi 原生 hook、Wire 进度、升级弹窗处理和真实 DM 证据；本 PLAN 将保留这些改动，只在 renderer、runtime 治理、文档与测试的交叉区域局部合并。
- 2026-09-22 00:39：共享 renderer 已输出无额外有序编号的 `Stage N` / 全角缩进 `N.M`，计划块与 commentary 之间保留一个空行；Claude、Codex、Kimi 相关 81 项测试与 21 个 subtest 通过。Stage 2 比原 ETA 01:15 提前 36 分钟，原因是三条 runtime 已共用同一个 `_plan_label`，只需修正共享投影层。
- 2026-09-22 00:50：Link16 现有 `RuntimeSpec` 已扩成小型 adapter 目录表，profile runtime、skill 安装、hook installer、entry document 与公开 event source 可枚举；standalone launcher、governance renderer 和 lifecycle 的未知 runtime 静默回退已改为显式拒绝。Kimi context import 目标由错误的 `CLAUDE.md` 修正为 catalog 指定的 `AGENTS.md`，agent registry 候选也纳入 Kimi 与未来 defaults。
- 2026-09-22 00:55：ARCH-110 已明确“配置 renderer / 飞书 renderer”两个窄职责，SPEC-210 已切到无外层有序编号的现行样式，ARCH-120 已补齐 Kimi 与 Qwen/GLM-ZCode 接入合同；三 runtime 黄金测试逐字相等。193 项 Link16 定向测试、41 个 subtest 与 24 项治理测试、3 个 subtest 通过。实现复用了既有公共 adapter，后续 ETA 提前至 02:30。
- 2026-09-22 00:58：profile governance dry-run 为 writes=0，七个受管入口、profile 与三份 wrapper doctor 全绿；Kimi profile home、共享 feishu skill 和原生 hook doctor 全绿。Link16 正式 `tests/` 为 996 passed、116 subtests，只有三条第三方 SDK deprecation warning。
- 2026-09-22 01:13：tb26-ccp 依次冷启动 Claude 2.1.278 与 Codex 0.155.1；两者的工作行标题、公开进度、只读工具与 final 均真实 DM 送达。当前这两个独立 TUI 工具面分别未暴露 TodoWrite 与 `update_plan`，因此没有伪造原生 plan 通过；这与支持 `update_plan` 的当前主 Codex harness 是能力面差异，renderer 仍只消费真实结构化事件。
- 2026-09-22 01:17：Kimi 0.41.0 `kp` 真实 owner DM 验收通过。原生 TodoList/Wire 送达的进度卡已精确显示 `📋 当前计划`、`✅ Stage 1｜…`、`🔄 Stage 2｜…`、`⏳ Stage 3｜…`，含实际时间与绝对 ETA；无外层 `1./2./3.`、无计划/commentary 黏连。同轮两次公开进度、只读 `git status --short`、工作行标题和 `KIMI_DM_FINAL_NEW_OK` 全部完成。
- 2026-09-22 01:19：tb26-ccp 已恢复并保持原 `kp` profile，profile doctor 为 ok、桥进程和 Kimi session 均在跑。本轮 Kimi outbox 只含 commentary/plan/tool 安全摘要和 answer，`private think` 步骤数为 0；送达回执为 card×5 + edit×4。Stage 6 比原 ETA 02:15 提前 56 分钟，原因是用同一可恢复测试 bot 串行冷启动，无需新增验收基础设施。
- 2026-09-22 01:25：最终定向回归 168 passed、34 subtests，`git diff --check` 通过，tb26-ccp outbox backlog=0。实现保持“一处共享格式修正 + 小型显式 runtime 目录”，没有新增 daemon、prose 计划猜测、重试层或第二套卡片协议，也没有改写用户已定稿的共享沟通规则。
