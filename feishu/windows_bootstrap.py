#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 新机的 8 项依赖计划与 wmux 收尾配置。

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
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import preflight
import network_route
import service_doctor


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
    Component("kimi", "Kimi Code CLI", False, "Moonshot native installer",
              "https://code.kimi.com/kimi-code/install.ps1",
              ("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
               "irm https://code.kimi.com/kimi-code/install.ps1 | iex")),
)
COMPONENT_BY_KEY = {row.key: row for row in COMPONENTS}
OPTIONAL_KEYS = {row.key for row in COMPONENTS if not row.required}
USER_STATUSES = {
    "将安装", "已存在跳过", "已验证", "需要登录", "需要人工确认", "本次不安装",
}

LINK16_ITEMS = (
    ("profiles", "隔离的 Claude/Codex/Kimi profiles"),
    ("feishu_skill", "Link16 飞书 skill"),
    ("hooks_transport", "消息 hooks / typed transport"),
    ("local_roster", "本机 bot 名册"),
    ("credentials", "飞书应用凭据"),
    ("bridge", "飞书桥"),
    ("cron", "定时任务调度器"),
    ("watchdog", "会话看门狗"),
    ("registration_monitor", "注册回调监督器"),
    ("history_ledger", "消息历史账本"),
)


def _first_file(paths):
    for path in paths:
        try:
            if path and Path(path).is_file():
                return Path(path)
        except OSError:
            continue
    return None


def _real_local_appdata():
    """真实的 %LOCALAPPDATA%，绕开 MSIX 容器重定向。

    2026-09-08 机器 3050：在 Claude 桌面版（MSIX 封装）里跑本脚本时，LOCALAPPDATA 被重定向成
    ...\\Packages\\Claude_xxx\\LocalCache\\Local，wmux/Python/WinGet 的目录全在这个假根下找不到，
    Run 键因此算出带版本号的错误路径。判据：LOCALAPPDATA 路径里含 \\Packages\\ 就改用 USERPROFILE 推。
    """
    local = os.environ.get("LOCALAPPDATA") or ""
    if "\\packages\\" in local.replace("/", "\\").casefold():
        profile = os.environ.get("USERPROFILE") or str(Path.home())
        return str(Path(profile) / "AppData" / "Local")
    return local or None


def inside_desktop_client():
    """是否在 Claude 桌面版之类的 MSIX 客户端里跑（LOCALAPPDATA 被重定向或 cwd 是 cowork 工作区）。"""
    local = (os.environ.get("LOCALAPPDATA") or "").replace("/", "\\").casefold()
    cwd = str(Path.cwd()).replace("/", "\\").casefold()
    return "\\packages\\claude_" in local or "\\claude\\scratch-workspaces\\" in cwd


DESKTOP_CLIENT_NOTES = (
    "LOCALAPPDATA 被 MSIX 容器重定向：本脚本已自动改用 %USERPROFILE%\\AppData\\Local 找 wmux/Python；"
    "service_installer plan 前请核对 after 路径不含 Packages\\Claude_。",
    "cowork 工作区路径很长，git clone 可能报 `$GIT_DIR too big`：先 `$env:GIT_DIR=$null` 再 clone。",
    "装完软件后【已打开的终端】PATH 和 PowerShell profile 是旧的：gh / claude-work 报找不到命令 → 新开一个窗口。",
)


def _winget_package(pattern):
    local = _real_local_appdata()
    if not local:
        return None
    base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    try:
        return _first_file(sorted(base.glob(pattern), reverse=True))
    except OSError:
        return None


