---
doc_type: SOP
doc_id: SOP-160
title: Claude Personal → Codex Personal 兼容层
status: active
purpose: 让 Codex Personal 复用主人维护的 Claude workflow，同时保持 Claude Code 完全可用且单一真源。
owns:
  - 兼容适配器的生成与放置
  - Codex 侧复用 Claude workflow 的读取方式
  - Codex 官方 Windows standalone 的安装/升级与旧 npm 安装兼容
does_not_own:
  - Claude 侧的任何配置（不得修改）
  - Codex bot 的注册（见 SOP-121）
  - profile 注册（见用户级 $agent-profile-governance）
read_when:
  - 要让 Codex 复用 Claude 的 skill/workflow
  - 安装或升级 Codex CLI
last_reviewed: 2026-08-26
---
# SOP-160 · Claude Personal → Codex Personal compatibility

> Goal: make Codex Personal reuse the user's maintained Claude workflows while
> keeping Claude Code fully operational and single-sourced. This is a
> compatibility layer, not a destructive migration.

## 安装 / 升级 Codex（官方 standalone 优先）

Windows 新装统一走 OpenAI 官方 standalone installer，不再默认依赖 Node/npm：

```powershell
python feishu/network_route.py run --url https://chatgpt.com/codex/install.ps1 --prefer proxy -- powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://chatgpt.com/codex/install.ps1 | iex"
codex --version
```

已有安装先看 `Get-Command codex | Select-Object -ExpandProperty Source`：

- 命中 `...\Programs\OpenAI\Codex\...`：继续用上面的官方 standalone installer 更新。
- 命中 npm 全局目录：可以先沿用 `codex update` / npm 更新，不在装机时偷偷迁移；计划迁移时再明确卸载旧入口，避免 PATH 双份。

若 `PROXY_URL` 未设或端口不确定，先运行 `python feishu/network_route.py proxy-doctor`。
每次都对真实官方 URL 选路，不写死某个网络或端口。

**升级时机注意**：安装器会替换磁盘二进制，但**已经跑着的 Codex 进程仍用旧版**（进程持旧码）。
飞书桥上的 Codex bot 要等各自会话重建才吃到新版；不必为升级专门重启整个桥。

## Boundary

| Surface | Claude Personal source | Codex Personal destination | Strategy |
|---|---|---|---|
| User rules | 经同一语义治理的 Claude runtime source + Codex runtime source | `~/.codex{,-personal}/AGENTS.md` | `$agent-profile-governance` 先分类公共规则/runtime 适配，再由 renderer 生成带 source hash 的实体入口 |
| Skills | `~/.claude-personal/skills/*` | `~/.agents/skills/claude-compat-*` | Thin adapter; upstream read at runtime |
| Legacy commands | `~/.claude-personal/commands/*.md` | Same adapter namespace | `/name` becomes `$name` |
| MCP | Claude settings | `~/.codex-personal/config.toml` | Re-register/authenticate per profile |
| Hooks | Claude hook schema | `~/.codex-personal/hooks.json` | Native Codex events, separate scripts |
| Transcripts | Claude project JSONL | Codex sessions/state | Never parse as Claude JSONL |
| Authentication | Claude account homes | `~/.codex-personal/auth.json` | Completely separate |
| gstack（可选） | Official source checkout | Generated Codex skill overlays | 默认不安装；仅用户明确 opt-in 后 official setup + controlled publish |

The adapter publisher does not edit Claude sources. The explicit
`$agent-profile-governance` workflow is the one authorized writer for generated
user entry documents and shell wrapper marker blocks; it never touches auth,
sessions, history, model selection, or secrets.

The adapter body is live, but Codex discovery metadata is generated. Changes
to workflow bodies are visible immediately; adding, renaming, deleting a
workflow, or changing its frontmatter name/description requires republishing
the adapters.

## Bootstrap from zero (fresh machine, no `~/.codex-personal` yet)

