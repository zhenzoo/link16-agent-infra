"""Recovery regression scenarios: real watchdog loop, all external I/O mocked."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_watchdog as w

WORKING = "Last operation\n• Working (2h 03m • esc to interrupt)\n› next prompt\ngpt-6-astra"
IDLE = "■ Conversation interrupted\n› Ask Codex to do anything\nGoal stalled (/goal resume)"


class EndReview(BaseException):
    pass


def run_scenes(monkeypatch, tmp_path, scenes, *, esc_ok=True):
    events = []
    cursor = {"i": -1}

    def current():
        return scenes[cursor["i"]]

    def topology():
        cursor["i"] += 1
        if cursor["i"] >= len(scenes):
            raise EndReview()
        return {"fake-pty": "fake-workspace"}, ["fake-pty"]

    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "scan_topology", topology)
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"fake-pty": "review-bot"})
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "review-bot"}])
    monkeypatch.setattr(w, "_profile_of", lambda *a: None)
    monkeypatch.setattr(w.agent_runtime, "profile_name", lambda *a, **kw: (_ for _ in ()).throw(ValueError("isolated")))
    monkeypatch.setattr(w.agent_quota, "collect", lambda: [])
    monkeypatch.setattr(w, "read_pane", lambda *a: current().get("screen", WORKING))
    monkeypatch.setattr(w, "at_picker", lambda *a: current().get("picker", False))
    monkeypatch.setattr(w, "codex_thread_id", lambda *a: "test-thread")
    monkeypatch.setattr(w, "codex_rollout_tail", lambda *a, **kw: json.dumps({
        "type": "event_msg", "payload": current().get("turn", {"type": "task_started"})}))
    monkeypatch.setattr(w, "_is_codex", lambda *a: True)
    monkeypatch.setattr(w.bridge_outbox, "silent_minutes", lambda *a: current().get("silent", 60))
    monkeypatch.setattr(w, "bridge_outbox_mtime", lambda *a: current().get("epoch", 100))
    monkeypatch.setattr(w.wmux_session, "pty_agent_status", lambda *a: current().get("status", "running"))
    monkeypatch.setattr(w, "interrupt_pane", lambda *a: events.append(("esc", cursor["i"])) or esc_ok)
    monkeypatch.setattr(w, "nudge_pane", lambda *a: events.append(("nudge", cursor["i"])) or True)
    monkeypatch.setattr(w, "notify", lambda b, k, t: events.append((k, cursor["i"])) or True)
    monkeypatch.setattr(w, "_alerts_load", lambda: {})
    monkeypatch.setattr(w, "_alerts_save", lambda *a: None)
    monkeypatch.setattr(w, "hwm_corrupt_unseen", lambda *a: (0, ""))
    monkeypatch.setattr(w, "bridge_alive", lambda: True)
    monkeypatch.setattr(w, "_heartbeat_write", lambda *a: None)
    monkeypatch.setattr(w, "log", lambda *a: None)
    monkeypatch.setattr(w, "rpc", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected real RPC")))
    monkeypatch.setattr(w, "time", SimpleNamespace(time=lambda: 10000 + max(cursor["i"], 0) * scenes[0].get("step_seconds", 120), sleep=lambda *a: None))
    with pytest.raises(EndReview):
        w.cmd_run()
    return events


@pytest.mark.parametrize("scene", [
    {"silent": 29}, {"silent": None}, {"status": "idle"},
    {"screen": IDLE}, {"picker": True}, {"screen": None},
    {"screen": "Worked for 40m\n› Ask Codex to do anything"},
])
def test_safe_cases_do_not_interrupt(monkeypatch, tmp_path, scene):
    assert not run_scenes(monkeypatch, tmp_path, [scene])


def test_long_working_silence_interrupts_once(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{}] * 20)
    assert [e for e in events if e[0] == "esc"] == [("esc", 0)]
    assert ("stall_stuck", 15) in events


def test_new_outbox_episode_can_recover_again(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{}, {"silent": 0, "epoch": 200}, {"epoch": 200}])
    assert [e for e in events if e[0] == "esc"] == [("esc", 0), ("esc", 2)]


def test_claude_running_footer_is_covered(monkeypatch, tmp_path):
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [{"screen": "Rendering\nesc to interrupt"}])


@pytest.mark.parametrize("status", ["working", "waiting"])
def test_wmux_active_states_are_covered(monkeypatch, tmp_path, status):
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [{"status": status}])


def test_long_render_without_outbox_is_also_interrupted(monkeypatch, tmp_path):
    # Documented upstream tradeoff; this verifies the effect on a video workload.
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [{"screen": "Rendering frame 4000 / 20000\n• Working (40m)"}])


def test_unknown_agent_status_must_not_authorize_interruption(monkeypatch, tmp_path):
    assert not run_scenes(monkeypatch, tmp_path, [{"status": None}])


def test_failed_escape_must_not_send_followup_into_active_turn(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{}], esc_ok=False)
    assert not [e for e in events if e[0] == "nudge"]


def test_screen_flicker_does_not_reset_same_silence_episode(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{}, {"screen": "redrawing screen"}, {}])
    assert len([e for e in events if e[0] == "esc"]) == 1


def test_readonly_preview_respects_picker_gate(monkeypatch, capsys):
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"fake-pty": "review-bot"})
    monkeypatch.setattr(w.bridge_outbox, "silent_minutes", lambda *a: 60)
    monkeypatch.setattr(w.wmux_session, "pty_agent_status", lambda *a: "running")
    monkeypatch.setattr(w, "read_pane", lambda *a: WORKING)
    monkeypatch.setattr(w, "picker_load", lambda directory, bot: {"active": True})
    w.cmd_stall_check()
    assert "会出手" not in capsys.readouterr().out


def failed_turn(number):
    return {"screen": "Error running remote compact task", "step_seconds": 700,
            "turn": {"type": "task_complete", "turn_id": f"failed-{number}",
                     "error": {"message": "Error running remote compact task: idle timeout waiting for SSE"}}}


def test_compact_errors_stop_after_three_retries_across_running_scans(monkeypatch, tmp_path):
    scenes = []
    for i in range(4):
        scenes.extend([failed_turn(i), {"screen": "retry running"}])
    events = run_scenes(monkeypatch, tmp_path, scenes)
    assert [e for e in events if e[0] == "nudge"] == [("nudge", 0), ("nudge", 2), ("nudge", 4)]
    assert ("policy_stuck", 6) in events


def test_same_failed_turn_does_not_receive_duplicate_retry(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [failed_turn(1)] * 8)
    assert [e for e in events if e[0] == "nudge"] == [("nudge", 0)]


@pytest.mark.parametrize("terminal", [{"type": "task_complete"}, {"type": "turn_aborted"}])
def test_success_or_manual_stop_resets_failure_budget(monkeypatch, tmp_path, terminal):
    scenes = [failed_turn(i) for i in range(4)]
    scenes += [{"screen": IDLE, "turn": terminal}, failed_turn(5)]
    events = run_scenes(monkeypatch, tmp_path, scenes)
    assert ("policy_stuck", 3) in events
    assert ("nudge", 4) not in events
    assert ("nudge", 5) in events


def test_failed_rpc_exit_is_not_reported_as_escape_success(monkeypatch):
    monkeypatch.setattr(w, "_allow", lambda *a: [])
    monkeypatch.setattr(w.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="RPC denied"))
    assert w.interrupt_pane("test-pty") is False


def test_failed_paste_does_not_press_enter(monkeypatch):
    calls = []
    monkeypatch.setattr(w, "_allow", lambda *a: [])
    monkeypatch.setattr(w, "rpc", lambda args: calls.append(args) or "__RPC_FAIL__ denied")
    assert w.nudge_pane("test-pty", "continue") is False
    assert len(calls) == 1
