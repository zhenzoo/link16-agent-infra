#!/usr/bin/env python3
"""Synchronous Stop gate for the Link16 feishu-workline receipt.

The first missing receipt continues the model with one repair instruction.  A
second miss is converted into a visible red failure workline so the session can
finish without an infinite Stop loop or a silently untitled card.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import session_work  # noqa: E402
import turn_delivery_guard  # noqa: E402


def _read_stdin_json():
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read()
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    return json.loads(text.lstrip("\ufeff")) if text.strip() else {}


def main():
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot or not session_work.enabled():
        return
    try:
        _inp = _read_stdin_json()
    except Exception:  # noqa: BLE001
        _inp = {}
    from bridge_env import nested_agent
    if nested_agent(_inp):
        return  # bot 里再起的智能体不是 bot 本身，不能替 bot 判工作行失败
    state_dir = Path(os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR") or
                     (Path(__file__).resolve().parents[2] / "_autopilot"))
    route = turn_delivery_guard.read_route(state_dir, bot)
    if not isinstance(route, dict) or route.get("workline_gate") != session_work.GATE_CONTRACT:
        return
    reason = session_work.request_stop_repair(
        bot, str(route.get("turn_key") or ""), state_dir=state_dir,
    )
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        from bridge_env import force_utf8_std
        force_utf8_std()
    except Exception:  # noqa: BLE001
        pass
    main()
