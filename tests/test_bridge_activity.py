"""Regression: completed Codex turn + stale Working display must never be nudged."""
import ast
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_activity as a
import bridge_watchdog as w
import codex_app_server_worker as worker


def active(turn="turn-1"):
    return {"state": "active", "key": ("thread-1", turn), "reason": "执行中"}


@pytest.mark.parametrize("state", ["idle", "waiting", "unknown"])
def test_non_active_never_counts_and_resume_starts_fresh(state):
    clock = a.SilenceClock()
    clock.measure("bot", active(), 0, 10000)
    assert clock.measure("bot", {"state": state}, 0, 18000) is None
    assert clock.measure("bot", active(), 0, 28000) == 0


def test_hour_boundary_old_idle_time_new_turn_and_new_progress():
    clock = a.SilenceClock()
    assert clock.measure("bot", active(), 0, 10000) == 0
    assert clock.measure("bot", active(), 0, 13599) < a.SILENT_MINUTES
    assert clock.measure("bot", active(), 0, 13600) == a.SILENT_MINUTES == 60
    assert clock.measure("bot", active("turn-2"), 0, 18000) == 0
    assert clock.measure("bot", active("turn-2"), 20000, 21000) == 1000 / 60
    assert clock.measure("other-bot", active("turn-2"), 0, 21000) == 0


@pytest.fixture
def rpc(monkeypatch):
    calls = []
    closed = []
    live = {"id": "thread-1", "status": {"type": "active", "activeFlags": []}}
    turn = {"id": "turn-1", "status": "inProgress", "items": []}
    live["turns"] = [turn]
    class FakeRpc:
        def __init__(self, url):
            pass
        def request(self, method, params, **kwargs):
            calls.append((method, params))
            return {"thread": live} if method == "thread/read" else {"data": [turn]}
        def notify(self, method):
            calls.append((method, {}))
        def close(self):
            closed.append(True)
    monkeypatch.setattr(worker, "RpcConnection", FakeRpc)
    monkeypatch.setattr(a, "_observer_endpoint", lambda *args: ("ws://127.0.0.1:1234", "thread-1"))
    return SimpleNamespace(live=live, turn=turn, calls=calls, closed=closed)


@pytest.mark.parametrize("status,expected", [
    ({"type": "idle"}, "idle"),
    ({"type": "active", "activeFlags": ["waitingOnApproval"]}, "waiting"),
    ({"type": "active", "activeFlags": ["waitingOnUserInput"]}, "waiting"),
    ({"type": "notLoaded"}, "unknown"),
    ({"type": "active", "activeFlags": ["futureFlag"]}, "unknown"),
])
def test_runtime_status_has_priority_over_working_display(rpc, status, expected):
    rpc.live["status"] = status
    assert a.read_activity("state", "bot")["state"] == expected
    assert all(method in {"initialize", "initialized", "thread/read"} for method, _ in rpc.calls)
    assert rpc.closed


def test_active_probe_is_read_only_and_omits_history(rpc):
    assert a.read_activity("state", "bot")["key"] == ("thread-1", "turn-1")
    calls = dict(rpc.calls)
    assert calls["thread/read"]["includeTurns"] is False
    assert calls["thread/turns/list"]["limit"] == 1
    assert calls["thread/turns/list"]["itemsView"] == "notLoaded"
    assert set(calls) == {"initialize", "initialized", "thread/read", "thread/turns/list"}


def test_mismatched_thread_or_completed_turn_is_unknown(rpc):
    rpc.live["id"] = "another-thread"
    assert a.read_activity("state", "bot")["state"] == "unknown"
    rpc.live["id"] = "thread-1"
    rpc.turn["status"] = "completed"
    assert a.read_activity("state", "bot")["state"] == "unknown"


def test_endpoint_is_the_workers_own_app_server_not_the_events_mirror(tmp_path, monkeypatch):
    # 2026-09-24: observers run `--url <gateway>/events --startup-id`, which cannot
    # answer RPC; every Codex bot was reported "ValueError". Use the worker's child.
    (tmp_path / "bridge-codex-app-ready-bot.json").write_text(json.dumps({"thread_id": "thread-1", "startup_id": "s1"}))
    (tmp_path / "bridge-codex-app-startup-bot.json").write_text(
        json.dumps({"thread_id": "thread-1", "startup_id": "s1", "worker_pid": 10}))
    rows = [
        {"ProcessId": 11, "ParentProcessId": 10,
         "CommandLine": "codex.exe --dangerously-bypass-hook-trust app-server --listen ws://127.0.0.1:1234"},
        {"ProcessId": 12, "ParentProcessId": 10, "CommandLine": "codex.exe --remote ws://127.0.0.1:9999"},
        {"ProcessId": 21, "ParentProcessId": 20,
         "CommandLine": "codex.exe app-server --listen ws://127.0.0.1:5555"},
    ]
    budgets = []
    monkeypatch.setattr(a, "_codex_snapshot", (0, None))
    monkeypatch.setattr(a.bridge_process, "_powershell", lambda script, timeout: budgets.append(timeout))
    monkeypatch.setattr(a.bridge_process, "_envelope", lambda result: {"ok": True, "processes": rows})
    assert a._observer_endpoint(tmp_path, "bot") == ("ws://127.0.0.1:1234", "thread-1")
    assert budgets and budgets[0] >= 15          # the old 3s budget timed out under load
    rows.append({"ProcessId": 13, "ParentProcessId": 10,
                 "CommandLine": "codex.exe app-server --listen ws://127.0.0.1:4321"})
    assert a.read_activity(tmp_path, "bot")["state"] == "unknown"   # ambiguous → never guess
    rows.pop()
    (tmp_path / "bridge-codex-app-startup-bot.json").write_text(
        json.dumps({"thread_id": "stale-thread", "startup_id": "s0", "worker_pid": 10}))
    assert a.read_activity(tmp_path, "bot")["state"] == "unknown"


