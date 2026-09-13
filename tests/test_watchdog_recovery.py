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


def run_scenes(monkeypatch, tmp_path, scenes, *, esc_ok=True, nudge_ok=True, configure=None):
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
    monkeypatch.setattr(w, "at_picker", lambda screen, bot: current().get("picker", False))
    monkeypatch.setattr(w, "codex_thread_id", lambda *a: "test-thread")
    monkeypatch.setattr(w, "codex_rollout_tail", lambda *a, **kw: json.dumps({
        "type": "event_msg", "timestamp": current().get("event_time"),
        "payload": current().get("turn", {"type": "task_started"})}))
    monkeypatch.setattr(w, "_is_codex", lambda *a: True)
    monkeypatch.setattr(w.bridge_outbox, "silent_minutes", lambda *a: current().get("silent", 60))
    monkeypatch.setattr(w, "bridge_outbox_mtime", lambda *a: current().get("epoch", 100))
    monkeypatch.setattr(w.wmux_session, "pty_agent_status", lambda *a: current().get("status", "running"))
    monkeypatch.setattr(w, "interrupt_pane", lambda *a: events.append(("esc", cursor["i"])) or esc_ok)
    monkeypatch.setattr(w, "nudge_pane", lambda *a: events.append(("nudge", cursor["i"])) or nudge_ok)
    monkeypatch.setattr(w, "notify", lambda b, k, t: events.append((k, cursor["i"])) or True)
    monkeypatch.setattr(w, "_alerts_load", lambda: {})
    monkeypatch.setattr(w, "_alerts_save", lambda *a: None)
    monkeypatch.setattr(w, "hwm_corrupt_unseen", lambda *a: (0, ""))
    monkeypatch.setattr(w, "bridge_alive", lambda: True)
    monkeypatch.setattr(w, "_heartbeat_write", lambda *a: None)
    monkeypatch.setattr(w, "log", lambda *a: None)
    monkeypatch.setattr(w, "rpc", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unexpected real RPC")))
    monkeypatch.setattr(w, "time", SimpleNamespace(time=lambda: 10000 + max(cursor["i"], 0) * scenes[0].get("step_seconds", 120), sleep=lambda *a: None))
    if configure:
        configure(events)
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
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "review-bot"}])
    monkeypatch.setattr(w, "codex_dead_turn", lambda *a: (None, None))
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


@pytest.mark.parametrize("elapsed", ["8s", "8m 24s", "29m 59s"])
def test_old_outbox_does_not_interrupt_fresh_compaction(monkeypatch, tmp_path, elapsed):
    screen = f"• Compacting context ({elapsed} • esc to interrupt)\n  └ Making room to continue.\n› Ask Codex to do anything"
    assert not run_scenes(monkeypatch, tmp_path, [{"screen": screen, "silent": 286}])



def test_compaction_stalled_for_thirty_minutes_is_still_recoverable(monkeypatch, tmp_path):
    screen = "• Compacting context (30m 01s • esc to interrupt)\n› Ask Codex to do anything"
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [{"screen": screen}])



@pytest.mark.parametrize("terminal", ["task_complete", "turn_aborted"])
def test_finished_turn_overrides_stale_working_footer(monkeypatch, tmp_path, terminal):
    scene = {"turn": {"type": terminal, "turn_id": "finished"},
             "event_time": "1970-01-01T02:46:00Z"}  # 9960; current clock is 10000
    assert not run_scenes(monkeypatch, tmp_path, [scene])



def test_failed_turn_with_stale_working_uses_r5_without_escape(monkeypatch, tmp_path):
    scene = failed_turn(1) | {"screen": WORKING, "event_time": "1970-01-01T02:46:00Z"}
    events = run_scenes(monkeypatch, tmp_path, [scene])
    assert ("nudge", 0) in events and ("policy_nudged", 0) in events
    assert not [event for event in events if event[0] == "esc"]



def test_old_thread_completion_cannot_disable_current_stall_recovery(monkeypatch, tmp_path):
    scene = {"turn": {"type": "task_complete", "completed_at": 1000}}
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [scene])



