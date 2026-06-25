#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex Stop hook: write the final assistant message to the bridge outbox.

Codex exposes `last_assistant_message` in the Stop hook payload. Use that stable
hook contract instead of parsing Codex JSONL transcripts.
"""
import json
import os
import sys
import time
from pathlib import Path


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    try:
        inp = json.loads(sys.stdin.read().lstrip("\ufeff"))
    except Exception:  # noqa: BLE001
        return

    text = (inp.get("last_assistant_message") or "").strip()
    if not text:
        return

    text += "\n\n---\n✅ 已完成"
    rec = {
        "kind": "answer",
        "ts": int(time.time()),
        "session": inp.get("session_id", ""),
        "anchor": inp.get("turn_id"),
        "text": text,
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