def _python_version(path):
    try:
        done = subprocess.run(
            [str(path), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10, check=False,
        )
        version = tuple(int(part) for part in done.stdout.strip().split("."))
        return version if done.returncode == 0 and len(version) == 3 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _python312_executable():
    """Re-probe installed interpreters; the bootstrap process may still be the old Python."""
    candidates = [Path(sys.executable)]
    found = preflight._fresh_which("python")
    if found:
        candidates.append(Path(found))
    local = _real_local_appdata()
    if local:
        try:
            candidates.extend((Path(local) / "Programs" / "Python").glob("Python*/python.exe"))
        except OSError:
            pass
    valid = []
    seen = set()
    for path in candidates:
        key = str(path).casefold()
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        version = _python_version(path)
        if version and version >= (3, 12, 0):
            valid.append((version, path))
    return max(valid, default=(None, None))[1]


def wmux_executable():
    local = _real_local_appdata()
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
        return _python312_executable()
    if key == "node":
        found = preflight._fresh_which("node")
        return Path(found) if found else _winget_package("OpenJS.NodeJS.LTS_*/node-*/node.exe")
    if key == "wmux":
        return wmux_executable()
    if key == "claude":
        found = preflight._fresh_which("claude")
        if found:
            return Path(found)
        return (_first_file([Path.home() / ".local" / "bin" / "claude.exe"])
                or _winget_package("Anthropic.ClaudeCode_*/claude.exe"))
    if key == "codex":
        found = preflight._fresh_which("codex")
        if found:
            return Path(found)
        local = os.environ.get("LOCALAPPDATA")
        return _first_file([Path(local) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"] if local else [])
    if key == "kimi":
        found = preflight._fresh_which("kimi")
        return Path(found) if found else _first_file([Path.home() / ".kimi-code" / "bin" / "kimi.exe"])
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


def software_user_plan(rows, *, installed_this_run=(), runtime_health=None):
    """Project technical detection into the small vocabulary shown to people."""
    installed_this_run = set(installed_this_run)
    runtimes = set(((runtime_health or {}).get("profiles") or {}).get("runtimes", {}).values())
    result = []
    for row in rows:
        status = row["status"]
        if status == "skipped":
            user_status = "本次不安装"
        elif status == "missing":
            user_status = "将安装"
        elif row["key"] in installed_this_run:
            user_status = "已验证"
        elif row["key"] in OPTIONAL_KEYS and row["key"] not in runtimes:
            user_status = "需要登录"
        elif row["key"] in OPTIONAL_KEYS:
            user_status = "已验证"
        else:
            user_status = "已存在跳过"
        result.append({**row, "user_status": user_status})
    return result


def _link_row(key, label, user_status, detail, technical):
    if user_status not in USER_STATUSES:
        raise ValueError(f"未知用户状态：{user_status}")
    return {
        "key": key, "label": label, "user_status": user_status,
        "detail": detail, "technical": technical,
    }


def link16_user_plan(raw=None, health=None):
    """Return the fixed Link16 deployment checklist without hiding raw evidence."""
    raw = service_doctor.collect_raw() if raw is None else raw
    health = service_doctor.evaluate(raw) if health is None else health
    components = health.get("components") or {}
    profiles = raw.get("profiles") or {}
    roster = raw.get("roster") or {}

    profile_component = components.get("profiles_skill") or {"layers": {}}
    profile_ok = profile_component["layers"].get("configured") == service_doctor.PASS
    profile_status = "已验证" if profile_ok else "需要人工确认"
    profile_detail = (
        f"已选：{', '.join(profiles.get('selected') or [])}"
        if profiles.get("selected") else "请给账号命名，并选择需要的 Claude、Codex、Kimi"
    )

    skill_rows = profiles.get("skills") or []
    skill_ok = bool(skill_rows) and all(row.get("status") == "ok" for row in skill_rows)
    skill_status = "已验证" if skill_ok else ("将安装" if profiles.get("selected") else "需要人工确认")
    skill_detail = "所有所选 runtime 使用同一仓内真源" if skill_ok else "选定 profile 后由仓内安装器部署"

    hook_component = components.get("hooks_transport") or {"layers": {}}
    hooks_ok = hook_component["layers"].get("configured") == service_doctor.PASS
    hook_status = "已验证" if hooks_ok else "将安装"

    roster_exists = bool(roster.get("exists") and not roster.get("error"))
    roster_status = "已验证" if roster_exists and roster.get("names") else "需要人工确认"
    roster_detail = (
        f"本机接管 {len(roster.get('names') or [])} 只 bot"
        if roster_exists else "请从 local example 建立本机名册"
    )
    missing = roster.get("credential_missing") or {}
    credentials_status = "已验证" if roster_exists and not missing else "需要人工确认"
    credentials_detail = "所需键均已找到（值未输出）" if credentials_status == "已验证" else "需注册/授权缺失的飞书应用"

    rows = [
        _link_row("profiles", LINK16_ITEMS[0][1], profile_status, profile_detail, profile_component),
        _link_row("feishu_skill", LINK16_ITEMS[1][1], skill_status, skill_detail, skill_rows),
        _link_row("hooks_transport", LINK16_ITEMS[2][1], hook_status,
                  "Claude 用桥专属 hooks；Codex 用 typed event transport", hook_component),
        _link_row("local_roster", LINK16_ITEMS[3][1], roster_status, roster_detail, roster),
        _link_row("credentials", LINK16_ITEMS[4][1], credentials_status, credentials_detail,
                  {"missing_keys_by_bot": missing}),
    ]
    for key, label in LINK16_ITEMS[5:]:
        component = components.get(key) or {"layers": {}}
        layers = component.get("layers") or {}
        if key == "registration_monitor":
            if layers.get("real_io") == service_doctor.PASS:
                status, detail = "已验证", "已有注册回调成功记录；有活动 job 时监督器才常驻"
            elif layers.get("file_present") == service_doctor.PASS:
                status, detail = "已存在跳过", "首次注册时自动启动并通过回调验收"
            else:
                status, detail = "将安装", component.get("fix") or ""
        elif key == "history_ledger" and layers.get("real_io") == service_doctor.PASS:
            status, detail = "已验证", component.get("evidence") or ""
        elif (layers.get("configured") != service_doctor.FAIL
              and layers.get("running") == service_doctor.PASS):
            status, detail = "已验证", component.get("evidence") or ""
        elif layers.get("file_present") == service_doctor.FAIL:
            status, detail = "将安装", component.get("fix") or ""
        else:
            status, detail = "需要人工确认", component.get("fix") or component.get("evidence") or ""
        rows.append(_link_row(key, label, status, detail, component))
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


def _windows_terminal_running():
    if os.name != "nt":
        return False
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "if (Get-Process WindowsTerminal -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    return done.returncode == 0


def _windows_terminal_settings(localappdata=None):
    raw = localappdata or os.environ.get("LOCALAPPDATA")
    if not raw:
        return None
    base = Path(raw) / "Packages"
    candidates = (
        base / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json",
        base / "Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe" / "LocalState" / "settings.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def configure_windows_terminal_git_bash(*, apply=False, localappdata=None, running=None):
    """Set WT's default profile while preserving all unrelated settings."""
    bash = preflight._git_bash_path()
    if not bash:
        return {"task": "windows-terminal-default", "status": "blocked", "detail": "Git Bash 未安装"}
    settings = _windows_terminal_settings(localappdata)
    if not settings:
        return {"task": "windows-terminal-default", "status": "not-found",
                "detail": "未发现 Windows Terminal settings.json（未安装时不阻塞 Link16）"}
    try:
        data = json.loads(settings.read_text(encoding="utf-8-sig"))
        profiles_obj = data.setdefault("profiles", {})
        profiles = profiles_obj.setdefault("list", [])
        if not isinstance(profiles, list):
            raise ValueError("profiles.list 不是数组")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"task": "windows-terminal-default", "status": "blocked",
                "detail": f"无法安全解析 {settings}: {exc}"}

    row = next(
        (item for item in profiles if isinstance(item, dict) and (
            preflight._looks_like_git_bash(item.get("commandline"))
            or str(item.get("name") or "").strip().casefold() == "git bash"
        )),
        None,
    )
    current = str(data.get("defaultProfile") or "").casefold()
    if row and current == str(row.get("guid") or "").casefold() \
            and preflight._looks_like_git_bash(row.get("commandline")):
        return {"task": "windows-terminal-default", "status": "ok",
                "detail": f"{settings} → {row.get('name') or 'Git Bash'}"}

    if (running is None and _windows_terminal_running()) or running is True:
        return {"task": "windows-terminal-default", "status": "needs-gui",
                "detail": "Windows Terminal 正在运行；请在 Settings → Startup → Default profile 选择 Git Bash"}

    guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"link16-git-bash:{str(bash).casefold()}")) + "}"
    if not row:
        row = {"guid": guid, "name": "Git Bash"}
        profiles.append(row)
    row.setdefault("guid", guid)
    row["commandline"] = f'"{bash}" --login -i'
    data["defaultProfile"] = row["guid"]
    if apply:
        _atomic_json(settings, data)
    return {"task": "windows-terminal-default", "status": "applied" if apply else "drift",
            "detail": f"{settings} → {row.get('name') or 'Git Bash'}"}


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


def _user_env_value(name):
    if os.name != "nt":
        return os.environ.get(name)
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _set_user_env_value(name, value):
    if os.name != "nt":
        os.environ[name] = value
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _broadcast_environment_change():
    """Tell Explorer/new terminals to refresh HKCU\\Environment without logout."""
    if os.name != "nt":
        return
    import ctypes
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 0x0002, 2000, ctypes.byref(result)
    )


