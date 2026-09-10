#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context_scan.py —— 新用户「带着自己的 context 上手」的只读盘点（PLAN-1000 S2 · 零写入）。

为什么有它（2026-09-08 机器 3050 实证）：同事装完 Link16，bot 跑在一个全新的 `.claude-work`
里，工作目录是个空文件夹；而他积累的记忆躺在 `~/.claude`（Claude 桌面版 Code 模式写的）和
Claude 桌面版 Cowork 的 memory 文件夹里，没人去读。他一问「你能看我桌面吗」就露馅：bot 是个
失忆的新人站在空房间里。本脚本把「他已经有什么」和「他最近在忙什么」列成一张可勾选的清单，
交给 context_import.py 导入、交给 register_feishu_app.py --from-scan 起 bot。

盘点五类来源（每类只报「找到什么 / 多少条 / 最近修改时间」，绝不打印内容、绝不复制凭据）：
  ① Claude Code 默认 home `~/.claude`：CLAUDE.md / memory / projects 记忆 / skills / commands / settings / 会话 cwd
  ② Claude 桌面版 Cowork：exe 装在 %APPDATA%\\Claude，MSIX 装在 %LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude；
     会话目录叫 local-agent-mode-sessions 或 claude-code-sessions；全局 memory/CLAUDE.md + memory/memory/*.md、
     各 space 的 memory、会话 manifest 与 jsonl
  ③ Codex home `~/.codex`：AGENTS.md / memories / sessions
  ④ ChatGPT 桌面版：只有 LevelDB 缓存，不可靠 → 只报「已安装，请走官方导出」
  ⑤ 官方导出包：~/Downloads（或 --exports）里的 zip / conversations.json，按内容分辨 Claude 与 ChatGPT

再扫近 N 天活跃项目（三路打分取前 K）：git 提交数与改动文件数、非 git 目录改动文件数、
Claude Code 会话 cwd 的最近活动时间。给出「机器代号-项目简称」的 bot 名建议。

用法：
    python feishu/context_scan.py                       # 人读清单
    python feishu/context_scan.py --json                # 机器可读（context_import / register --from-scan 吃它）
    python feishu/context_scan.py --out <file.json>     # 落盘（默认不落）
    --roots <dir> ... --days 7 --top 3 --exports <dir> --profile <目标 profile>
退出码恒为 0（只读盘点不判死）；找不到任何来源也照样输出空清单。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SCHEMA = "link16-context-scan-v1"
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".cache", "third_party",
             "site-packages", ".git", ".idea", ".vscode", "AppData", "Lab", "$RECYCLE.BIN"}
# 这些文件名永远不进清单、不进导入（只报「存在」都不报）：
SECRET_NAMES = {".credentials.json", "auth.json", ".env", "credentials", "token", "tokens", "hosts.yml"}
MEMORY_EXTS = {".md", ".txt"}


# ---------------------------------------------------------------- 小工具
def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _count_files(root: Path, exts=None, limit=5000) -> tuple[int, float]:
    """(文件数, 最新 mtime)；只数不读。"""
    n, latest = 0, 0.0
    if not root.is_dir():
        return 0, 0.0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for name in fn:
            if exts and Path(name).suffix.lower() not in exts:
                continue
            n += 1
            latest = max(latest, _mtime(Path(dp) / name))
            if n >= limit:
                return n, latest
    return n, latest


def _real_local_appdata() -> Path | None:
    """绕开 MSIX 容器重定向（与 windows_bootstrap._real_local_appdata 同判据）。"""
    local = os.environ.get("LOCALAPPDATA") or ""
    if "\\packages\\" in local.replace("/", "\\").casefold():
        return Path(os.environ.get("USERPROFILE") or str(Path.home())) / "AppData" / "Local"
    return Path(local) if local else None


def _appdata() -> Path | None:
    raw = os.environ.get("APPDATA")
    if raw and "\\packages\\" not in raw.replace("/", "\\").casefold():
        return Path(raw)
    profile = os.environ.get("USERPROFILE")
    return Path(profile) / "AppData" / "Roaming" if profile else None


def _has_secret_files(root: Path, limit=3000) -> list[str]:
    """只返回【文件名】，不返回路径细节，不读内容。"""
    found = set()
    if not root.is_dir():
        return []
    n = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for name in fn:
            n += 1
            if name in SECRET_NAMES or name.lower().endswith((".pem", ".key")):
                found.add(name)
            if n >= limit:
                return sorted(found)
    return sorted(found)


# ---------------------------------------------------------------- ① Claude Code home
def _jsonl_cwd(path: Path, max_lines=40) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for i, line in enumerate(handle):
                if i >= max_lines:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                cwd = row.get("cwd") if isinstance(row, dict) else None
                if cwd:
                    return str(cwd)
    except OSError:
        pass
    return ""


def scan_claude_code_home(home: Path, days: int) -> dict:
    row = {"id": "claude-code-home", "kind": "claude-code", "label": "Claude Code 默认 home",
           "path": str(home), "found": home.is_dir(), "items": {}, "sessions": [], "latest": "",
           "secrets_present": [], "import_default": True, "note": ""}
    if not home.is_dir():
        row["note"] = "没有 ~/.claude（从没在这台电脑用过 Claude Code / Claude 桌面版 Code 模式）"
        row["import_default"] = False
        return row
    items = row["items"]
    claude_md = home / "CLAUDE.md"
    items["CLAUDE.md"] = {"count": int(claude_md.is_file()), "latest": _iso(_mtime(claude_md))}
    n, latest = _count_files(home / "memory", MEMORY_EXTS)
    items["memory"] = {"count": n, "latest": _iso(latest)}
    n_proj_mem, latest_pm, proj_with_mem = 0, 0.0, 0
    for proj in (home / "projects").glob("*"):
        if (proj / "memory").is_dir():
            c, l = _count_files(proj / "memory", MEMORY_EXTS)
            if c:
                proj_with_mem += 1
                n_proj_mem += c
                latest_pm = max(latest_pm, l)
    items["projects_memory"] = {"count": n_proj_mem, "projects": proj_with_mem, "latest": _iso(latest_pm)}
    skills = [p.parent.name for p in (home / "skills").glob("*/SKILL.md")]
    items["skills"] = {"count": len(skills), "names": sorted(skills)[:40]}
    commands = list((home / "commands").glob("*.md"))
    items["commands"] = {"count": len(commands)}
    settings = home / "settings.json"
    items["settings.json"] = {"count": int(settings.is_file())}
    # 会话 cwd（活跃项目的第三路信号）：每个 project 目录取最新 jsonl 的 cwd 与 mtime
    cutoff = time.time() - days * 86400
    sessions = []
    for proj in (home / "projects").glob("*"):
        jsonls = list(proj.glob("*.jsonl"))
        if not jsonls:
            continue
        newest = max(jsonls, key=_mtime)
        ts = _mtime(newest)
        cwd = _jsonl_cwd(newest)
        sessions.append({"cwd": cwd, "slug": proj.name, "sessions": len(jsonls),
                         "latest": _iso(ts), "latest_ts": ts, "recent": ts >= cutoff})
    sessions.sort(key=lambda s: -s["latest_ts"])
    row["sessions"] = sessions[:30]
    items["sessions"] = {"count": sum(s["sessions"] for s in sessions), "projects": len(sessions),
                         "recent_projects": sum(1 for s in sessions if s["recent"])}
    row["latest"] = _iso(max([latest, latest_pm, _mtime(claude_md)] + [s["latest_ts"] for s in sessions] or [0]))
    row["secrets_present"] = _has_secret_files(home)
    if not any(v.get("count") for k, v in items.items() if k != "sessions"):
        row["import_default"] = False
        row["note"] = "只有会话记录，没有 CLAUDE.md / memory / skills 可导入"
    return row


