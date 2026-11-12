"""An exited native runtime must not remain bound to a restored terminal."""
import json
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


@pytest.mark.parametrize("runtime", ["codex", "kimi", "claude"])
def test_confirmed_exit_releases_runtime_but_preserves_conversation(dead, runtime):
    dead.bot["agent"] = runtime
    dead.start["native_pid"] = 104
    if runtime == "claude":
        bridge.save_session(dead.bot["name"], {**dead.rec, "agent": "claude",
                            "runtime_processes": [{"pid": 101, "birth": "10"}]})
    assert bridge._release_exited_session_unlocked(dead.bot)
    saved = bridge.load_session(dead.bot["name"])
    assert saved["pty"] is saved["workspace_id"] is None
    assert saved["jsonl"] == 'old.jsonl'
    assert {k: saved[k] for k in ("chat_id", "open_id", "cwd", "profile")} == {
        k: dead.rec[k] for k in ("chat_id", "open_id", "cwd", "profile")}
    assert dead.thread.exists()
    dead.close.assert_called_once_with("old")
    assert dead.inventory.call_count == 2
    assert not bridge._release_exited_session_unlocked(dead.bot)


@pytest.mark.parametrize('runtime', ['codex', 'kimi', 'claude'])
def test_daemon_restart_invalidates_only_automatic_resume(dead, monkeypatch, runtime):
    dead.bot['agent'] = runtime
    bridge.save_session(dead.bot['name'], {**dead.rec, 'agent': runtime, 'daemon_fp': 'old'})
    monkeypatch.setattr(bridge.wmux_session, 'daemon_fingerprint', lambda: 'new')
    kimi = bridge.STATE_DIR / f"bridge-kimi-thread-{dead.bot['name']}.json"
    kimi.write_text('{"session_id":"old"}')
    history = bridge.STATE_DIR / 'history.jsonl'
    history.write_text('original conversation')
    assert bridge._invalidate_restart_resume_unlocked(dead.bot)
    assert not dead.thread.exists()
    assert json.loads(kimi.read_text()) == {'closed': True}
    rec = bridge.load_session(dead.bot['name'])
    assert rec['jsonl'] is None
    assert rec['pty'] == dead.rec['pty']  # No speculative process killing.
    assert rec['chat_id'] == 'owner-chat'
    assert history.read_text() == 'original conversation'
    dead.close.assert_not_called()
    assert bridge._invalidate_restart_resume_unlocked(dead.bot)  # idempotent


@pytest.mark.parametrize('old,current', [('same', 'same'), (None, 'new'), ('old', None)])
def test_same_or_unknown_daemon_preserves_resume(dead, monkeypatch, old, current):
    bridge.save_session(dead.bot['name'], {**dead.rec, 'daemon_fp': old})
    monkeypatch.setattr(bridge.wmux_session, 'daemon_fingerprint', lambda: current)
    assert not bridge._invalidate_restart_resume_unlocked(dead.bot)
    assert dead.thread.exists()
    assert bridge.load_session(dead.bot['name'])['jsonl'] == 'old.jsonl'


def test_releasing_a_dead_process_keeps_epoch_for_a_later_reboot_check(dead, monkeypatch):
    bridge.save_session(dead.bot['name'], {**dead.rec, 'daemon_fp': 'old'})
    assert bridge._release_exited_session_unlocked(dead.bot)
    assert bridge.load_session(dead.bot['name'])['daemon_fp'] == 'old'
    monkeypatch.setattr(bridge.wmux_session, 'daemon_fingerprint', lambda: 'new')
    assert bridge._invalidate_restart_resume_unlocked(dead.bot)
    assert not dead.thread.exists()


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


def test_read_only_exit_probe_does_not_erase_runtime_or_conversation(dead):
    assert bridge._release_exited_session_unlocked(dead.bot, release=False)
    assert bridge.load_session(dead.bot['name']) == dead.rec
    dead.close.assert_not_called()
    assert dead.thread.exists()


def test_reused_pid_after_old_native_receipt_does_not_block_reboot_recovery(dead):
    dead.start['ts'] = 1700000000
    dead.inventory.return_value = [{'pid': 101, 'ppid': 1,
        'birth': str((1700000010 + 11644473600) * 10_000_000), 'cmd': 'unrelated', 'name': 'other.exe'}]
    assert bridge._release_exited_session_unlocked(dead.bot)
    assert dead.thread.exists()


def test_child_started_after_receipt_still_prevents_releasing_dead_parent(dead):
    dead.start['ts'] = 1700000000
    dead.inventory.return_value = [{'pid': 999, 'ppid': 101,
        'birth': str((1700000010 + 11644473600) * 10_000_000), 'cmd': 'task', 'name': 'python.exe'}]
    assert not bridge._release_exited_session_unlocked(dead.bot)
    dead.close.assert_not_called()