@pytest.mark.parametrize("item_type", ["commandExecution", "mcpToolCall", "dynamicToolCall", "fileChange"])
def test_long_tool_is_never_interrupted(rpc, monkeypatch, item_type):
    monkeypatch.setattr(a, "progress_time", lambda *args: 0)
    rpc.turn["items"] = [{"type": item_type, "status": "inProgress"}]
    confirmed, result = a.confirm_stall("state", "bot", active(), now=20000)
    assert confirmed is False
    assert result["tool_running"] is True


def test_recheck_rejects_new_turn_new_output_and_waiting(rpc, monkeypatch):
    monkeypatch.setattr(a, "progress_time", lambda *args: 0)
    assert a.confirm_stall("state", "bot", active(), now=20000)[0]
    assert not a.confirm_stall("state", "bot", active("old-turn"), now=20000)[0]
    monkeypatch.setattr(a, "progress_time", lambda *args: 19999)
    assert not a.confirm_stall("state", "bot", active(), now=20000)[0]
    rpc.live["status"] = {"type": "idle"}
    assert not a.confirm_stall("state", "bot", active(), now=20000)[0]


@pytest.mark.parametrize("state,minutes,confirmed,esc_count", [
    ("idle", None, False, 0), ("waiting", None, False, 0), ("unknown", None, False, 0),
    ("active", 59.99, True, 0), ("active", 60, True, 1), ("active", 60, False, 0),
])
def test_watchdog_real_loop_respects_state_hour_and_recheck(
    tmp_path, monkeypatch, state, minutes, confirmed, esc_count,
):
    class Done(BaseException):
        pass
    pokes, alerts = [], []
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "ALERTS_PATH", tmp_path / "alerts.json")
    monkeypatch.setattr(w, "scan_topology", lambda: ({"pty": "workspace"}, ["pty"]))
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"pty": "bot"})
    monkeypatch.setattr(w, "read_pane", lambda *args: "• Working (2h • esc to interrupt)")
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "bot"}])
    monkeypatch.setattr(w, "_profile_of", lambda *args: None)
    monkeypatch.setattr(w, "at_picker", lambda *args: False)
    monkeypatch.setattr(w.agent_quota, "collect", lambda: [])
    monkeypatch.setattr(w, "codex_dead_turn", lambda *args: (None, False))
    monkeypatch.setattr(w, "bridge_alive", lambda: True)
    monkeypatch.setattr(w, "_heartbeat_write", lambda *args: None)
    monkeypatch.setattr(w, "interrupt_pane", lambda *args: pokes.append("esc") or True)
    monkeypatch.setattr(w, "nudge_pane", lambda *args: pokes.append("nudge") or True)
    monkeypatch.setattr(w, "notify", lambda *args: alerts.append(args) or True)
    monkeypatch.setattr(a.SilenceClock, "sample", lambda *args: {
        "state": state, "key": ("t", "1"), "minutes": minutes, "reason": "test",
    })
    monkeypatch.setattr(a, "confirm_stall", lambda *args: (confirmed, {"state": "idle", "key": None}))
    ticks = 0
    def sleep(seconds):
        nonlocal ticks
        if seconds != 3:
            ticks += 1
            if ticks == 3:
                raise Done()
    monkeypatch.setattr(w.time, "sleep", sleep)
    with pytest.raises(Done):
        w.cmd_run()
    assert pokes.count("esc") == pokes.count("nudge") == esc_count
    assert sum(args[1] == "stall_nudged" for args in alerts) == esc_count


@pytest.mark.parametrize("state,picker,should_send", [
    ("active", None, True), ("idle", None, False), ("waiting", None, False),
    ("unknown", None, False), ("active", {"questions": ["choose"]}, False),
])
def test_bridge_silence_alert_uses_same_clock(monkeypatch, state, picker, should_send):
    # Execute the actual nested callback with isolated I/O, without starting a bridge.
    source = Path(w.__file__).with_name("feishu_bridge.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "_silent_escalate")
    sent = []
    async def send(*args):
        sent.append(args)
    clock = a.SilenceClock()
    clock.measure("bot", active(), 0, 10000)
    monkeypatch.setattr(a, "read_activity", lambda *args: {**active(), "state": state})
    monkeypatch.setattr(a, "progress_time", lambda *args: 0)
    monkeypatch.setattr(a.time, "time", lambda: 13600)
    context = dict(asyncio=asyncio, time=a.time, bridge_activity=a, _silence=clock,
                   bridge_outbox=SimpleNamespace(picker_load=lambda *args: picker),
                   ad="state", bname="bot", _silent_alerted={"at": 0}, blog=lambda *args: None,
                   mirror_target=lambda *args: "owner", card_send=send, ch=None)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bridge-callback", "exec"), context)
    asyncio.run(context["_silent_escalate"]("test"))
    assert bool(sent) is should_send