# ---------------------------------------------------------------- ② Claude 桌面版 Cowork
def claude_desktop_roots() -> list[Path]:
    roots = []
    appdata = _appdata()
    if appdata:
        roots.append(appdata / "Claude")
    local = _real_local_appdata()
    if local:
        try:
            for pkg in (local / "Packages").glob("Claude_*"):
                roots.append(pkg / "LocalCache" / "Roaming" / "Claude")
        except OSError:
            pass
        roots.append(local / "Claude-3p")
    seen, out = set(), []
    for r in roots:
        key = str(r).casefold()
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def scan_claude_desktop(root: Path, days: int) -> dict:
    flavor = "MSIX（应用商店/winget）" if "\\packages\\" in str(root).replace("/", "\\").casefold() else "exe 安装"
    row = {"id": f"claude-desktop:{root.parent.name if flavor.startswith('MSIX') else 'exe'}",
           "kind": "claude-desktop", "label": f"Claude 桌面版 Cowork 记忆（{flavor}）", "path": str(root),
           "found": True, "items": {}, "memory_dirs": [], "latest": "", "secrets_present": [],
           "import_default": True, "note": ""}
    items = row["items"]
    sessions_roots = [root / name for name in ("local-agent-mode-sessions", "claude-code-sessions") if (root / name).is_dir()]
    n_global_md, n_global_notes, n_space_notes, n_manifests, n_transcripts, n_audit = 0, 0, 0, 0, 0, 0
    latest = 0.0
    for sroot in sessions_roots:
        # 结构：<sessions>/<org>/<user>/{memory/, spaces/, <sessionId>/...}
        for org in sroot.glob("*"):
            if not org.is_dir():
                continue
            for user in org.glob("*"):
                if not user.is_dir():
                    continue
                mem = user / "memory"
                if (mem / "CLAUDE.md").is_file():
                    n_global_md += 1
                    latest = max(latest, _mtime(mem / "CLAUDE.md"))
                    row["memory_dirs"].append(str(mem))
                c, l = _count_files(mem / "memory", MEMORY_EXTS)
                n_global_notes += c
                latest = max(latest, l)
                for space in (user / "spaces").glob("*"):
                    c, l = _count_files(space / "memory", MEMORY_EXTS)
                    if c:
                        n_space_notes += c
                        latest = max(latest, l)
                        row["memory_dirs"].append(str(space / "memory"))
                for manifest in user.glob("local_*.json"):
                    n_manifests += 1
                    latest = max(latest, _mtime(manifest))
                for sess in user.glob("local_*"):
                    if sess.is_dir():
                        if (sess / "audit.jsonl").is_file():
                            n_audit += 1
                        n_transcripts += sum(1 for _ in (sess / ".claude" / "projects").glob("*/*.jsonl"))
    items["global_CLAUDE.md"] = {"count": n_global_md}
    items["global_memory_notes"] = {"count": n_global_notes}
    items["project_memory_notes"] = {"count": n_space_notes}
    items["cowork_sessions"] = {"count": n_manifests, "transcripts": n_transcripts, "audit_logs": n_audit}
    config = root / "claude_desktop_config.json"
    mcp = 0
    if config.is_file():
        try:
            mcp = len((json.loads(config.read_text(encoding="utf-8")).get("mcpServers") or {}))
        except (OSError, ValueError):
            mcp = 0
    items["mcp_servers"] = {"count": mcp}
    row["latest"] = _iso(latest)
    if not sessions_roots:
        row["note"] = "装了桌面版但没有 Cowork 会话目录（没用过 Cowork，或聊天只在云端）"
        row["import_default"] = False
    elif not (n_global_md or n_global_notes or n_space_notes):
        row["note"] = "有 Cowork 会话，但记忆功能没开或还没写过笔记；聊天记录本身走「设置→隐私→导出数据」"
        row["import_default"] = False
    row["secrets_present"] = _has_secret_files(root, limit=2000)
    return row


