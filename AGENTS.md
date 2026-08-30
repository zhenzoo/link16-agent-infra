---
doc_type: AGENTS
doc_id: AGENTS
title: Link16 · Codex 部署工程师入口
status: active
purpose: 让 Codex 直接进入 Link16 的共享部署职责，并只保留 Codex 专属执行翻译。
owns:
  - Codex 在本仓的入口路由
  - Codex hooks、event、skills 与 session 继承适配
does_not_own:
  - 部署职责、人工停点与完成闸（见 ROLE-010）
  - 安装顺序、命令、回滚与排错（见 SOP-100）
  - 桥与 profile 架构（见 docs/ARCH-*）
  - Claude 专属机制（见 CLAUDE.md）
read_when:
  - 以 Codex 身份在本仓开工
  - 用户要求开始部署 Link16
last_reviewed: 2026-08-26
---

# Link16 · Codex 部署工程师入口

本仓是飞书（Lark）↔ Claude/Codex 会话桥与 wmux 驱动层。内容业务属于各内容仓，不进入这里。

## 共同职责与唯一真源

开工先读三份：

1. [ROLE-010](docs/ROLE-010-link16-deployment-engineer.md)：部署工程师的 mandate、决策权、人工停点与完成闸。
2. [SOP-100](docs/SOP-100-new-machine-setup.md)：从零安装的顺序、命令、回滚、验收和排错。
3. [TOOLS.md](TOOLS.md)：所有现成工具及调用入口。

架构理由与硬合同按任务进入相应 [ARCH](docs/ARCH-110-feishu-bridge.md)；出站格式、分片与去重见
[SPEC-210](docs/SPEC-210-outbound-delivery.md)，Cloudflare inventory 见
[SPEC-200](docs/SPEC-200-cloudflare-inventory.md)。单 bot 注册见
[SOP-120](docs/SOP-120-feishu-register.md)，Codex 增量见
[SOP-121](docs/SOP-121-codex-bot-register.md)。

## 接到“开始部署 Link16”

立即按 ROLE 与 SOP 推进，不在入口复制第二份安装教程。对只读检测、已确认范围内的可恢复安装和机械验收
自主执行；只在 ROLE 列出的人工停点暂停。长时 OAuth/权限/认主/入群流程使用 SOP 的 registrar +
Monitor，收到稳定回调后继续，不用短 timeout 等待。

## Codex 执行适配

- 飞书相关任务先使用 repo-owned $feishu skill。Codex skill 原生写作 $name；桥只在 legacy
  /name 恰好匹配一个已安装 skill 时翻译，Codex 原生 slash 命令不改写。
- Codex 完成信号只认 Codex hook/event 记录，不解析 Claude transcript 或假设 Claude JSONL 结构。
  app-server worker 的 typed commentary/tool 只用于正常进度，typed final 才是答案。
- 每个 turn 的 route 由 UserPromptSubmit 捕获，并在异步 drainer 前同时钉进 progress 与 answer
  记录；不得在输出阶段重新猜目的地。
- Codex 原生 spawn_agent 由当前 harness 继承身份，不另选账号。只有独立 wmux session 才经
  feishu/agent_profile_cli.py command/run，并继承父 session 的精确 LINK16_AGENT_PROFILE。
- profile/runtime/home 只从本机 effective registry 解析。禁止从 CODEX_HOME、cwd、alias 或模型名
  反推身份；值缺失、未知或 doctor 不健康时，在创建 pane 之前失败。
- 维护 Codex 适配时不修改 Claude runtime 的 home、hooks、settings、skills 或认证。repo-owned Codex
  skill 的用户级安装位置是 $HOME/.agents/skills；认证与会话仍由各隔离 Codex home 管理。

## 仓库与生产边界

共享的 dirty-worktree、凭据、dry-run、生产重启、启动项和唯一 watchdog 边界只认 ROLE/ARCH/SOP。
Codex adapter 不另写一份；修改后用对应 tests 与机械信号验真。

## 完成声明

不在 Codex 入口自定义完成标准。只有 [ROLE-010](docs/ROLE-010-link16-deployment-engineer.md) 中用户
所选范围的 Core/Feishu/Group 层全部满足，才能说“部署完成”；否则报告已完成层、唯一待人动作与机械恢复信号。
