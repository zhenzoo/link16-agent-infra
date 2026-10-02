"""Quota-window handoff regression: one-shot short wait and same-series weekly failover."""

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_quota as q
import bridge_watchdog as w
from test_watchdog_recovery import run_scenes


SESSION_SCREEN = ("⎿  You've hit your session limit · resets 4:40am (Asia/Shanghai)\n"
                  "⚠ Usage limit reached · limit resets 4:40am\n"
                  "Continuing automatically at 4:40am · esc to cancel")
WEEKLY_SCREEN = "⎿  You've hit your weekly limit · resets Oct 9, 6pm (Asia/Shanghai)"
REC = {"profile": "ccp2", "pty": "pty-1", "workspace_id": "ws-1",
       "jsonl": "C:/tmp/original-session.jsonl"}
BOTS = {"voiceover": {"name": "voiceover", "profile": "ccp2"}}


def row(*, short=100, weekly=45, reset_at=10400):
    return {"profile": "ccp2", "runtime": "claude", "status": "ok",
            "session_percent": short, "weekly_percent": weekly,
            "session_reset": "10-02 04:40", "session_reset_at": reset_at,
            "weekly_reset": "10-02 17:59", "verdict": "满" if max(short, weekly) >= 95 else "够用"}


def test_provider_reset_time_is_stored_as_epoch_for_a_restart_safe_wakeup():
    assert q._reset_epoch("2026-10-01T20:40:00Z") == q._reset_epoch("2026-10-02T04:40:00+08:00")
    assert q._reset_epoch(None) is None


def test_session_banner_supplies_reset_time_when_quota_api_is_blind():
    now = datetime(2026, 10, 2, 3, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    hint = w._screen_short_reset(SESSION_SCREEN, now)
    assert hint["session_reset_at"] == q._reset_epoch("2026-10-02T04:40:00+08:00")
    assert hint["session_reset"] == "10-02 04:40"
    assert w._screen_short_reset(WEEKLY_SCREEN, now) is None


def test_session_limit_with_45_percent_weekly_is_short_and_notice_names_correct_reset(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    messages = []
    monkeypatch.setattr(w, "notify", lambda bot, kind, body: messages.append((kind, body)) or True)
    assert w.limit_kind(SESSION_SCREEN, row()) == "short"
    jobs = {}
    assert w._arm_short_wait("voiceover", REC, row(), jobs, 10000)
    assert jobs["voiceover"]["due_at"] == 10400
    assert len(messages) == 1
    assert "短时会话限额" in messages[0][1]
    assert "10-02 04:40" in messages[0][1]
    assert "剩余 55%" in messages[0][1]
    assert "17:59" not in messages[0][1]
    w._arm_short_wait("voiceover", REC, row(), jobs, 10120)
    assert len(messages) == 1  # no repeated DM on every poll


def test_due_short_wait_survives_restart_injects_once_then_removes_job(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "notify", lambda *a: True)
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    monkeypatch.setattr(w, "read_pane", lambda pty: SESSION_SCREEN)
    monkeypatch.setattr(w, "at_picker", lambda *a: False)
    import feishu_bridge as fb
    injected = []
    monkeypatch.setattr(fb, "_inject", lambda pty, ws, text: injected.append((pty, ws, text)) or True)
    w._arm_short_wait("voiceover", REC, row(), {}, 10000)
    jobs = w._short_waits_load()  # a new watchdog process reads the persisted one-shot job
    assert w._resume_short_waits(jobs, {"ccp2": row(short=0)}, {"pty-1": "voiceover"}, BOTS, 10400) == {"voiceover"}
    assert len(injected) == 1 and injected[0][:2] == ("pty-1", "ws-1")
    assert jobs == {} and json.loads(w.SHORT_WAIT_PATH.read_text(encoding="utf-8")) == {}
    assert not w._resume_short_waits(jobs, {"ccp2": row(short=0)}, {"pty-1": "voiceover"}, BOTS, 10520)
    assert len(injected) == 1


@pytest.mark.parametrize("change,screen", [
    ({"pty": "replacement-pty"}, SESSION_SCREEN),
    ({}, "● Working on the next step"),
    ({}, SESSION_SCREEN + "\n● Working on the next step"),
])
def test_short_wait_does_not_nudge_replaced_or_already_resumed_session(monkeypatch, tmp_path, change, screen):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "notify", lambda *a: True)
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC, **change))
    monkeypatch.setattr(w, "read_pane", lambda pty: screen)
    monkeypatch.setattr(w, "at_picker", lambda *a: False)
    import feishu_bridge as fb
    monkeypatch.setattr(fb, "_inject", lambda *a: pytest.fail("unexpected injection"))
    w._arm_short_wait("voiceover", REC, row(), {}, 10000)
    jobs = w._short_waits_load()
    assert not w._resume_short_waits(jobs, {"ccp2": row(short=0)}, {"pty-1": "voiceover"}, BOTS, 10400)
    assert jobs == {}