# ---------------------------------------------------------------- ③ Codex home
def scan_codex_home(home: Path, days: int) -> dict:
    row = {"id": "codex-home", "kind": "codex", "label": "Codex CLI / 桌面版 home", "path": str(home),
           "found": home.is_dir(), "items": {}, "latest": "", "secrets_present": [], "import_default": True, "note": ""}
    if not home.is_dir():
        row["note"] = "没有 ~/.codex"
        row["import_default"] = False
        return row
    items = row["items"]
    agents = home / "AGENTS.md"
    items["AGENTS.md"] = {"count": int(agents.is_file()), "latest": _iso(_mtime(agents))}
    n, latest = _count_files(home / "memories", MEMORY_EXTS)
    items["memories"] = {"count": n, "latest": _iso(latest)}
    n_sess, latest_s = _count_files(home / "sessions", {".jsonl"}, limit=20000)
    items["sessions"] = {"count": n_sess, "latest": _iso(latest_s)}
    # `claude-compat-*` 与 `feishu` 是 Link16 自己生成的薄壳，不算用户资产
    all_skills = [p.parent.name for p in (Path.home() / ".agents" / "skills").glob("*/SKILL.md")]
    skills = [s for s in all_skills if not s.startswith("claude-compat-") and s != "feishu"]
    items["agents_skills"] = {"count": len(skills), "names": sorted(skills)[:40],
                              "generated_shells": len(all_skills) - len(skills)}
    row["latest"] = _iso(max(latest, latest_s, _mtime(agents)))
    row["secrets_present"] = _has_secret_files(home, limit=2000)
    if not (agents.is_file() or n or skills):
        row["import_default"] = False
        row["note"] = "只有会话记录，没有 AGENTS.md / memories / skills 可导入"
    return row


