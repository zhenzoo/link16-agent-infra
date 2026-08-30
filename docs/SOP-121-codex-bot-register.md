---
doc_type: SOP
doc_id: SOP-121
title: 建一个 Codex 飞书 bot（SOP-120 之上的 Codex 增量）
status: active
purpose: 在通用注册流程之上，列出由 Codex CLI 驱动的 bot 需要额外做的那几处。
owns:
  - Codex 专属的 profile 选择与名册字段
  - app-server typed progress 与 final 的取用
  - cli-legacy hook 回退路径
does_not_own:
  - 通用注册步骤（见 SOP-120）
  - Codex 运行时机制（见 ARCH-110 §2.4.1）
  - Codex Personal 的迁移（见 SOP-160）
read_when:
  - 要新建一个 Codex（而非 Claude）飞书 bot
last_reviewed: 2026-08-26
---
# SOP-121 · 建一个 Feishu Codex bot（SOP-120 之上的 Codex 增量）

> **一句话**：建 Codex bot = 先按 [`SOP-120`](SOP-120-feishu-register.md) 那套建 Feishu bot（OAuth 应用 / 名册 / 权限 / 拉群 / 双机），**再叠下面这几处 Codex 专属增量**。运行时差异全收束在 `feishu/agent_runtime.py`（机制见 [`ARCH-110 §2.4.1`](ARCH-110-feishu-bridge.md)），本 SOP 只做「照做清单」，不重复讲机制。
> **适用**：在任意机器新建一个由 **Codex CLI**（而非 Claude Code）驱动的飞书智能体。
> 缘起：2026-07-21 tb24 上线 GPT 5.6 Codex bot 时，发现建 Codex bot 的流程散在 ARCH-110 §2.4.1（机制）+ SOP-120（OAuth）两处、没有一份照做清单 → 合成本 SOP。

## 前置（Codex 专属 · Link16 单仓基线）

Codex bot 依赖一个隔离的 Codex home。认证、额度、会话和历史按 profile 隔离；同事只 clone Link16 时不依赖任何人的 `.claude-personal`：

1. **建立或选择 local registry 中的隔离 Codex profile**：
   ```powershell
   # 新机若还没有 local registry：先用 profile_bootstrap.py --init-registry 建立
   python feishu/profile_bootstrap.py --apply
   python feishu/agent_profile_cli.py doctor --profile <codex-profile>
   ```
2. **人工登录所选 profile**：重开 Git Bash 后输入用户自己命名的 profile 函数，按 Codex 原生页面登录；不得复制别人的 `auth.json`、sessions 或 history。登录后运行：
   ```powershell
   python feishu/agent_profile_cli.py selftest --profile <codex-profile>
   ```
3. **确认 repo-owned skill 与 hooks**：`profile_bootstrap.py --doctor` 必须确认 `$HOME/.agents/skills/feishu` hash 正确，并确认所选隔离 Codex home 的 `hooks.json` 已无损合并三类 bridge hooks。损坏 JSON 必须先人工修复，安装器不会覆盖。
   其它个人 adapters 另见可选的 [`SOP-160`](SOP-160-codex-personal-migration.md)，不是建 bot 或运行桥的前置条件。

> Codex 的 repo-owned `feishu` skill 按官方用户级位置安装，不复制进每个 `CODEX_HOME`。没有个人 adapters 时，
> 所选 Codex profile 仍可正常运行 Link16 与 provider 原生能力。

## 建 bot（增量步 · 其余照 SOP-120）

1. **注册应用 → 显式选择已通过 doctor 的 Codex profile**：
   ```powershell
   python feishu/register_feishu_app.py --name "<显示名>" --bot <key> --profile <codex-profile> --background
   ```
   OAuth 扫码 / 自动写 `.env` / 自动补 `agent-registry.json` stub 全同 SOP-120；脚本先从 Link16 effective
   local registry 解析所选 profile 为 Codex 并 doctor，注册成功后自动 upsert 本机运行名册。

2. **核对运行时名册**（注册脚本已自动写；身份只允许一个 `profile`）：
   ```jsonc
   {
      "name": "<key>",
      "app_id_env": "FEISHU_BRIDGE_<KEY>_APP_ID",
      "profile": "<codex-profile>",              // ← runtime/home/launcher 全由 registry 派生
      "cwd": "<workspace 绝对路径>",               // ← 该 bot 的工作目录
      "codex_transport": "app-server-canary",     // ← typed-event 干净卡（省略也是它·见下「默认 canary」）
      "delivery_contract": "milestone-v1"         // ← 投递契约
   }
   ```
   > `codex_transport` / `delivery_contract` 是投递协议，不是账号身份；可保留。禁止再加 `agent/account/codex_home`。**别写 `cli-legacy`** —— 那是已弃用的应急回退口。

3. **运行 Link16 doctor/selftest**；registry 中没有目标 profile 时，用
   `profile_bootstrap.py --register-profile <name> --runtime codex --profile-home <home>` 先预览再 `--apply`。

4. **其余全照 [`SOP-120 §4`](SOP-120-feishu-register.md) 清单**：按用途选能力档；普通 Codex agent 使用 `core + group-a2a`，不默认申请 Drive、plain `im:chat` 或听全群。仍需人工拉进共享群、私聊认主、双机同步 `.env`，并核对 `agent-registry.json` 的 `repo`/`machine`。

   注册器会 arm 独立 Monitor。Codex turn 已结束后，OAuth/权限/认主/入群信号由 Link16 经 wmux 注回发起 bot，不能依赖 Claude Code 的后台任务完成通知，也不读取 Codex transcript 猜完成。

