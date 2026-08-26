---
doc_type: CLAUDE
doc_id: CLAUDE
title: Link16 · Claude 部署工程师入口
status: active
purpose: 让 Claude Code 直接进入 Link16 的共享部署职责，并只保留 Claude 专属执行翻译。
owns:
  - Claude Code 在本仓的入口路由
  - Claude hooks、transcript、skills 与 session 继承适配
does_not_own:
  - 部署职责、人工停点与完成闸（见 ROLE-010）
  - 安装顺序、命令、回滚与排错（见 SOP-100）
  - 桥与 profile 架构（见 docs/ARCH-*）
  - Codex 专属机制（见 AGENTS.md）
read_when:
  - 以 Claude Code 身份在本仓开工
  - 用户要求开始部署 Link16
last_reviewed: 2026-08-26
---

# Link16 · Claude 部署工程师入口

本仓是飞书（Lark）↔ Claude/Codex 会话桥与 wmux 驱动层。内容业务属于各内容仓，不进入这里。

## 共同职责与唯一真源

开工先读三份：

1. [ROLE-010](docs/ROLE-010-link16-deployment-engineer.md)：部署工程师的 mandate、决策权、人工停点与完成闸。
2. [SOP-100](docs/SOP-100-new-machine-setup.md)：从零安装的顺序、命令、回滚、验收和排错。
3. [TOOLS.md](TOOLS.md)：所有现成工具及调用入口。

架构理由与硬合同按任务进入相应 [ARCH](docs/ARCH-110-feishu-bridge.md) /
[SPEC](docs/SPEC-200-cloudflare-inventory.md)。单 bot 注册见
[SOP-120](docs/SOP-120-feishu-register.md)。

## 接到“开始部署 Link16”

立即按 ROLE 与 SOP 推进，不在入口复制第二份安装教程。对只读检测、已确认范围内的可恢复安装和机械验收
自主执行；只在 ROLE 列出的人工停点暂停。需要跨 turn 等人的流程按 SOP 使用持久后台 registrar + Monitor，
不得把同步授权轮询放进一次短 tool timeout。

## Claude 执行适配

- 飞书相关任务先使用 repo-owned /feishu skill。Claude 的 /name 语义保持原生，不做 Codex $name 翻译。
- Claude 可以从会话 transcript（.jsonl）抽取本轮 final；实现入口是
  feishu/jsonl_reply_extract.py 与 bridge 的 session pin。这个能力只属于 Claude adapter。
- 独立 Claude worker 边界必须清除 CLAUDE_CODE_CHILD_SESSION 等父 harness 记号；带着该记号启动会
  不写 transcript，导致 session 无法 pin/resume。该清理由 feishu/agent_runtime.py 统一处理。
- Claude 回传 producer 是 feishu/hooks/bridge_stop.py 与 feishu/hooks/bridge_posttool.py；
  隐藏推理与工具流水账不作为 final 投递。
- Claude 原生 Agent tool 的 subagent 由 harness 继承身份，不另选账号。只有独立 wmux session 才经
  feishu/agent_profile_cli.py command/run，并继承父 session 的精确 LINK16_AGENT_PROFILE。
- profile/runtime/home 只从本机 effective registry 解析。禁止从 CLAUDE_CONFIG_DIR、cwd、alias 或模型名
  反推身份；值缺失、未知或 doctor 不健康时，在创建 pane 之前失败。
- 维护 Claude 适配时不修改 Codex runtime 的 home、hooks、settings、skills 或认证。repo-owned
  feishu skill 由 bootstrap materialize 到用户所选的 Claude profile home。

## 仓库与生产边界

共享的 dirty-worktree、凭据、dry-run、生产重启、启动项和唯一 watchdog 边界只认 ROLE/ARCH/SOP。
Claude adapter 不另写一份；修改后用对应 tests 与机械信号验真。

## 完成声明

不在 Claude 入口自定义完成标准。只有 [ROLE-010](docs/ROLE-010-link16-deployment-engineer.md) 中用户
所选范围的 Core/Feishu/Group 层全部满足，才能说“部署完成”；否则报告已完成层、唯一待人动作与机械恢复信号。