`configure_personal.py` can seed minimal MCP/hook overlay files, but it does not
create authentication. On a machine that has never run the profile, initialize
the home and run `codex login` under that `CODEX_HOME`; afterward use Link16
profile wrappers for daily sessions.

```powershell
# 0a. 先按本文「升级 Codex」自动选路更新，再核对版本。

# 0b. Point every step at the isolated home for the whole session.
$env:CODEX_HOME = "$HOME\.codex-personal"

# 0c. OAuth login (ChatGPT plan, NOT an API key). Creates ~/.codex-personal +
#     auth.json + skeleton dirs. Login does NOT guarantee a config.toml, and the
#     script cannot log in for you, so this step is always required.
codex login

# 0d. Seed a minimal config.toml. OPTIONAL since configure_personal.py now
#     self-seeds this exact content when config.toml is missing (see below), but
#     run it explicitly on older script versions. Do NOT copy ~/.codex/config.toml
#     (drags in per-machine project-trust paths, stale notify, old MCP tables).
#     The wmux/mattermost MCP tables are appended by configure_personal.py.
@'
cli_auth_credentials_store = "file"
model = "gpt-5.6-sol"
sandbox_mode = "danger-full-access"
approval_policy = "on-request"
model_reasoning_effort = "xhigh"
'@ | Set-Content -Encoding utf8 "$HOME\.codex-personal\config.toml"
```

