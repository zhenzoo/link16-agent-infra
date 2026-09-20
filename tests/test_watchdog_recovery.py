"""Recovery regression scenarios: real watchdog loop, all external I/O mocked.

R8 (long silence → Esc) reads the bridge_activity clock shared with the bridge
(tb26 design, 2026-09-14): a scene drives it through ``activity`` /
``confirmed``. R5 (server-side failed turn → nudge with a retry budget) reads the
Codex rollout tail exactly as on main.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_watchdog as w

WORKING = "Last operation\n• Working (2h 03m • esc to interrupt)\n› next prompt\ngpt-6-astra"
IDLE = "■ Conversation interrupted\n› Ask Codex to do anything\nGoal stalled (/goal resume)"
ACTIVE_60 = {"state": "active", "key": "turn-1", "minutes": 60, "reason": "test"}
UNKNOWN = {"state": "unknown", "key": None, "minutes": None, "reason": "no observer"}


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

    class FakeClock:
        def sample(self, *a):
            return dict(current().get("activity", UNKNOWN))

        def reset(self, *a):
            events.append(("reset", cursor["i"]))

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
    monkeypatch.setattr(w.bridge_activity, "SilenceClock", FakeClock)
    monkeypatch.setattr(w.bridge_activity, "confirm_stall",
                        lambda state_dir, bot, activity: (current().get("confirmed", True),
                                                          dict(current().get("activity", UNKNOWN))))
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


# ---------- R8 · the activity clock decides; the screen alone never authorises Esc ----------

@pytest.mark.parametrize("scene", [
    {"activity": UNKNOWN},
    {"activity": {"state": "waiting", "key": "turn-1", "minutes": None, "reason": "等待用户回答"}},
    {"activity": {"state": "active", "key": "turn-1", "minutes": 29, "reason": "test"}},
    {"activity": {"state": "idle", "key": None, "minutes": None, "reason": "回合已结束"}},
])
def test_not_confirmed_active_never_interrupts(monkeypatch, tmp_path, scene):
    assert not run_scenes(monkeypatch, tmp_path, [scene])


def test_confirmed_long_silence_interrupts_once_then_only_alerts(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{"activity": ACTIVE_60, "step_seconds": 700}] + [{"activity": ACTIVE_60}] * 19)
    assert [e for e in events if e[0] == "esc"] == [("esc", 0)]
    assert ("nudge", 0) in events and ("stall_nudged", 0) in events
    assert any(k == "stall_stuck" for k, _ in events)


def test_new_turn_key_can_be_recovered_again(monkeypatch, tmp_path):
    second = dict(ACTIVE_60, key="turn-2")
    events = run_scenes(monkeypatch, tmp_path, [{"activity": ACTIVE_60}, {"activity": UNKNOWN}, {"activity": second}])
    assert [e for e in events if e[0] == "esc"] == [("esc", 0), ("esc", 2)]


def test_unconfirmed_stall_with_running_tool_only_alerts(monkeypatch, tmp_path):
    scene = {"activity": dict(ACTIVE_60, tool_running=True), "confirmed": False}
    events = run_scenes(monkeypatch, tmp_path, [scene])
    assert not [e for e in events if e[0] in ("esc", "nudge")]
    assert ("stall_stuck", 0) in events


def test_failed_escape_must_not_send_followup_into_active_turn(monkeypatch, tmp_path):
    events = run_scenes(monkeypatch, tmp_path, [{"activity": ACTIVE_60}], esc_ok=False)
    assert not [e for e in events if e[0] == "nudge"]
    assert ("stall_nudged", 0) in events  # the owner still hears that recovery failed


def test_readonly_preview_respects_picker_gate(monkeypatch, capsys):
    monkeypatch.setattr(w, "live_bot_by_pty", lambda: {"fake-pty": "review-bot"})
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "review-bot"}])
    monkeypatch.setattr(w, "scan_topology", lambda: ({"fake-pty": "fake-workspace"}, ["fake-pty"]))
    monkeypatch.setattr(w, "read_pane", lambda *a: WORKING)
    monkeypatch.setattr(w.bridge_activity, "read_activity", lambda *a: dict(ACTIVE_60))
    monkeypatch.setattr(w, "at_picker", lambda screen, bot: True)
    w.cmd_stall_check()
    out = capsys.readouterr().out
    assert "等待用户回答" in out and "不计时" in out


# ---------- R5 · failed turn retry budget (unchanged from main) ----------

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


def test_failed_turn_while_r8_confirmed_stall_is_left_to_r8(monkeypatch, tmp_path):
    scene = failed_turn(1) | {"activity": ACTIVE_60}
    events = run_scenes(monkeypatch, tmp_path, [scene])
    assert ("esc", 0) in events and ("policy_nudged", 0) not in events


# ---------- RPC transport honesty ----------

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
