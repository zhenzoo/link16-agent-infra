# SOP-121 · 建一个 Feishu Codex bot（SOP-120 之上的 Codex 增量）

> **一句话**：建 Codex bot = 先按 [`SOP-120`](SOP-120-feishu-register.md) 那套建 Feishu bot（OAuth 应用 / 名册 / 权限 / 拉群 / 双机），**再叠下面这几处 Codex 专属增量**。运行时差异全收束在 `feishu/agent_runtime.py`（机制见 [`ARCH-110 §2.4.1`](ARCH-110-feishu-bridge.md)），本 SOP 只做「照做清单」，不重复讲机制。
> **适用**：在任意机器新建一个由 **Codex CLI**（而非 Claude Code）驱动的飞书智能体。
> 缘起：2026-07-21 tb24 上线 GPT 5.6 Codex bot 时，发现建 Codex bot 的流程散在 ARCH-110 §2.4.1（机制）+ SOP-120（OAuth）两处、没有一份照做清单 → 合成本 SOP。

## 前置（Codex 专属 · 每台机一次性 · 非每 bot）

Codex bot 依赖一个隔离的 Codex home + 装好桥 hook；同一台机所有 Codex bot 共用这一套：

1. **隔离 Codex home 就绪**：`~/.codex-personal` 已 bootstrap（`codex login` + `config.toml`）——见 [`SOP-160 §Bootstrap from zero`](SOP-160-codex-personal-migration.md)。
2. **桥 hook 装进 `CODEX_HOME/hooks.json`**（每个 codex-home 装一次）：
   ```powershell
   python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal"          # 默认 dry-run
   python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal" --write   # 写入（合并·保留已有 hook）
   ```
   Codex 从 `CODEX_HOME` 发现 hook（不像 Claude 走 per-process `--settings`）。hook 脚本自身用 `FEISHU_BRIDGE_SESSION` 守门 → 只对**桥 spawn 的** Codex 会话写 outbox；普通 Codex 会话 env 不命中即 no-op，全局安装也安全。

## 建 bot（增量步 · 其余照 SOP-120）

1. **注册应用 → 传 `--runtime codex`**：
   ```powershell
   python feishu/register_feishu_app.py --name "<显示名>" --bot <key> --runtime codex
   ```
   OAuth 扫码 / 自动写 `.env` / 自动补 `agent-registry.json` stub 全同 SOP-120；`--runtime codex` 让 stub 的 `runtime` 字段写成 `codex`（默认 `claude`）。

2. **运行时名册 `bridge-bots.local.json` 加行 → 带 5 个 Codex 字段**（`register` 脚本**不写**这个 · 手动加）：
   ```jsonc
   {
     "name": "<key>",
     "app_id_env": "FEISHU_BRIDGE_<KEY>_APP_ID",
     "agent": "codex",                          // ← 桥用 Codex runtime 起（省略 = 默认 claude）
     "codex_home": "~/.codex-personal",          // ← 指隔离 home（省略 = ~/.codex-personal）
     "cwd": "<workspace 绝对路径>",               // ← 该 bot 的工作目录
     "codex_transport": "app-server-canary",     // ← typed-event 干净卡（省略也是它·见下「默认 canary」）
     "delivery_contract": "milestone-v1"         // ← 投递契约
   }
   ```
   > 后两个字段**照写**（桥的 app-server 就绪信号目前仍认字面值，写了多一路 ready 信号；漏写也能跑，只是少一路）。**别写 `cli-legacy`** —— 那是已弃用的应急回退口。

3. **其余全照 [`SOP-120 §4`](SOP-120-feishu-register.md) 清单**：开 `drive:drive` + `im:chat`（★群 a2a 必开）权限（勾选 → 创版本 → 发布）、拉进共享群、互换 open_id、双机各配 `.env`、核对 `agent-registry.json` 的 `repo`/`machine`。

4. **重启桥**（只起这一只即可）：
   ```powershell
   python feishu/feishu_bridge.py start --bot <key>
   ```
   `agent_runtime.py` 会用 **app-server worker** 起该 bot（默认路 · 见下节）：
   ```
   CODEX_HOME=... FEISHU_CODEX_EVENT_STREAM=1 python feishu/codex_app_server_worker.py --bot <key> --cwd <cwd> ...
   ```
   最终回复仍靠 Codex 官方 **Stop hook 的 `last_assistant_message`**（`codex_bridge_stop.py`）；进度卡由 typed-event observer 产出（工具类型 / 次数 / 仓库相对路径，**不带命令原文**）。worker 起时带 `FEISHU_CODEX_EVENT_STREAM=1`，`codex_bridge_posttool.py` 读到就自动让路 → **不会双投、也不用卸 hook**。

## 验收

- 群里 `@<新 codex bot>` 一句 → 能回（走 Codex Stop hook）。
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