# ---------------------------------------------------------------- ④ ChatGPT 桌面版
def scan_chatgpt_desktop() -> dict:
    local = _real_local_appdata()
    pkgs = []
    if local:
        try:
            pkgs = [p for p in (local / "Packages").glob("OpenAI.ChatGPT-Desktop_*") if p.is_dir()]
        except OSError:
            pkgs = []
    row = {"id": "chatgpt-desktop", "kind": "chatgpt-desktop", "label": "ChatGPT 桌面版", "path": str(pkgs[0]) if pkgs else "",
           "found": bool(pkgs), "items": {}, "latest": "", "secrets_present": [], "import_default": False,
           "note": ""}
    if pkgs:
        cache = pkgs[0] / "LocalCache" / "Roaming" / "ChatGPT"
        row["latest"] = _iso(_mtime(cache)) if cache.is_dir() else ""
        row["note"] = ("已安装。本地只有 IndexedDB/LevelDB 缓存，不是可靠的聊天记录来源。"
                       "要导入请：设置 → 数据控制 → 导出数据（邮件收 zip，放到下载目录再跑本脚本）；"
                       "记忆：设置 → 个性化 → 管理记忆，全选复制成 chatgpt-memories.txt 放到下载目录。")
    else:
        row["note"] = "未安装"
    return row


# ---------------------------------------------------------------- ⑤ 官方导出包
def _classify_export_zip(path: Path) -> dict | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = {Path(n).name for n in zf.namelist()}
            sizes = {Path(i.filename).name: i.file_size for i in zf.infolist()}
    except (OSError, zipfile.BadZipFile):
        return None
    if "conversations.json" not in names:
        return None
    if names & {"memories.json", "projects.json", "users.json"}:
        vendor = "claude"
    elif names & {"chat.html", "message_feedback.json", "user.json", "shared_conversations.json"}:
        vendor = "chatgpt"
    else:
        vendor = "unknown"
    return {"vendor": vendor, "files": sorted(names & {"conversations.json", "memories.json", "projects.json",
                                                        "users.json", "chat.html", "user.json", "message_feedback.json"}),
            "conversations_bytes": sizes.get("conversations.json", 0),
            "memories_bytes": sizes.get("memories.json", 0)}