def configure_python_utf8(*, apply=False):
    """Persist Python UTF-8 mode for future shells without changing Windows locale."""
    if (_user_env_value("PYTHONUTF8") or "").strip() == "1":
        os.environ["PYTHONUTF8"] = "1"
        return {"task": "python-utf8", "status": "ok",
                "detail": "用户级 PYTHONUTF8=1（无需启用 Windows 系统区域 UTF-8 Beta）"}
    if not apply:
        return {"task": "python-utf8", "status": "missing",
                "detail": "将写入用户级 PYTHONUTF8=1；完成后须重开终端"}
    try:
        _set_user_env_value("PYTHONUTF8", "1")
        _broadcast_environment_change()
        os.environ["PYTHONUTF8"] = "1"
    except OSError as exc:
        return {"task": "python-utf8", "status": "blocked", "detail": str(exc)}
    return {"task": "python-utf8", "status": "applied",
            "detail": "已写入用户级 PYTHONUTF8=1；重开终端后由 preflight 验收"}


def _user_path_entries():
    raw = _user_env_value("Path") or ""
    return [part for part in raw.split(";") if part.strip()]


def _same_dir(a, b):
    return str(a).replace("/", "\\").rstrip("\\").casefold() == str(b).replace("/", "\\").rstrip("\\").casefold()


