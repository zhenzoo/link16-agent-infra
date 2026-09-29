#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macOS 新机的 9 项依赖计划（windows_bootstrap 的 mac 对偶 · 默认只预览）。

与 Windows 版的差异：
  · 包管理器是 brew 不是 winget；brew 不在时它是第一项任务（git/gh/python/node 都靠它）。
  · git 也可用 `xcode-select --install`（Xcode CLT 自带）；本清单统一走 brew，保证同源。
  · wmux 没有包管理器渠道，官方只发 Apple Silicon .dmg → detect 看
    /Applications/wmux.app，--apply 只会 `open` 官方下载页，由人拖进 /Applications。
  · 三个 provider CLI 用官方 install.sh（curl | bash/sh），与 Windows 的 install.ps1 对偶。
  · 没有 Windows Terminal / 桌面快捷方式收尾；configure_python_utf8 在 mac 恒 ok
    （默认 UTF-8 locale），仅实测非 UTF-8 时才写 ~/.zprofile。

用法同 windows_bootstrap：默认只读预览；``--apply --yes`` 才执行。
Link16 部署清单的展示层（software_user_plan / link16_user_plan / apply_missing /
deployment_failures）是平台无关的公共函数，直接从 windows_bootstrap 复用，
不 import 它的 Windows 私有函数。
"""
from __future__ import annotations

import argparse
import json
import locale
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import preflight
import network_route
import service_doctor
from windows_bootstrap import (  # 平台无关的公共展示/应用层，勿 import 其 Windows 私有函数
    apply_missing, deployment_failures, link16_user_plan, software_user_plan,
)


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    required: bool
    official_source: str
    probe_url: str
    install_command: tuple[str, ...]


BREW_INSTALL = (
    "bash", "-c",
    "curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh | bash",
)
WMUX_RELEASES = "https://github.com/openwong2kim/wmux/releases/latest"
COMPONENTS = (
    Component("brew", "Homebrew", True, "brew.sh 官方安装脚本（需输入开机密码）",
              "https://brew.sh/", BREW_INSTALL),
    Component("git", "Git", True, "brew（也可用 xcode-select --install 装 Xcode CLT 自带 git）",
              "https://github.com/git/git/releases/latest",
              ("brew", "install", "git")),
    Component("gh", "GitHub CLI", True, "GitHub / brew",
              "https://github.com/cli/cli/releases/latest",
              ("brew", "install", "gh")),
    Component("python", "Python 3.12+", True, "python.org / brew python@3.13",
              "https://www.python.org/downloads/macos/",
              ("brew", "install", "python@3.13")),
    Component("node", "Node.js LTS", True, "OpenJS / brew node@22（keg-only，PATH 由本脚本补）",
              "https://nodejs.org/en/download",
              ("brew", "install", "node@22")),
    Component("wmux", "wmux", True, "wmux 官方 .dmg（仅 Apple Silicon · 无包管理器渠道）",
              WMUX_RELEASES,
              ("open", WMUX_RELEASES)),  # 只能打开下载页；拖进 /Applications 是人工步骤
    Component("claude", "Claude Code", False, "Anthropic native installer",
              "https://claude.ai/install.sh",
              ("bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash")),
    Component("codex", "Codex CLI", False, "OpenAI native installer",
              "https://chatgpt.com/codex/install.sh",
              ("sh", "-c", "curl -fsSL https://chatgpt.com/codex/install.sh | sh")),
    Component("kimi", "Kimi Code CLI", False, "Moonshot native installer",
              "https://code.kimi.com/kimi-code/install.sh",
              ("bash", "-c", "curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash")),
)
COMPONENT_BY_KEY = {row.key: row for row in COMPONENTS}
OPTIONAL_KEYS = {row.key for row in COMPONENTS if not row.required}

BREW_CANDIDATES = (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew"))
# brew 的 versioned formula 是 keg-only（不进 /opt/homebrew/bin），node@22 落在这里
NODE_KEG_BINS = (Path("/opt/homebrew/opt/node@22/bin"), Path("/usr/local/opt/node@22/bin"))


def _first_path(candidates, *, is_dir=False):
    for path in candidates:
        try:
            if path and (path.is_dir() if is_dir else path.is_file()):
                return Path(path)
        except OSError:
            continue
    return None


def _which(name):
    found = preflight._fresh_which(name)
    return Path(found) if found else None


def _wmux_candidates():
    return [Path("/Applications/wmux.app"), Path.home() / "Applications" / "wmux.app"]


def wmux_app():
    """mac 上 wmux 是 .dmg 拖拽安装的 .app；没有包管理器渠道可查。"""
    return _first_path(_wmux_candidates(), is_dir=True)


def _python_version(path):
    try:
        done = subprocess.run(
            [str(path), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            check=False,
        )
        version = tuple(int(part) for part in done.stdout.strip().split("."))
        return version if done.returncode == 0 and len(version) == 3 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _python_candidates():
    return [Path(sys.executable), _which("python3"), _which("python3.13"),
            Path("/opt/homebrew/bin/python3.13"), Path("/usr/local/bin/python3.13")]


def _python312_executable():
    candidates = _python_candidates()
    valid = []
    seen = set()
    for path in candidates:
        if not path:
            continue
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        version = _python_version(path)
        if version and version >= (3, 12, 0):
            valid.append((version, path))
    return max(valid, default=(None, None))[1]


def detect_component(key):
    """返回已安装入口；None 表示 PATH 与已知落点都找不到。"""
    if key == "brew":
        return _which("brew") or _first_path(BREW_CANDIDATES)
    if key == "git":
        return _which("git")  # mac 不需要 Git Bash；Xcode CLT 的 /usr/bin/git 也算
    if key == "gh":
        return _which("gh")
    if key == "python":
        return _python312_executable()
    if key == "node":
        return _which("node") or _first_path([d / "node" for d in NODE_KEG_BINS])
    if key == "wmux":
        return wmux_app()
    if key == "claude":
        return _which("claude") or _first_path([Path.home() / ".local" / "bin" / "claude"])
    if key == "codex":
        return _which("codex") or _first_path([Path.home() / ".codex" / "bin" / "codex"])
    if key == "kimi":
        return _which("kimi") or _first_path([Path.home() / ".kimi-code" / "bin" / "kimi"])
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


ZPROFILE_MARKER = "# link16-macos-bootstrap"


def _zprofile(home=None):
    return (Path(home) if home else Path.home()) / ".zprofile"


def configure_python_utf8(*, apply=False, home=None, preferred=None):
    """macOS 默认 UTF-8 locale → 恒 ok；仅实测非 UTF-8 时才把 LANG/LC_ALL 写进 ~/.zprofile。"""
    pref = (preferred if preferred is not None else (locale.getpreferredencoding(False) or ""))
    pref = pref.lower().replace("-", "")
    if pref in {"utf8", "utf8mb4"}:
        return {"task": "python-utf8", "status": "ok",
                "detail": f"macOS 默认 UTF-8（preferred encoding = {pref}），无需 PYTHONUTF8"}
    profile = _zprofile(home)
    lines = [ZPROFILE_MARKER, "export LANG=en_US.UTF-8", "export LC_ALL=en_US.UTF-8"]
    existing = profile.read_text(encoding="utf-8", errors="replace") if profile.is_file() else ""
    if "LC_ALL=en_US.UTF-8" in existing:
        return {"task": "python-utf8", "status": "ok", "detail": f"{profile} 已有 UTF-8 locale 设置"}
    if not apply:
        return {"task": "python-utf8", "status": "missing",
                "detail": f"当前 locale = {pref or '未知'}；将往 {profile} 追加 LANG/LC_ALL=en_US.UTF-8"}
    with profile.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(lines) + "\n")
    return {"task": "python-utf8", "status": "applied",
            "detail": f"已写入 {profile}；重开终端后由 preflight 验收"}


def configure_user_path(*, apply=False, home=None):
    """把 brew / node@22 keg / ~/.local/bin 补进 ~/.zprofile 的 PATH（只追加、不删、不重排）。

    mac 没有「用户级 PATH 注册表」；官方惯例就是写 shell profile。已在当前 PATH 或
    已在 ~/.zprofile 里的目录不动。
    """
    profile = _zprofile(home)
    existing = profile.read_text(encoding="utf-8", errors="replace") if profile.is_file() else ""
    current = set((os.environ.get("PATH") or "").split(":"))
    wanted = []
    brew = detect_component("brew")
    if brew:
        wanted.append(brew.parent)
    node = detect_component("node")
    if node and any(node.parent == d for d in NODE_KEG_BINS):
        wanted.append(node.parent)
    local_bin = (Path(home) if home else Path.home()) / ".local" / "bin"
    if local_bin.is_dir():
        wanted.append(local_bin)
    missing = [d for d in wanted if str(d) not in current and str(d) not in existing]
    if not missing:
        return {"task": "user-path", "status": "ok", "detail": "没有需要补的目录"}
    listing = "、".join(str(d) for d in missing)
    lines = [f'export PATH="{d}:$PATH"' for d in missing]
    if not apply:
        return {"task": "user-path", "status": "missing",
                "detail": f"将追加到 {profile}：{'；'.join(lines)}；完成后须重开终端"}
    with profile.open("a", encoding="utf-8") as handle:
        handle.write("\n" + ZPROFILE_MARKER + "\n")
        for d in missing:
            handle.write(f'export PATH="{d}:$PATH"\n')
    return {"task": "user-path", "status": "applied",
            "detail": f"已追加到 {profile}：{listing}；【重开终端】新会话才继承"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Link16 macOS {len(COMPONENTS)} 项依赖安装计划（默认只预览）")
    parser.add_argument("--skip", action="append", default=[],
                        help="只允许跳过 provider：claude、codex、kimi；可逗号分隔")
    parser.add_argument("--apply", action="store_true", help="安装缺失项并执行 zprofile 收尾")
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
    failures = deployment_failures(rows, installs, post) if args.apply else []
    raw_health = service_doctor.collect_raw()
    evaluated_health = service_doctor.evaluate(raw_health)
    installed_this_run = [row["key"] for row in installs if row.get("returncode") == 0]
    software = software_user_plan(rows, installed_this_run=installed_this_run,
                                  runtime_health=raw_health)
    link16 = link16_user_plan(raw_health, evaluated_health)
    notes = []
    if platform.machine() != "arm64":
        notes.append("wmux 官方只发 Apple Silicon .dmg：本机不是 arm64，wmux 一项大概率装不了。")
    if any(row["key"] == "brew" and row["status"] == "missing" for row in rows):
        notes.append("brew 不在：它是第一项任务（git/gh/python/node 都靠它）；"
                     "官方安装脚本需要交互输入开机密码，请在终端里跑。")
    if "wmux" not in skipped:
        notes.append("wmux 无包管理器渠道：--apply 只会打开官方下载页，请手动把 wmux.app 拖进 /Applications 并打开一次。")
    payload = {"applied": args.apply, "components": software, "software": software,
               "link16": link16, "installs": installs,
               "post_install": post, "failures": failures, "gstack_default": False,
               "desktop_client_notes": notes}
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
        if notes:
            print("\nmacOS 装机注意：")
            for note in notes:
                print(f"  · {note}")
        if not args.apply:
            print("下一步：把清单展示给用户；确认后运行 --apply --yes，可用 --skip claude,codex,kimi 跳过不需要的 CLI。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
