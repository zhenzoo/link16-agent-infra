"""Cron delivers into the existing conversation, including legacy job configs."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_cron as cron
import bridge_injection


@pytest.fixture
def scene(tmp_path, monkeypatch):
    bot = {"name": "target", "profile": "test", "agent": "claude"}
    job = {"name": "nightly", "bot": "target", "prompt": "go"}
    path = tmp_path / "bridge-session-target.json"
    record = {"workspace_id": "old", "pty": "pty", "profile": "test"}
    path.write_text(json.dumps(record))
    events = []
    fb = SimpleNamespace(
        STATE_DIR=tmp_path, bridge_injection=bridge_injection,
        load_bots=lambda: [bot], load_session=Mock(return_value=record),
        agent_runtime=SimpleNamespace(profile_name=lambda *a, **k: "test", runtime_name=lambda b: b["agent"]),
        bridge_activity=SimpleNamespace(read_activity=Mock(return_value={"state": "idle"})),
        wmux_session=SimpleNamespace(pty_agent_status=Mock(return_value="idle")),
        bridge_outbox=SimpleNamespace(pending_clear=Mock()),
        session_work=SimpleNamespace(clear=Mock()),
        clear_codex_thread=Mock(), clear_session=Mock(), _close_session_unlocked=Mock(),
    )
    def ensure(bot):
        if path.exists():
            events.append("reuse")
            return "old", "pty", False, None
        events.append("create")
        return "new", "new-pty", True, None
    fb._ensure_session_unlocked = Mock(side_effect=ensure)
    fb.ensure_session = Mock(side_effect=lambda bot: fb._ensure_session_unlocked(bot))
    fb._inject = Mock(side_effect=lambda *a, **k: events.append("inject") or True)
    monkeypatch.setitem(sys.modules, "feishu_bridge", fb)
    monkeypatch.setattr(cron, "_watchdog_waiting_for_short_limit", lambda name: False)
    monkeypatch.setattr(cron, "log", lambda message: None)
    monkeypatch.setattr(cron, "_deliver", lambda fb, bot, job, pty, ws, marker:
                        fb._inject(pty, ws, marker, runtime_bot=bot))
    return SimpleNamespace(fb=fb, bot=bot, job=job, path=path, events=events)


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
@pytest.mark.parametrize("state", ["idle", "active", "waiting", "unknown"])
@pytest.mark.parametrize("legacy_policy", [None, "idle-fresh", "reuse", "typo"])
def test_cron_never_resets_existing_conversation(scene, runtime, state, legacy_policy):
    scene.bot["agent"] = runtime
    scene.fb.wmux_session.pty_agent_status.return_value = state
    scene.fb.bridge_activity.read_activity.return_value = {"state": state}
    if legacy_policy is not None:
        scene.job["session_policy"] = legacy_policy
    original = scene.path.read_bytes()
    assert cron.fire(scene.job)
    assert scene.events == ["reuse", "inject"]
    assert scene.fb._inject.call_args.args[:2] == ("pty", "old")
    assert scene.path.read_bytes() == original
    scene.fb._close_session_unlocked.assert_not_called()
    scene.fb.clear_session.assert_not_called()
    scene.fb.clear_codex_thread.assert_not_called()
    scene.fb.session_work.clear.assert_not_called()
    scene.fb.bridge_outbox.pending_clear.assert_not_called()


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
def test_absent_session_uses_normal_startup_without_clearing_history(scene, runtime):
    scene.bot["agent"] = runtime
    scene.path.unlink()
    assert cron.fire(scene.job)
    assert scene.events == ["create", "inject"]
    scene.fb._close_session_unlocked.assert_not_called()
    scene.fb.clear_codex_thread.assert_not_called()


def test_failed_session_check_does_not_send_or_clear_binding(scene):
    scene.fb._ensure_session_unlocked.side_effect = RuntimeError("RPC unavailable")
    assert not cron.fire(scene.job)
    assert not scene.events
    assert scene.path.exists()
    scene.fb._close_session_unlocked.assert_not_called()
    scene.fb.clear_session.assert_not_called()


def test_dry_run_does_not_touch_runtime(scene):
    assert cron.fire(scene.job, dry_run=True)
    assert not scene.events


def test_injection_failure_does_not_queue_legacy_retry(scene):
    scene.fb._inject.return_value = False
    scene.fb._inject.side_effect = None
    assert not cron.fire(scene.job)
    scene.fb._close_session_unlocked.assert_not_called()
    assert not hasattr(scene.fb.bridge_outbox, "pending_write")


def test_removed_cli_cannot_restore_reset_policy(scene, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bridge_cron.py", "session-policy", "--bot", "target",
                                     "--name", "nightly", "--policy", "idle-fresh"])
    with pytest.raises(SystemExit) as failure:
        cron.main()
    assert failure.value.code != 0
    assert not scene.events
