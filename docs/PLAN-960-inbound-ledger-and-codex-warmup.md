---
doc_type: PLAN
doc_id: PLAN-960
title: 飞书入站消息账本与 Codex 首启等待修复
status: complete
purpose: 让群聊、桥内命令和会话启动失败前的消息都可追溯，并消除 app-server 合法 warm-up 被 30 秒默认误判的问题。
owns:
  - 本次入站账本、历史合并和 ready timeout 修复步骤
  - 本次异构测试、变异验证与验收账本
does_not_own:
  - 飞书桥长期架构（见 ARCH-110）
  - 注册状态机与能力档（见 PLAN-940、SOP-120）
  - provider 自身的模型刷新性能
read_when:
  - 修复或复盘飞书消息历史缺口
  - 排查 Codex app-server 首启被过早判失败
last_reviewed: 2026-08-26
---

# PLAN-960 · 飞书入站消息账本与 Codex 首启等待修复

plan_version: 3

## 目标与已证事实

目标是让“bot 已收到”的消息在是否有 runtime 会话、是否成功启动、是否由桥内命令消费之外都能查到全文，同时让桥的默认等待窗口覆盖 Codex worker 明示允许的 warm-up。

已由生产日志、当前代码和已安装 SDK 三源交叉确认：

- `tb26-baseball` 在 10:30:09 收到群 @，随后 Codex app-server 会话未就绪；桥日志保留了 80 字预览，但当前 `bridge_history` 不显示该入站。
- 当前 history 只读 `bridge-session-<bot>.json` 指向的一份 transcript；slash、启动失败和旧 session 都可缺失。
- bridge 默认每次只等 30 秒；fresh app-server thread 在 remote TUI 启动前允许 warm-up 最多 120 秒。合法 warm-up 超过 30 秒时，桥必然至少产生一次错误未就绪判定。
- 本次日志中的 model refresh error 支持“首启慢”解释，但不足以证明它是唯一慢因；本计划只修机械可证的 30/120 合同矛盾。

## Stage 1 · 合同先行

- [x] S1.1 在 ARCH-110 定义 accepted inbound 的留存边界、字段安全、同步落盘、确定性去重和 legacy cutover。
  - 文件：`docs/ARCH-110-feishu-bridge.md`
  - 验证：明确群 @、slash、启动失败 3 类消息都在 session 前落盘；未授权/未 @ 消息不扩大留存。
- [x] S1.2 在 ARCH-110 定义 app-server ready timeout 必须覆盖 120 秒 warm-up，且显式 override 优先。
  - 文件：`docs/ARCH-110-feishu-bridge.md`
  - 验证：文档不把本次唯一根因归咎于 model refresh。

## Stage 2 · 持久化与查询实现

- [x] S2.1 新增按 bot append-only 入站账本，记录完整原文与安全元数据；进程锁、flush/fsync、坏行跳过。
  - 文件：`feishu/bridge_inbound.py`、`feishu/feishu_bridge.py`
  - 验证档位：cheap + 并发逆境。
  - 量化判据：群 @、slash、长 DM 3/3 在无 session 时仍可读；并发 N 条有效 JSON = N。
- [x] S2.2 history 以账本为新 SSOT，按 message ID 去重，并只用 cutover 前 transcript 补旧历史。
  - 文件：`feishu/bridge_history.py`、`tests/test_bridge_history.py`
  - 验证档位：stage。
  - 量化判据：同 ID 重投 1 条、同文不同 ID 2 条、legacy+ledger 边界无重复。
- [x] S2.3 app-server 使用独立长默认，保留显式 timeout、roster override 与 Claude/legacy 短默认。
  - 文件：`feishu/feishu_bridge.py`、`tests/test_agent_runtime.py`
  - 验证档位：cheap。
  - 量化判据：app-server 默认 >120；其余默认 =30；两个 override 优先级正确。

## Stage 3 · 验证与生产事实回填

- [x] S3.1 三类异构测试：纯函数/边界单测、并发与坏行逆境、history CLI 临时目录集成；再跑全套回归并检查日志/中间 JSONL。
- [x] S3.2 新增评分器与变异验证：空目录 0 分；移除 handler append 或缩回 30 秒时对应维度必须变红。
- [x] S3.3 用飞书 API 的 message ID/create_time 和桥日志交叉核对，把本次群 @ 作为 `source=backfill` 写入 `tb26-baseball` 入站账本，再由 history 读回；不重启正在工作的生产会话。
- [x] S3.4 更新 TOOLS/CHANGELOG，回填本 PLAN 的影响、分数与交付事实。

## 验收账本

| 版本 | 完成度 | 质量度 | 证据 | 备注 |
|---|---:|---:|---|---|
| before | 0/3 | 0/4 | 群 @、bridge slash、启动失败消息均可能不在当前 transcript；app-server 默认 30 < warm-up 120 | 基线 |
| v3 | 3/3 | 4/4 | focused 51 passed；eval 空目录 0/0、实仓 3/3+4/4；两种变异分别降到 1/3 与 3/4；生产 history 回读 9 条入站；全仓 306 passed + 16 subtests | 验证收口完成；代码随本批发布 |

ceiling:

- 完成度 3/3：群 @、slash、启动失败长消息三类 accepted inbound 都先落盘且可由 history 读回。
- 质量度 4/4：跨进程写完整、坏行可恢复、确定性合并无误吞、runtime timeout 分流无回归。

tool_fixes:

- 第一次变异运行在 Windows 子进程读取评分器中文 JSON 时按错误代码页解码，reader thread 崩溃、stdout 变成 `None`。夹具现显式给子进程 `PYTHONIOENCODING=utf-8`；这是测试传输层修复，不改被测结论。

blind_spots:

- 账本从 Link16 handler 边界开始，不覆盖 SDK 在调用 handler 前已拒绝或去重的原始 WS 事件。
- 生产故障的唯一慢因无法仅从既有日志证明；本次只宣称修复可机械证明的 timeout 合同矛盾。

rejected:

- 拒绝“相同文本 + 相近时间”模糊去重：会误吞用户在失败后的真实重发。
- 拒绝为了加载新代码重启当前正工作的 `tb26-baseball`；先完成离线验证，生产自然重启后生效。

blocked: []

delivered:

- `tb26-baseball` 已回填 6 条可证历史：两次早期 `/account`、失败长消息（14883 字）、`/close`、恢复用 `/account ccp2`、群内“你是干什么的”。message ID/create_time 来自飞书 API；群正文同时由桥日志核对。

## v2 回填影响

- 入站账本只存资源类型/文件名/时长，不存可下载资源的 `file_key`；对已有附件下载链零改动。
- history 不再用模糊文本窗口去重；native ledger 首次成功写入时间是唯一 cutover，旧 bot 无 ledger 时保持原行为。
- timeout 分流收束为 `_ready_timeout()`：函数显式值 > roster 值 > runtime 默认。现有测试使用的 `0.1` 秒显式窗口继续有效，不会被 `or` 吞掉。
