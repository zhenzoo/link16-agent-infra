"""Shared live activity check and silence clock. Waiting time never counts as work.

Read the exact bot's existing Codex observer endpoint; never send a prompt or
resume a thread. Unknown/unsupported runtime state cannot authorize interruption.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

import bridge_process

SILENT_MINUTES = 60
_codex_snapshot = (0.0, None)
_CODEX_QUERY = """
$ErrorActionPreference = 'Stop'
try {
  $rows = @(Get-CimInstance Win32_Process -Filter "Name='codex.exe'" -ErrorAction Stop |
    Select-Object ProcessId,ParentProcessId,CommandLine)
  @{ok=$true; processes=$rows} | ConvertTo-Json -Depth 4 -Compress
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""
_LISTEN = re.compile(r"\bapp-server\s+--listen\s+(ws://127\.0\.0\.1:\d+)(?=\s|$)")


def _codex_rows():
    """codex.exe snapshot, cached 10s; same PowerShell budget as bridge_process queries."""
    global _codex_snapshot
    now = time.monotonic()
    if now - _codex_snapshot[0] >= 10 or _codex_snapshot[1] is None:
        rows = bridge_process._envelope(
            bridge_process._powershell(_CODEX_QUERY, bridge_process.QUERY_TIMEOUTS[0])).get("processes")
        if not isinstance(rows, list):
            raise ValueError("进程状态未知")
        _codex_snapshot = (now, rows)
    return _codex_snapshot[1]


def _observer_endpoint(state_dir, bot):
    """The bot's own app-server RPC endpoint and current thread.

    The worker records its PID in the startup record and is the only parent of
    `codex app-server --listen ws://127.0.0.1:<port>`. The observer now reads a
    private TUI-gateway `/events` stream and cannot answer RPC, so it is not used.
    """
    state_dir = Path(state_dir)
    ready = json.loads((state_dir / f"bridge-codex-app-ready-{bot}.json").read_text(encoding="utf-8"))
    start = json.loads((state_dir / f"bridge-codex-app-startup-{bot}.json").read_text(encoding="utf-8"))
    thread = ready["thread_id"]
    if start.get("thread_id") != thread or start.get("startup_id") != ready.get("startup_id"):
        raise ValueError("启动记录与当前会话不一致")
    worker = int(start["worker_pid"])
    urls = {m.group(1) for row in _codex_rows() if row.get("ParentProcessId") == worker
            for m in [_LISTEN.search(row.get("CommandLine") or "")] if m}
    if len(urls) != 1:
        raise ValueError("无法唯一匹配当前 bot 的 app-server")
    return urls.pop(), thread


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
