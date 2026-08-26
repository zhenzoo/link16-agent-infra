---
doc_type: CLAUDE
doc_id: CLAUDE
title: Link 16 · Claude 执行适配层
status: active
purpose: 承载 Claude Code 专属的执行机制，并把全部共享规则路由到 AGENTS.md，避免两个入口各写一份正文。
owns:
  - Claude Code 专属的执行机制（hooks / 完成信号抽取 / subagent 继承 / 默认 profile / 长任务续跑）
does_not_own:
  - 仓库身份、结构、硬边界、验证命令、文档地图（全在 AGENTS.md）
  - Codex 专属机制（AGENTS.md 的「Codex 适配层」）
  - 架构理由（docs/ARCH-*）与操作步骤（docs/SOP-*）
read_when:
  - 以 Claude Code 身份在本仓开工
last_reviewed: 2026-08-26
---

# Link 16 · Claude 执行适配层

## 👉 开工先读 [`AGENTS.md`](AGENTS.md)

**共享正文只在 `AGENTS.md` 维护一份**——仓库身份、结构与依赖方向、两个 registry / 两本名册的分工、
硬边界（身份 / 名册 / 飞书投递 / 生产纪律）、验证命令、文档地图、文档规范，全在那里。
本文件**只放 Claude Code 专属的执行机制**，不复制那些内容。

人类向的介绍与快速开始：[`README.md`](README.md)。

---

## Claude 专属机制

### 完成信号：可以读 Claude JSONL
桥抽取「这一轮 Claude 干完了没、答复是什么」时，**允许**解析 Claude Code 的会话 transcript（`.jsonl`）。
这是 Claude 侧独有的能力——**Codex 侧不成立**（Codex 必须走它自己的 hook event 记录）。
相关实现：`feishu/jsonl_reply_extract.py`、`feishu/feishu_bridge.py` 的会话 pin 逻辑。

> ⚠️ 由此派生一条易踩的坑：带着 `CLAUDE_CODE_CHILD_SESSION` 起的 Claude 会话**不写 transcript**，
> 于是桥 pin 不住会话、`/resume` 也恢复不了。起号脚本必须先洗掉这组 harness 记号
> `agent_runtime.py` 已在 Link16 worker 边界统一 `unset`，不依赖部署者的私人配置目录。

### Hooks
Claude 侧的回传 producer 是 `feishu/hooks/bridge_stop.py` 与 `feishu/hooks/bridge_posttool.py`
（Codex 侧另有 `codex_bridge_*.py`；app-server 模式的 Codex 会自动 no-op）。

### Subagent
Claude Code 原生 Agent tool 起的 subagent **由 harness 继承身份，不另选 profile**。
只有**独立 wmux session** 才走 Link16 launcher（`feishu/agent_profile_cli.py`）。

### 默认 profile
本 runtime 的机器默认 profile 见 `feishu/agent-profiles.json` 的 `default_profiles.claude`；
各机可用 `feishu/bridge-bots.local.json` 的 `defaults.profiles.claude` 覆盖本机默认。
**不要在本文件或任何仓内代码里硬编码 profile 名与 home 路径。**

### Skills
Claude 的 skill 用 `/name` 触发，行为不变；桥不会改写 Claude 的 slash 命令
（只在 Codex 侧才做 `/name` → `$name` 的受限翻译）。

---

## 新机部署的 Claude 适配

共享的部署职责、人工停点和完成判据只认 [`AGENTS.md` §9](AGENTS.md)；完整步骤只认
[`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md)。Claude 侧只补执行机制：

- 注册/OAuth 这类跨 turn 等人的进程必须用 SOP 指定的 background registrar + Monitor；不要把同步
  Device Grant 放在一次 Bash/tool timeout 里等待。
- 注册 SOP 规定的每个人工链接都由人打开。Claude 只转发裸链接、说明当前要点什么，然后等 Monitor
  的机械回调；不得替用户宣称审批或发布完成。
- provider 登录、GitHub 设备码和飞书组织选择是人工边界；其余检测、安装、profile/roster 生成与
  验收由 agent 继续执行，不把 shell 教程转嫁给主人。

---

## 提醒：改 `_linkify` 后要重启桥

发飞书的链接不能套反引号——这条规则与其代码强制说明在 [`AGENTS.md` §4.3](AGENTS.md)。
这里只补一句 Claude session 常忘的操作后果：**改完 `feishu/feishu_bridge.py` 的 `_linkify`
必须 `stop` → `start` 重启桥进程**，跑着的 Python 进程持的是旧字节码，改了文件不生效。
而重启桥会中断主人正在进行的会话——属于运维动作，先确认再做。
