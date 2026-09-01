---
doc_type: PLAN
doc_id: PLAN-1020
title: Link16 独立 wmux Worker 与并行 Stage 路由
status: complete
purpose: 把“何时开独立面板、如何继承 profile、怎样隔离写入并可靠派活”固化为一个通用 CLI 与薄 Skill。
owns:
  - 原生 subagent 与独立 wmux Session 的选择边界
  - 通用 wmux worker CLI 的启动、派活、判活、收件和关闭合同
  - 并行 Stage 的文件所有权与回收规则
does_not_own:
  - wmux daemon 与 RPC 协议
  - Link16 profile 注册表
  - 业务仓内部的任务拆解和产品决策
read_when:
  - 用户要求开新面板或并行执行独立 Stage
  - 创建或修改通用 wmux worker 能力
last_reviewed: 2026-09-01
---

# PLAN-1020 · 独立 wmux Worker 与并行 Stage 路由

## 目标

用户说“把这个 Stage 单独开一个面板做”时，agent 不再忽略，也不在 `AGENTS.md` 里临时手写一串 wmux 命令。最终行为是：Living Plan 先证明任务可安全并行；`$wmux-worker` 选择并调用 Link16 唯一 CLI；CLI 继承当前 `LINK16_AGENT_PROFILE`，创建独立 Session，写入任务/路径所有权，机械确认 kickoff、运行状态和完成回执。

## 目标态回执样稿

下面是实施完成后用户实际收到的启动回执，不是功能描述：

```text
🟢 Baseball Stage 3 独立 wmux Session 已启动并确认开跑

1. Worker 身份：Codex / cxp（继承主 Session，profile doctor 已通过）
2. 工作目录：企业租户A-baseball；独立 Session ID 与 wmux workspace/pty 已登记
3. 只允许写入：场地图释义研究、统一几何计算脚本、2 张指定新版场地图、Stage 3 receipt
4. 明确禁止写入：PRD、requirements-meta.json、Stage 4/5 接口与结构合同
5. 当前任务：复算 18.29 / 36.73 / 42.38 / 57.09m 和 54.5°，生成带反例的计算回执
6. 当前 Step ETA：16:05；Stage 3 ETA：18:15
7. 汇合方式：主 Session 验收 receipt 与图片数值后，单点写回 PRD

主 Session 继续 Stage 1–2；两个执行者没有重叠写入路径。
```

worker 完成时必须另发：实际完成时间、产物路径/在线 URL、验证结果、未完成项和是否已释放写入所有权；只说“子任务完成”不算交接。

## 选择合同

| 形态 | 适用 | 不适用 |
|---|---|---|
| 原生 subagent | 10～40 分钟、边界清楚、分析/检查为主、无需独立存续；继承当前 harness/profile | 需要用户单独查看、跨 Session 续接、长时 GUI 面板或独立飞书身份 |
| 独立 wmux Session | 约 1 小时以上、完整 Stage、独立上下文、需要可视面板/续接/长期保留 | 写入路径无法隔离、任务仍依赖主会话逐步决策、启动成本高于可并行收益 |

并行不是默认提速按钮。只有同时满足以下条件才允许：输入已冻结；输出可独立验收；写入路径与主会话互斥；不会阻塞当前关键决策；预计节省时间显著高于启动、复核和合并成本。否则留在主会话串行执行。

## 架构

1. **Living Plan 决策层**：识别可并行 Stage，记录输入、交付物、验收、文件所有权和汇合点。
2. **`$wmux-worker` Skill 路由层**：说明何时选 wmux、调用哪条 CLI、何时必须停；不复制 RPC 或 profile 逻辑。
3. **`feishu/wmux_worker.py` 执行层**：提供 `plan/start/probe/kickoff/status/close`，复用 `wmux_session.py`、`wmux-rpc.js` 与 `agent_profile_cli.py`。
4. **既有 SSOT**：profile 只认 `feishu/agent-profiles.json` effective registry；pane/workspace 只认 wmux RPC；任务状态只认 CLI receipt 和 worker 产物，不靠空闲屏截图猜。

## Living Plan

### S1 · 合同与失败边界（实际 15:11 完成）

