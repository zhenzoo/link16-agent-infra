# Codex Personal Guide

This file governs the user's Personal Codex profile. Keep Claude Code's
configuration read-only unless the user explicitly asks to change it.

## Profile Boundary

- This profile lives at `$HOME/.codex-personal` and is launched with
  `CODEX_HOME=$HOME/.codex-personal`.
- Prefer `$HOME/.agents/skills` for shared Codex skills.
- Claude compatibility adapters may read current workflow instructions and
  referenced assets from `$HOME/.claude-personal`, but must not edit that tree.
- Do not change `$HOME/.codex` while working on the Personal profile.

## Working Style

- Use the current Asia/Shanghai time when a task depends on "today" or a
  deadline; do not rely on stale conversation timestamps.
- Report visible, verifiable outcomes instead of line counts.
- Explain technical work in plain language first, then add implementation
  details that help the user verify or maintain it.
- For medium or heavy technical explanations, keep three things together:
  explain the system change in the user's own plain-language concepts, define
  each technical term where it appears, and show a concrete before/after or a
  real runtime branch. Keep simple operational answers simple.
- Preserve unrelated user changes in dirty worktrees.
- Use `apply_patch` for intentional file edits and `rg`/`rg --files` for search.

## Paths And Secrets

- Resolve shared paths from `$env:VIBECODING_ROOT` when set; otherwise use the
  current workspace or the known `D:/410_VibeCoding` root only after checking
  that it exists.
- Load API keys from `$VIBECODING_ROOT/.env` at runtime. Never print secret
  values, copy them into tracked files, or duplicate them in Codex config.
- Use HOME-relative paths for user tools where practical so the profile stays
  portable across machines.

## Skills

- Invoke Codex skills with `$skill-name` when the user names one or the task
  clearly matches its description.
- `claude-compat-*` entries are thin adapters. Read the complete upstream
  `SKILL.md` or command file named by the adapter before acting, and translate
  Claude surfaces to current Codex tools:
  - `AskUserQuestion` -> Codex user-input flow when available.
  - `Read`, `Glob`, `Grep` -> native filesystem tools and `rg`.
  - `Write`, `Edit`, `MultiEdit` -> `apply_patch`.
  - `Bash` or PowerShell -> the current shell tool.
  - `Task`/subagents -> Codex collaboration only when delegation is permitted.
  - `WebSearch`/`WebFetch` -> the search and capture policy below.
- Do not duplicate a workflow when a maintained native Codex skill already
  exists. Prefer native gstack and plugin skills over compatibility adapters.

## Network Search And Web Capture

For network search, source lookup, or full-page web capture, prefer the user's
local AnySearch and Jina tools before native browsing.

### AnySearch

Use AnySearch for broad search, forums, HN/Reddit/GitHub/npm discussions,
vertical search, and fallback full-page extraction.

```powershell
$root = if ($env:VIBECODING_ROOT) { $env:VIBECODING_ROOT } else { 'D:\410_VibeCoding' }
$env:ANYSEARCH_API_KEY=((Get-Content "$root\.env" | Where-Object { $_ -match '^ANYSEARCH_API_KEY=' }) -replace '^ANYSEARCH_API_KEY=','').Trim()
$AS="$HOME\.claude-personal\skills\anysearch\scripts\anysearch_cli.py"
python $AS search "query" -m 10
python $AS extract --url "<URL>"
python $AS batch_search --query "A" --query "B"
python $AS list_domains --domain finance
```

Only fall back to native search/fetch if AnySearch fails or is unsuitable, and
say so briefly.

### Jina

Use Jina for turning one article, documentation page, blog, or SPA into clean
Markdown, and for embedding or reranking.

```powershell
$root = if ($env:VIBECODING_ROOT) { $env:VIBECODING_ROOT } else { 'D:\410_VibeCoding' }
$env:JINA_API_KEY=((Get-Content "$root\.env" | Where-Object { $_ -match '^JINA_API_KEY=' }) -replace '^JINA_API_KEY=','').Trim()
jina read "<URL>"
jina search "query"
jina embed "t1" "t2"
Get-Content docs.txt | jina rerank "query" --top-n 5
```

For a single-page capture, try `jina read <URL>` first; use AnySearch extract
as fallback or when broader context is needed.

## External Services

- GitHub: use `gh` or the connected GitHub app and respect the user's requested
  repository/PR scope.
- Mattermost: use the configured Mattermost MCP or the maintained `mm-send`
  workflow; never send a message without the confirmation required by that
  workflow.
- Feishu: use Link16 infrastructure and its registered bot routing. Do not
  assume Claude JSONL semantics for Codex; use the Codex hook/event records.
- In a Feishu `route=p2a` turn, publish finished documents through the
  maintained Feishu workflow and list each resulting external URL visibly in
  the final reply. Put external delivery URLs on their own lines. Show local
  filesystem paths as plain or inline-code text, never as links that imply a
  phone can open them.

## Shared Repositories

Before commit or push, verify the intended diff, current branch, configured git
identity, and whether the repository requires team notification. Never force
push or rewrite unrelated user work without explicit approval.
