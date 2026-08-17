---
doc_type: AGENTS
doc_id: AGENTS
title: Link 16 agent 入口（runtime-neutral 共享正文）
status: active
purpose: 让任意 runtime（Claude Code / Codex / 其它）的 agent 在一次阅读内拿到本仓身份、硬边界、验证方式与文档路由。
owns:
  - 仓库身份与职责边界
  - 全 runtime 共享的硬约束（身份、名册、飞书投递、生产纪律）
  - 变更后的验证命令
  - 文档地图与工具路由
does_not_own:
  - 架构理由（见 docs/ARCH-*）
  - 操作步骤（见 docs/SOP-*）
  - Claude 专属执行机制（见 CLAUDE.md）
  - 内容业务规则（属各内容仓）
read_when:
  - 每一个在本仓开工的 agent session
last_reviewed: 2026-08-17
---

# Link 16 · agent 入口

> 本文件是**全 runtime 共享的正文**。`CLAUDE.md` 只放 Claude 专属适配并路由回这里；
> 本文件末尾的「Codex 适配」是 Codex 专属那一层。**共享规则只在本文件维护一份，两个入口不各写一遍。**

## 1. 身份

本仓是**舰队级 agent 基建**：飞书（Lark）↔ Claude/Codex 会话桥 + wmux 面板驱动层。
它只负责**把消息和会话事件在飞书与本机 agent 面板之间转运**；
任何内容业务（写帖 / 随笔 / MV / CAD）都属于各内容仓，不进本仓。

面向人类的介绍与快速开始见 [`README.md`](README.md)。

## 2. 结构与依赖方向

| 目录 | 是什么 | 依赖 |
|---|---|---|
| `wmux/` | wmux daemon 的 JSON-RPC 客户端（`wmux/wmux-rpc.js`）+ 面板原语 | 零依赖（最底层） |
| `feishu/` | 桥主进程、回传链（hook→outbox→drainer）、`send_feishu_*` 发送工具、注册流、cron、profile 运行时 | 依赖 `wmux/` |
| `docs/` | ARCH（为什么这么设计）/ SOP（怎么一步步做）/ SPEC（精确合同）/ PLAN（一次有界改动） | — |
| `tests/` | pytest 套件 | — |
| `TOOLS.md` | **工具索引（SSOT）**——「有没有现成的工具干 X」一律先查它 | — |

依赖方向单向：`feishu/ → wmux/`；各内容仓 → 依赖本仓。

## 3. 两个 registry、两本名册（最常被搞混，先记住分工）

| 文件 | 唯一职责 | 进 git |
|---|---|---|
| `feishu/agent-profiles.json` | **profile → runtime / home / launcher 的唯一映射**。仓库内任何地方都不得再写第二份映射。 | ✅ |
| `feishu/agent-registry.json`（或本机 `.local.json`） | **身份目录**：全舰队谁是谁、在哪台机、分管哪个仓、open_id。路径解析唯一入口 = `feishu/bridge_env.py` 的 `registry_path()`。 | 迁移中（见 `docs/PLAN-926`） |
| `feishu/bridge-bots.local.json` | **本机运行时名册**：桥要跑哪些 bot、用哪个 profile、cwd 在哪。语义是**整盘接管**——桥只跑它列的。 | ❌ 每台机各管各的 |
| `feishu/agent-registry.example.json` | 脱敏样例，给新用户看 schema | ✅ |

## 4. 硬边界（违反即停）

### 4.1 身份
- 桥主 session 与每个独立 wmux worker 各自携带**恰好一个** `LINK16_AGENT_PROFILE`。
- 独立 worker 必须经 `feishu/agent_profile_cli.py` **继承父 session 的确切值**；
  值缺失 / 未知 / doctor 不健康时，**在建 pane 之前就失败**。
- **绝不**从 `CODEX_HOME`、`CLAUDE_CONFIG_DIR`、alias 或 cwd 反推身份。
- 各 runtime 的原生 subagent（Claude 的 Agent tool、Codex 的 `spawn_agent`）由各自 harness 继承，**不另选 profile**。
- 新 profile / 新 bot / 新仓 worker 接入，一律走 `$agent-profile-governance`。

