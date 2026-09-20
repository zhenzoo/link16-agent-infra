"""Shared live activity check and silence clock. Waiting time never counts as work.

Read the exact bot's existing Codex observer endpoint; never send a prompt or
resume a thread. Unknown/unsupported runtime state cannot authorize interruption.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import time

import bridge_process

SILENT_MINUTES = 60
_process_snapshot = (0.0, None)


def _observer_endpoint(state_dir, bot):
    global _process_snapshot
    ready = json.loads((Path(state_dir) / f"bridge-codex-app-ready-{bot}.json").read_text(encoding="utf-8"))
    thread = ready["thread_id"]
    now = time.monotonic()
    if now - _process_snapshot[0] >= 10 or _process_snapshot[1] is None:
        _process_snapshot = (now, bridge_process.query_processes(timeouts=(3,)))
    rows = _process_snapshot[1]
    if rows is None:
        raise ValueError("进程状态未知")
    matches = []
    script = Path(__file__).with_name("codex_app_server_worker.py").resolve()
    for row in rows:
        args = [x.strip('"\'') for x in shlex.split(row["CommandLine"], posix=False)]
        if "observe" not in args:
            continue
        i = args.index("observe")
        if i < 2 or Path(args[i - 1]).resolve() != script:
            continue
        flags = args[i + 1:]
        def flag(name):
            return flags[flags.index(name) + 1] if name in flags else None
        if flag("--bot") == bot and flag("--thread") == thread:
            url = flag("--url") or ""
            if re.fullmatch(r"ws://127\.0\.0\.1:\d+", url):
                matches.append(url)
    if len(matches) != 1:
        raise ValueError("无法唯一匹配当前 bot 的观察连接")
    return matches[0], thread


def read_activity(state_dir, bot, *, inspect_tools=False):
    """Return only runtime metadata. Full items are read only before intervention."""
    rpc = None
    try:
        from codex_app_server_worker import RpcConnection
        url, thread = _observer_endpoint(state_dir, bot)
        rpc = RpcConnection(url)
        rpc.request("initialize", {
            "clientInfo": {"name": "link16-activity", "version": "1"},
            "capabilities": {"experimentalApi": True},
        }, timeout=5)
        rpc.notify("initialized")
        data = rpc.request("thread/read", {"threadId": thread, "includeTurns": inspect_tools}, timeout=5)
        live = data.get("thread") or {}
        if live.get("id") != thread:
            raise ValueError("会话标识不匹配")
        status = live.get("status") or {}
        if status.get("type") == "idle":
            return {"state": "idle", "key": None, "reason": "回合已结束，等待下一步指令"}
        if status.get("type") != "active":
            raise ValueError("后台未确认执行中")
        flags = status.get("activeFlags") or []
        if any(x in flags for x in ("waitingOnApproval", "waitingOnUserInput", "waitingOnUser")):
            return {"state": "waiting", "key": None, "reason": "等待用户回答或确认"}
        if flags:
            raise ValueError("未识别的执行状态")
        turns = live.get("turns") if inspect_tools else rpc.request("thread/turns/list", {
            "threadId": thread, "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded",
        }, timeout=5).get("data")
        turn = (turns or [{}])[-1]
        if not turn.get("id") or turn.get("status") != "inProgress":
            raise ValueError("当前回合状态与后台状态不一致")
        busy = any(
            item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
                                 "collabAgentToolCall"}
            and item.get("status") not in {"completed", "failed", "declined"}
            for item in turn.get("items", [])
        )
        return {"state": "active", "key": (thread, turn["id"]),
                "tool_running": busy, "reason": "工具尚未结束" if busy else "当前回合执行中"}
    except Exception as exc:
        return {"state": "unknown", "key": None, "reason": f"状态无法确认：{type(exc).__name__}"}
    finally:
        if rpc:
            rpc.close()


def progress_time(state_dir, bot):
    try:
        return os.path.getmtime(Path(state_dir) / f"bridge-outbox-{bot}.jsonl")
    except OSError:
        return 0.0


class SilenceClock:
    """Consecutive observed active time, scoped to a bot and its current turn.

    Starting/restarting this monitor starts a fresh observation window. An idle,
    waiting or unknown observation clears it; transport polling itself is never
    progress. New output advances the clock without changing the turn identity.
    """
    def __init__(self):
        self.active = {}

    def reset(self, bot):
        self.active.pop(bot, None)

    def measure(self, bot, activity, last_progress, now):
        if activity["state"] != "active":
            self.reset(bot)
            return None
        key = activity["key"]
        previous, since = self.active.get(bot, (None, now))
        if key != previous:
            since = now
        since = min(now, max(since, last_progress))
        self.active[bot] = key, since
        return max(0.0, (now - since) / 60)

    def sample(self, state_dir, bot, *, now=None):
        activity = read_activity(state_dir, bot)
        activity["minutes"] = self.measure(
            bot, activity, progress_time(state_dir, bot), time.time() if now is None else now,
        )
        return activity


def confirm_stall(state_dir, bot, activity, *, now=None):
    """Recheck the same turn and tool/output activity immediately before acting."""
    current = read_activity(state_dir, bot, inspect_tools=True)
    now = time.time() if now is None else now
    confirmed = (current["state"] == "active" and current["key"] == activity["key"]
                 and not current.get("tool_running")
                 and now - progress_time(state_dir, bot) >= SILENT_MINUTES * 60)
    return confirmed, current
