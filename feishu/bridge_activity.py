"""Shared live activity check and silence clock. Waiting time never counts as work.

Each platform in agent_runtime's adapter table answers the same question from its
own structured record: Codex from the bot's app-server, Claude Code from the
pinned transcript, Kimi Code from the bound Wire journal. Never send a prompt or
resume a thread. Unknown state cannot authorize interruption.
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


def _unknown(exc):
    detail = type(exc).__name__
    if isinstance(exc, FileNotFoundError) and exc.filename:
        detail += f"（缺 {Path(exc.filename).name}）"
    elif isinstance(exc, (ValueError, KeyError)) and str(exc):
        detail += f"（{exc}）"
    return {"state": "unknown", "key": None, "reason": f"状态无法确认：{detail}"}


def _tail_records(path, limit=2 * 1024 * 1024):
    """(byte offset, record) for the JSON lines in the last ``limit`` bytes."""
    with Path(path).open("rb") as fh:
        start = max(0, fh.seek(0, os.SEEK_END) - limit)
        fh.seek(start)
        data = fh.read()
    if start:
        cut = data.find(b"\n") + 1                       # drop the partial first line
        start, data = start + cut, data[cut:]
    rows = []
    for line in data.splitlines(keepends=True):
        try:
            rows.append((start, json.loads(line)))
        except ValueError:
            pass
        start += len(line)
    return rows


def _turn_state(key, ended, pending, *, waiting_tools=()):
    if key is None or ended:
        return {"state": "idle", "key": None, "reason": "回合已结束，等待下一步指令"}
    if any(name in waiting_tools for name in pending.values()):
        return {"state": "waiting", "key": None, "reason": "等待用户回答或确认"}
    return {"state": "active", "key": key, "tool_running": bool(pending),
            "reason": "工具尚未结束" if pending else "当前回合执行中"}


def _claude_activity(state_dir, name, bot, inspect_tools):
    """Claude writes each message of the pinned session to its transcript as it happens.

    A turn opens with a real user prompt and closes with an end_turn reply, an
    API-error reply or the interrupt marker; tool_use ids without a tool_result
    are tools still running. Local slash/bash command echoes are not turns.
    """
    pin = json.loads((Path(state_dir) / f"bridge-session-{name}.json").read_text(encoding="utf-8"))
    if not pin.get("jsonl"):
        raise ValueError("会话还没钉住 transcript")
    path = Path(pin["jsonl"])
    key, ended, pending = None, True, {}
    for _offset, rec in _tail_records(path):
        if rec.get("isSidechain") or rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        message = rec.get("message") or {}
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        blocks = [b for b in blocks if isinstance(b, dict)]
        if rec.get("type") == "user":
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for block in results:
                pending.pop(block.get("tool_use_id"), None)
            text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text").lstrip()
            if results or text.startswith(("<command-", "<local-command", "<bash-")):
                continue
            if text.startswith("[Request interrupted by user"):
                ended, pending = True, {}
                continue
            key, ended, pending = (path.name, rec.get("uuid")), False, {}
        elif rec.get("type") == "assistant":
            if rec.get("isApiErrorMessage"):
                ended, pending = True, {}
                continue
            for block in blocks:
                if block.get("type") == "tool_use":
                    pending[block.get("id")] = block.get("name")
            if message.get("stop_reason") in {"end_turn", "stop_sequence"} and not pending:
                ended = True
    return _turn_state(key, ended, pending, waiting_tools={"AskUserQuestion"})


def _kimi_activity(state_dir, name, bot, inspect_tools):
    """Kimi's main-agent Wire journal brackets each turn with turn.prompt / turn.ended."""
    import agent_runtime
    from kimi_native_worker import find_wire

    binding = json.loads((Path(state_dir) / f"bridge-kimi-thread-{name}.json").read_text(encoding="utf-8"))
    if binding.get("closed"):
        return {"state": "idle", "key": None, "reason": "Kimi 会话已关闭"}
    wire = find_wire(agent_runtime.profile_spec(binding["profile"]).home_path, binding["session"])
    key, ended, pending = None, True, {}
    for offset, rec in _tail_records(wire):
        if rec.get("agentId") != "main":
            continue
        kind = rec.get("type")
        if kind == "turn.prompt":
            key, ended, pending = (binding["session"], offset), False, {}
        elif kind == "turn.ended":
            ended, pending = True, {}
        elif kind == "context.append_loop_event":
            event = rec.get("event") or {}
            if event.get("type") == "tool.call":
                pending[event.get("uuid")] = event.get("name")
            elif event.get("type") == "tool.result":
                pending.pop(event.get("parentUuid"), None)
    return _turn_state(key, ended, pending)


def read_activity(state_dir, bot, *, inspect_tools=False):
    """Return only runtime metadata for one roster bot, read the way its platform exposes it."""
    name = bot.get("name") if isinstance(bot, dict) else None
    try:
        if not name:
            raise ValueError("缺少该 bot 的名册记录")
        import agent_runtime
        reader = _READERS[agent_runtime.runtime_spec(bot).name]
        return reader(state_dir, name, bot, inspect_tools)
    except Exception as exc:
        return _unknown(exc)


def _codex_activity(state_dir, name, bot, inspect_tools):
    """Codex answers from the worker's own app-server. Full items only before intervention."""
    rpc = None
    try:
        from codex_app_server_worker import RpcConnection
        url, thread = _observer_endpoint(state_dir, name)
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
        return _unknown(exc)
    finally:
        if rpc:
            rpc.close()


# One reader per platform in agent_runtime's adapter table (a test enforces it).
_READERS = {"codex": _codex_activity, "claude": _claude_activity, "kimi": _kimi_activity}


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
        name = bot["name"] if isinstance(bot, dict) else bot
        activity = read_activity(state_dir, bot)
        activity["minutes"] = self.measure(
            name, activity, progress_time(state_dir, name), time.time() if now is None else now,
        )
        return activity


def confirm_stall(state_dir, bot, activity, *, now=None):
    """Recheck the same turn and tool/output activity immediately before acting."""
    current = read_activity(state_dir, bot, inspect_tools=True)
    now = time.time() if now is None else now
    name = bot["name"] if isinstance(bot, dict) else bot
    confirmed = (current["state"] == "active" and current["key"] == activity["key"]
                 and not current.get("tool_running")
                 and now - progress_time(state_dir, name) >= SILENT_MINUTES * 60)
    return confirmed, current
