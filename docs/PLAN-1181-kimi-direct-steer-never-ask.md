---
doc_type: PLAN
doc_id: PLAN-1181
title: Kimi 直接 steer 与 Never Ask 无人值守修复
status: archived
purpose: 记录已验收的 Kimi 直接 steer 与 Never Ask 修复及生产加载边界。
owns:
  - Kimi 活跃输入直接 steer 的实现与真实验证
  - Kimi 受管启动 Never Ask 的实现与验证
does_not_own:
  - Wmux Autonomy 和跨机发布执行
  - Kimi 原生退出恢复
read_when:
  - 维护 Kimi 提交键或无人值守启动模式
last_reviewed: 2026-10-25
---

# PLAN-1181 · Kimi 直接 steer 与 Never Ask 无人值守修复

## §0 目标、边界与恢复点

目标：交付 `feishu/feishu_bridge.py` 的 Kimi 直接 steer 与 `feishu/agent_runtime.py` 的 Never Ask 启动，供飞书、Cron、看门狗及独立 Wmux worker 共用；真实 Wmux 记录与全仓测试是验收产物。

完整输入与边界（按主人拍板顺序）：
1. Kimi 活跃消息必须直接 steer，不先 Enter 入队，不残留 composer；idle 新回合仍 Enter，Claude/Codex 不改提交键。
2. 受管 Wmux 会话不因 plan approve/reject/revise 或 AskUserQuestion 停住；疑问以完整对话表达，授权内持续推进，不再向主人追加确认。
3. 查最近 commits 并复用已有机制，以最小改动根治；联合做单元、异构和真实 Wmux 压测，通过后才算完成。
4. 不自动回答真人任意非受管终端的审批，不接管工作区现有 wmux paste 安全改动；生产只重载桥，不重开业务 TUI。

基线与方案：`aadae75` 引入 active 时 `Enter → Ctrl+S`，官方语义却是 Enter 入队、Ctrl+S steer；`c9464f1` 已拦 Kimi AskUserQuestion，但 `agent_runtime.standalone_worker_cmd` 仍默认 `--yolo`，官方文档明确它不跳过 plan 退出审批。Claude/Codex 受管启动已有权限 bypass；Kimi 改为官方 `--auto`，不另造自动点击器。

当前 Step／恢复点：全部完成。生产 baseball-3/8 均已重载最终桥代码，原 TUI pty 分别为 `daemon-a72973f5`、`daemon-04eddeec`，底栏均为 Never Ask；测试 workspace、探针、输出和测试目录信任已清理。工作区 `wmux/wmux-rpc.js`、`tests/test_wmux_rpc_contract.py`、`docs/PLAN-1151-wmux-approval-safe-paste.md` 是其他会话改动，主 Session 未写。

## §1 Stage / Step

✅ Stage 1｜Kimi 活跃注入直接 steer，不再入队或假报提交（实际 02:44）
　✅ 1.1 在 `_inject_unlocked` 将 active 改为 paste 后仅 Ctrl+S 的 CSI-u 序列，idle/waiting/unknown 与其他 runtime 不变；`tests/test_inject_busy_guard.py` 固定无 Enter 命令序列（实际 02:45）
　✅ 1.2 隔离真实 Kimi Wmux 持续回合 `session_0a2f2347-a4ea-409c-8d09-f56585426b7c` 产生精确 `turn.steer=1`、对应 `turn.prompt=0`，composer 无残留；`│ >` 提示符与忙屏幕反例均有单测（实际 02:44）

✅ Stage 2｜受管 Kimi 默认 Never Ask，复用现有提问硬拦截（实际 02:40）
　✅ 2.1 将未显式指定模式的 `standalone_worker_cmd` 从 `--yolo` 改为 `--auto`；显式模式、probe/subcommand 不变，同步 ARCH-110/120 与 `tests/test_kimi_profile.py`；两个现存生产 TUI 也已切到 Never Ask（实际 02:40）

✅ Stage 3｜隔离真实 Wmux 压测 plan 自动批准与无人提问（实际 02:49）
　✅ 3.1 以 `--auto` 启动一次性 Kimi TUI，再用 `/plan` 进入计划模式；计划自动批准并写出精确内容 `AUTO_PLAN_OK`，最终屏幕无 approve/reject/revise picker，Wire 无 AskUserQuestion（实际 02:49）
　✅ 3.2 覆盖持续工具回合 steer、idle 新回合、composer 残留反例和无原生回执不盲重；测试 workspace、探针、输出和测试目录信任均已清理（实际 02:50）

✅ Stage 4｜全仓回归与生产加载终审（实际 02:50）
　✅ 4.1 全仓 `1340 passed、3 skipped、171 subtests passed`，定向 `62 passed、15 subtests passed`；diff/旧注释扫描通过，两只目标桥已加载新代码且业务 TUI 不变，其他会话未提交文件保留（实际 02:50）

⏸ 24 小时外候选：无；本轮两项修复及真实验收全部在当前窗口闭环。

## §2 追加回执

- 02:23：直接 steer 代码与文档已改；`test_inject_busy_guard + test_bridge_activity + test_bridge_inbound` 为 50 passed、5 subtests。第一次真实 A2A 验收消息已持久收件，但新桥在 `_ensure_session` 前的 Windows `Get-NetTCPConnection` 探活连续超时，尚无 `turn.steer`，不能算通过；同一探活手工执行已成功。
- 02:44：真实持续回合证明 CSI-u `\x1b[115;5u` 直接 steer 成功，`steers=1、prompts=0`。此前 30 秒窗口短于前台 `sleep 90` 工具回合，不能据此判断旧编码永久失效；最终只采用已获完整回合证据的路径。
- 02:49：Never Ask 下 `/plan` 自动批准并创建精确内容 `AUTO_PLAN_OK`，无 picker、无 AskUserQuestion；全仓 1340 passed、3 skipped、171 subtests passed。
