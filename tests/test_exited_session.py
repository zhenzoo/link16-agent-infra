"""An exited native runtime must not remain bound to a restored terminal."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import feishu_bridge as bridge
import bridge_cron as cron


@pytest.fixture
def dead(tmp_path, monkeypatch):
    bot = {"name": "unit-dead", "profile": "test", "agent": "codex"}
    rec = {"workspace_id": "old", "pty": "pty", "profile": "test", "agent": "codex",
           "chat_id": "owner-chat", "open_id": "owner", "cwd": "project", "jsonl": "old.jsonl"}
    start = {"profile": "test", "worker_pid": 101, "tui_pid": 102, "observer_pid": 103}
    windows = [{"id": "old", "ptyIds": ["pty"], "name": "bot-unit-dead"}]
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(bridge.agent_runtime, "profile_name", lambda *a, **kw: "test")
    monkeypatch.setattr(bridge.agent_runtime, "runtime_name", lambda b: b["agent"])
    startup = Mock(side_effect=lambda b: dict(start))
    monkeypatch.setattr(bridge, "_agent_startup_record", startup)
    monkeypatch.setattr(bridge, "_codex_startup_record", startup)
    inventory = Mock(return_value=[])
    monkeypatch.setattr(bridge.kimi_process, "inventory", inventory)
    monkeypatch.setattr(bridge.wmux_session, "workspaces", Mock(side_effect=lambda: list(windows)))
    close = Mock(side_effect=lambda ws: windows.clear())
    monkeypatch.setattr(bridge.wmux_session, "close", close)
    monkeypatch.setattr(bridge, "blog", Mock())
    bridge.save_session(bot["name"], rec)
    thread = tmp_path / f"bridge-codex-app-thread-{bot['name']}.json"
    thread.write_text('{"thread_id": "old-thread"}')
    return SimpleNamespace(bot=bot, rec=rec, start=start, windows=windows, inventory=inventory,
                           close=close, thread=thread, startup=startup)


@pytest.mark.parametrize("runtime", ["codex", "kimi"])
def test_confirmed_exit_releases_only_runtime_and_resume_pointers(dead, runtime):
    dead.bot["agent"] = runtime
    dead.start["native_pid"] = 104
    assert bridge._release_exited_session_unlocked(dead.bot)
    saved = bridge.load_session(dead.bot["name"])
    assert saved["pty"] is saved["workspace_id"] is saved["jsonl"] is None
    assert {k: saved[k] for k in ("chat_id", "open_id", "cwd", "profile")} == {
        k: dead.rec[k] for k in ("chat_id", "open_id", "cwd", "profile")}
    assert not dead.thread.exists()
    dead.close.assert_called_once_with("old")
    assert dead.inventory.call_count == 2
    assert not bridge._release_exited_session_unlocked(dead.bot)


@pytest.mark.parametrize("pid,ppid", [(101, 1), (102, 1), (103, 1), (999, 101), (999, 102)])
def test_live_pid_reused_pid_or_orphan_child_prevents_release(dead, pid, ppid):
    dead.inventory.return_value = [{"pid": pid, "ppid": ppid, "birth": "1"}]
    assert not bridge._release_exited_session_unlocked(dead.bot)
    assert bridge.load_session(dead.bot["name"]) == dead.rec
    assert dead.thread.exists()
    dead.close.assert_not_called()


def test_unknown_os_query_never_means_dead(dead):
    dead.inventory.side_effect = RuntimeError("process query failed")
    with pytest.raises(RuntimeError, match="process query failed"):
        bridge._release_exited_session_unlocked(dead.bot)
    assert bridge.load_session(dead.bot["name"]) == dead.rec
    dead.close.assert_not_called()


@pytest.mark.parametrize("change", ["missing-pid", "wrong-profile", "pending", "claude"])
def test_without_exact_native_evidence_existing_checks_remain(dead, change):
    if change == "missing-pid":
        dead.start.pop("worker_pid")
    elif change == "wrong-profile":
        dead.start["profile"] = "other"
    elif change == "pending":
        bridge.save_session(dead.bot["name"], {**dead.rec, "pending_launch": {"startup_id": "new"}})
    else:
        dead.bot["agent"] = "claude"
    assert not bridge._release_exited_session_unlocked(dead.bot)
    dead.inventory.assert_not_called()
    dead.close.assert_not_called()
    assert dead.thread.exists()


def test_binding_change_or_process_reappearance_prevents_close(dead):
    dead.inventory.side_effect = [[], [{"pid": 101, "ppid": 1, "birth": "1"}]]
    assert not bridge._release_exited_session_unlocked(dead.bot)
    dead.close.assert_not_called()
    dead.inventory.side_effect = None
    dead.startup.side_effect = [dict(dead.start), {**dead.start, "worker_pid": 201}]
    assert not bridge._release_exited_session_unlocked(dead.bot)
    dead.close.assert_not_called()


def test_changed_terminal_layout_is_not_closed(dead):
    dead.windows[0]["ptyIds"].append("another-terminal")
    assert not bridge._release_exited_session_unlocked(dead.bot)
    dead.close.assert_not_called()


@pytest.mark.parametrize("failure", ["exception", "still-present"])
def test_failed_close_preserves_binding_for_retry(dead, failure):
    dead.close.side_effect = RuntimeError("close failed") if failure == "exception" else lambda ws: None
    with pytest.raises(RuntimeError):
        bridge._release_exited_session_unlocked(dead.bot)
    assert bridge.load_session(dead.bot["name"]) == dead.rec
    assert dead.thread.exists()


def test_regular_session_creation_releases_before_reuse(dead, monkeypatch):
    class Checked(Exception):
        pass
    def reuse(bot, rec):
        assert rec["pty"] is None
        assert not dead.thread.exists()
        raise Checked
    monkeypatch.setattr(bridge, "_reuse_check", reuse)
    with pytest.raises(Checked):
        bridge.ensure_session(dead.bot)


def test_nightly_unknown_old_activity_can_start_after_verified_process_exit(dead, monkeypatch):
    monkeypatch.setattr(bridge, "load_bots", lambda: [dead.bot])
    monkeypatch.setattr(bridge.bridge_inbox, "Inbox", Mock(return_value=SimpleNamespace(counts=lambda: {})))
    monkeypatch.setattr(bridge.bridge_outbox, "picker_load", lambda *a: None)
    monkeypatch.setattr(bridge.bridge_outbox, "pending_clear", lambda *a: None)
    monkeypatch.setattr(bridge.session_work, "clear", lambda *a: None)
    monkeypatch.setattr(bridge.bridge_activity, "read_activity", lambda *a: {"state": "unknown"})
    monkeypatch.setattr(bridge, "_close_session_unlocked", Mock())
    create = Mock(return_value=("new", "new-pty", True, None))
    inject = Mock(return_value=True)
    monkeypatch.setattr(bridge, "_ensure_session_unlocked", create)
    monkeypatch.setattr(bridge, "_inject", inject)
    monkeypatch.setattr(cron, "_watchdog_waiting_for_short_limit", lambda *a: False)
    monkeypatch.setattr(cron, "log", lambda *a: None)
    assert cron.fire({"name": "nightly", "bot": dead.bot["name"], "prompt": "go", "session_policy": "idle-fresh"})
    create.assert_called_once()
    inject.assert_called_once()
    assert not dead.thread.exists()