def configure_user_path(*, apply=False):
    """把桥真正依赖、但官方安装器不写的两个目录补进【用户级】PATH。

    2026-09-08 机器 3050：① Git 装在用户目录，bin 不在 PATH → 桥开面板敲 `bash` 直接 command not found；
    ② Claude Code 原生安装器把 claude.exe 放在 ~/.local/bin 却明说「not in your PATH」。
    只追加、不删、不重排；已在则 ok。改完新面板才继承 → detail 里提醒重启 wmux。
    """
    wanted = []
    bash = preflight._git_bash_path()
    if bash:
        wanted.append(("git-bin", bash.parent))
    local_bin = Path.home() / ".local" / "bin"
    if (local_bin / "claude.exe").is_file():
        wanted.append(("claude-local-bin", local_bin))
    if not wanted:
        return {"task": "user-path", "status": "ok", "detail": "没有需要补的目录"}
    if os.name != "nt":
        return {"task": "user-path", "status": "ok", "detail": "非 Windows，跳过"}
    current = _user_path_entries()
    missing = [(label, d) for label, d in wanted if not any(_same_dir(d, e) for e in current)]
    if not missing:
        return {"task": "user-path", "status": "ok",
                "detail": "已在用户 PATH：" + "、".join(str(d) for _, d in wanted)}
    listing = "、".join(f"{label}={d}" for label, d in missing)
    if not apply:
        return {"task": "user-path", "status": "missing", "detail": f"将追加到用户 PATH：{listing}；完成后须重启 wmux/终端"}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                raw, kind = winreg.QueryValueEx(key, "Path")
            except OSError:
                raw, kind = "", winreg.REG_EXPAND_SZ
            parts = [part for part in str(raw).split(";") if part.strip()]
            parts.extend(str(d) for _, d in missing)
            winreg.SetValueEx(key, "Path", 0, kind if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) else winreg.REG_EXPAND_SZ,
                              ";".join(parts))
        _broadcast_environment_change()
        os.environ["PATH"] = os.environ.get("PATH", "") + "".join(f";{d}" for _, d in missing)
    except OSError as exc:
        return {"task": "user-path", "status": "blocked", "detail": str(exc)}
    return {"task": "user-path", "status": "applied",
            "detail": f"已追加到用户 PATH：{listing}；【重启 wmux】新面板才继承"}


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
        if row["key"] == "codex":
            # The official installer otherwise offers to launch Codex in a TTY.
            # Authentication belongs to the selected profile's later login step.
            env["CODEX_NON_INTERACTIVE"] = "1"
        done = subprocess.run(command, env=env, check=False)
        results.append({"key": row["key"], "returncode": done.returncode,
                        "route": decision.selected, "reason": decision.reason})
        if done.returncode != 0:
            break
    return results