def scan_exports(dirs: list[Path]) -> list[dict]:
    rows = []
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                if p.suffix.lower() == ".zip" and p.stat().st_size < 4 * 1024 ** 3:
                    info = _classify_export_zip(p)
                    if info:
                        rows.append({"id": f"export:{p.name}", "kind": f"export-{info['vendor']}",
                                     "label": {"claude": "Claude 官方导出包", "chatgpt": "ChatGPT 官方导出包",
                                               "unknown": "未识别的对话导出包"}[info["vendor"]],
                                     "path": str(p), "found": True, "items": {"files": info["files"],
                                     "conversations_MB": round(info["conversations_bytes"] / 1048576, 1),
                                     "memories_KB": round(info["memories_bytes"] / 1024, 1)},
                                     "latest": _iso(_mtime(p)), "secrets_present": [],
                                     "import_default": info["vendor"] != "unknown", "note": ""})
                elif p.name.lower() in {"chatgpt-memories.txt", "memories.txt", "chatgpt-memory.txt"} and p.is_file():
                    rows.append({"id": f"export:{p.name}", "kind": "export-chatgpt-memories",
                                 "label": "ChatGPT 记忆（手动复制的文本）", "path": str(p), "found": True,
                                 "items": {"bytes": p.stat().st_size}, "latest": _iso(_mtime(p)),
                                 "secrets_present": [], "import_default": True, "note": ""})
            except OSError:
                continue
    return rows


# ---------------------------------------------------------------- 活跃项目
def _git_activity(repo: Path, days: int) -> tuple[int, int]:
    try:
        done = subprocess.run(["git", "-C", str(repo), "log", f"--since={days}.days", "--name-only",
                               "--pretty=format:%H"], capture_output=True, text=True, timeout=20,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    commits, files = 0, set()
    for line in done.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            commits += 1
        else:
            files.add(line)
    return commits, len(files)


def _recent_file_changes(root: Path, cutoff: float, limit=4000) -> tuple[int, float]:
    n, latest, seen = 0, 0.0, 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".")]
        for name in fn:
            seen += 1
            ts = _mtime(Path(dp) / name)
            if ts >= cutoff:
                n += 1
                latest = max(latest, ts)
            if seen >= limit:
                return n, latest
    return n, latest


def default_roots() -> list[Path]:
    home = Path.home()
    roots = [home / "Desktop", home / "Documents", home / "Projects", home / "code", home / "src"]
    vibe = os.environ.get("VIBECODING_ROOT")
    if vibe:
        roots.append(Path(vibe))
    out, seen = [], set()
    for r in roots:
        key = str(r).casefold()
        if r.is_dir() and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _walk_candidates(roots: list[Path], max_depth=3):
    """产出 (path, is_git)。遇到 .git 就不再往下钻；非 git 目录钻到 max_depth。"""
    for root in roots:
        root = root.resolve()
        stack = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                children = [c for c in cur.iterdir() if c.is_dir() and c.name not in SKIP_DIRS and not c.name.startswith(".")]
            except OSError:
                continue
            for child in children:
                if (child / ".git").exists():
                    yield child, True
                elif depth + 1 < max_depth:
                    yield child, False
                    stack.append((child, depth + 1))