def test_readonly_preview_respects_finished_visible_turn(monkeypatch, capsys):
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"fake-pty": "review-bot"})
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "review-bot"}])
    monkeypatch.setattr(w, "codex_dead_turn", lambda *args: (None, {"type": "task_complete", "completed_at": 9960}))
    monkeypatch.setattr(w.bridge_outbox, "silent_minutes", lambda *args: 60)
    monkeypatch.setattr(w.wmux_session, "pty_agent_status", lambda *args: "running")
    monkeypatch.setattr(w, "read_pane", lambda *args: WORKING)
    monkeypatch.setattr(w, "at_picker", lambda screen, bot: False)
    monkeypatch.setattr(w, "time", SimpleNamespace(time=lambda: 10000))
    w.cmd_stall_check()
    output = capsys.readouterr().out
    assert "会出手" not in output
    assert "已结束" in output



@pytest.mark.parametrize("session, started, expect_esc", [
    ("test-thread", 9900, False), ("test-thread", 9990, False), ("new-thread", 9900, False),
    ("new-thread", 8100, True),
])
def test_timerless_app_server_uses_current_prompt_binding(monkeypatch, tmp_path, session, started, expect_esc):
    # Actual tb24-link16 footer: Working with no elapsed timer.
    scene = {"screen": "• Working…\n› Ask Codex", "turn": {"type": "task_complete", "completed_at": 9960}}
    (tmp_path / "bridge-turn-route-review-bot.json").write_text(
        json.dumps({"session": session, "started_at": started}), encoding="utf-8")
    events = run_scenes(monkeypatch, tmp_path, [scene])
    assert (("esc", 0) in events) is expect_esc



@pytest.mark.parametrize("source", ["task_started", "prompt_hook"])
def test_new_timerless_turn_does_not_inherit_idle_outbox_silence(monkeypatch, tmp_path, source):
    # 2026-09-13: a new request was Esc'd after 4 seconds using 99 idle minutes.
    scene = {"screen": "• Working…\n› Ask Codex", "silent": 99}
    if source == "task_started":
        scene["event_time"] = "1970-01-01T02:46:36Z"  # 9996, now=10000
    else:
        (tmp_path / "bridge-turn-route-review-bot.json").write_text(
            json.dumps({"session": "test-thread", "started_at": 9996}), encoding="utf-8")
    assert not run_scenes(monkeypatch, tmp_path, [scene])



def test_timerless_turn_really_silent_for_thirty_minutes_is_recovered(monkeypatch, tmp_path):
    scene = {"screen": "• Working…\n› Ask Codex", "silent": 99,
             "event_time": "1970-01-01T02:15:00Z"}  # 8100, now=10000
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [scene])



@pytest.mark.parametrize("started", [0, -1, 10001, "invalid", None])
def test_invalid_prompt_times_do_not_disable_stall_recovery(monkeypatch, tmp_path, started):
    (tmp_path / "bridge-turn-route-review-bot.json").write_text(
        json.dumps({"session": "test-thread", "started_at": started}), encoding="utf-8")
    assert ("esc", 0) in run_scenes(monkeypatch, tmp_path, [{"screen": "• Working…\n› Ask Codex"}])



def test_prompt_arriving_during_watchdog_scan_uses_fresh_decision_time(monkeypatch, tmp_path):
    clock = {"now": 10000}

    def configure(events):
        monkeypatch.setattr(w, "time", SimpleNamespace(time=lambda: clock["now"], sleep=lambda *a: None))

        def read_turn(*a, **kw):
            clock["now"] = 10010  # scanning took time; turn began after the tick started
            return None, {"type": "task_started", "_event_timestamp": "1970-01-01T02:46:45Z"}

        monkeypatch.setattr(w, "codex_dead_turn", read_turn)

    assert not run_scenes(monkeypatch, tmp_path, [{"screen": "• Working…\n› Ask Codex"}], configure=configure)
