#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 新机的 7 项依赖计划与 wmux 收尾配置。

默认只预览，不安装。桌面 agent 先把清单一次性展示给用户；用户只需说
哪些 provider 不要，再用 ``--apply --yes`` 执行。核心五项不能跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import preflight
import network_route


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    required: bool
    official_source: str
    probe_url: str
    install_command: tuple[str, ...]


WINGET_FLAGS = ("-e", "--accept-source-agreements", "--accept-package-agreements")
COMPONENTS = (
    Component("git", "Git + Git Bash", True, "Git for Windows / WinGet",
              "https://github.com/git-for-windows/git/releases/latest",
              ("winget", "install", "--id", "Git.Git", *WINGET_FLAGS)),
    Component("gh", "GitHub CLI", True, "GitHub / WinGet",
              "https://github.com/cli/cli/releases/latest",
              ("winget", "install", "--id", "GitHub.cli", *WINGET_FLAGS)),
    Component("python", "Python 3.12+", True, "Python Software Foundation / WinGet",
              "https://www.python.org/downloads/windows/",
              ("winget", "install", "--id", "Python.Python.3.13", *WINGET_FLAGS)),
    Component("node", "Node.js LTS", True, "OpenJS / WinGet",
              "https://nodejs.org/en/download",
              ("winget", "install", "--id", "OpenJS.NodeJS.LTS", *WINGET_FLAGS)),
    Component("wmux", "wmux", True, "wmux official / WinGet",
              "https://github.com/openwong2kim/wmux/releases/latest",
              ("winget", "install", "--id", "openwong2kim.wmux", *WINGET_FLAGS)),
    Component("claude", "Claude Code", False, "Anthropic native installer",
              "https://claude.ai/install.ps1",
              ("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               "irm https://claude.ai/install.ps1 | iex")),
    Component("codex", "Codex CLI", False, "OpenAI Windows standalone installer",
              "https://chatgpt.com/codex/install.ps1",
              ("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               "irm https://chatgpt.com/codex/install.ps1 | iex")),
)
COMPONENT_BY_KEY = {row.key: row for row in COMPONENTS}
OPTIONAL_KEYS = {row.key for row in COMPONENTS if not row.required}


def _first_file(paths):
    for path in paths:
        try:
            if path and Path(path).is_file():
                return Path(path)
        except OSError:
            continue
    return None


def _winget_package(pattern):
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    try:
        return _first_file(sorted(base.glob(pattern), reverse=True))
    except OSError:
        return None


def wmux_executable():
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        root = Path(local) / "wmux"
        candidates.append(root / "wmux.exe")  # Squirrel stable shim; upgrades do not break the shortcut.
        try:
            candidates.extend(sorted(root.glob("app-*/wmux.exe"), reverse=True))
        except OSError:
            pass
    candidates.append(preflight._fresh_which("wmux"))
    return _first_file(candidates)


def detect_component(key):
    """返回已安装入口；None 表示当前和持久化 PATH 都找不到。"""
    if key == "git":
        git = preflight._fresh_which("git")
        return Path(git) if git and preflight._git_bash_path() else None
    if key == "gh":
        return preflight._gh_path()
    if key == "python":
        return Path(sys.executable) if sys.version_info >= (3, 12) else None
    if key == "node":
        found = preflight._fresh_which("node")
        return Path(found) if found else _winget_package("OpenJS.NodeJS.LTS_*/node-*/node.exe")
    if key == "wmux":
        return wmux_executable()
    if key == "claude":
        found = preflight._fresh_which("claude")
        return Path(found) if found else _winget_package("Anthropic.ClaudeCode_*/claude.exe")
    if key == "codex":
        found = preflight._fresh_which("codex")
        if found:
            return Path(found)
        local = os.environ.get("LOCALAPPDATA")
        return _first_file([Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"] if local else [])
    raise KeyError(key)


def parse_skips(values):
    skipped = set()
    for value in values or ():
        skipped.update(part.strip().lower() for part in value.split(",") if part.strip())
    unknown = skipped - set(COMPONENT_BY_KEY)
    if unknown:
        raise ValueError("未知组件：" + ", ".join(sorted(unknown)))
    required = skipped - OPTIONAL_KEYS
    if required:
        raise ValueError("Link16 核心依赖不能跳过：" + ", ".join(sorted(required)))
    return skipped


def installation_plan(skipped=()):
    rows = []
    for component in COMPONENTS:
        path = detect_component(component.key)
        status = "skipped" if component.key in skipped else ("installed" if path else "missing")
        row = asdict(component)
        row.update(status=status, detected_path=str(path) if path else "",
                   install_command=list(component.install_command))
        rows.append(row)
    return rows


def _wmux_running():
    if os.name != "nt":
        return False
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "if (Get-Process wmux -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    return done.returncode == 0


def _atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def configure_wmux_git_bash(*, apply=False, appdata=None, running=None):
    bash = preflight._git_bash_path()
    if not bash:
        return {"task": "wmux-default-shell", "status": "blocked", "detail": "Git Bash 未安装"}
    root = Path(appdata or os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    session = root / "wmux" / "session.json"
    data = {}
    if session.is_file():
        try:
            data = json.loads(session.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {"task": "wmux-default-shell", "status": "blocked",
                    "detail": f"无法安全解析 {session}"}
    current = str(data.get("defaultShell") or "").strip().strip('"')
    if current.lower() == str(bash).lower():
        return {"task": "wmux-default-shell", "status": "ok", "detail": str(bash)}
    is_running = _wmux_running() if running is None else running
    if is_running:
        return {"task": "wmux-default-shell", "status": "needs-gui",
                "detail": "wmux 正在运行；请在 Settings → Default Shell 选择 Git Bash，避免退出时覆盖文件"}
    if apply:
        data["defaultShell"] = str(bash)
        _atomic_json(session, data)
    return {"task": "wmux-default-shell", "status": "applied" if apply else "missing",
            "detail": f"{session} → {bash}"}


def _shortcut_target(shortcut: Path):
    if not shortcut.is_file() or os.name != "nt":
        return ""
    env = os.environ.copy()
    env["LINK16_SHORTCUT"] = str(shortcut)
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "$w=New-Object -ComObject WScript.Shell; $w.CreateShortcut($env:LINK16_SHORTCUT).TargetPath"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def ensure_wmux_desktop_shortcut(*, apply=False, desktop=None, executable=None):
    exe = Path(executable) if executable else wmux_executable()
    if not exe or not exe.is_file():
        return {"task": "wmux-desktop-shortcut", "status": "blocked", "detail": "wmux.exe 未安装"}
    folder = Path(desktop) if desktop else Path.home() / "Desktop"
    shortcut = folder / "wmux.lnk"
    target = _shortcut_target(shortcut)
    if target and Path(target).resolve() == exe.resolve():
        return {"task": "wmux-desktop-shortcut", "status": "ok", "detail": str(shortcut)}
    if not apply:
        return {"task": "wmux-desktop-shortcut", "status": "drift" if shortcut.exists() else "missing",
                "detail": f"{shortcut} → {exe}"}
    folder.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(LINK16_SHORTCUT=str(shortcut), LINK16_WMUX_EXE=str(exe))
    icon = exe.parent / "app.ico"
    env["LINK16_WMUX_ICON"] = str(icon if icon.is_file() else exe)
    script = (
        "$w=New-Object -ComObject WScript.Shell; "
        "$s=$w.CreateShortcut($env:LINK16_SHORTCUT); "
        "$s.TargetPath=$env:LINK16_WMUX_EXE; $s.WorkingDirectory=$HOME; "
        "$s.IconLocation=$env:LINK16_WMUX_ICON; $s.Description='Open wmux'; $s.Save()"
    )
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script], env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )
    status = "applied" if done.returncode == 0 and shortcut.is_file() else "blocked"
    detail = str(shortcut) if status == "applied" else (done.stderr.strip() or "快捷方式创建失败")
    return {"task": "wmux-desktop-shortcut", "status": status, "detail": detail}


def apply_missing(rows):
    results = []
    for row in rows:
        if row["status"] != "missing":
            continue
        command = row["install_command"]
        proxy = network_route.proxy_url()
        configured = (os.environ.get("LINK16_ROUTE_DEFAULT") or "").strip().lower()
        prefer = configured if configured in {"direct", "proxy"} else ("proxy" if proxy else "direct")
        decision = network_route.detect(row["probe_url"], prefer, proxy)
        if not decision.usable:
            results.append({"key": row["key"], "returncode": 2, "route": decision.selected,
                            "reason": decision.reason})
            break
        env = network_route.child_environment(decision.selected, proxy)
        done = subprocess.run(command, env=env, check=False)
        results.append({"key": row["key"], "returncode": done.returncode,
                        "route": decision.selected, "reason": decision.reason})
        if done.returncode != 0:
            break
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Link16 Windows 7 项依赖安装计划（默认只预览）")
    parser.add_argument("--skip", action="append", default=[],
                        help="只允许跳过 provider：claude、codex；可逗号分隔")
    parser.add_argument("--apply", action="store_true", help="安装缺失项并执行 wmux 收尾")
    parser.add_argument("--yes", action="store_true", help="确认已把完整清单展示给用户")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        skipped = parse_skips(args.skip)
    except ValueError as exc:
        parser.error(str(exc))
    rows = installation_plan(skipped)
    if args.apply and not args.yes:
        parser.error("--apply 需要 --yes；agent 必须先把 7 项清单一次性展示给用户")
    installs = apply_missing(rows) if args.apply else []
    post = []
    if "wmux" not in skipped:
        post = [
            ensure_wmux_desktop_shortcut(apply=args.apply),
            configure_wmux_git_bash(apply=args.apply),
        ]
    payload = {"applied": args.apply, "components": rows, "installs": installs,
               "post_install": post, "gstack_default": False}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("Link16 Windows 依赖（默认全选；核心 5 项 + provider 2 项）：")
        for idx, row in enumerate(rows, 1):
            flag = "必需" if row["required"] else "默认选中/可跳过"
            print(f"  {idx}. [{row['status']:^9}] {row['label']} · {flag} · {row['official_source']}")
        print("  gstack：默认不安装（不在 7 项清单里）")
        for row in post:
            print(f"  [{row['status']:^9}] {row['task']} · {row['detail']}")
        if not args.apply:
            print("下一步：把清单展示给用户；确认后运行 --apply --yes，可用 --skip claude/codex。")
    return 1 if any(row.get("returncode") for row in installs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