def test_short_wait_keeps_waiting_when_api_has_not_recovered(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "notify", lambda *a: True)
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    monkeypatch.setattr(w, "read_pane", lambda pty: pytest.fail("quota still full; must not read or inject"))
    w._arm_short_wait("voiceover", REC, row(), {}, 10000)
    jobs = w._short_waits_load()
    w._resume_short_waits(jobs, {"ccp2": row(reset_at=20000)}, {"pty-1": "voiceover"}, BOTS, 10400)
    assert jobs["voiceover"]["due_at"] == 20000


def test_new_owner_message_cancels_pending_automatic_wakeup(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "notify", lambda *a: True)
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    monkeypatch.setattr(w, "read_pane", lambda pty: pytest.fail("owner took control; must not read or inject"))
    w._arm_short_wait("voiceover", REC, row(), {}, 10000)
    inbound = tmp_path / "bridge-inbound-voiceover.jsonl"
    inbound.write_text('{"text":"/stop"}\n', encoding="utf-8")
    jobs = w._short_waits_load()
    w._resume_short_waits(jobs, {"ccp2": row(short=0)}, {"pty-1": "voiceover"}, BOTS, 10400)
    assert jobs == {} and w._short_waits_load() == {}


def test_unreadable_inbound_ledger_cannot_arm_or_fire_a_wakeup(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "notify", lambda *a: True)
    monkeypatch.setattr(w, "_inbound_size", lambda bot: None)
    assert not w._arm_short_wait("voiceover", REC, row(), {}, 10000)
    assert not w.SHORT_WAIT_PATH.exists()


def test_restarted_watchdog_never_repeats_a_wakeup_with_uncertain_submit(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "SHORT_WAIT_PATH", tmp_path / "short-waits.json")
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    monkeypatch.setattr(w, "read_pane", lambda pty: pytest.fail("firing job must not retry"))
    messages = []
    monkeypatch.setattr(w, "notify", lambda bot, kind, body: messages.append(kind) or True)
    jobs = {"voiceover": {"identity": dict(REC), "due_at": 10400,
                          "notified": True, "status": "firing"}}
    assert w._short_waits_save(jobs)
    reloaded = w._short_waits_load()
    w._resume_short_waits(reloaded, {"ccp2": row(short=0)}, {"pty-1": "voiceover"}, BOTS, 10400)
    assert reloaded == {} and messages == ["short_resume_failed"]
    assert w._short_waits_load() == {}


def test_only_weekly_limit_and_same_profile_series_can_select_a_new_account(monkeypatch):
    source = {"profile": "ccp", "runtime": "claude", "status": "ok",
              "session_percent": 0, "weekly_percent": 100, "verdict": "满"}
    ccp2 = {"profile": "ccp2", "runtime": "claude", "status": "ok",
            "session_percent": 0, "weekly_percent": 20, "verdict": "够用"}
    cck = dict(ccp2, profile="cck")  # Claude CLI with another backend/series
    cxp = dict(ccp2, profile="cxp", runtime="codex")
    monkeypatch.setattr(q, "auto_failover_profiles", lambda: frozenset({"ccp2", "cck", "cxp"}))
    assert w.limit_kind(WEEKLY_SCREEN, source) == "weekly"
    assert q.pick([cck, cxp, ccp2], exclude=["ccp"], prefer_runtime="claude", source=source) == ccp2
    assert q.pick([cck, cxp], exclude=["ccp"], prefer_runtime="claude", source=source) is None
    assert q.same_failover_series({"profile": "cxp", "runtime": "codex"},
                                  {"profile": "cxp2", "runtime": "codex"})
    assert not q.same_failover_series({"profile": "kp", "runtime": "kimi"},
                                      {"profile": "ccp2", "runtime": "claude"})
    assert w.limit_kind(WEEKLY_SCREEN, {"verdict": "问不到", "runtime": "kimi"}) is None
    assert w.limit_kind(SESSION_SCREEN, row(short=100, weekly=45)) == "short"
    assert w.limit_kind(SESSION_SCREEN + "\n" + WEEKLY_SCREEN,
                        row(short=0, weekly=100)) == "weekly"
    assert w.limit_kind(SESSION_SCREEN, row(short=0, weekly=100)) is None
    assert w.limit_kind("Usage limit reached", row(short=100, weekly=100)) is None


