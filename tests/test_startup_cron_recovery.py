"""Regression for a historical handoff blocking midnight and a lost launcher Enter."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_inbox
import feishu_bridge as fb
import wmux_session


@pytest.mark.parametrize("state", ["submitting", "awaiting_confirmation", "uncertain"])
def test_old_thread_does_not_block_current_idle_thread_but_record_is_preserved(tmp_path, state):
    inbox = bridge_inbox.Inbox(tmp_path, "bot")
    inbox.receive({"message": {"message_id": "old"}})
    with inbox.connect() as db:
        db.execute("UPDATE messages SET state=?,session='old-thread'", (state,))
    assert inbox.reset_blockers("new-thread") == {}
    assert inbox.reset_blockers("old-thread") == {state: 1}
    assert inbox.reset_blockers() == {state: 1}
    assert inbox.get("old")["state"] == state
    assert inbox.get("old")["payload"] is not None


@pytest.mark.parametrize("state,session", [
    ("queued", "old"), ("processing", "old"), ("uncertain", None),
    ("uncertain", ""), ("awaiting_confirmation", "current"),
])
def test_preparation_and_unknown_or_current_handoff_still_block(tmp_path, state, session):
    inbox = bridge_inbox.Inbox(tmp_path, "bot")
    inbox.receive({"message": {"message_id": "pending"}})
    with inbox.connect() as db:
        db.execute("UPDATE messages SET state=?,session=?", (state, session))
    assert inbox.reset_blockers("current") == {state: 1}


@pytest.fixture
def startup(tmp_path, monkeypatch):
    bot = {"name": "bot", "agent": "codex", "_startup_id": "attempt"}
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    monkeypatch.setattr(fb, "blog", Mock())
    ready = Mock(side_effect=[False, True])
    monkeypatch.setattr(fb, "_wait_agent_ready", ready)
    monkeypatch.setattr(fb, "_codex_startup_record", Mock(return_value={}))
    monkeypatch.setattr(wmux_session, "pty_state", Mock(return_value=(True, "")))
    monkeypatch.setattr(wmux_session, "shell_has_no_children", Mock(return_value=True))
    monkeypatch.setattr(wmux_session, "send_command", Mock())
    monkeypatch.setattr(fb, "wmux", Mock())
    monkeypatch.setattr(fb, "read_screen", Mock(return_value="owner MINGW64 /repo\n$ "))
    return bot, ready


def test_empty_shell_retries_once_and_requires_native_ready(startup, tmp_path):
    bot, ready = startup
    assert fb._finish_worker_startup(bot, "ws", "pty", tmp_path)
    wmux_session.send_command.assert_called_once()
    fb.wmux.assert_not_called()
    assert ready.call_count == 2


def test_exact_launcher_draft_gets_enter_only(startup, tmp_path):
    bot, ready = startup
    command = f'bash "{(tmp_path / "agent-launch-bot-attempt.sh").as_posix()}"'
    fb.read_screen.return_value = "owner MINGW64 /repo\n$ " + command
    assert fb._finish_worker_startup(bot, "ws", "pty", tmp_path)
    fb.wmux.assert_called_once_with("enter", "pty", "--allow-ws", "ws")
    wmux_session.send_command.assert_not_called()


@pytest.mark.parametrize("condition", ["worker", "children", "unknown-screen", "other-draft", "agent-label"])
def test_unsafe_startup_never_sends(startup, tmp_path, condition):
    bot, ready = startup
    if condition == "worker":
        fb._codex_startup_record.return_value = {"stage": "tui_starting", "worker_pid": 123}
    elif condition == "children":
        wmux_session.shell_has_no_children.return_value = False
    elif condition == "unknown-screen":
        fb.read_screen.return_value = ""
    elif condition == "other-draft":
        fb.read_screen.return_value = "owner MINGW64 /repo\n$ somebody else's command"
    else:
        wmux_session.pty_state.return_value = (True, "codex")
    assert not fb._finish_worker_startup(bot, "ws", "pty", tmp_path)
    fb.wmux.assert_not_called()
    wmux_session.send_command.assert_not_called()
    assert ready.call_count == 1


def test_retry_exhaustion_is_bounded_and_failure_survives_later_success(startup, tmp_path):
    bot, ready = startup
    ready.side_effect = [False, False, True]
    assert not fb._finish_worker_startup(bot, "ws", "pty", tmp_path)
    wmux_session.send_command.assert_called_once()
    assert fb._load_startup_failure("bot")
    assert fb._finish_worker_startup(bot, "ws", "pty", tmp_path)
    assert fb._load_startup_failure("bot") is None
    history = tmp_path / "bridge-startup-failures-bot.jsonl"
    assert len(history.read_text().splitlines()) == 1
    assert "自动恢复一次" in json.loads(history.read_text())["reason"]


def test_shell_process_evidence_requires_birth_and_no_children(tmp_path, monkeypatch):
    import kimi_process
    monkeypatch.setattr(wmux_session, "WMUX_HOME", tmp_path)
    (tmp_path / "sessions.json").write_text(json.dumps({"sessions": [
        {"id": "pty", "pid": 10, "pidCreatedAtFt": "12300"}]}))
    shell = {"pid": 10, "ppid": 1, "birth": "12300"}
    inventory = Mock(return_value=[shell])
    monkeypatch.setattr(kimi_process, "inventory", inventory)
    assert wmux_session.shell_has_no_children("pty")
    inventory.return_value = [shell, {"pid": 11, "ppid": 10, "birth": "12500"}]
    assert not wmux_session.shell_has_no_children("pty")
    inventory.return_value = [{**shell, "birth": "99900"}]
    assert not wmux_session.shell_has_no_children("pty")


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
def test_slow_official_update_cannot_consume_native_ready_budget(tmp_path, monkeypatch, runtime):
    bot = {"name": "clock-probe", "profile": "test", "agent": runtime}
    clock = [100.0]
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    monkeypatch.setattr(fb.time, "time", lambda: clock[0])
    monkeypatch.setattr(fb, "_release_exited_session_unlocked", lambda b: False)
    monkeypatch.setattr(fb, "load_session", lambda b: {})
    monkeypatch.setattr(fb, "_reuse_check", lambda *a: (False, False, ""))
    monkeypatch.setattr(fb.agent_runtime, "profile_name", lambda *a, **k: "test")
    monkeypatch.setattr(fb.agent_runtime, "profile_spec", Mock())
    monkeypatch.setattr(fb.agent_runtime, "profile_login_state", lambda *a: {"status": "unknown"})
    monkeypatch.setattr(fb.agent_runtime, "ensure_launch_cwd_trust", Mock())
    monkeypatch.setattr(fb, "_project_jsonls", lambda b: [])
    monkeypatch.setattr(fb, "current_cwd", lambda b: str(tmp_path))
    monkeypatch.setattr(fb, "_worker_launcher", lambda *a: "prepared-launcher")
    monkeypatch.setattr(fb, "_prepare_worker_update", lambda b: clock.__setitem__(0, 175.0))
    monkeypatch.setattr(wmux_session, "spawn", lambda *a, **k: {"workspace_id": "ws", "pty": "pty"})
    monkeypatch.setattr(wmux_session, "runtime_processes", lambda *a: [])
    monkeypatch.setattr(wmux_session, "daemon_fingerprint", lambda: "fp")
    ready = Mock(return_value=True)
    monkeypatch.setattr(fb, "_finish_worker_startup", ready)
    monkeypatch.setattr(fb, "_detect_new_jsonl", lambda *a: None)
    monkeypatch.setattr(fb, "_merge_session", Mock())
    assert fb._ensure_session_unlocked(bot)[:2] == ("ws", "pty")
    assert ready.call_args.kwargs["started"] == 175.0


def test_official_update_failure_does_not_abort_launch(monkeypatch):
    import agent_update
    monkeypatch.setattr(fb.agent_runtime, "profile_name", lambda *a, **k: "test")
    monkeypatch.setattr(agent_update, "ensure", Mock(side_effect=RuntimeError("failed")))
    monkeypatch.setattr(fb, "blog", Mock())
    fb._prepare_worker_update({"name": "bot"})
    assert "继续使用当前版本启动" in fb.blog.call_args.args[1]