def deployment_failures(rows, installs, post_install):
    """Return visible failures after installers and post-install configuration."""
    failures = [
        f"installer:{item.get('key')}"
        for item in installs if item.get("returncode")
    ]
    failures.extend(
        f"missing:{row['key']}" for row in rows
        if row.get("status") == "missing"
    )
    failures.extend(
        f"post:{row['task']}:{row['status']}" for row in post_install
        if row.get("status") in {"blocked", "needs-gui"}
        # wmux 默认 Shell 只是「想变绿」的 GUI 项，桥不依赖它（开面板后自己敲 bash）——不算装机失败。
        and not (row.get("task") == "wmux-default-shell" and row.get("status") == "needs-gui")
    )
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Link16 Windows {len(COMPONENTS)} 项依赖安装计划（默认只预览）")
    parser.add_argument("--skip", action="append", default=[],
                        help="只允许跳过 provider：claude、codex、kimi；可逗号分隔")
    parser.add_argument("--apply", action="store_true", help="安装缺失项并执行 wmux 收尾")
    parser.add_argument("--yes", action="store_true", help="确认已把完整清单展示给用户")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        skipped = parse_skips(args.skip)
    except ValueError as exc:
        parser.error(str(exc))
    initial_rows = installation_plan(skipped)
    if args.apply and not args.yes:
        parser.error(f"--apply 需要 --yes；agent 必须先把 {len(COMPONENTS)} 项清单一次性展示给用户")
    installs = apply_missing(initial_rows) if args.apply else []
    rows = installation_plan(skipped) if args.apply else initial_rows
    post = [configure_python_utf8(apply=args.apply), configure_user_path(apply=args.apply)]
    if "wmux" not in skipped:
        post.extend([
            configure_windows_terminal_git_bash(apply=args.apply),
            ensure_wmux_desktop_shortcut(apply=args.apply),
            configure_wmux_git_bash(apply=args.apply),
        ])
    failures = deployment_failures(rows, installs, post) if args.apply else []
    raw_health = service_doctor.collect_raw()
    evaluated_health = service_doctor.evaluate(raw_health)
    installed_this_run = [row["key"] for row in installs if row.get("returncode") == 0]
    software = software_user_plan(rows, installed_this_run=installed_this_run,
                                  runtime_health=raw_health)
    link16 = link16_user_plan(raw_health, evaluated_health)
    desktop_notes = list(DESKTOP_CLIENT_NOTES) if inside_desktop_client() else []
    payload = {"applied": args.apply, "components": software, "software": software,
               "link16": link16, "installs": installs,
               "post_install": post, "failures": failures, "gstack_default": False,
               "desktop_client_notes": desktop_notes}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"软件层（默认全选；核心 {len(COMPONENTS) - len(OPTIONAL_KEYS)} 项 + provider {len(OPTIONAL_KEYS)} 项）：")
        for idx, row in enumerate(software, 1):
            flag = "必需" if row["required"] else "默认选中/可跳过"
            print(f"  {idx}. [{row['user_status']}] {row['label']} · {flag} · {row['official_source']}")
        print(f"  gstack：默认不安装（不在 {len(COMPONENTS)} 项清单里）")
        for row in post:
            print(f"  [{row['status']:^9}] {row['task']} · {row['detail']}")
        print("\nLink16 层（飞书智能体真正能工作所需）：")
        for idx, row in enumerate(link16, 1):
            print(f"  {idx}. [{row['user_status']}] {row['label']} · {row['detail']}")
        if failures:
            print("  [  FAIL   ] 安装后复查未通过：" + ", ".join(failures))
        if desktop_notes:
            print("\n在 Claude 桌面版里跑的三条注意：")
            for note in desktop_notes:
                print(f"  · {note}")
        if not args.apply:
            print("下一步：把清单展示给用户；确认后运行 --apply --yes，可用 --skip claude,codex,kimi 跳过不需要的 CLI。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