### 4.2 名册
- 名册只持久化 `profile`（或 `defaults.profiles`）。
  legacy 的 `agent` / `account` / `claude_config_dir` / `codex_home` 是**只读迁移输入**，新行不得再写。
- 🚨 **本机没有 `bridge-bots.local.json` 时不要直接注册 bot**：注册流会拿 committed 的
  `bridge-bots.json` **整盘做种子**，把别的机器的 bot 抄进本机名册并被本机桥拉起——
  而同一个飞书应用同时只允许一条长连接，等于**抢掉对方正在用的连接**。先落一本只含本机 bot 的名册。
  `python feishu/preflight.py` 会检查这一项。

### 4.3 发往飞书的内容
- **链接必须裸写或 `[标签](url)`，绝不套反引号或代码围栏** —— 否则 `feishu/feishu_bridge.py` 的
  `_linkify` 按「代码区原样留」跳过，飞书渲成不可点的等宽码。
  已做成代码强制（`_unwrap_url_code()`），但**改完 `_linkify` 必须重启桥进程**才生效（跑着的进程持旧码）。
- `file://` 本地路径在飞书**不可点**；要让人在手机上打开，只有在线文档链接这一条路。
- 飞书云文档（`my.feishu.cn/docx/`）会自动渲染成带标题的卡片 → 裸发 URL + 建文档时设好标题即可。

### 4.4 生产纪律
- 这是**生产仓库**：保留工作树里与本次任务无关的用户改动。
- **重启或切换正在跑的桥是运维动作，不是自动测试的一部分** —— 会中断主人正在进行的对话。
- 绝不打印 app secret、MCP token，或共享 `.env` 里的任何值。
- 注册器 / hook 安装器先跑 dry-run。

## 5. 变更后的验证

```powershell
python feishu/preflight.py                  # 环境体检（只读·装桥前也用它）
python -m py_compile feishu/agent_runtime.py feishu/feishu_bridge.py `
       feishu/hooks/codex_bridge_stop.py feishu/hooks/codex_bridge_posttool.py
python -m pytest tests/ -q                  # 全套（亦可 python -m unittest discover -s tests -v）
python feishu/feishu_bridge.py status       # 桥进程 / 凭据 / 会话活性
```

改了跨机路由相关代码，额外验：

```powershell
python feishu/registry.py --json peers <仓名> --exclude-machine <本机代号>
```

## 6. 文档地图（按「我想干什么」找）

| 我想… | 看 |
|---|---|
| **找有没有现成工具** | [`TOOLS.md`](TOOLS.md) ⭐ 工具索引 SSOT |
| 在新电脑上从零装好 | [`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md) |
| 注册一只飞书 bot | [`docs/SOP-120-feishu-register.md`](docs/SOP-120-feishu-register.md) · Codex 版 [`SOP-121`](docs/SOP-121-codex-bot-register.md) |
| 给已有 bot 改名 | [`docs/SOP-125-bot-rename.md`](docs/SOP-125-bot-rename.md) |
| 安全升级 wmux | [`docs/SOP-010-wmux-upgrade.md`](docs/SOP-010-wmux-upgrade.md) |
| 弄懂桥怎么运转 | [`docs/ARCH-110-feishu-bridge.md`](docs/ARCH-110-feishu-bridge.md) |
| 弄懂 wmux 面板怎么被驱动 | [`docs/ARCH-010-wmux-orchestration.md`](docs/ARCH-010-wmux-orchestration.md) |
| profile / 多账号运行时 | [`docs/ARCH-120-agent-profile-runtime.md`](docs/ARCH-120-agent-profile-runtime.md) |
| 智能体互相喊话（a2a） | [`docs/ARCH-140-a2a-comm-protocol.md`](docs/ARCH-140-a2a-comm-protocol.md) |
| 给智能体排定时任务 | [`docs/ARCH-150-agent-cron.md`](docs/ARCH-150-agent-cron.md) |
| Cloudflare 上有什么 | [`docs/SPEC-200-cloudflare-inventory.md`](docs/SPEC-200-cloudflare-inventory.md) |
| **对外开放前要做什么** | [`docs/PLAN-926-public-onboarding.md`](docs/PLAN-926-public-onboarding.md) |