- [x] S1.1 定义 subagent / wmux / 串行三路选择矩阵及反例；交付可被 Living Plan 直接消费的判断合同。（15:11）
- [x] S1.2 定义并行任务包：profile、cwd、任务、输入、输出白名单、禁止写入、验收、汇合点和停止条件。（15:11）
- [x] S1.3 定义真实 blocker：profile 缺失/不健康、wmux 不可达、写入重叠、kickoff 未验真、worker 未交 receipt。（15:11）

### S2 · 通用 wmux worker CLI（实际 15:23 完成；真实面板验证见 S4）

- [x] S2.1 `plan` 只读解析当前 profile、cwd、目标 workspace 和文件所有权，不创建面板；当前 `cxp` 实跑预演回执通过。（15:23）
- [x] S2.2 `start` 经 `agent_profile_cli.py command` 启动正确 Claude/Codex，并写入 `custom.link16.agentProfile / workerId / workerCwd / ownerWorkspace`。（15:23）
- [x] S2.3 `probe/kickoff/status` 使用副作用 marker、spinner 和结构化 receipt；禁止把空闲 banner 当死亡证明。（15:23）
- [x] S2.4 `close` 只关闭 state 与 metadata 同时指向的精确 pane，拒绝当前 pane、跨 profile、跨 workspace 和未知角色。（15:23）

### S3 · 薄 Skill 与 Living Plan 接入（实际 15:23 完成）

- [x] S3.1 新增用户级 `$wmux-worker` Skill；description 覆盖“开新面板/独立 Session/并行 Stage”，正文只保存决策和 CLI 路由。（15:22）
- [x] S3.2 Living Plan 增加并行候选闸：只有输入冻结、输出独立、文件互斥、净收益为正才调用 Skill。（15:18）
- [x] S3.3 完成治理登记、Claude junction 与 Codex adapter 定向同步；用户级入口不复制完整工作流。（15:23）

### S4 · 回归与真实面板验收（实际 15:34 完成）

- [x] S4.1 14 个 Python fixtures + 4 个 JS 合同检查覆盖 profile 命令、缺 profile、doctor 失败、路径重叠、跨 profile/workspace 写入拒绝、当前 pane 关闭保护、Codex spinner 与 split 重复创建防线。（15:34）
- [x] S4.2 在当前 `cxp` profile 下创建短生命周期 Worker `link16-worker-smoke`；profile/cwd/workspace 三项 receipt 检查全部通过。首次 Codex 请求重连 5 次后超时，保留同 pane 重发后成功，证明 runtime 超时不会被伪报成完成。（15:32）
- [x] S4.3 Worker pane `daemon-c90f50b7` 经 state + namespaced metadata 双重核对后精确关闭；主 pane `daemon-b12311bc` 与其他 workspace 均保留，审计 receipt 留在 gitignored state 目录。（15:34）

## 变更记录

- 2026-09-01 15:11：盘点证明 Link16 已有 profile registry、`split-here`、metadata 与确切信号原语；计划从“重建架构”收敛为“提拔通用 CLI + 薄 Skill”，Tennis 的 PNN/lease/内容 marker 保留在业务仓。
- 2026-09-01 15:23：CLI、Skill、Living Plan 并行准入与 12 个 failure fixtures 完成；S1–S3 提前完成，当前只剩真实面板生命周期验收。
- 2026-09-01 15:34：真实测试发现 wmux 3.46 的 `workspace.list.ptyIds` 短暂陈旧会导致旧 `split-here` 重复建 pane；已改为 `surface.list` 交叉观察并禁止无证据重试。清理本轮精确创建的 4 个空白 pane 后，第二次实测完成 `cxp` 启动、kickoff、receipt、metadata 回读与精确 close。

## Baseball Stage 3 的推荐任务包

- 主会话独占：PRD、`requirements-meta.json`、Stage 1–2 的交付文档。
- worker 独占：Stage 3 的场地图释义研究、统一几何计算脚本、两张指定新版图片和一份交接 receipt。
- worker 禁止：修改 PRD、改 Stage 4/5 接口或结构合同、发布最终 PRD。
- 汇合点：主会话验收计算反例、图片数值和 receipt 后，单点写回 PRD。
- 若暂停 Stage 4/5：Stage 6 只能改为“Stage 1–3 部分验收/中间版”，不得宣称全部 P0 或最终总验收。