5. **重启桥**（只起这一只即可）：
   ```powershell
   python feishu/feishu_bridge.py start --bot <key>
   ```
   `agent_runtime.py` 会用 **app-server worker** 起该 bot（默认路 · 见下节）：
   ```
   CODEX_HOME=... FEISHU_CODEX_EVENT_STREAM=1 python feishu/codex_app_server_worker.py --bot <key> --cwd <cwd> ...
   ```
   最终回复和进度卡都由 typed-event observer 产出：前者取 `agentMessage.phase=final_answer`，后者只保留工具类型 / 次数 / 仓库相对路径（**不带命令原文**）。worker 起时带 `FEISHU_CODEX_EVENT_STREAM=1`，`codex_bridge_stop.py` 与 `codex_bridge_posttool.py` 读到就自动让路 → **不会双投，默认 typed transport 不依赖 hooks 产出 final**。bootstrap 仍安装 hooks，作为 `cli-legacy` 回退与 `UserPromptSubmit` 回址捕获的部署完整性保障。

## 验收

- 群里 `@<新 codex bot>` 一句 → 能回（默认走 app-server typed final；`cli-legacy` 才走 Stop hook）。
- 在该 bot 会话里 `python feishu/whoami.py` → 身份卡显示该 bot（`FEISHU_BRIDGE_SESSION` 钉的身份）。
- 让它 `send_feishu_msg` @ 另一台机的 bot → 能送达（a2a 通）。

## 默认 canary（typed-event）· 老「标准路径」已弃用（主人 2026-07-23 拍板）

**新建的 Codex bot 一律走 typed-event（app-server worker · 契约 `milestone-v1`）**，不再有「先标准路径、富投递等转正」这一说 —— 那条老路（裸 `codex` CLI + `codex_bridge_posttool.py` 的 `_label` 逐条 dump 命令首行）会让主人手机上的进度卡**一条条刷原始命令**（🔧 Get-Content… / 🔧 git status…），已**弃用**。

- **机制上已经兜住**：`agent_runtime.codex_transport()` —— 名册**没写** `codex_transport` = 默认 `app-server-canary`；**只有显式**写 `cli-legacy` / `bare-cli` / `standard` 才回退老路（应急口，正常别用）。⇒ 漏写字段不再会把 bot 掉回刷屏路（tb24 那两只就是这么掉的）。
- **两条路的区别**（同一个 Codex，只是桥怎么起它）：

  | | 默认 typed-event | 已弃用 `cli-legacy` |
  |---|---|---|
  | 桥怎么起 | `codex_app_server_worker.py`（官方 TUI `--remote` + 私有 app-server + typed-event observer） | 裸 `codex` CLI + PostToolUse hook |
  | 进度卡 | 工具**类型 / 次数 / 仓库相对路径**聚合（`读取`、`搜索 rg ×2`…） | **命令原文首行**逐条刷 |
  | 命令 / 参数 / 输出 / reasoning | **不进飞书**（实测 691 条 tool 事件 raw leak = 0） | 命令首行进飞书 |

## 给【已在跑的】Codex bot 切过来（⚠️ 比新建多一步 · tb24 2026-07-23 实测）

**只有存量 bot 需要这节；新建 bot 第一次就带 canary 起，没有这个问题（没有旧会话）。**

1. 名册补 `codex_transport` + `delivery_contract` 两个字段（或确认没写 `cli-legacy`）。
2. 单 bot 重启桥：`python feishu/feishu_bridge.py stop --bot <key>` → `start --bot <key>`。
3. ⚠️ **必须再发一次 `/new`** —— **光加字段 + stop/start 不够**：桥会**复用那只 bot 的旧 bare-codex 会话**（还在刷命令原文），`/new` 才会关掉旧 workspace、按新 `worker_cmd` 全新 spawn，真正切到 worker。
4. ⚠️ **`/new` 这条命令别用 Git-bash 发** —— MSYS 路径转换会把 `/new` 吃成 `C:/Program Files/Git/new`（本机实测；tb24 上是 `D:/Git/...`），命令根本到不了桥。**用 PowerShell 发**；非要用 Git-bash 就前缀 `MSYS_NO_PATHCONV=1`（实测可解）。
5. 验收：让它干一件带工具的活 → 进度卡应显示「读取 / 搜索 / Git ×N + 路径」，**看不到任何命令原文**。

## 关联

- [`SOP-120`](SOP-120-feishu-register.md) — 通用 Feishu bot 注册（OAuth / 名册 / 权限 / 群 / 双机 · 本 SOP 的基座）
- [`SOP-160`](SOP-160-codex-personal-migration.md) — Codex Personal 兼容层 + `~/.codex-personal` bootstrap（前置）
- [`ARCH-110 §2.4.1`](ARCH-110-feishu-bridge.md) — 多 runtime 适配机制（Claude / Codex 差异收束在 `agent_runtime.py`）
- [`PLAN-915`](PLAN-915-codex-feishu-terminal-event-delivery.md) · [`PLAN-916`](PLAN-916-feishu-tool-observability.md) — Codex 终端事件 → 飞书卡片投递契约 + 工具可观测性（**2026-07-23 转正为默认路**）