`docs/PLAN-9xx` 是历史执行记录（一次有界改动的计划 + 复选框状态），**不是现行真源**；
现行真源在对应的 ARCH / SOP / SPEC，已发布的变更在 [`CHANGELOG.md`](CHANGELOG.md)。

## 7. 文档规范

新建 / 重命名 / 拆分 / 实质性重写本仓 Markdown 前，先读用户级
`$agent-profile-governance` 的 `references/SPEC-010-document-taxonomy.md`。要点：

- 路径 `docs/<TYPE>-<NNN>-<slug>.md`，新序列按 `010 → 020 → 030` 留空编号。
- 类别职责互斥：**ARCH** 讲结构与理由 · **SOP** 讲操作顺序 · **SPEC** 写精确合同 ·
  **PLAN** 写一次有界改动 · **LOG** 只追加已验证事实。
- 根 singleton 不编号：`README.md` / `AGENTS.md` / `CLAUDE.md` / `CHANGELOG.md`。
- 每篇文档从 byte one 起写 YAML front matter（`doc_type` / `doc_id` / `title` /
  `status` / `purpose` / `owns` / `does_not_own` / `read_when` / `last_reviewed`）。
- ⚠️ `PROPOSAL` / `STRATEGY` / `GUIDE` / `STATUS` 是**禁用前缀**。本仓 `docs/PROPOSAL-91x`、
  `docs/STRATEGY-900` 是历史遗留，按 SPEC 不做批量重命名，但**不得新增**。

## 8. 本仓将设为 public

对外开放前必须完成 [`docs/PLAN-926-public-onboarding.md`](docs/PLAN-926-public-onboarding.md) 的 S1 脱敏闸。
安全基线（2026-08-17 全量审查 150 个 commit）：**零凭据泄漏**——app secret / API key / token / 私钥
从未进过仓库，`.env` 始终在仓库外。待清理的是身份标识符与主机名，不是权限。

---

# Codex 适配层

> 以下仅适用于 Codex runtime。Claude 专属机制见 [`CLAUDE.md`](CLAUDE.md)。
> **本节只翻译执行机制，不改变上面任何共享规则的结果。**

- **CXP**（`profile=cxp`，`CODEX_HOME=$HOME/.codex-personal`）是推荐且机器默认的生产 Codex profile；
  CX 是独立的可选 profile。
- 维护 Codex 兼容性时，**不得修改** `$HOME/.claude-personal`、Claude 的 hooks / settings / skills / 认证。
- Claude 兼容适配器由 `codex-personal/sync_claude_skills.py` 生成在 `$HOME/.agents/skills` 下，
  运行时读取 Claude 的 workflow 源。
- 原生 Codex skill 用 `$name`。桥只在 `name` 恰好匹配一个已安装 skill 时才翻译 legacy 的 `/name`；
  Codex 原生 slash 命令与所有 Claude 命令保持不变。
- **完成信号**：Codex 输出必须走 Codex 的 hook event 记录，**不得假设 Claude 的 JSONL 结构**
  （Claude 侧的完成抽取可以读 Claude JSONL，Codex 侧不行）。
- **路由捕获**：每个 Codex turn 的路由由 `UserPromptSubmit` 捕获，并在异步 drainer 处理之前
  同时钉进 progress 记录与 answer 记录。
- 迁移与排错见 [`docs/SOP-160-codex-personal-migration.md`](docs/SOP-160-codex-personal-migration.md)、
  注册见 [`docs/SOP-121-codex-bot-register.md`](docs/SOP-121-codex-bot-register.md)。
