#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在新 Windows 用户下建立 ccp / ccp2 / cxp 账号目录与 Shell 函数。

只建目录、ccp2 launcher 和带 marker 的启动函数；不复制 auth.json、
token、session 或历史。三个名字是动态函数，不是 shell alias。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import agent_runtime

BASH_BEGIN = "# >>> link16 agent profiles (managed) >>>"
BASH_END = "# <<< link16 agent profiles (managed) <<<"
PS_BEGIN = BASH_BEGIN
PS_END = BASH_END
DEFAULT_PROFILES = ("ccp", "ccp2", "cxp")


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"


def _replace_block(current: str, block: str) -> str:
    current = _normalized(current) if current else ""
    pattern = re.compile(rf"(?ms)^{re.escape(BASH_BEGIN)}\n.*?^{re.escape(BASH_END)}\n?")
    block = _normalized(block)
    if pattern.search(current):
        return _normalized(pattern.sub(lambda _match: block, current, count=1))
    return _normalized((current.rstrip() + "\n\n" if current.strip() else "") + block)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_normalized(text))
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _bash_block(profiles=DEFAULT_PROFILES) -> str:
    names = " ".join(profiles)
    return f'''{BASH_BEGIN}
__link16_user_env() {{
  powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('$1', 'User')" 2>/dev/null | tr -d '\\r\\n'
}}
__link16_root() {{
  local root="${{LINK16_AGENT_INFRA_ROOT:-}}"
  [ -n "$root" ] || root="$(__link16_user_env LINK16_AGENT_INFRA_ROOT)"
  [ -n "$root" ] || {{ local vibe="${{VIBECODING_ROOT:-$(__link16_user_env VIBECODING_ROOT)}}"; root="$vibe/Post/link16-agent-infra"; }}
  command -v cygpath >/dev/null 2>&1 && root="$(cygpath -u "$root")"
  [ -f "$root/feishu/agent_profile_cli.py" ] || {{ printf '%s\n' 'Link16 profile CLI not found; set LINK16_AGENT_INFRA_ROOT.' >&2; return 2; }}
  printf '%s\n' "$root"
}}
__link16_python() {{
  local base="${{LOCALAPPDATA:-}}" found="" candidate
  command -v cygpath >/dev/null 2>&1 && base="$(cygpath -u "$base")"
  while IFS= read -r candidate; do
    [ -x "$candidate" ] || continue
    "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1 && found="$candidate"
  done < <(printf '%s\n' "$base"/Programs/Python/Python*/python.exe | sort -V)
  [ -n "$found" ] && {{ printf '%s\n' "$found"; return; }}
  candidate="$(command -v python 2>/dev/null)" || return 1
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1 || return 1
  printf '%s\n' "$candidate"
}}
__link16_run_profile() {{
  local profile="$1" root py; shift
  root="$(__link16_root)" || return $?; py="$(__link16_python)" || return $?
  "$py" "$root/feishu/agent_profile_cli.py" run --profile "$profile" --cwd "$PWD" -- "$@"
}}
for __link16_name in {names}; do
  unalias "$__link16_name" 2>/dev/null || true
  eval "$__link16_name() {{ __link16_run_profile '$__link16_name' \"\\$@\"; }}"
done
unset __link16_name
{BASH_END}
'''


