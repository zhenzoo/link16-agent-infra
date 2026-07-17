# SOP-160 · Claude Personal → Codex Personal compatibility

> Goal: make Codex Personal reuse the user's maintained Claude workflows while
> keeping Claude Code fully operational and single-sourced. This is a
> compatibility layer, not a destructive migration.

## Boundary

| Surface | Claude Personal source | Codex Personal destination | Strategy |
|---|---|---|---|
| User rules | `~/.claude-personal/CLAUDE.md` | `~/.codex-personal/AGENTS.md` | Codex-specific maintained template |
| Skills | `~/.claude-personal/skills/*` | `~/.agents/skills/claude-compat-*` | Thin adapter; upstream read at runtime |
| Legacy commands | `~/.claude-personal/commands/*.md` | Same adapter namespace | `/name` becomes `$name` |
| MCP | Claude settings | `~/.codex-personal/config.toml` | Re-register/authenticate per profile |
| Hooks | Claude hook schema | `~/.codex-personal/hooks.json` | Native Codex events, separate scripts |
| Transcripts | Claude project JSONL | Codex sessions/state | Never parse as Claude JSONL |
| Authentication | Claude account homes | `~/.codex-personal/auth.json` | Completely separate |
| gstack | Official source checkout | Generated Codex skill overlays | Official setup + controlled publish |

Nothing under `~/.claude-personal` is written by this workflow. Adapters load
the current upstream `SKILL.md`, scripts, references, and assets at invocation
time, so Claude remains the source of truth and edits are immediately visible
to Codex.

The adapter body is live, but Codex discovery metadata is generated. Changes
to workflow bodies are visible immediately; adding, renaming, deleting a
workflow, or changing its frontmatter name/description requires republishing
the adapters.

## Install or refresh

```powershell
# 1. Personal rules and MCP wrappers
python codex-personal/configure_personal.py
python codex-personal/configure_personal.py --apply

# 2. Claude work environments + Codex adapters (one public entry)
powershell -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync
powershell -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync -Apply

# 3. gstack generated Codex skills
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1 -Apply

# 4. Link16 Codex hooks
python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal"
python feishu/install_codex_bridge_hooks.py --codex-home "$HOME\.codex-personal" --write
```

Configuration and gstack apply paths archive their previous state. The adapter
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
then invokes Link16's Codex adapter publisher exactly once. Hook failures are
warn-first and never block a commit, pull, or rebase.

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
  -> wmux Codex session (CODEX_HOME=~/.codex-personal)
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
  "agent": "codex",
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

Do not apply the flag fleet-wide until the real Feishu before/after is accepted.

## Verification

```powershell
$env:CODEX_HOME="$HOME\.codex-personal"
codex --version
codex doctor --summary --no-color
codex mcp list
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
