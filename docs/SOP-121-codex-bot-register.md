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

2. **运行时名册 `bridge-bots.local.json` 加行 → 带 3 个 Codex 字段**（`register` 脚本**不写**这个 · 手动加）：
   ```jsonc
   {
     "name": "<key>",
     "app_id_env": "FEISHU_BRIDGE_<KEY>_APP_ID",
     "agent": "codex",                 // ← 桥用 Codex runtime 起（省略 = 默认 claude）
     "codex_home": "~/.codex-personal", // ← 指隔离 home（省略 = ~/.codex-personal）
     "cwd": "<workspace 绝对路径>"       // ← 该 bot 的工作目录
   }
   ```

3. **其余全照 [`SOP-120 §4`](SOP-120-feishu-register.md) 清单**：开 `drive:drive` + `im:chat`（★群 a2a 必开）权限（勾选 → 创版本 → 发布）、拉进共享群、互换 open_id、双机各配 `.env`、核对 `agent-registry.json` 的 `repo`/`machine`。

4. **重启桥**：
   ```powershell
   python feishu/feishu_bridge.py stop ; python feishu/feishu_bridge.py start
   ```
   `agent_runtime.py` 会用下面这条起该 bot（**不要**再叠 `-a never` / `-s danger-full-access`，Codex CLI 拒绝该组合）：
   ```
   codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --no-alt-screen -C "<cwd>"
   ```
   回复靠 Codex 官方 **Stop hook 的 `last_assistant_message`**（`codex_bridge_stop.py`）· PostToolUse 写压缩进度——**不复刻 Claude JSONL parser**（见 ARCH-110 §2.4.1）。

## 验收

- 群里 `@<新 codex bot>` 一句 → 能回（走 Codex Stop hook）。
- 在该 bot 会话里 `python feishu/whoami.py` → 身份卡显示该 bot（`FEISHU_BRIDGE_SESSION` 钉的身份）。
- 让它 `send_feishu_msg` @ 另一台机的 bot → 能送达（a2a 通）。

## 不阻塞项（别把建 bot 卡在这上面）

- **PLAN-915 app-server「富卡片投递」canary = 增强 · 非必需**：它把 Codex 终端事件（commentary / 工具活动摘要 / 最终答案）分卡投递，目前只对 `tb25-link16-codex` 开了**单 bot canary**、全舰队 rollout 仍等真实飞书验收。**基础可用的 Codex bot 只需上面标准 hook 路径**（`codex_bridge_stop.py` / `codex_bridge_posttool.py`）就够——先按本 SOP 建通，富投递等 canary 转正再统一开。

## 关联

- [`SOP-120`](SOP-120-feishu-register.md) — 通用 Feishu bot 注册（OAuth / 名册 / 权限 / 群 / 双机 · 本 SOP 的基座）
- [`SOP-160`](SOP-160-codex-personal-migration.md) — Codex Personal 兼容层 + `~/.codex-personal` bootstrap（前置）
- [`ARCH-110 §2.4.1`](ARCH-110-feishu-bridge.md) — 多 runtime 适配机制（Claude / Codex 差异收束在 `agent_runtime.py`）
- [`PLAN-915`](PLAN-915-codex-feishu-terminal-event-delivery.md) — Codex 终端事件 → 飞书卡片富投递契约（canary · 非建 bot 前置）
