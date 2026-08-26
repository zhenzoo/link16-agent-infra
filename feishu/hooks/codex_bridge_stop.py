#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy Codex Stop hook: write the final assistant message to the outbox.

Codex exposes `last_assistant_message` in the Stop hook payload. Use that stable
hook contract instead of parsing Codex JSONL transcripts. App-server workers
use their typed final item instead and suppress this hook.
"""
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


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        return
    try:
        inp = _read_stdin_json()
    except Exception:  # noqa: BLE001
        return

    if os.environ.get("FEISHU_CODEX_EVENT_STREAM") == "1":
        # The app-server observer receives the authoritative typed final item.
        # Suppress both root and child Stop hooks so account-specific hook
        # configuration cannot cause either final loss or duplicate delivery.
        return

    outdir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or (Path.cwd() / "_autopilot"))
    text = (inp.get("last_assistant_message") or "").strip()
    if not text:
        return
    if text == "LINK16_APP_SERVER_READY":
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
    try:                       # PLAN-929：同上。hooks/ 不在 sys.path 上，先把 feishu/ 加进去
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).resolve().parents[1]))
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001
        pass
    main()
