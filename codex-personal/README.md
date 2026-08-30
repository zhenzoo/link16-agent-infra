# Codex Personal compatibility layer（maintainer opt-in）

This directory configures Codex Personal without modifying Claude Code's
configuration. `~/.claude-personal` remains read-only and continues to be the
upstream source for the user's custom workflows.

This is a maintainer-only optional layer, not the blank-machine Link16 path.
Repo-owned `feishu` is deliberately excluded from adapter generation and is
installed by `feishu/profile_bootstrap.py` from `.agents/skills/feishu`.

## Refresh gstack（可选，默认不安装）

gstack 不属于 Link16 核心依赖，也不属于默认的新机 7 项清单。只有用户明确选择后才执行本节；
不要因为安装 Codex、wmux 或同步个人 Claude skills 而自动安装/刷新 gstack。

The gstack source checkout lives outside Codex skill discovery at
`~/.gstack/repos/gstack`. Its official setup generates Codex-format skills.
Publish those generated skills into `~/.agents/skills` with:

```powershell
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1
powershell -ExecutionPolicy Bypass -File codex-personal/refresh_gstack_codex.ps1 -Apply
```

The apply mode archives the previous gstack entries under
`~/.codex-migration-backups` before publishing replacements.

## Publish Claude Personal workflows

The public control entry is Claude Personal's `govctl`; it refreshes Claude
work environments and then calls this publisher once:

```powershell
powershell -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync
powershell -NoProfile -File "$HOME\.claude-personal\scripts\govctl.ps1" sync -Apply
```

The commands below are maintainer-level probes for the internal publisher,
not a second user-facing sync path.

Dry-run all top-level skills and legacy commands as an authoritative mirror:

```powershell
python codex-personal/sync_claude_skills.py --include-commands --prune --summary
```

Apply:

```powershell
python codex-personal/sync_claude_skills.py --include-commands --prune --summary --apply
```

The generated adapters live under `~/.agents/skills/claude-compat-*`. Each
adapter loads the current source workflow from `~/.claude-personal` at runtime
and translates Claude-specific tool names to Codex surfaces. Scripts and
references are not copied, so there is no second source of truth. The publisher
never generates `claude-compat-feishu`; Link16 owns that skill directly.

Prune is accepted only for a full `--include-commands` scan and is invoked by
`govctl sync -Prune`. It removes only a
stale `claude-compat-*` directory whose sole file carries the Link16 marker;
linked directories/files, unmanaged skills, and directories with extra files
are preserved.

Use `--name envsync --name transcribe` to publish or verify a subset.

## Configure the Personal profile（maintainer-only）

Preview and apply the isolated Personal profile configuration:

```powershell
python codex-personal/configure_personal.py
python codex-personal/configure_personal.py --apply
```

This aligns the Mattermost runtime loader, wmux MCP table, and Link16 hooks
without touching `AGENTS.md`, auth, sessions, history, models, or project trust.
User-level entry governance is optional personal configuration. It is not
required to create a Link16 profile, register a bot, or run the bridge.

## Updates and authentication

Codex CLI has a built-in updater:

```powershell
codex update
codex --version
$env:CODEX_HOME="$HOME\.codex-personal"
codex doctor --summary --no-color
```

OAuth MCP servers are authenticated per `CODEX_HOME`:

```powershell
$env:CODEX_HOME="$HOME\.codex-personal"
codex mcp login mobbin
codex mcp login slack
codex mcp login vercel
codex mcp list
```

If the user explicitly opted in, update gstack separately, regenerate its Codex overlays with the official
setup, then publish them with `refresh_gstack_codex.ps1`. Do not install the
gstack source checkout directly inside `~/.agents/skills`, because recursive
discovery sees its internal source skills as duplicates.
