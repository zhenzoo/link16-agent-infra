"""Explicit close/new must release the bot seat even after its workspace is gone."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

FEISHU = Path(__file__).resolve().parents[1] / "feishu"
sys.path.insert(0, str(FEISHU))
import bridge_injection as locks
import feishu_bridge as bridge
import kimi_native_worker as worker
import kimi_process as process


def row(pid, parent=0, birth="100", *, bot="target", state=None, script=None):
    argv = [sys.executable, str(script or process.WORKER), "--bot", bot,
            "--cwd", str(Path.cwd()), "--state-dir", str(state or Path.cwd())]
    return dict(pid=pid, ppid=parent, birth=birth, name="python.exe", argv=argv)


def test_exact_ownership_not_substrings_or_latest_pid(tmp_path):
    good = row(100, state=tmp_path)
    assert process.owned(good, "target", tmp_path)
    for bad in [row(100, bot="target-other", state=tmp_path),
                row(100, state=tmp_path / "another"),
                row(100, state=tmp_path, script=tmp_path / "kimi_native_worker.py"),
                {**good, "argv": [sys.executable, "-c", " ".join(good["argv"][1:])]},
                {**good, "argv": good["argv"] + ["--bot", "target"]}]:
        assert not process.owned(bad, "target", tmp_path)
    # Actual Windows CIM command line, including a quoted path containing spaces.
    win = {**good, "command": subprocess.list2cmdline(good["argv"])}
    win.pop("argv")
    assert process.owned(win, "target", tmp_path)


def test_descendants_reject_children_older_than_reused_parent():
    root = row(100, birth="200")
    selected = process.descendants([row(101, 100, "150"), row(102, 100, "201"),
                                    row(103, 102, "202")], {100: root})
    assert set(selected) == {100, 102, 103}


def test_inventory_failure_never_means_no_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "inventory", Mock(side_effect=process.Error("unknown")))
    with pytest.raises(process.Error, match="unknown"):
        with process.KimiClose("target", tmp_path):
            pytest.fail("unknown inventory must prevent any close")


def test_foreign_bot_nested_under_target_prevents_any_close(monkeypatch, tmp_path):
    rows = [row(100, state=tmp_path), row(101, 100, "101", bot="other", state=tmp_path)]
    monkeypatch.setattr(process, "inventory", lambda: rows)
    pin = Mock()
    monkeypatch.setattr(process, "PinnedProcess", pin)
    with pytest.raises(process.Error, match="其他 bot"):
        with process.KimiClose("target", tmp_path):
            pytest.fail("must reject before window close")
    pin.assert_not_called()


def test_reused_identity_stops_capture(monkeypatch, tmp_path):
    first = row(100, state=tmp_path)
    fake = Mock()
    monkeypatch.setattr(process, "inventory", lambda: [first])
    monkeypatch.setattr(process, "PinnedProcess", lambda _: fake)
    with process.KimiClose("target", tmp_path) as close:
        with pytest.raises(process.Error, match="身份已变化"):
            close._capture([{**first, "birth": "200"}])
    fake.terminate.assert_not_called()
    fake.close.assert_called_once()


def test_lock_still_owned_does_not_report_success(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "inventory", lambda: [])
    class Busy:
        def __init__(self, *a, **kw): pass
        def __enter__(self): raise TimeoutError()
        def __exit__(self, *a): pass
    monkeypatch.setattr(process, "ProcessFileLock", Busy)
    with process.KimiClose("target", tmp_path) as close:
        with pytest.raises(process.Error, match="未释放"):
            close.finish()


@pytest.fixture
def isolated_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bridge.agent_runtime, "runtime_name", lambda bot: bot["agent"])
    monkeypatch.setattr(bridge.session_work, "clear", Mock())
    monkeypatch.setattr(bridge.agent_runtime, "reset_account", Mock())
    return tmp_path


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
@pytest.mark.parametrize("create", [False, True])
def test_close_and_new_order_for_every_runtime(isolated_bridge, monkeypatch, runtime, create):
    path = isolated_bridge
    bot = {"name": "target", "agent": runtime}
    bridge.save_session("target", {"workspace_id": "ws", "pty": "pty", "agent": runtime})
    pointer = path / "bridge-kimi-thread-target.json"
    pointer.write_text('{"session":"old"}', encoding="utf-8")
    events = []
    monkeypatch.setattr(bridge.wmux_session, "close", lambda ws, **kw: events.append(("window", kw)))
    class Recovery:
        def __init__(self, *a): pass
        def __enter__(self): events.append(("capture", {})); return self
        def __exit__(self, *a): pass
        def finish(self, **kw): events.append(("release", {})); return {"reaped_pids": [10], "locks_released": ["kimi-worker", "observer"]}
    monkeypatch.setattr(bridge.kimi_process, "KimiClose", Recovery)
    def spawn(b):
        assert bridge.load_session("target") is None
        assert json.loads(pointer.read_text()) == {"closed": True}
        if runtime == "kimi": assert "release" in [x[0] for x in events]
        events.append(("spawn", {}))
        return ("new", "new-pty", True, None)
    monkeypatch.setattr(bridge, "_ensure_session_unlocked", spawn)
    result = bridge.replace_session(bot, {}, create=create)
    assert result["had_session"]
    assert next(x[1] for x in events if x[0] == "window") == {"force": True}
    assert [x[0] for x in events] == (["capture", "window", "release"] if runtime == "kimi" else ["window"]) + (["spawn"] if create else [])


def test_failed_release_keeps_pointer_and_never_creates(isolated_bridge, monkeypatch):
    bridge.save_session("target", {"workspace_id": "old"})
    monkeypatch.setattr(bridge, "_close_session_unlocked", Mock(side_effect=process.Error("still locked")))
    create = Mock()
    monkeypatch.setattr(bridge, "_ensure_session_unlocked", create)
    with pytest.raises(process.Error):
        bridge.replace_session({"name": "target"}, {}, create=True)
    assert bridge.load_session("target")["workspace_id"] == "old"
    create.assert_not_called()
    bridge.agent_runtime.reset_account.assert_not_called()


def test_new_serializes_external_ensure_until_old_seat_is_released(isolated_bridge, monkeypatch):
    entered, release, outsider_started = threading.Event(), threading.Event(), threading.Event()
    def closing(bot):
        entered.set()
        assert release.wait(5)
        return {"had_session": True}
    monkeypatch.setattr(bridge, "_close_session_unlocked", closing)
    calls = []
    monkeypatch.setattr(bridge, "_ensure_session_unlocked", lambda bot: calls.append(threading.current_thread().name))
    errors = []
    def run_new():
        try: bridge.replace_session({"name": "target"}, {}, create=True)
        except Exception as e: errors.append(e)
    def run_ensure():
        outsider_started.set()
        try: bridge.ensure_session({"name": "target"})
        except Exception as e: errors.append(e)
    a = threading.Thread(target=run_new, name="new")
    b = threading.Thread(target=run_ensure, name="external")
    a.start()
    try:
        assert entered.wait(5)
        b.start(); assert outsider_started.wait(5)
        time.sleep(.15)
        assert not calls
    finally:
        release.set(); a.join(5)
        if b.ident: b.join(5)
    assert not errors
    assert calls == ["new", "external"]


def test_busy_attempt_keeps_owner_and_old_owner_cannot_overwrite_attempt(tmp_path):
    old = worker.StartupProgress(tmp_path, "target", "kp", tmp_path, "old")
    new = worker.StartupProgress(tmp_path, "target", "kp2", tmp_path, "new")
    old.update("worker_started", "old", begin=True)
    with old.claim(locks.ProcessFileLock(locks.lock_path(tmp_path, "kimi-worker", "target"))):
        old.update("ready", "old owner")
        new.update("worker_started", "new", begin=True)
        with pytest.raises(TimeoutError):
            with new.claim(locks.ProcessFileLock(locks.lock_path(tmp_path, "kimi-worker", "target"), timeout=0)):
                pytest.fail("already owned")
        new.update("failed", "busy")
        old.update("session_ready", "late binding")
        assert json.loads(old.path.read_text())["startup_id"] == "old"
        assert json.loads(old.attempt_path.read_text())["startup_id"] == "new"
        assert json.loads(old.attempt_path.read_text())["stage"] == "failed"


def test_startup_reader_requires_exact_attempt(isolated_bridge, monkeypatch):
    monkeypatch.setattr(bridge.agent_runtime, "runtime_spec", lambda _: SimpleNamespace(name="kimi"))
    monkeypatch.setattr(bridge.agent_runtime, "profile_name", lambda *a, **kw: "kp")
    p = worker.StartupProgress(isolated_bridge, "target", "kp", isolated_bridge, "attempt")
    p.update("failed", "busy", begin=True)
    bot = {"name": "target", "_startup_id": "other"}
    assert bridge._kimi_startup_record(bot) == {}
    bot["_startup_id"] = "attempt"
    assert bridge._kimi_startup_record(bot)["stage"] == "failed"


@pytest.mark.skipif(os.name != "nt" and not hasattr(os, "pidfd_open"), reason="requires pinned OS processes")
@pytest.mark.parametrize("create", [False, True])
@pytest.mark.parametrize("window_exists", [False, True])
def test_real_orphan_tree_released_without_touching_other_bot(isolated_bridge, monkeypatch, create, window_exists):
    root = isolated_bridge
    script = root / "fixture worker.py"
    script.write_text(f'''import sys,time,argparse,subprocess,json
from pathlib import Path
sys.path.insert(0, {str(FEISHU)!r})
from bridge_injection import ProcessFileLock,lock_path
p=argparse.ArgumentParser()
for key in ['bot','cwd','state-dir']: p.add_argument('--'+key,required=True)
p.add_argument('--observe-only',action='store_true')
a=p.parse_args(); state=Path(a.state_dir)
with ProcessFileLock(lock_path(state,'observer' if a.observe_only else 'kimi-worker',a.bot)):
 if not a.observe_only:
  child=subprocess.Popen([sys.executable,__file__,*sys.argv[1:],'--observe-only'])
  (state/(a.bot+'.ready')).write_text(json.dumps({{'parent':__import__('os').getpid(),'child':child.pid}}))
 while True: time.sleep(.1)
''', encoding="utf-8")
    monkeypatch.setattr(process, "WORKER", script.resolve())
    procs = []
    try:
        for name in ["target", "other"]:
            p = subprocess.Popen([sys.executable, str(script), "--bot", name,
                                  "--cwd", str(root), "--state-dir", str(root)])
            procs.append(p)
        deadline = time.monotonic() + 10
        while not all((root / (n + ".ready")).exists() for n in ["target", "other"]):
            assert time.monotonic() < deadline
            time.sleep(.05)
        # Missing workspace reproduces production. Closing a surviving window
        # removes only the parent, so recovery must retain the child's identity.
        bridge.save_session("target", {"chat_id": "owner", **({"workspace_id": "ws"} if window_exists else {})})
        pointer = root / "bridge-kimi-thread-target.json"
        pointer.write_text('{"closed":true}')
        def remove_parent(ws, **kw):
            assert ws == "ws" and kw == {"force": True}
            procs[0].terminate()
            procs[0].wait(timeout=5)
        close_window = Mock(side_effect=remove_parent if window_exists else AssertionError("missing workspace must not call wmux"))
        monkeypatch.setattr(bridge.wmux_session, "close", close_window)
        def new_session(bot):
            assert procs[0].poll() is not None
            for kind in ["kimi-worker", "observer"]:
                with locks.ProcessFileLock(locks.lock_path(root, kind, "target"), timeout=0): pass
            return ("new", "new-pty", True, None)
        monkeypatch.setattr(bridge, "_ensure_session_unlocked", new_session)
        result = bridge.replace_session({"name": "target", "agent": "kimi"}, {}, create=create)
        assert result["locks_released"] == ["kimi-worker", "observer"]
        assert len(result["reaped_pids"]) == 2
        assert procs[0].wait(timeout=5) is not None
        assert procs[1].poll() is None
        with pytest.raises(TimeoutError):
            with locks.ProcessFileLock(locks.lock_path(root, "kimi-worker", "other"), timeout=0): pass
        if window_exists:
            close_window.assert_called_once()
        else:
            close_window.assert_not_called()
    finally:
        # Cleanup uses the same scoped identities and only the temporary fixture.
        for name in ["target", "other"]:
            with process.KimiClose(name, root) as cleanup:
                cleanup.finish(check_observer=True)
        for p in procs: p.wait(timeout=5)
