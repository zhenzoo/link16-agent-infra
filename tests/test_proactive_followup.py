import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_followup as follow


def record(key="sample"):
    return dict(id=key, peer="worker", request_mid="om_a", next_check="2020-01-01T00:00:00+00:00",
                interval_seconds=60)


def test_active_rearms_but_stagnant_observations_wake_owner(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record()])
    monkeypatch.setattr(tracker, "observe", lambda row: {"state": "目标回合已接手", "session": "active"})
    now = [1700000000.]
    monkeypatch.setattr(follow.time, "time", lambda: now[0])
    for number in range(3):
        result = tracker.tick(wake=False)
        assert result["inspected"] == 1
        assert result["attention"] == (["sample"] if number == 2 else [])
        assert tracker.read()["tasks"]["sample"]["next_check"] == now[0] + 60
        now[0] += 61


def test_matching_request_cannot_be_rebound_or_closed_by_unrelated_reply(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record()])
    with pytest.raises(ValueError, match="冲突"):
        tracker.register([{**record(), "request_mid": "other"}])
    monkeypatch.setattr(tracker, "observe", lambda row: {"state": "已答复", "reply_mid": "om_b"})
    tracker.tick(wake=False)
    assert not tracker.read()["tasks"]["sample"]["closed"]
    for preview, receipt in [("", "om_b"), ("https://example.org/preview", "wrong")]:
        with pytest.raises(ValueError):
            tracker.close("sample", "验收通过", preview, receipt)
    verified = tmp_path / "preview.html"
    verified.write_text("tested")
    tracker.close("sample", "独立验收通过", str(verified), "om_b")
    assert follow.Followup("sender", tmp_path).tick(wake=False)["inspected"] == 0


def test_idle_worker_and_future_due_have_independent_results(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    future = "2090-06-01T12:00:00+08:00"
    tracker.register([record("idle"), {**record("later"), "next_check": future}])
    monkeypatch.setattr(tracker, "observe", lambda row: {"state": "目标桥已收件", "session": "idle"})
    assert tracker.tick(wake=False)["attention"] == ["idle"]
    tracker.register([record("later")])
    assert tracker.read()["tasks"]["later"]["next_check"] == follow.timestamp(future)


def test_native_completion_and_failed_poll_remain_open(tmp_path):
    tracker = follow.Followup("sender", tmp_path)
    path = tmp_path / "rollout.jsonl"
    path.write_text('{"type":"event_msg","timestamp":"2020","payload":{"type":"task_complete"}}')
    tracker.register([dict(id="ok", native_log=str(path), next_check="2020-01-01T00:00:00+00:00"),
                      dict(id="missing", native_log=str(tmp_path / "missing"), next_check="2020-01-01T00:00:00+00:00")])
    tracker.tick(wake=False)
    tasks = tracker.read()["tasks"]
    assert tasks["ok"]["evidence"]["state"] == "task_complete"
    assert tasks["missing"]["evidence"]["state"] == "检查失败"
    assert not any(row["closed"] for row in tasks.values())


def test_new_followup_preserves_stage_reply_but_disqualifies_it_from_final_acceptance(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record()])
    route = {"kind": "a2a", "peer": "worker", "dest": "chat", "at": "worker-id"}
    outgoing = [dict(message_id="om_a", ts=1, route=route), dict(message_id="om_update", ts=2, route=route),
                dict(message_id="wrong", ts=3, route={**route, "dest":"different-chat"})]
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger", lambda bot: outgoing)
    monkeypatch.setattr(tracker, "observe", lambda row: {"state": "已答复", "reply_mid": "progress-only"})
    tracker.tick(wake=False)
    with pytest.raises(ValueError, match="补单"):
        tracker.link("sample", "wrong")
    tracker.link("sample", "om_update")
    assert tracker.read()["tasks"]["sample"]["prior_evidence"][-1]["reply_mid"] == "progress-only"
    with pytest.raises(ValueError):
        tracker.close("sample", "完成", "https://example.org/review", "progress-only")


def test_dependent_task_keeps_prerequisites_when_it_gets_an_owner(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record("upstream"), dict(id="handoff", dependency=["upstream"],
                     next_check="2020-01-01T00:00:00+00:00")])
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger", lambda bot: [
        dict(message_id="new", ts=1, route={"peer":"owner", "dest":"chat", "kind":"a2a"})])
    tracker.link("handoff", "new")
    monkeypatch.setattr(tracker, "observe", lambda row: {"state":"已回复", "reply_mid":"receipt"})
    tracker.tick(wake=False)
    with pytest.raises(ValueError, match="前置"):
        tracker.close("handoff", "交付", "https://example.org/review", "receipt")


def test_direct_group_receipt_uses_sender_group_and_original_mid(monkeypatch):
    peer = "ou_fixture"
    outgoing = {"message_id":"om_original", "target":"oc_private", "ts":100,
                "route":{"kind":"a2a", "peer":peer, "at":peer, "dest":"oc_private"}}
    valid = {"message_id":"om_valid", "source":"feishu-ws", "chat_id":"oc_private", "ts":101,
             "sender":{"is_bot":True, "open_id":peer},
             "raw_text":"阶段回执 [飞书_from_wOrKer_to_ou_owner reply_to=om_original]"}
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger", lambda bot:[outgoing])
    records = [valid, {**valid,"chat_id":"oc_other"}, {**valid,"sender":{"is_bot":False,"open_id":peer}},
               {**valid,"sender":{"is_bot":True,"open_id":"ou_other"}},
               {**valid,"raw_text":valid["raw_text"].replace("om_original", "om_other")}]
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound, "read_records", lambda *a:records)
    assert follow.addressed_reply("owner", peer, "om_original")["reply_mid"] == "om_valid"
    records.pop(0)
    assert follow.addressed_reply("owner", peer, "om_original") is None


def test_local_named_worker_only_accepts_changed_address_with_authenticated_turn(monkeypatch):
    original = {"mid":"new", "kind":"a2a", "peer":"owner", "dest":"chat", "at":"sender-scope-id", "active":True}
    inbound = {"message_id":"new", "source":"feishu-ws", "chat_id":"chat", "bot":"worker",
               "sender":{"is_bot":True, "open_id":"sender-scope-id"}, "mentioned_bot":True}
    monkeypatch.setattr(follow.dispatch, "target_identity", lambda peer: ({"peer":peer}, {"name":peer}, None))
    monkeypatch.setattr(follow.dispatch, "read_object", lambda path: original if "turn-route" in path.name else {})
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound, "read_records", lambda *a:[inbound])
    monkeypatch.setattr(follow.dispatch, "session_view", lambda *a:{"state":"active"})
    assert follow.local_receipt("owner", "worker", "new", "chat")["session"]["state"] == "active"
    assert follow.local_receipt("other-owner", "worker", "new", "chat") is None
    inbound["mentioned_bot"] = False
    assert follow.local_receipt("owner", "worker", "new", "chat") is None