def _powershell_block(profiles=DEFAULT_PROFILES) -> str:
    quoted = ", ".join(f"'{name}'" for name in profiles)
    return rf'''{PS_BEGIN}
function Resolve-Link16Python {{
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') {{
        & $command.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) {{ return $command.Source }}
    }}
    $root = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    $candidate = Get-ChildItem -LiteralPath $root -Directory -Filter 'Python*' -ErrorAction SilentlyContinue |
        Sort-Object @{{ Expression = {{
            if ($_.Name -match '^Python(\d)(\d+)$') {{ [version]("$($Matches[1]).$($Matches[2])") }}
            else {{ [version]'0.0' }}
        }}; Descending = $true }} |
        ForEach-Object {{ Join-Path $_.FullName 'python.exe' }} |
        Where-Object {{
            if (-not (Test-Path -LiteralPath $_ -PathType Leaf)) {{ return $false }}
            & $_ -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
            $LASTEXITCODE -eq 0
        }} | Select-Object -First 1
    if ($candidate) {{ return $candidate }}
    throw 'Python 3.12+ not found. Install it and reopen the terminal.'
}}
function Resolve-Link16Root {{
    $root = $env:LINK16_AGENT_INFRA_ROOT
    if (-not $root) {{ $root = [Environment]::GetEnvironmentVariable('LINK16_AGENT_INFRA_ROOT', 'User') }}
    if (-not $root) {{
        $vibe = $env:VIBECODING_ROOT
        if (-not $vibe) {{ $vibe = [Environment]::GetEnvironmentVariable('VIBECODING_ROOT', 'User') }}
        if ($vibe) {{ $root = Join-Path $vibe 'Post\link16-agent-infra' }}
    }}
    if (-not $root -or -not (Test-Path -LiteralPath (Join-Path $root 'feishu\agent_profile_cli.py'))) {{
        throw 'Link16 profile CLI not found. Set LINK16_AGENT_INFRA_ROOT.'
    }}
    return $root
}}
function Invoke-Link16Profile {{
    param([Parameter(Mandatory=$true)][string]$Profile)
    $root = Resolve-Link16Root; $python = Resolve-Link16Python
    & $python (Join-Path $root 'feishu\agent_profile_cli.py') run --profile $Profile --cwd (Get-Location).Path -- @args
}}
foreach ($profileName in @({quoted})) {{
    $body = [scriptblock]::Create("Invoke-Link16Profile '$profileName' @args")
    Set-Item -Path "Function:global:$profileName" -Value $body
}}
Remove-Variable profileName, body -ErrorAction SilentlyContinue
{PS_END}
'''


def target_plan(home: Path, profiles=DEFAULT_PROFILES):
    targets = (
        (home / ".bashrc", _bash_block(profiles)),
        (home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1", _powershell_block(profiles)),
        (home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1", _powershell_block(profiles)),
    )
    rows = []
    for path, block in targets:
        current = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        desired = _replace_block(current, block)
        rows.append({"path": path, "desired": desired,
                     "status": "ok" if current and _normalized(current) == desired else ("drift" if current else "missing")})
    return rows


def bootstrap(home: Path, *, apply=False, profiles=DEFAULT_PROFILES):
    rows = []
    for name in profiles:
        spec = agent_runtime.profile_spec(name)
        target = home / Path(spec.home).name
        existed = target.is_dir()
        if apply:
            target.mkdir(parents=True, exist_ok=True)
        rows.append({"kind": "profile-home", "name": name, "path": str(target),
                     "status": "ok" if existed else "missing"})
        if spec.launcher == "launch-sh":
            launch = target / "launch.sh"
            launch_existed = launch.is_file()
            if apply and not launch_existed:
                _atomic_write(launch, """#!/usr/bin/env bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_CHILD_SESSION
export CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude-personal2}"
""")
            rows.append({"kind": "launcher", "name": name, "path": str(launch),
                         "status": "ok" if launch_existed else "missing"})
    for row in target_plan(home, profiles):
        before = row["status"]
        if apply and before != "ok":
            _atomic_write(row["path"], row["desired"])
        rows.append({"kind": "shell-function", "path": str(row["path"]), "status": before})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="建立 ccp / ccp2 / cxp 本机账号入口")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    parser.add_argument("--home", help="测试/特殊用户目录；默认当前用户 home")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = bootstrap(Path(args.home).resolve() if args.home else Path.home(), apply=args.apply)
    if args.json:
        print(json.dumps({"applied": args.apply, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        action = "已应用" if args.apply else "只预览"
        print(f"{action}：ccp / ccp2 / cxp（Shell 函数，不是 alias）")
        for row in rows:
            print(f"  {row['status']:<7} {row['kind']:<14} {row['path']}")
        print("下一步：重开 Git Bash，分别输入 ccp、ccp2、cxp，在各自浏览器页面登录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
