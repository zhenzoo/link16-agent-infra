"""Nightly reset must never interrupt work or start before verified closure."""
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
    job = {"name": "nightly", "bot": "target", "prompt": "go", "session_policy": "idle-fresh"}
    path = tmp_path / "bridge-session-target.json"
    record = {"workspace_id": "old", "pty": "pty", "profile": "test"}
    path.write_text(json.dumps(record))
    windows = [{"id": "old", "name": "bot-target", "ptyIds": ["pty"]}]
    events = []
    fb = SimpleNamespace(
        STATE_DIR=tmp_path, bridge_injection=bridge_injection,
        load_bots=lambda: [bot],
        bridge_inbox=SimpleNamespace(Inbox=Mock(return_value=SimpleNamespace(counts=Mock(return_value={})))),
        bridge_outbox=SimpleNamespace(picker_load=Mock(return_value=None), pending_clear=Mock()),
        agent_runtime=SimpleNamespace(profile_name=lambda *a, **k: "test", runtime_name=lambda b: b["agent"]),
        bridge_activity=SimpleNamespace(read_activity=Mock(return_value={"state": "idle"})),
        wmux_session=SimpleNamespace(workspaces=Mock(side_effect=lambda: list(windows))),
        kimi_process=SimpleNamespace(inventory=Mock(return_value=[]), descendants=lambda rows, seeds: seeds),
        session_work=SimpleNamespace(clear=Mock()),
        clear_codex_thread=Mock(side_effect=lambda name: events.append("clear-thread")),
    )
    def close(b):
        events.append("close")
        windows.clear()
    fb._close_session_unlocked = Mock(side_effect=close)
    def clear(name):
        events.append("clear-session")
        if path.exists():
            path.unlink()
    fb.clear_session = Mock(side_effect=clear)
    def create(b):
        assert not windows and not path.exists()
        assert "clear-thread" in events
        events.append("create")
        return "new", "new-pty", True, None
    fb._ensure_session_unlocked = Mock(side_effect=create)
    fb._inject = Mock(side_effect=lambda *a, **k: events.append("inject") or True)
    fb._codex_startup_record = Mock(return_value={"worker_pid": 123})
    monkeypatch.setitem(sys.modules, "feishu_bridge", fb)
    monkeypatch.setattr(cron, "_watchdog_waiting_for_short_limit", lambda name: False)
    monkeypatch.setattr(cron, "log", lambda message: None)
    return SimpleNamespace(fb=fb, bot=bot, job=job, path=path, windows=windows, events=events)


@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
def test_idle_closes_clears_old_thread_creates_then_injects(scene, runtime):
    scene.bot["agent"] = runtime
    assert cron.fire(scene.job)
    assert scene.events == ["close", "clear-session", "clear-thread", "create", "inject"]
    assert scene.fb.bridge_activity.read_activity.call_count == 2


@pytest.mark.parametrize("state", ["active", "waiting", "unknown"])
@pytest.mark.parametrize("runtime", ["claude", "codex", "kimi"])
def test_nonidle_has_no_close_create_or_send(scene, state, runtime):
    scene.bot["agent"] = runtime
    scene.fb.bridge_activity.read_activity.return_value = {"state": state}
    assert not cron.fire(scene.job)
    assert not scene.events
    assert scene.path.exists()


def test_becomes_busy_before_close_is_skipped(scene):
    scene.fb.bridge_activity.read_activity.side_effect = [{"state": "idle"}, {"state": "active"}]
    assert not cron.fire(scene.job)
    assert not scene.events


@pytest.mark.parametrize("state", ["queued", "processing", "submitting", "awaiting_confirmation", "uncertain"])
def test_pending_message_blocks_reset(scene, state):
    scene.fb.bridge_inbox.Inbox.return_value.counts.return_value = {state: 1}
    assert not cron.fire(scene.job)
    assert not scene.events


def test_bad_binding_or_rpc_never_means_no_session(scene):
    scene.path.write_text("{")
    assert not cron.fire(scene.job)
    assert not scene.events
    scene.path.unlink()
    scene.fb.wmux_session.workspaces.side_effect = RuntimeError("RPC unavailable")
    assert not cron.fire(scene.job)
    assert not scene.events


def test_close_failure_preserves_binding_and_never_sends(scene):
    scene.fb._close_session_unlocked.side_effect = RuntimeError("close failed")
    assert not cron.fire(scene.job)
    assert scene.path.exists()
    assert not scene.events


def test_close_ack_without_window_disappearance_never_starts(scene, monkeypatch):
    scene.fb._close_session_unlocked.side_effect = lambda b: None
    original = cron._wait_closed
    monkeypatch.setattr(cron, "_wait_closed", lambda *a: original(*a, timeout=0))
    assert not cron.fire(scene.job)
    assert scene.path.exists()
    assert not scene.events


def test_lingering_process_blocks_start_even_when_window_gone(scene, monkeypatch):
    scene.bot["agent"] = "codex"
    scene.fb.kimi_process.inventory.return_value = [{"pid": 123, "birth": "1"}]
    original = cron._wait_closed
    monkeypatch.setattr(cron, "_wait_closed", lambda *a: original(*a, timeout=0))
    assert not cron.fire(scene.job)
    assert scene.events == ["close"]
    assert scene.path.exists()


def test_no_old_window_still_clears_resume_pointer_before_start(scene):
    scene.path.unlink()
    scene.windows.clear()
    assert cron.fire(scene.job)
    assert scene.events[-2:] == ["create", "inject"]


def test_unknown_policy_and_dry_run_do_not_touch_runtime(scene):
    assert not cron.fire({**scene.job, "session_policy": "typo"})
    assert cron.fire(scene.job, dry_run=True)
    assert not scene.events


def test_injection_failure_does_not_queue_legacy_retry(scene):
    scene.fb._inject.return_value = False
    scene.fb._inject.side_effect = None
    assert not cron.fire(scene.job)
    assert not hasattr(scene.fb.bridge_outbox, "pending_write")


def test_configuration_preserves_schedule_prompt_and_other_jobs(scene, monkeypatch):
    monkeypatch.setattr(cron, "JOBS_DIR", scene.path.parent / "jobs")
    monkeypatch.setattr(cron, "_require_roster_bot", lambda bot: None)
    jobs = [{"name": "nightly", "cron": "0 0 * * *", "enabled": True, "prompt": "original"},
            {"name": "other", "enabled": False, "prompt": "unchanged"}]
    cron._save_bot_file("target", jobs)
    cron.cmd_session_policy("target", "nightly", "idle-fresh")
    actual = cron._load_bot_file("target")
    assert actual == [{**jobs[0], "bot": "target", "session_policy": "idle-fresh"},
                      {**jobs[1], "bot": "target"}]


def test_lifecycle_lock_contention_skips_without_close(scene):
    with bridge_injection.injection_lock(scene.fb.STATE_DIR, "ensure-session", "target"):
        assert not cron.fire(scene.job)
    assert not scene.events


def test_orphan_workspace_without_binding_is_not_treated_as_absent(scene):
    scene.path.unlink()
    assert not cron.fire(scene.job)
    assert not scene.events


def test_picker_waiting_is_not_idle(scene):
    scene.fb.bridge_outbox.picker_load.return_value = {"question": "choose"}
    assert not cron.fire(scene.job)
    assert not scene.events


def test_missing_window_does_not_authorize_closing_active_native_worker(scene):
    scene.windows.clear()
    scene.fb.bridge_activity.read_activity.return_value = {"state": "active"}
    assert not cron.fire(scene.job)
    assert not scene.events
