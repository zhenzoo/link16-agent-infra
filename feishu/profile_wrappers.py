#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single renderer for Link16's Git Bash and PowerShell profile wrappers.

The wrapper never owns a profile list.  Every new shell asks the effective
Link16 registry for registered names through ``agent_profile_cli.py list --names``.
Both ``profile_bootstrap.py`` and the user-level profile governance tool call
this module, so one checker cannot report another checker's desired output as
drift.
"""
from __future__ import annotations

import re
from pathlib import Path


MARKER_BEGIN = "# >>> link16 agent profiles (managed) >>>"
MARKER_END = "# <<< link16 agent profiles (managed) <<<"


def normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def replace_block(current: str, block: str) -> str:
    current = normalized(current) if current else ""
    pattern = re.compile(
        rf"(?ms)^{re.escape(MARKER_BEGIN)}\n.*?^{re.escape(MARKER_END)}\n?"
    )
    replacement = normalized(block)
    if pattern.search(current):
        return normalized(pattern.sub(lambda _match: replacement, current, count=1))
    prefix = current.rstrip()
    return normalized((prefix + "\n\n" if prefix else "") + replacement)


def bash_block() -> str:
    return r'''# >>> link16 agent profiles (managed) >>>
__link16_user_env() {
  local name="$1"
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('$name', 'User')" 2>/dev/null | tr -d '\r\n'
  fi
}

__link16_python() {
  local candidate="" resolved=""
  local appdata="${LOCALAPPDATA:-}"
  if [ -n "$appdata" ] && command -v cygpath >/dev/null 2>&1; then
    appdata="$(cygpath -u "$appdata")"
  fi
  while IFS= read -r candidate; do
    [ -x "$candidate" ] || continue
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1 && resolved="$candidate"
  done < <(printf '%s\n' "$appdata"/Programs/Python/Python*/python.exe | sort -V)
  if [ -n "$resolved" ]; then
    printf '%s\n' "$resolved"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  printf '%s\n' "Python not found" >&2
  return 2
}

__link16_profile_cli() {
  local root="${LINK16_AGENT_INFRA_ROOT:-}"
  local vibe_root="${VIBECODING_ROOT:-}"
  local candidate
  [ -n "$root" ] || root="$(__link16_user_env LINK16_AGENT_INFRA_ROOT)"
  if [ -n "$root" ] && command -v cygpath >/dev/null 2>&1; then
    root="$(cygpath -u "$root")"
  fi
  if [ -n "$root" ] && [ -f "$root/feishu/agent_profile_cli.py" ]; then
    printf '%s\n' "$root/feishu/agent_profile_cli.py"
    return 0
  fi
  [ -n "$vibe_root" ] || vibe_root="$(__link16_user_env VIBECODING_ROOT)"
  if [ -n "$vibe_root" ] && command -v cygpath >/dev/null 2>&1; then
    vibe_root="$(cygpath -u "$vibe_root")"
  fi
  for candidate in \
    "${vibe_root:-}/Post/link16-agent-infra" \
    "${vibe_root:-}/Post/tools/link16-agent-infra"; do
    if [ -f "$candidate/feishu/agent_profile_cli.py" ]; then
      printf '%s\n' "$candidate/feishu/agent_profile_cli.py"
      return 0
    fi
  done
  printf '%s\n' "Link16 agent profile CLI not found" >&2
  return 2
}

__link16_run_profile() {
  local profile="$1" cli py
  shift
  cli="$(__link16_profile_cli)" || return $?
  py="$(__link16_python)" || return $?
  "$py" "$cli" run --profile "$profile" --cwd "$PWD" -- "$@"
}

__link16_cli="$(__link16_profile_cli 2>/dev/null || true)"
__link16_py="$(__link16_python 2>/dev/null || true)"
if [ -n "$__link16_cli" ] && [ -n "$__link16_py" ]; then
  while IFS= read -r __link16_profile; do
    [ -n "$__link16_profile" ] || continue
    unalias "$__link16_profile" 2>/dev/null || true
    eval "$__link16_profile() { __link16_run_profile '$__link16_profile' \"\$@\"; }"
  done < <("$__link16_py" "$__link16_cli" list --names)
fi
unset __link16_cli __link16_profile __link16_py
# <<< link16 agent profiles (managed) <<<
'''


def powershell_block() -> str:
    return r'''# >>> link16 agent profiles (managed) >>>
function Resolve-Link16UserEnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)
    [Environment]::GetEnvironmentVariable($Name, 'User')
}

function Resolve-Link16Python {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') {
        & $command.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { return $command.Source }
    }
    $pythonRoot = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    $candidate = Get-ChildItem -LiteralPath $pythonRoot -Directory -Filter 'Python*' -ErrorAction SilentlyContinue |
        Sort-Object @{ Expression = {
            if ($_.Name -match '^Python(\d)(\d+)$') { [version]("$($Matches[1]).$($Matches[2])") }
            else { [version]'0.0' }
        }; Descending = $true } |
        ForEach-Object { Join-Path $_.FullName 'python.exe' } |
        Where-Object {
            if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) { return $false }
            & $_ -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
            $LASTEXITCODE -eq 0
        } | Select-Object -First 1
    if ($candidate) { return $candidate }
    if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') { return $command.Source }
    throw 'Python not found. Install Python 3.11+ or add python.exe to PATH.'
}

function Resolve-Link16AgentProfileCli {
    $roots = @()
    $infraRoot = $env:LINK16_AGENT_INFRA_ROOT
    if (-not $infraRoot) { $infraRoot = Resolve-Link16UserEnvironmentValue 'LINK16_AGENT_INFRA_ROOT' }
    if ($infraRoot) { $roots += $infraRoot }
    $vibeRoot = $env:VIBECODING_ROOT
    if (-not $vibeRoot) { $vibeRoot = Resolve-Link16UserEnvironmentValue 'VIBECODING_ROOT' }
    if ($vibeRoot) {
        $roots += (Join-Path $vibeRoot 'Post\link16-agent-infra')
        $roots += (Join-Path $vibeRoot 'Post\tools\link16-agent-infra')
    }
    foreach ($root in ($roots | Select-Object -Unique)) {
        $candidate = Join-Path $root 'feishu\agent_profile_cli.py'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Link16 agent profile CLI not found. Set LINK16_AGENT_INFRA_ROOT or VIBECODING_ROOT.'
}

function Invoke-Link16AgentProfile {
    # No named parameters: even -p is a provider flag, not a Profile abbreviation.
    if ($args.Count -eq 0 -or -not $args[0]) { throw 'Link16 profile is required.' }
    $link16Profile = [string]$args[0]
    $providerArgs = @($args | Select-Object -Skip 1)
    $cli = Resolve-Link16AgentProfileCli
    $python = Resolve-Link16Python
    & $python $cli run --profile $link16Profile --cwd (Get-Location).Path -- @providerArgs
}

try {
    $link16ProfileCli = Resolve-Link16AgentProfileCli
    $link16Python = Resolve-Link16Python
    foreach ($profileName in (& $link16Python $link16ProfileCli list --names)) {
        $profileName = "$profileName".Trim()
        if (-not $profileName) { continue }
        $body = [scriptblock]::Create("Invoke-Link16AgentProfile '$profileName' @args")
        Set-Item -Path "Function:global:$profileName" -Value $body
    }
} catch {
    Write-Warning $_.Exception.Message
}
Remove-Variable link16ProfileCli, link16Python, profileName, body -ErrorAction SilentlyContinue
# <<< link16 agent profiles (managed) <<<
'''


def target_plan(home_root: Path) -> list[dict]:
    targets = (
        (home_root / ".bashrc", bash_block()),
        (
            home_root / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1",
            powershell_block(),
        ),
        (
            home_root / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1",
            powershell_block(),
        ),
    )
    rows = []
    for path, block in targets:
        current = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        desired = replace_block(current, block)
        rows.append(
            {
                "path": path,
                "desired": desired,
                "status": "ok"
                if path.is_file() and normalized(current) == desired
                else ("drift" if path.is_file() else "missing"),
            }
        )
    return rows
