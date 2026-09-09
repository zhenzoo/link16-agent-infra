#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在新 Windows 用户下建立 ccp / ccp2 / cxp 账号目录与 Shell 函数。

只建目录、ccp2 launcher 和带 marker 的启动函数；不复制 auth.json、
token、session 或历史。三个名字是动态函数，不是 shell alias。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import agent_runtime
import install_codex_bridge_hooks

BASH_BEGIN = "# >>> link16 agent profiles (managed) >>>"
BASH_END = "# <<< link16 agent profiles (managed) <<<"
PS_BEGIN = BASH_BEGIN
PS_END = BASH_END
DEFAULT_PROFILES = ("ccp", "ccp2", "cxp")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEISHU_SKILL_SOURCE = PROJECT_ROOT / ".agents" / "skills" / "feishu"
SKILL_MANIFEST = ".link16-skill-install.json"
SKILL_MANAGED_BY = "link16-agent-infra"
LEGACY_CODEX_ADAPTER = "claude-compat-feishu"
LEGACY_ADAPTER_MARKER = "<!-- link16-codex-compat-adapter -->"


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


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
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
  # 2026-08-30 主人拍板放宽：上面仍【优先】挑 LOCALAPPDATA 里的 3.12+；这里的 PATH 兜底
  # 不再硬卡版本 —— tb24 这台老机只有 Python 3.10.10，硬卡会让 ccp/cxp 等启动器全起不来。
  # 3.12+ 仍是建议档（见 preflight.py 的 WARN），但不该成为老机不能开工的硬闸。
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
    # 放宽（2026-08-30）：没有 3.12+ 就用 PATH 上的 python，别让老机（如 tb24 的 3.10.10）直接 throw。
    if ($command) {{ return $command.Source }}
    throw 'No usable python found. Install Python 3.12+ and reopen the terminal.'
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
    # No named parameters: even -p is a provider flag, not a Profile abbreviation.
    if ($args.Count -eq 0 -or -not $args[0]) {{ throw 'Link16 profile is required.' }}
    $link16Profile = [string]$args[0]
    $providerArgs = @($args | Select-Object -Skip 1)
    $root = Resolve-Link16Root; $python = Resolve-Link16Python
    & $python (Join-Path $root 'feishu\agent_profile_cli.py') run --profile $link16Profile --cwd (Get-Location).Path -- @providerArgs
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


def _skill_name(path: Path) -> str | None:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        return None
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---", head, re.S)
    if not match:
        return path.parent.name
    field = re.search(r"(?m)^name:\s*['\"]?([^'\"\r\n]+)['\"]?\s*$", match.group(1))
    return field.group(1).strip() if field else path.parent.name


def _tree_files(root: Path):
    if not root.is_dir():
        raise ValueError(f"Link16 feishu skill 真源不存在：{root}")
    rows = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file() and path.name != SKILL_MANIFEST:
            rows.append((path.relative_to(root).as_posix(), path))
    skill = root / "SKILL.md"
    if not skill.is_file() or _skill_name(skill) != "feishu":
        raise ValueError(f"Link16 feishu skill 真源无效：{skill}")
    return rows


def _tree_hash(root: Path) -> tuple[str, dict[str, str]]:
    digest = hashlib.sha256()
    files = {}
    for relative, path in _tree_files(root):
        payload = path.read_bytes()
        file_hash = hashlib.sha256(payload).hexdigest()
        files[relative] = file_hash
        digest.update(relative.encode("utf-8") + b"\0" + payload + b"\0")
    return digest.hexdigest(), files