Notes:
- **Model / posture are a deliberate choice, not a blind copy.** `gpt-5.6-sol`
  needs Codex >= 0.144.x. `sandbox_mode = "danger-full-access"` bypasses the
  sandbox (matches the reference machine's posture) — confirm this is intended
  for the target machine before adopting it.
- **wmux bundle must be installed** before step 1 below: `configure_personal.py`
  aborts with `No installed wmux MCP bundle found` if
  `%LOCALAPPDATA%\wmux\app-*\resources\mcp-bundle\index.js` is absent.
- Reference: the mature machine's `~/.codex-personal` was originally created by
  `codex login` under `CODEX_HOME`, not by this toolkit — hence this section
  closes the previously-undocumented from-zero step.

## Install or refresh

```powershell
# 1. MCP wrappers/hooks overlay (AGENTS.md is intentionally not touched here)
python codex-personal/configure_personal.py
python codex-personal/configure_personal.py --apply

# 2. Claude environments + Codex adapters + generated entry docs/wrappers
pwsh -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync
pwsh -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync -Apply

# 3. Link16 Codex hooks
python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal"
python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal" --write
```

默认安装到此结束。gstack 不属于 Link16 新机依赖；只有用户明确要求时，才单独运行：

```powershell
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1 -Apply
```

Configuration and optional gstack apply paths archive their previous state. The adapter
publisher uses atomic writes and may remove only stale marker-owned leaf
adapters under the prune rules below. Re-running every command is idempotent.

## Automatic refresh after Claude config updates

The Claude config repository owns the versioned update trigger:

```powershell
powershell -NoProfile -File scripts/govctl.ps1 sync
powershell -NoProfile -File scripts/govctl.ps1 sync -Apply
```

Its `post-commit` and `post-merge` hooks run apply mode only when
`skills/`, `commands/`, or `governance/` changed; `post-rewrite` covers the
less common rebase path. `govctl sync` first refreshes Claude environments and
then invokes Link16's Codex adapter publisher, Codex overlay configurator, and
agent-profile entry/wrapper sync. Hook failures are warn-first and never block
a commit, pull, or rebase.

Ordinary sync creates and updates only. Deletion is explicit and shared across
both runtimes: after reviewing dry-run candidates, use `sync -Apply -Prune`.
The publisher may prune only adapters containing the Link16 managed marker,
with no links or extra user files. Native Codex skills, plugin skills,
collisions, and unmanaged directories are never removed. Repository paths
resolve from `VIBECODING_ROOT` or `LINK16_AGENT_INFRA_ROOT`.

## Invocation semantics

In a normal Codex session, invoke an imported workflow as `$envsync`,
`$transcribe`, etc. Natural-language matching also works from the skill
description.

For Codex sessions reached through Feishu, Link16 accepts Claude muscle-memory
syntax such as `/envsync dry-run`. It translates to `$envsync dry-run` only if
an installed skill with that exact frontmatter name exists. Native commands
such as `/model`, `/resume`, and `/compact` remain slash commands. Claude bots
are never translated.

Claude-specific tool names inside an upstream workflow map as follows:

| Claude surface | Codex behavior |
|---|---|
| `AskUserQuestion` | Codex user-input flow when available |
| `Read` / `Glob` / `Grep` | Native file reads and `rg` |
| `Write` / `Edit` / `MultiEdit` | `apply_patch` |
| Bash / PowerShell | Current shell runner |
| `Task` subagents | Codex collaboration only when delegation is permitted |
| `WebSearch` / `WebFetch` | AnySearch/Jina policy, then native fallback |

An adapter is not a claim of perfect binary compatibility: workflows that
depend on Claude-only UI, undocumented hooks, or a Claude plugin-specific tool
need a native Codex branch. The adapter makes those differences explicit at
runtime instead of silently pretending the APIs are identical.

## Link16 event path

```text
Feishu message
  -> feishu_bridge.py
  -> profile=cxp -> registry derives CODEX_HOME=~/.codex-personal
  -> UserPromptSubmit pins this turn's reply route
  -> PostToolUse writes progress records
  -> Stop writes the answer record
  -> outbox drainer sends the pinned record to Feishu
```

Codex hooks use Codex event payloads. Claude JSONL extraction remains only for
Claude runtimes. This prevents the two transcript formats from becoming a
shared, fragile parser contract.

PLAN-915 canary uses an explicit machine-local bot flag:

```json
{
  "name": "tb25-link16-codex",
  "profile": "cxp",
  "codex_transport": "app-server-canary",
  "delivery_contract": "milestone-v1"
}
```

In that mode the official TUI connects to a private app-server with `--remote`.
A second typed-event connection sends commentary, plan state, grouped tool
counts, and collab status to the outbox. Raw reasoning and command/output text
are excluded. PostToolUse is disabled for this mode; Stop remains the one final
answer producer. Activate it by respawning only that bot's worker, then refresh
only that bridge process:

```powershell
python feishu/feishu_bridge.py start --bot tb25-link16-codex
```

**Graduated 2026-07-23 (owner decision).** Typed-event delivery is now the
default for every Codex bot: `agent_runtime.codex_transport()` returns
`app-server-canary` unless the roster explicitly says `cli-legacy`, so a missing
field can no longer drop a bot back to the deprecated raw-command path. The old
"do not apply fleet-wide until accepted" gate is retired — the real Feishu
before/after was accepted (v0.7.0, real canary on `tb25-link16-codex` and
`tb25-cartoonMV-codex`). Still switch **one bot at a time**, and see
[`SOP-121`](SOP-121-codex-bot-register.md) for the extra `/new` step an
already-running bot needs.

## Verification

```powershell
python feishu/agent_profile_cli.py doctor --profile cxp
python feishu/agent_profile_cli.py command --profile cxp --cwd .
python "$HOME\.claude-personal\skills\agent-profile-governance\scripts\profile_governance.py" doctor
python -m unittest discover -s tests -v
python feishu/feishu_bridge.py status --bot tb25-link16-codex
python feishu/bridge_scope_audit.py --bot tb25-link16-codex
```

For a real skill smoke, start an ephemeral read-only Personal session and ask
it to load one adapter without running the workflow. For a Feishu bot, first
private-message it once so Link16 can bind the owner/DM target, then invoke a
non-destructive skill dry-run.

## Manual gates

- OAuth MCP consent pages require the user to choose and approve an account.
- A new Feishu bot must be manually added to the shared group; Feishu does not
  allow another app to add it through the API.
- The first direct message establishes the per-app user `open_id` and DM route.
- Rotate a bearer token before moving it out of a legacy inline header; do not
  silently invalidate a working MCP server during migration.
