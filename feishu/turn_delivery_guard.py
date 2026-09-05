#!/usr/bin/env python3
"""Per-turn automatic-reply guard for proactive Feishu sends."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

import bridge_injection


def route_from_prompt(prompt):
    """Read the last bridge envelope, shared by all runtime producers."""
    matches = re.findall(
        r"\[飞书 [^\]]*?route=(p2a-ext|a2a|p2a)(?:\s+dest=([^\]\s]+))?(?:\s+at=([^\]\s]+))?",
        str(prompt or ""),
    )
    if matches:
        kind, dest, at = matches[-1]
        if kind in ("a2a", "p2a-ext") and dest:
            return {"kind": kind, "dest": dest, "at": at}
    return {"kind": "p2a"}


def route_path(state_dir, bot):
    return Path(state_dir) / f"bridge-turn-route-{bot}.json"


def public_route(route):
    route = route if isinstance(route, dict) else {}
    result = {"kind": route.get("kind") or "p2a"}
    for key in ("dest", "at"):
        if route.get(key):
            result[key] = route[key]
    return result


def read_route(state_dir, bot):
    try:
        row = json.loads(route_path(state_dir, bot).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def activate(state_dir, bot, route, *, session=None, now=None, turn_key=None):
    state_dir = Path(state_dir)
    row = {
        **public_route(route),
        "active": True,
        "turn_key": turn_key or uuid.uuid4().hex,
        "session": str(session or ""),
        "started_at": int(time.time() if now is None else now),
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    with bridge_injection.injection_lock(state_dir, "turn-route", bot):
        bridge_injection.atomic_write_json(route_path(state_dir, bot), row)
    return row


def compare_and_clear(state_dir, bot, turn_key, *, now=None):
    if not turn_key:
        return False
    state_dir = Path(state_dir)
    with bridge_injection.injection_lock(state_dir, "turn-route", bot):
        current = read_route(state_dir, bot)
        if not current or current.get("turn_key") != turn_key or not current.get("active"):
            return False
        current["active"] = False
        current["completed_at"] = int(time.time() if now is None else now)
        bridge_injection.atomic_write_json(route_path(state_dir, bot), current)
    return True


def _p2a_targets(state_dir, bot):
    state_dir = Path(state_dir)
    targets = set()
    for name in (f"bridge-owner-{bot}.json", f"bridge-session-{bot}.json"):
        try:
            row = json.loads((state_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("open_id", "chat_id"):
            if row.get(key):
                targets.add(str(row[key]))
    return targets


def guard_outbound(state_dir, bot, target, *, bridge_session=None, proactive=False):
    """Return guard evidence or raise before any network call."""
    bridge_session = str(bridge_session or "")
    if bridge_session != bot:
        return {"guarded": False, "reason": "outside-bridge-session"}
    if proactive:
        return {"guarded": False, "reason": "proactive-override"}
    route = read_route(state_dir, bot)
    if not route:
        raise RuntimeError("本轮自动回址状态缺失；拒绝主动发送。确需额外通知请加 --proactive")
    if not route.get("active"):
        return {"guarded": False, "reason": "turn-complete", "route": public_route(route)}
    kind = route.get("kind") or "p2a"
    automatic_targets = ({str(route.get("dest"))} if kind in {"p2a-ext", "a2a"}
                         and route.get("dest") else _p2a_targets(state_dir, bot))
    if kind == "p2a" and not automatic_targets:
        raise RuntimeError(
            "本轮私聊自动回址无法解析；拒绝主动发送。确需额外通知请加 --proactive"
        )
    if str(target) in automatic_targets:
        raise RuntimeError(
            "这条会由本轮自动回原处，已阻止重复投递；若确实是额外通知，请加 --proactive"
        )
    return {"guarded": False, "reason": "different-target", "route": public_route(route)}
