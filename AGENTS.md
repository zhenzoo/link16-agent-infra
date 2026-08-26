---
doc_type: AGENTS
doc_id: AGENTS
title: Link 16 agent 入口（runtime-neutral 共享正文）
status: active
purpose: 让任意 runtime（Claude Code / Codex / 其它）的 agent 在一次阅读内拿到本仓身份、硬边界、验证方式与文档路由。
owns:
  - 仓库身份与职责边界
  - 全 runtime 共享的硬约束（身份、名册、飞书投递、生产纪律）
  - 新机部署的触发、人工停点与完成判据
  - 变更后的验证命令
  - 文档地图与工具路由
does_not_own:
  - 架构理由（见 docs/ARCH-*）
  - 操作步骤（见 docs/SOP-*）
  - Claude 专属执行机制（见 CLAUDE.md）
  - 内容业务规则（属各内容仓）
read_when:
  - 每一个在本仓开工的 agent session
last_reviewed: 2026-08-26
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
- 本仓独立部署的新 profile 先走 `feishu/profile_bootstrap.py`；仓库维护者本人已安装
  `$agent-profile-governance` 时，再用它治理完整用户规则/skills。新 bot / 新仓 worker 的运行时
  映射仍以本仓 `agent-profiles.json` + `agent_profile_cli.py` 为准，禁止复制私人配置正文。

### 4.2 名册
- 名册只持久化 `profile`（或 `defaults.profiles`）。
  legacy 的 `agent` / `account` / `claude_config_dir` / `codex_home` 是**只读迁移输入**，新行不得再写。
- 🚨 **本机跑哪些 bot 只认 `bridge-bots.local.json`（gitignored），没有它桥就报错停住**。
  committed 的 `bridge-bots.json` 是**空模板**（`bots: []`），只给人看 schema。
  **永远不要往它加真 bot**：`.env` 跨机同步（每台机都握有全舰队钥匙）＋ 一个飞书应用只允许一条长连接
  ⇒ 模板里一旦有真 bot，任何没配本机名册的机器一起桥就会连上**别人机器的应用**、把对方消息抢走。
  2026-08-17 实证：tuf19 首次起桥连上 committed 里登记的 7 只 tb24 bot，抢了 6.5 小时消息才被发现
  （tb24 那边并没断线，所以更难察觉）。详见 `docs/PLAN-928`。
  新机器：`cp feishu/bridge-bots.local.example.json feishu/bridge-bots.local.json` 再按本机情况填；
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
| 会话卡住/撞额度上限怎么自愈 | [`docs/ARCH-160-agent-watchdog.md`](docs/ARCH-160-agent-watchdog.md) |
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

## 8. Private 协作与隐私边界

本仓保持 **private**。仅向受信同事授予 collaborator 权限；他们可见当前文件和完整 Git 历史。
`feishu/agent-registry.json` 中的 hostname / open_id / chat_id 不是认证凭据，但属于私有运维元数据；
非受信人不应获得仓库访问权。`.env` / auth.json / token / 私钥必须始终在 Git 外，
不同同事之间禁止复制整份 `.env` 或 provider 认证目录。

若将来对外开放，必须先完成 [`docs/PLAN-926-public-onboarding.md`](docs/PLAN-926-public-onboarding.md)
的 registry 迁移、脱敏与历史边界处理；仅删除当前文件不能清除 Git 历史。

## 9. 「开始部署 Link16」是新机入口

用户在 Claude / Codex / QX 等桌面客户端说「开始部署 Link16」时，立即按
[`docs/SOP-100-new-machine-setup.md`](docs/SOP-100-new-machine-setup.md) 执行。这里只定义部署工程师的
决策边界；命令、顺序和排错只在 SOP-100 维护，禁止把第二份安装教程复制进 `AGENTS.md` / `CLAUDE.md`。
默认用户不懂 terminal：

- 安装前一次性展示 7 项清单，默认全选，只问哪些 provider 不要；gstack 默认不安装；
- 检测已有安装，避免重复；Claude Code / Codex 新装走各自官方原生安装器，不默认走 npm；
- agent 执行命令、检测网络/机型/环境并记录结果；
- 用户只负责 GitHub/provider 登录、选择飞书组织、逐条审阅授权链接、发布版本和批准必要审批；
- 沟通「你会看到什么 / 现在要做什么」，不要用 alias、PATH、env 等内部实现教学打断流程。

部署 agent 必须在后台保持需要跨 turn 等待的注册器/Monitor；出现人工步骤时给出唯一可点击链接与
页面动作，并用机械信号继续，不能让短 tool timeout 杀掉授权流程。只有以下终态同时成立，才能说
“部署完成”：本地与 `origin/main` 为 `0 0`、`preflight.py` 的必需项全绿、选中的 profile doctor/
selftest 可启动、Windows Terminal（若安装）与 wmux 的 Git Bash 默认值已复查、本机 local roster 只列
本机 bot、桥状态健康，并完成至少一条真实 owner DM 往返；选择群能力时再加一条目标群 @ 往返。没注册 bot 时应明确说“核心已部署，
飞书接入尚未验收”，不能把脚本安装成功当成整套完成。

---

# Codex 适配层

> 以下仅适用于 Codex runtime。Claude 专属机制见 [`CLAUDE.md`](CLAUDE.md)。
> **本节只翻译执行机制，不改变上面任何共享规则的结果。**

- **CXP**（`profile=cxp`，`CODEX_HOME=$HOME/.codex-personal`）是推荐且机器默认的生产 Codex profile；
  CX 是独立的可选 profile。
- 维护 Codex 兼容性时，**不得修改** `$HOME/.claude-personal`、Claude 的 hooks / settings / skills / 认证。
- Claude 兼容适配器是**个人配置增强项**：仅当用户确有自己的 `.claude-personal` workflow 源时，
  才由 `codex-personal/sync_claude_skills.py` 生成到 `$HOME/.agents/skills`；Link16 核心运行不依赖它。
- 原生 Codex skill 用 `$name`。桥只在 `name` 恰好匹配一个已安装 skill 时才翻译 legacy 的 `/name`；
  Codex 原生 slash 命令与所有 Claude 命令保持不变。
- **完成信号**：Codex 输出必须走 Codex 的 hook event 记录，**不得假设 Claude 的 JSONL 结构**
  （Claude 侧的完成抽取可以读 Claude JSONL，Codex 侧不行）。
- **路由捕获**：每个 Codex turn 的路由由 `UserPromptSubmit` 捕获，并在异步 drainer 处理之前
  同时钉进 progress 记录与 answer 记录。
- 迁移与排错见 [`docs/SOP-160-codex-personal-migration.md`](docs/SOP-160-codex-personal-migration.md)、
  注册见 [`docs/SOP-121-codex-bot-register.md`](docs/SOP-121-codex-bot-register.md)。