def active_projects(roots: list[Path], days: int, top: int, session_hints: list[dict]) -> list[dict]:
    cutoff = time.time() - days * 86400
    hints = {}
    for s in session_hints:
        if s.get("cwd"):
            hints[str(Path(s["cwd"])).casefold()] = s
    rows = {}
    for path, is_git in _walk_candidates(roots):
        key = str(path).casefold()
        if is_git:
            commits, files = _git_activity(path, days)
            changed, latest = (files, 0.0) if commits else _recent_file_changes(path, cutoff, limit=1500)
            score = commits * 3 + min(changed, 200) / 10
            reason = f"近 {days} 天 {commits} 次提交 · 改动 {changed} 个文件" if commits else (
                f"近 {days} 天有 {changed} 个文件改动（未提交）" if changed else "")
        else:
            changed, latest = _recent_file_changes(path, cutoff, limit=800)
            commits = 0
            score = min(changed, 200) / 10
            reason = f"近 {days} 天有 {changed} 个文件改动（非 git）" if changed else ""
        hint = hints.get(key)
        sessions = 0
        if hint and hint.get("recent"):
            sessions = hint["sessions"]
            score += 5 + min(sessions, 10)
            reason = (reason + " · " if reason else "") + f"Claude 会话 {sessions} 次（最近 {hint['latest']}）"
        if score <= 0:
            continue
        # 子目录不与父目录重复计分：父是 git 仓时子目录已被跳过；非 git 父目录保留分高者
        rows[key] = {"path": str(path), "name": path.name, "is_git": is_git, "commits": commits,
                     "changed_files": changed, "claude_sessions": sessions, "score": round(score, 1),
                     "reason": reason}
    # Claude 会话指向、但不在扫描根下的目录（如 Desktop 之外）也算候选
    for key, hint in hints.items():
        if key in rows or not hint.get("recent"):
            continue
        p = Path(hint["cwd"])
        if p.is_dir() and not any(str(p).casefold().startswith(str(r.resolve()).casefold()) for r in roots):
            rows[key] = {"path": str(p), "name": p.name, "is_git": (p / ".git").exists(), "commits": 0,
                         "changed_files": 0, "claude_sessions": hint["sessions"], "score": 5 + min(hint["sessions"], 10),
                         "reason": f"Claude 会话 {hint['sessions']} 次（最近 {hint['latest']}）"}
    ranked = sorted(rows.values(), key=lambda r: -r["score"])
    # 同一非 git 树里父子同时上榜时，去掉被父目录覆盖的子目录
    picked = []
    for r in ranked:
        if any(r["path"].casefold().startswith(p["path"].casefold() + os.sep) for p in picked):
            continue
        picked.append(r)
        if len(picked) >= top:
            break
    return picked


# ---------------------------------------------------------------- 机器代号 / bot 名
def machine_prefix() -> dict:
    """优先 machine_identity（型号+BIOS 年份）；推不出或跑不了就 hostname 兜底（host 2026-09-10 拍板：不询问）。"""
    host = re.sub(r"[^a-z0-9]+", "", socket.gethostname().lower()) or "pc"
    try:
        import machine_identity as mi
        identity = mi.collect_windows_identity()
        existing = None
        try:
            existing = mi._registry_machines()
        except Exception:  # noqa: BLE001
            existing = None
        row = mi.suggest_prefix(identity, existing=existing)
        if row.prefix and row.confidence in {"high", "medium"}:
            return {"prefix": row.prefix, "source": row.evidence or "machine_identity", "confidence": row.confidence}
        return {"prefix": host[:12], "source": f"型号推不出可靠代号（{row.prefix}/{row.confidence}）→ hostname 兜底",
                "confidence": "low"}
    except Exception as exc:  # noqa: BLE001
        return {"prefix": host[:12], "source": f"machine_identity 不可用（{exc}）→ hostname 兜底", "confidence": "low"}


def _name_tokens(name: str) -> list[str]:
    """目录名切成候选简称：小写、只留字母数字、丢掉纯日期/纯数字段（`20260910`）。"""
    tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", name) if t]
    tokens = [t for t in tokens if not (t.isdigit() and len(t) >= 4)]
    return [re.sub(r"[^a-z0-9]", "", t)[:12] for t in tokens if re.sub(r"[^a-z0-9]", "", t)]


