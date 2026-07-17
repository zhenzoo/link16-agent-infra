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

    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (Path.cwd() / "_autopilot"))
    if os.environ.get("FEISHU_CODEX_EVENT_STREAM") == "1":
        # Subagent hooks inherit the root environment.  Only the app-server
        # thread selected by the Link16 wrapper may publish the user-facing
        # final answer; child turns remain collab milestones.
        try:
            root = json.loads(
                (outdir / f"bridge-codex-app-thread-{bot}.json").read_text(encoding="utf-8")
            ).get("thread_id")
        except (OSError, ValueError, AttributeError):
            root = None
        if root and inp.get("session_id") != root:
            return

    text = (inp.get("last_assistant_message") or "").strip()
    if not text:
        return
    if os.environ.get("FEISHU_CODEX_EVENT_STREAM") == "1" and text == "LINK16_APP_SERVER_READY":
        return

    text += "\n\n---\n✅ 已完成"
    rec = {
        "kind": "answer",
        "ts": int(time.time()),
        "session": inp.get("session_id", ""),
        "anchor": inp.get("turn_id"),
        "text": text,
    }
    # UserPromptSubmit parses the route envelope from this exact turn. Pin the
    # route into the answer record before the asynchronous drainer sees a later
    # turn, matching the Claude bridge's per-turn routing guarantee.
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
    main()
