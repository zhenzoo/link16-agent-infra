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


def _stuck_delivery(bot, turn_key, state_dir):
    """本轮结束前再看一次：之前的回复有没有卡在 outbox 里没发出去。

    本轮最终回复要等模型停下后才由 hook 写出、再由 drainer 发送，模型自己看不到它的送达；
    但在它之前的回复（进度卡、上一轮的回复）此刻应当早已发完。有卡住的就拦一次，让模型
    当场查清再结束；同一轮只拦一次，第二次照常放行。
    """
    try:
        from bridge_userprompt import _delivery_context
        ctx = _delivery_context(bot, state_dir)
    except Exception:  # noqa: BLE001 — 检查挂了不能挡住回合结束
        return ""
    if not ctx.startswith("Delivery check: STUCK"):
        return ""
    if not session_work.hold_for_delivery(bot, turn_key, state_dir=state_dir):
        return ""
    return ctx.replace("Before continuing this turn", "Before ending this turn")


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
    turn_key = str(route.get("turn_key") or "")
    reason = session_work.request_stop_repair(bot, turn_key, state_dir=state_dir)
    if not reason:
        reason = _stuck_delivery(bot, turn_key, state_dir)
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        from bridge_env import force_utf8_std
        force_utf8_std()
    except Exception:  # noqa: BLE001
        pass
    main()
