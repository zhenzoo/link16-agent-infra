#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install Feishu bridge hooks into CODEX_HOME/hooks.json.

Default mode is dry-run. Use --write to merge the bridge hooks while preserving
existing Codex hooks. This is intentionally separate from bridge startup because
Codex discovers hooks from CODEX_HOME/project config, not from a per-process
`--settings` file like Claude Code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    if raw:
        return Path(os.path.expanduser(raw))
    personal = Path.home() / ".codex-personal"
    return personal if personal.exists() else Path.home() / ".codex"


def _hook_cmd(path: Path) -> str:
    return f'python "{path.as_posix()}"'


def bridge_hooks(repo: Path) -> dict:
    stop = repo / "feishu" / "hooks" / "codex_bridge_stop.py"
    post = repo / "feishu" / "hooks" / "codex_bridge_posttool.py"
    route = repo / "feishu" / "hooks" / "bridge_userprompt.py"
    return {
        "Stop": [{
            "hooks": [{"type": "command", "command": _hook_cmd(stop), "timeout": 10}],
        }],
        "PostToolUse": [{
            # Current Codex canonical hook surfaces. Edit/Write are documented
            # aliases for apply_patch matchers; input still reports apply_patch.
            "matcher": "Bash|apply_patch|Edit|Write|mcp__.*",
            "hooks": [{"type": "command", "command": _hook_cmd(post), "timeout": 5}],
        }],
        "UserPromptSubmit": [{
            "hooks": [{"type": "command", "command": _hook_cmd(route), "timeout": 5}],
        }],
    }


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_bridge_command(command: str | None) -> bool:
    """Recognize both the old XHS location and the Link16-owned hooks.

    This lets the installer replace a legacy installation instead of appending
    a second copy that would emit duplicate progress/answer records.
    """
    if not command:
        return False
    normalized = command.replace("\\", "/").lower()
    return any(name in normalized for name in (
        "/hooks/codex_bridge_stop.py",
        "/hooks/codex_bridge_posttool.py",
        "/hooks/bridge_userprompt.py",
    ))


def _without_bridge_commands(entries: list) -> list:
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            cleaned.append(entry)
            continue
        kept = [
            hook for hook in (entry.get("hooks") or [])
            if not (isinstance(hook, dict) and _is_bridge_command(hook.get("command")))
        ]
        if kept:
            cleaned.append({**entry, "hooks": kept})
    return cleaned


def merge_hooks(existing: dict, additions: dict) -> dict:
    out = dict(existing or {})
    hooks = dict(out.get("hooks") or {})
    # Remove stale/previous bridge definitions across every event first while
    # preserving unrelated hooks such as the user's completion sound.
    for event, entries in list(hooks.items()):
        hooks[event] = _without_bridge_commands(list(entries or []))
    for event, entries in additions.items():
        current = list(hooks.get(event) or [])
        seen = {
            h.get("command")
            for e in current
            for h in (e.get("hooks") or [])
            if isinstance(h, dict)
        }
        for entry in entries:
            new_hooks = []
            for hook in entry.get("hooks") or []:
                cmd = hook.get("command")
                if cmd and cmd not in seen:
                    new_hooks.append(hook)
                    seen.add(cmd)
            if new_hooks:
                current.append({**entry, "hooks": new_hooks})
        hooks[event] = current
    out["hooks"] = hooks
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codex-home", default=str(default_codex_home()))
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--write", action="store_true", help="write merged hooks.json; default is dry-run")
    args = ap.parse_args()

    codex_home = Path(os.path.expanduser(args.codex_home))
    target = codex_home / "hooks.json"
    repo = Path(args.repo).resolve()
    additions = bridge_hooks(repo)
    missing = [
        hook["command"]
        for entries in additions.values()
        for entry in entries
        for hook in entry.get("hooks") or []
        if not Path(hook["command"].split('"', 2)[1]).exists()
    ]
    if missing:
        raise SystemExit("bridge hook script missing: " + ", ".join(missing))
    merged = merge_hooks(load_json(target), additions)
    text = json.dumps(merged, ensure_ascii=False, indent=2)

    if not args.write:
        print(f"[dry-run] would write: {target}")
        print(text)
        return

    codex_home.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, target)
    print(f"wrote {target}")


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
