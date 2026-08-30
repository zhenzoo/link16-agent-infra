#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex PostToolUse hook: write compact tool progress to the bridge outbox."""
import json
import os
import sys
import time
from pathlib import Path


def _read_stdin_json():
    """Decode hook payload bytes as UTF-8, independent of Windows ANSI locale."""
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read()
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return json.loads(text.lstrip("\ufeff"))


def _label(tool_name, tool_input):
    tool_input = tool_input or {}
    if tool_name in ("Bash", "Shell", "PowerShell"):
        cmd = tool_input.get("command") or tool_input.get("cmd") or ""
        return "🔧 " + (cmd.strip().splitlines()[0][:140] if cmd else tool_name)
    if tool_name in ("apply_patch", "Edit", "Write"):
        command = tool_input.get("command") or ""
        match = None
        if command:
            import re
            match = re.search(r"(?m)^\*\*\* (?:Update|Add|Delete) File: (.+)$", command)
        path = (match.group(1).strip() if match else None) or tool_input.get("path") or tool_input.get("file_path") or ""
        return "✏️ " + (path or "apply_patch")
    return f"🔧 {tool_name}"


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    # app-server-canary has a typed item observer that emits grouped tool
    # milestones.  Keeping this raw PostToolUse producer enabled would double
    # every tool and reintroduce command text into the card.
    if os.environ.get("FEISHU_CODEX_EVENT_STREAM") == "1":
        return
    try:
        inp = _read_stdin_json()
    except Exception:  # noqa: BLE001
        return
    tool_name = inp.get("tool_name")
    if not tool_name:
        return
    rec = {
        "kind": "progress",
        "ts": int(time.time()),
        "session": inp.get("session_id", ""),
        "turn": inp.get("turn_id"),
        "label": _label(tool_name, inp.get("tool_input") or {}),
    }
    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (Path.cwd() / "_autopilot"))
    try:
        route = json.loads((outdir / f"bridge-turn-route-{bot}.json").read_text(encoding="utf-8"))
        if isinstance(route, dict):
            rec["route"] = route
    except (OSError, ValueError):
        pass
    outbox = outdir / f"bridge-outbox-{bot}.jsonl"
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with open(outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    try:                       # PLAN-929：同上。hooks/ 不在 sys.path 上，先把 feishu/ 加进去
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parents[1]))
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001
        pass
    main()