def test_child_of_proven_reused_parent_does_not_block_reboot_recovery(dead):
    dead.start['ts'] = 1700000000
    dead.inventory.return_value = [
        {'pid': 101, 'ppid': 1, 'birth': str((1700000010 + 11644473600) * 10_000_000), 'cmd': 'other', 'name': 'other.exe'},
        {'pid': 999, 'ppid': 101, 'birth': str((1700000020 + 11644473600) * 10_000_000), 'cmd': 'child', 'name': 'other.exe'},
    ]
    assert bridge._release_exited_session_unlocked(dead.bot)


@pytest.mark.parametrize('change', ['valid', 'profile', 'directory', 'outside-home', 'missing'])
def test_claude_resume_uses_only_exact_profile_directory_and_pinned_history(tmp_path, monkeypatch, change):
    home = tmp_path / 'home'
    path = home / 'projects' / 'project' / '8cce3f30-2294-4211-9f2c-0377e306dcf2.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text('{}')
    bot = {'name': 'claude-bot'}
    rec = dict(agent='claude', profile='ccp2', cwd=str(tmp_path), jsonl=str(path))
    if change == 'profile':
        rec['profile'] = 'ccp'
    elif change == 'directory':
        rec['cwd'] = str(tmp_path / 'other')
    elif change == 'outside-home':
        rec['jsonl'] = str(tmp_path / path.name)
        (tmp_path / path.name).write_text('{}')
    elif change == 'missing':
        rec['jsonl'] = str(path.parent / 'missing.jsonl')
    monkeypatch.setattr(bridge, 'load_session', lambda name: rec)
    monkeypatch.setattr(bridge.agent_runtime, 'runtime_name', lambda bot: 'claude')
    monkeypatch.setattr(bridge.agent_runtime, 'profile_name', lambda *a, **kw: 'ccp2')
    monkeypatch.setattr(bridge.agent_runtime, 'profile_spec', lambda profile: SimpleNamespace(home_path=home))
    assert bridge._resumable_claude_jsonl(bot, tmp_path) == (str(path) if change == 'valid' else None)


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
        assert dead.thread.exists()
        raise Checked
    monkeypatch.setattr(bridge, "_reuse_check", reuse)
    with pytest.raises(Checked):
        bridge.ensure_session(dead.bot)


def test_cron_keeps_native_thread_when_normal_startup_reuses_history(dead, monkeypatch):
    monkeypatch.setattr(bridge, "load_bots", lambda: [dead.bot])
    monkeypatch.setattr(bridge.bridge_outbox, "picker_load", lambda *a: None)
    monkeypatch.setattr(bridge.bridge_outbox, "pending_clear", lambda *a: None)
    monkeypatch.setattr(bridge.session_work, "clear", lambda *a: None)
    monkeypatch.setattr(bridge.bridge_activity, "read_activity", lambda *a: {"state": "unknown"})
    monkeypatch.setattr(bridge, "_close_session_unlocked", Mock())
    create = Mock(return_value=("new", "new-pty", True, None))
    inject = Mock(return_value=True)
    monkeypatch.setattr(bridge, "ensure_session", create)
    monkeypatch.setattr(bridge, "_inject", inject)
    # Cron delegates startup to the bridge without clearing the saved thread.
    monkeypatch.setattr(cron, "_deliver", lambda fb, bot, job, pty, ws, marker:
                        fb._inject(pty, ws, marker, runtime_bot=bot))
    monkeypatch.setattr(cron, "_watchdog_waiting_for_short_limit", lambda *a: False)
    monkeypatch.setattr(cron, "log", lambda *a: None)
    assert cron.fire({"name": "nightly", "bot": dead.bot["name"], "prompt": "go", "session_policy": "idle-fresh"})
    create.assert_called_once()
    inject.assert_called_once()
    assert dead.thread.exists()
    bridge._close_session_unlocked.assert_not_called()


def test_native_identity_requires_matching_shell_birth_and_tree(tmp_path, monkeypatch):
    wmux = bridge.wmux_session
    monkeypatch.setattr(wmux, "WMUX_HOME", tmp_path)
    saved = {"sessions": [{"id": "pty", "pid": 10, "pidCreatedAtFt": "1009"}]}
    (tmp_path / "sessions.json").write_text(json.dumps(saved))
    rows = [{"pid": 10, "ppid": 1, "birth": "1000", "name": "bash.exe"},
            {"pid": 20, "ppid": 10, "birth": "2000", "name": "claude.exe"},
            {"pid": 30, "ppid": 2, "birth": "3000", "name": "claude.exe"}]
    inventory = Mock(return_value=rows)
    monkeypatch.setattr(bridge.kimi_process, "inventory", inventory)
    assert wmux.runtime_processes("pty", "claude") == [{"pid": 20, "birth": "2000"}]
    rows[0]["birth"] = "9000"  # A reused shell PID cannot identify the terminal.
    assert wmux.runtime_processes("pty", "claude") == []
    inventory.side_effect = bridge.kimi_process.Error("unknown")
    assert wmux.runtime_processes("pty", "claude") == []