def project_short_name(name: str, taken: set[str] | None = None) -> str:
    """目录名 → bot 名里的项目简称，按排名先到先得：

    `link16-agent-infra` → `link16`；同批再来一个 `link16-miaoda-skill` 时第一段已被占 → `miaoda`；
    `OBSBOT-baseball` 在 `obsbot` 已被占时 → `baseball`（和主人 tb26-baseball 的习惯一致）。
    """
    tokens = _name_tokens(name)
    if not tokens:
        return "proj"
    taken = taken or set()
    for t in tokens:
        if t not in taken:
            return t
    return tokens[0]


def suggest_bots(projects: list[dict], prefix: str, existing_names: set[str] | None = None) -> list[dict]:
    out, used = [], set(existing_names or ())
    taken_short = {n[len(prefix) + 1:] for n in used if n.startswith(prefix + "-")}
    for p in projects:
        short = project_short_name(p["name"], taken_short)
        taken_short.add(short)
        bot = f"{prefix}-{short}"
        n = 2
        while bot in used:
            bot = f"{prefix}-{short}-{n}"
            n += 1
        used.add(bot)
        out.append({"bot": bot, "display_name": bot, "cwd": p["path"], "project": p["name"], "reason": p["reason"]})
    return out


def _existing_bot_names() -> set[str]:
    names = set()
    for fname in ("bridge-bots.local.json", "bridge-bots.json"):
        try:
            for b in json.loads((HERE / fname).read_text(encoding="utf-8")).get("bots") or []:
                if isinstance(b, dict) and b.get("name"):
                    names.add(str(b["name"]))
        except (OSError, ValueError):
            continue
    return names


# ---------------------------------------------------------------- 目标 profile
def target_profiles(explicit: str | None) -> dict:
    try:
        import agent_runtime
        specs = agent_runtime.profile_specs()
    except Exception as exc:  # noqa: BLE001
        return {"claude": None, "codex": None, "all": [], "note": f"读不到 profile registry：{exc}"}
    by_runtime = {}
    for s in specs:
        by_runtime.setdefault(s.runtime, []).append(s)
    picked = {}
    for runtime, rows in by_runtime.items():
        try:
            picked[runtime] = agent_runtime.machine_default_profile(runtime)
        except Exception:  # noqa: BLE001
            picked[runtime] = rows[0].name
    if explicit:
        for s in specs:
            if s.name == explicit:
                picked[s.runtime] = s.name
    return {"claude": picked.get("claude"), "codex": picked.get("codex"), "kimi": picked.get("kimi"),
            "all": [{"name": s.name, "runtime": s.runtime, "home": s.home} for s in specs], "note": ""}


# ---------------------------------------------------------------- 总装
def scan(roots=None, days=7, top=3, exports=None, profile=None) -> dict:
    home = Path.home()
    sources = []
    claude_home = scan_claude_code_home(home / ".claude", days)
    sources.append(claude_home)
    for root in claude_desktop_roots():
        sources.append(scan_claude_desktop(root, days))
    if not any(s["kind"] == "claude-desktop" for s in sources):
        sources.append({"id": "claude-desktop", "kind": "claude-desktop", "label": "Claude 桌面版 Cowork 记忆",
                        "path": "", "found": False, "items": {}, "latest": "", "secrets_present": [],
                        "import_default": False, "note": "未安装（exe 与 MSIX 两种落点都没有）"})
    sources.append(scan_codex_home(home / ".codex", days))
    sources.append(scan_chatgpt_desktop())
    export_dirs = [home / "Downloads"] + [Path(e) for e in (exports or [])]
    sources.extend(scan_exports(export_dirs))
    roots = [Path(r) for r in roots] if roots else default_roots()
    projects = active_projects(roots, days, top, claude_home.get("sessions") or [])
    prefix = machine_prefix()
    bots = suggest_bots(projects, prefix["prefix"], _existing_bot_names())
    return {
        "schema": SCHEMA,
        "generated_at": _iso(time.time()),
        "hostname": socket.gethostname(),
        "days": days,
        "roots": [str(r) for r in roots],
        "sources": sources,
        "active_projects": projects,
        "machine_prefix": prefix,
        "suggested_bots": bots,
        "targets": target_profiles(profile),
        "never_imported": sorted(SECRET_NAMES | {"sessions/", "projects/*/*.jsonl", ".claude.json"}),
    }


