#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex PostToolUse hook: write compact tool progress to the bridge outbox."""
import json
import os
import sys
import time
from pathlib import Path


def _label(tool_name, tool_input):
    tool_input = tool_input or {}
    if tool_name in ("Bash", "Shell", "PowerShell"):
        cmd = tool_input.get("command") or tool_input.get("cmd") or ""
        return "🔧 " + (cmd.strip().splitlines()[0][:140] if cmd else tool_name)
    if tool_name in ("apply_patch", "Edit", "Write", "MultiEdit"):
        path = tool_input.get("path") or tool_input.get("file_path") or ""
        return "✏️ " + (path or tool_name)
    if tool_name in ("Read", "Grep", "Glob"):
        path = tool_input.get("path") or tool_input.get("pattern") or ""
        return "📖 " + (path or tool_name)
    return f"🔧 {tool_name}"


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    try:
        inp = json.loads(sys.stdin.read().lstrip("\ufeff"))
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
    outbox = outdir / f"bridge-outbox-{bot}.jsonl"
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with open(outbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    main()