def _manifest(path: Path) -> dict | None:
    try:
        raw = json.loads((path / SKILL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _named_skill_conflicts(target: Path) -> list[str]:
    parent = target.parent
    if not parent.is_dir():
        return []
    target_key = target.resolve().as_posix().casefold()
    conflicts = []
    for skill_file in parent.glob("*/SKILL.md"):
        if skill_file.parent.resolve().as_posix().casefold() == target_key:
            continue
        if (_skill_name(skill_file) or "").casefold() == "feishu":
            conflicts.append(str(skill_file.parent))
    return sorted(set(conflicts), key=str.casefold)


def _skill_status(source: Path, target: Path) -> dict:
    source_hash, source_files = _tree_hash(source)
    conflicts = _named_skill_conflicts(target)
    base = {
        "kind": "skill", "name": "feishu", "path": str(target),
        "source": str(source), "source_sha256": source_hash,
    }
    if conflicts:
        return {**base, "status": "name-conflict", "conflicts": conflicts}
    if not target.exists():
        return {**base, "status": "missing"}
    if target.is_dir() and not (target / "SKILL.md").is_file() and not any(target.iterdir()):
        return {**base, "status": "missing"}
    if not target.is_dir() or not (target / "SKILL.md").is_file():
        return {**base, "status": "conflict"}
    actual_hash, _actual_files = _tree_hash(target)
    manifest = _manifest(target)
    if manifest is None:
        return {**base, "status": "adoptable" if actual_hash == source_hash else "conflict",
                "actual_sha256": actual_hash}
    if manifest.get("managed_by") != SKILL_MANAGED_BY:
        return {**base, "status": "conflict", "actual_sha256": actual_hash}
    installed_hash = str(manifest.get("installed_sha256") or "")
    if actual_hash != installed_hash:
        return {**base, "status": "drift", "actual_sha256": actual_hash,
                "installed_sha256": installed_hash}
    if source_hash != str(manifest.get("source_sha256") or ""):
        return {**base, "status": "outdated", "actual_sha256": actual_hash}
    if manifest.get("files") != source_files:
        return {**base, "status": "manifest-drift", "actual_sha256": actual_hash}
    return {**base, "status": "ok", "actual_sha256": actual_hash}


def _skill_targets(home: Path, profiles, *, registry_path=None):
    targets, seen = [], set()
    for name in profiles:
        spec = agent_runtime.profile_spec(name, registry_path)
        if spec.runtime == "claude":
            target = _home_relative_target(home, spec.home) / "skills" / "feishu"
        else:
            target = home / ".agents" / "skills" / "feishu"
        key = (target.parent.resolve() / target.name).as_posix().casefold()
        if key in seen:
            continue
        seen.add(key)
        targets.append(target)
    return targets


def _apply_skill(source: Path, target: Path) -> None:
    row = _skill_status(source, target)
    if row["status"] not in {"missing", "adoptable", "outdated"}:
        raise ValueError(f"feishu skill 不能安全安装：{target}：{row['status']}")
    if row["status"] != "adoptable":
        target.mkdir(parents=True, exist_ok=True)
        for relative, path in _tree_files(source):
            _atomic_write_bytes(target / Path(relative), path.read_bytes())
    source_hash, files = _tree_hash(source)
    actual_hash, _ = _tree_hash(target)
    if actual_hash != source_hash:
        raise ValueError(f"feishu skill 安装后 hash 不一致：{target}")
    _atomic_write(target / SKILL_MANIFEST, json.dumps({
        "version": 1,
        "managed_by": SKILL_MANAGED_BY,
        "source": ".agents/skills/feishu",
        "source_sha256": source_hash,
        "installed_sha256": actual_hash,
        "files": files,
    }, ensure_ascii=False, indent=2))


def legacy_adapter_plan(home: Path) -> dict:
    source = home / ".agents" / "skills" / LEGACY_CODEX_ADAPTER
    backup = home / ".agents" / "link16-disabled-skills" / LEGACY_CODEX_ADAPTER
    if not source.exists():
        return {"kind": "legacy-skill-adapter", "path": str(source),
                "backup": str(backup), "status": "migrated" if backup.exists() else "absent"}
    skill = source / "SKILL.md"
    try:
        text = skill.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    marker_owned = LEGACY_ADAPTER_MARKER in text and (_skill_name(skill) or "").casefold() == "feishu"
    return {"kind": "legacy-skill-adapter", "path": str(source), "backup": str(backup),
            "status": "ready-to-migrate" if marker_owned and not backup.exists() else "conflict"}


def migrate_legacy_adapter(home: Path) -> dict:
    row = legacy_adapter_plan(home)
    if row["status"] in {"absent", "migrated"}:
        return row
    if row["status"] != "ready-to-migrate":
        raise ValueError(f"旧 feishu adapter 不是可安全迁移的 Link16 生成物：{row['path']}")
    source, backup = Path(row["path"]), Path(row["backup"])
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, backup)
    return legacy_adapter_plan(home)


def _normalized_profile_home(value: str) -> str:
    home = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not home.startswith("~/") or home == "~":
        raise ValueError("profile home 必须是 HOME-relative（例如 ~/.claude-work）")
    if home.casefold() in {"~/.claude", "~/.codex", "~/.kimi-code"}:
        raise ValueError("新 profile 禁止使用 ~/.claude、~/.codex 或 ~/.kimi-code；请选择隔离名称")
    return home


def _new_profile(name, runtime, home, launcher="direct", label="") -> dict:
    name = str(name or "").strip().lower()
    runtime = str(runtime or "").strip().lower()
    launcher = str(launcher or "").strip().lower()
    if not agent_runtime._PROFILE_NAME_RE.fullmatch(name):
        raise ValueError(f"非法 profile 名：{name!r}")
    if runtime not in agent_runtime._PROFILE_RUNTIMES:
        raise ValueError(f"非法 runtime：{runtime!r}")
    if launcher not in agent_runtime._PROFILE_LAUNCHERS:
        raise ValueError(f"非法 launcher：{launcher!r}")
    return {"name": name, "runtime": runtime, "home": _normalized_profile_home(home),
            "launcher": launcher, "label": str(label or name)}


def initialize_registry(target: Path, specs, *, apply=False) -> dict:
    rows = [_new_profile(**spec) for spec in specs]
    if not rows:
        raise ValueError("新 registry 首次建立必须至少提供一个 Claude、Codex 或 Kimi profile")
    names = [row["name"] for row in rows]
    homes = [row["home"].casefold() for row in rows]
    if len(names) != len(set(names)) or len(homes) != len(set(homes)):
        raise ValueError("profile 名和 home 都必须唯一")
    document = {
        "version": 1,
        "default_profiles": {
            runtime: next(row["name"] for row in rows if row["runtime"] == runtime)
            for runtime in sorted({row["runtime"] for row in rows})
        },
        "entry_documents": {"managed_profiles": names},
        "profiles": {
            row["name"]: {key: row[key] for key in ("runtime", "home", "launcher", "label")}
            for row in rows
        },
    }
    status = "missing" if not target.exists() else "ok" if json.loads(target.read_text(encoding="utf-8")) == document else "conflict"
    if apply and status == "missing":
        _atomic_write(target, json.dumps(document, ensure_ascii=False, indent=2))
        agent_runtime._profile_document(target)
        status = "ok"
    return {"kind": "profile-registry", "path": str(target), "status": status,
            "profiles": names, "defaults": document["default_profiles"]}


def _validate_roster_profiles(registry: Path, roster: Path | None = None) -> None:
    roster = roster or (agent_runtime.ROSTER_LOCAL_PATH if agent_runtime.ROSTER_LOCAL_PATH.is_file()
                        else agent_runtime.ROSTER_COMMITTED_PATH)
    try:
        data = json.loads(roster.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    refs = []
    for runtime, name in ((data.get("defaults") or {}).get("profiles") or {}).items():
        refs.append((str(name), str(runtime)))
    for bot in data.get("bots") or []:
        if isinstance(bot, dict) and bot.get("profile"):
            refs.append((str(bot["profile"]), None))
    for name, runtime in refs:
        spec = agent_runtime.profile_spec(name, registry)
        if runtime and spec.runtime != runtime:
            raise ValueError(f"roster default {runtime} 指向错误 runtime profile：{name}")


def migrate_registry(source: Path, target: Path, *, roster=None, apply=False) -> dict:
    source = Path(source)
    target = Path(target)
    document = agent_runtime._profile_document(source)
    _validate_roster_profiles(source, Path(roster) if roster else None)
    if target.exists():
        current = agent_runtime._profile_document(target)
        status = "ok" if current == document else "conflict"
    else:
        status = "missing"
    if apply and status == "missing":
        _atomic_write(target, json.dumps(document, ensure_ascii=False, indent=2))
        if agent_runtime._profile_document(target) != document:
            raise ValueError("profile registry 写后回读不一致")
        _validate_roster_profiles(target, Path(roster) if roster else None)
        status = "ok"
    return {"kind": "profile-registry-migration", "source": str(source),
            "path": str(target), "status": status,
            "profiles": list(document["profiles"]), "defaults": document["default_profiles"]}


def register_profile(target: Path, *, name, runtime, profile_home, launcher="direct",
                     label="", set_default=False, apply=False) -> dict:
    target = Path(target)
    if not target.is_file():
        raise ValueError("本机 profile registry 尚未建立；先 init 或 migrate")
    document = agent_runtime._profile_document(target)
    row = _new_profile(name=name, runtime=runtime, home=profile_home,
                       launcher=launcher, label=label)
    if row["name"] in document["profiles"]:
        raise ValueError(f"profile 已存在：{row['name']}")
    used_homes = {str(raw.get("home") or "").replace("\\", "/").rstrip("/").casefold()
                  for raw in document["profiles"].values() if isinstance(raw, dict)}
    if row["home"].casefold() in used_homes:
        raise ValueError(f"profile home 已被使用：{row['home']}")
    document["profiles"][row["name"]] = {
        key: row[key] for key in ("runtime", "home", "launcher", "label")
    }
    managed = document.setdefault("entry_documents", {}).setdefault("managed_profiles", [])
    if row["name"] not in managed:
        managed.append(row["name"])
    if set_default:
        document["default_profiles"][row["runtime"]] = row["name"]
    status = "planned"
    if apply:
        _atomic_write(target, json.dumps(document, ensure_ascii=False, indent=2))
        agent_runtime._profile_document(target)
        status = "ok"
    return {"kind": "profile-register", "path": str(target), "status": status, **row,
            "set_default": bool(set_default)}


def _selected_specs(profiles, registry_path=None):
    if profiles is not None:
        return [agent_runtime.profile_spec(name, registry_path) for name in profiles]
    available = agent_runtime.profile_specs(registry_path)
    by_name = {spec.name for spec in available}
    names = DEFAULT_PROFILES if all(name in by_name for name in DEFAULT_PROFILES) else tuple(spec.name for spec in available)
    return [agent_runtime.profile_spec(name, registry_path) for name in names]


def _home_relative_target(home: Path, profile_home: str) -> Path:
    value = str(profile_home or "").replace("\\", "/").rstrip("/")
    if value == "~":
        return Path(home)
    if not value.startswith("~/"):
        raise ValueError(f"profile home 必须是 HOME-relative：{profile_home!r}")
    return Path(home) / Path(value[2:])


def _profile_target(home: Path, spec) -> Path:
    """Resolve a registry home under the user selected by ``--home``.

    Using the caller's home keeps blank-user previews and tests away from the
    operator's live profiles while preserving the registry basename contract.
    """
    return _home_relative_target(home, spec.home)


def bootstrap(home: Path, *, apply=False, profiles=None, registry_path=None,
              migrate_legacy_feishu_adapter=False):
    specs = _selected_specs(profiles, registry_path)
    profile_names = tuple(spec.name for spec in specs)
    wrapper_names = tuple(spec.name for spec in agent_runtime.profile_specs(registry_path))
    rows = []
    profile_targets = {}
    if migrate_legacy_feishu_adapter:
        rows.append(migrate_legacy_adapter(home) if apply else legacy_adapter_plan(home))
    for spec in specs:
        name = spec.name
        target = _profile_target(home, spec)
        profile_targets[name] = target
        existed = target.is_dir()
        if apply:
            target.mkdir(parents=True, exist_ok=True)
        rows.append({"kind": "profile-home", "name": name, "path": str(target),
                     "status": "ok" if existed else "missing"})
        if spec.launcher == "launch-sh":
            launch = target / "launch.sh"
            launch_existed = launch.is_file()
            if apply and not launch_existed:
                config_relative = str(spec.home)[2:].replace("\\", "/")
                _atomic_write(launch, f"""#!/usr/bin/env bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_CHILD_SESSION
export CLAUDE_CONFIG_DIR="${{CLAUDE_CONFIG_DIR:-$HOME/{config_relative}}}"
""")
            rows.append({"kind": "launcher", "name": name, "path": str(launch),
                         "status": "ok" if launch_existed else "missing"})
    for row in target_plan(home, wrapper_names):
        before = row["status"]
        if apply and before != "ok":
            _atomic_write(row["path"], row["desired"])
        rows.append({"kind": "shell-function", "path": str(row["path"]), "status": before})
    for target in _skill_targets(home, profile_names, registry_path=registry_path):
        before = _skill_status(FEISHU_SKILL_SOURCE, target)
        if apply and before["status"] in {"missing", "adoptable", "outdated"}:
            _apply_skill(FEISHU_SKILL_SOURCE, target)
        after = _skill_status(FEISHU_SKILL_SOURCE, target)
        rows.append({**after, "before": before["status"]})
    for spec in specs:
        if spec.runtime != "codex":
            continue
        codex_home = profile_targets[spec.name]
        before, _desired = install_codex_bridge_hooks.hooks_plan(codex_home, PROJECT_ROOT)
        if apply and before["status"] in {"missing", "outdated"}:
            install_codex_bridge_hooks.apply_hooks(codex_home, PROJECT_ROOT)
        after, _desired = install_codex_bridge_hooks.hooks_plan(codex_home, PROJECT_ROOT)
        rows.append({**after, "name": spec.name, "before": before["status"]})
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="建立隔离 agent profiles、Shell 入口与 Link16 feishu skill")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只预览")
    parser.add_argument("--doctor", action="store_true", help="只检查；任何 missing/drift/conflict 返回非 0")
    parser.add_argument("--home", help="测试/特殊用户目录；默认当前用户 home")
    parser.add_argument("--json", action="store_true")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--migrate-registry", action="store_true",
                           help="把 legacy committed registry 无损迁入本机 local registry")
    operation.add_argument("--init-registry", action="store_true",
                           help="为新用户用所选 Claude/Codex/Kimi 名称与 home 建 local registry")
    operation.add_argument("--register-profile", metavar="NAME",
                           help="向已存在的 local registry 新增一个隔离 profile")
    parser.add_argument("--profile", action="append", default=[],
                        help="install/doctor 只处理指定 profile（可重复）")
    parser.add_argument("--runtime", choices=sorted(agent_runtime._PROFILE_RUNTIMES))
    parser.add_argument("--profile-home", help="register-profile 的 HOME-relative 目录")
    parser.add_argument("--launcher", choices=sorted(agent_runtime._PROFILE_LAUNCHERS), default="direct")
    parser.add_argument("--label", default="")
    parser.add_argument("--set-default", action="store_true")
    parser.add_argument("--claude-profile")
    parser.add_argument("--claude-home")
    parser.add_argument("--codex-profile")
    parser.add_argument("--codex-home")
    parser.add_argument("--kimi-profile")
    parser.add_argument("--kimi-home")
    parser.add_argument("--migrate-legacy-feishu-adapter", action="store_true",
                        help="把 marker-owned 旧 Codex feishu adapter 移到可恢复备份目录")
    args = parser.parse_args(argv)
    if args.apply and args.doctor:
        parser.error("--apply 与 --doctor 不能同时使用")
    home = Path(args.home).resolve() if args.home else Path.home()
    local_registry = agent_runtime.PROFILE_REGISTRY_LOCAL_PATH
    if args.migrate_registry:
        rows = [migrate_registry(agent_runtime.PROFILE_REGISTRY_PATH, local_registry,
                                 apply=args.apply)]
    elif args.init_registry:
        pairs = {
            "claude": (args.claude_profile, args.claude_home),
            "codex": (args.codex_profile, args.codex_home),
            "kimi": (args.kimi_profile, args.kimi_home),
        }
        incomplete = [runtime for runtime, pair in pairs.items() if bool(pair[0]) != bool(pair[1])]
        if incomplete:
            parser.error("每个选择的 runtime 必须同时提供 profile 名与 home：" + ", ".join(incomplete))
        specs = [
            {"name": name, "runtime": runtime, "home": profile_home,
             "launcher": "direct", "label": name}
            for runtime, (name, profile_home) in pairs.items() if name
        ]
        if not specs:
            parser.error("--init-registry 至少需要一组 --claude-profile/--claude-home、--codex-profile/--codex-home 或 --kimi-profile/--kimi-home")
        rows = [initialize_registry(local_registry, specs, apply=args.apply)]
    elif args.register_profile:
        if not args.runtime or not args.profile_home:
            parser.error("--register-profile 需要 --runtime 和 --profile-home")
        rows = [register_profile(
            local_registry, name=args.register_profile, runtime=args.runtime,
            profile_home=args.profile_home, launcher=args.launcher, label=args.label,
            set_default=args.set_default, apply=args.apply,
        )]
    else:
        selected = tuple(args.profile) if args.profile else None
        rows = bootstrap(
            home, apply=args.apply, profiles=selected,
            migrate_legacy_feishu_adapter=args.migrate_legacy_feishu_adapter,
        )
    if args.json:
        print(json.dumps({"applied": args.apply, "rows": rows}, ensure_ascii=False, indent=2))
    else:
        action = "已应用" if args.apply else ("体检" if args.doctor else "只预览")
        print(f"{action}：Link16 profiles / Shell 函数 / feishu skill")
        for row in rows:
            print(f"  {row['status']:<16} {row['kind']:<24} {row['path']}")
        if not (args.migrate_registry or args.init_registry or args.register_profile):
            print("下一步：重开 Git Bash，用所选 profile 函数启动并在各自浏览器页面登录。")
    bad = {"missing", "outdated", "conflict", "drift", "manifest-drift", "name-conflict", "planned", "ready-to-migrate"}
    return 2 if args.doctor and any(row.get("status") in bad for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