def test_watchdog_failover_rejects_short_quota_before_any_handoff(monkeypatch):
    import feishu_bridge as fb
    monkeypatch.setattr(fb, "load_bots", lambda: [{"name": "voiceover", "profile": "ccp2"}])
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    monkeypatch.setattr(q, "collect", lambda: [row(short=100, weekly=45)])
    monkeypatch.setattr(q, "auto_failover_profiles", lambda: frozenset({"ccp"}))
    monkeypatch.setattr(w, "snapshot_handoff", lambda *a, **kw: pytest.fail("must not hand off short limit"))
    assert w.failover("voiceover", target="ccp") is False


@pytest.mark.parametrize("target,runtime", [("cxp", "codex"), ("cck", "claude")])
def test_watchdog_rejects_explicit_cross_runtime_or_series_even_for_weekly_limit(
        monkeypatch, target, runtime):
    import feishu_bridge as fb
    monkeypatch.setattr(fb, "load_bots", lambda: [{"name": "voiceover", "profile": "ccp2"}])
    monkeypatch.setattr(w, "session_record", lambda bot: dict(REC))
    target_row = {"profile": target, "runtime": runtime, "status": "ok",
                  "session_percent": 0, "weekly_percent": 10, "verdict": "够用"}
    monkeypatch.setattr(q, "collect", lambda: [row(short=0, weekly=100), target_row])
    monkeypatch.setattr(q, "auto_failover_profiles", lambda: frozenset({target}))
    monkeypatch.setattr(w, "snapshot_handoff", lambda *a, **kw: pytest.fail("cross-series handoff"))
    assert w.failover("voiceover", target=target) is False


def test_status_does_not_report_cross_series_account_as_a_usable_failover(monkeypatch):
    source = {"profile": "ccp", "runtime": "claude", "status": "ok",
              "session_percent": 0, "weekly_percent": 100, "verdict": "满"}
    cxp = {"profile": "cxp", "runtime": "codex", "status": "ok",
           "session_percent": 0, "weekly_percent": 10, "verdict": "够用"}
    ccp2 = dict(cxp, profile="ccp2", runtime="claude")
    monkeypatch.setattr(w, "_iter_bots", lambda: [{"name": "voiceover"}])
    monkeypatch.setattr(w, "_profile_of", lambda *a: "ccp")
    monkeypatch.setattr(q, "auto_failover_profiles", lambda: frozenset({"cxp", "ccp2"}))
    assert w.failover_readiness([source, cxp])["claude"]["ok"] is False
    assert w.failover_readiness([source, cxp, ccp2])["claude"]["usable"] == ["ccp2"]


@pytest.mark.parametrize("screen,quota_row,expected", [
    (SESSION_SCREEN, row(reset_at=20000), "short_wait"),
    (SESSION_SCREEN, {"profile": "ccp2", "runtime": "claude", "status": "error",
                      "verdict": "问不到", "weekly_percent": None}, "short_wait"),
    (WEEKLY_SCREEN, row(short=0, weekly=100), "failover"),
])
def test_live_watchdog_loop_routes_short_wait_and_weekly_failover(
        monkeypatch, tmp_path, screen, quota_row, expected):
    def configure(events):
        monkeypatch.setattr(w, "_profile_of", lambda *a: "ccp2")
        monkeypatch.setattr(w, "session_record", lambda bot: dict(REC, pty="fake-pty", workspace_id="fake-workspace"))
        monkeypatch.setattr(w.agent_quota, "collect", lambda: [quota_row])
        monkeypatch.setattr(w, "_short_waits_load", lambda: {})
        monkeypatch.setattr(w, "_short_waits_save", lambda jobs: True)
        monkeypatch.setattr(w, "failover", lambda *a, **kw: events.append(("failover", 1)) or True)

    events = run_scenes(monkeypatch, tmp_path, [{"screen": screen}, {"screen": screen}], configure=configure)
    assert (expected, 1) in events
    assert ("failover", 1) in events if expected == "failover" else not any(e[0] == "failover" for e in events)