def render(report: dict) -> str:
    lines = [f"===== Link16 · 已有 context 盘点（{report['hostname']} · 近 {report['days']} 天）=====", ""]
    lines.append("① 已有的 AI 记忆 / 配置来源（只数不读 · 勾选后由 context_import.py 导入）")
    for s in report["sources"]:
        mark = "☑" if s["import_default"] else ("☐" if s["found"] else "—")
        lines.append(f"  {mark} {s['label']}")
        if s["path"]:
            lines.append(f"      位置：{s['path']}")
        items = s.get("items") or {}
        parts = []
        for key, val in items.items():
            if isinstance(val, dict):
                c = val.get("count")
                extra = "".join(f" · {k}={v}" for k, v in val.items() if k not in {"count", "names", "latest"} and v)
                names = val.get("names")
                tail = f"（{', '.join(names[:8])}{'…' if len(names) > 8 else ''}）" if names else ""
                if c is not None:
                    parts.append(f"{key}={c}{extra}{tail}")
                elif val:
                    parts.append(f"{key}={val}")
        if parts:
            lines.append("      内容：" + " · ".join(parts))
        if s.get("latest"):
            lines.append(f"      最近修改：{s['latest']}")
        if s.get("secrets_present"):
            lines.append(f"      ⚠️ 含凭据类文件（永不导入）：{', '.join(s['secrets_present'])}")
        if s.get("note"):
            lines.append(f"      说明：{s['note']}")
    lines.append("")
    lines.append(f"② 近 {report['days']} 天最活跃的项目（扫描根：{'; '.join(report['roots'])}）")
    if not report["active_projects"]:
        lines.append("  （没找到近期有改动的项目；用 --roots 指定目录再扫）")
    for i, p in enumerate(report["active_projects"], 1):
        lines.append(f"  {i}. {p['path']}")
        lines.append(f"      {p['reason']} · 得分 {p['score']}")
    lines.append("")
    mp = report["machine_prefix"]
    lines.append(f"③ 建议的智能体（机器代号 {mp['prefix']} · 来源：{mp['source']}）")
    for b in report["suggested_bots"]:
        lines.append(f"  · {b['bot']}   工作目录 {b['cwd']}")
    if not report["suggested_bots"]:
        lines.append("  （没有活跃项目就不建议 bot；可手动 --cwd 指定）")
    t = report["targets"]
    lines.append("")
    lines.append("④ 导入目标 profile：" + (f"claude → {t.get('claude')} · codex → {t.get('codex')}" if t.get("all") else t.get("note", "")))
    lines.append("   永不导入：" + "、".join(report["never_imported"]))
    lines.append("")
    lines.append("下一步：python feishu/context_import.py --scan <本报告 json> --profile <目标> [--only id,id] [--skip id]   # 先预览，--apply 才写")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="只读盘点本机已有 AI context 与活跃项目（PLAN-1000）")
    ap.add_argument("--roots", action="append", default=None, help="活跃项目扫描根，可重复；默认 Desktop/Documents/VIBECODING_ROOT")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--exports", action="append", default=None, help="官方导出包所在目录，可重复；默认 ~/Downloads")
    ap.add_argument("--profile", default=None, help="导入目标 profile（不给则取本机默认）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="把 JSON 报告写到这个文件（给 context_import / register --from-scan）")
    args = ap.parse_args(argv)
    report = scan(args.roots, args.days, args.top, args.exports, args.profile)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))
        if args.out:
            print(f"\n报告已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
