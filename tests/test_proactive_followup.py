import sys
import copy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_followup as follow


def native_addresses_fixture(monkeypatch):
    import docio_cli
    import send_feishu_msg
    def msg(mid, sender, target):
        return dict(message_id=mid, chat_id="oc_group", deleted=False, mentions=[{"id":target}],
                    sender=dict(id=sender, sender_type="app", id_type="app_id", tenant_key="tenant"))
    rows = {"orig":msg("orig","cli_desk","cli_worker"), "query":msg("query","cli_desk","cli_worker"),
            "receipt":msg("receipt","cli_worker","cli_desk")}
    def fetch(args, *, profile, timeout):
        assert profile=="desk" and args[:4]==["im","+messages-mget","--as","bot"]
        return dict(messages=[rows[k] for k in args[args.index("--message-ids")+1].split(",")])
    monkeypatch.setattr(docio_cli,"run_lark",fetch)
    monkeypatch.setattr(docio_cli,"_data",lambda response:response)
    monkeypatch.setattr(send_feishu_msg,"resolve_app_id",lambda bot:"cli_desk")
    route = dict(kind="a2a", dest="oc_group", at="ou_first", peer="worker")
    original = dict(message_id="orig", ts=1, target="oc_group",route=route,text="original")
    query = dict(message_id="query", ts=2, target="oc_group",
                 route={**route,"peer":"ou_second","at":"ou_second","coordination":"receipt"},text="respond to orig")
    reply = dict(message_id="receipt",ts=3,route=dict(kind="a2a", dest="oc_group", peer="desk", reply_to="orig"))
    monkeypatch.setattr(follow.dispatch.bridge_history,"_outbound_from_ledger",
                        lambda bot:[original,query] if bot=="desk" else [reply])
    monkeypatch.setattr(follow,"local_receipt",lambda *args:None)
    return rows, original, query, reply


@pytest.mark.parametrize("error", ["wrong-app","wrong-sender","wrong-group","extra-mention","deleted","wrong-task"])
def test_sdk_address_alias_is_not_a_wildcard(monkeypatch, error):
    rows, original, query, _ = native_addresses_fixture(monkeypatch)
    assert follow.verified_mention_alias("desk",original,query)["recipient_app"]=="cli_worker"
    raw=rows["query"]
    if error=="wrong-app": raw["mentions"][0]["id"]="cli_else"
    elif error=="wrong-sender": raw["sender"]["id"]="cli_else"
    elif error=="wrong-group": raw["chat_id"]="oc_else"
    elif error=="extra-mention": raw["mentions"].append({"id":"cli_third"})
    elif error=="deleted": raw["deleted"]=True
    else: original["route"]["task_id"], query["route"]["task_id"] = "first","second"
    assert follow.verified_mention_alias("desk",original,query) is None


def test_reused_named_address_links_existing_request_once_and_verifies_actual_reply(tmp_path, monkeypatch):
    native, original, alternate, _ = native_addresses_fixture(monkeypatch)
    current = dict(original, message_id="current", ts=3)
    native["current"] = dict(native["orig"], message_id="current")
    inbound = []
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger",
                        lambda bot: [original, alternate, current] if bot == "desk" else [])
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound, "read_records", lambda *args: inbound)
    monkeypatch.setattr(follow.dispatch, "progress", lambda *args, **kwargs: {"state":"未找到匹配派单"})
    monkeypatch.setattr(follow, "local_addressed_receipt", lambda *args: None)
    tracker = follow.Followup("desk", tmp_path)
    tracker.register([dict(record(), request_mid="orig")])
    tracker.link("sample", "query")
    # Old releases stored a named continuation without its native app identity.
    state = tracker.read()
    state["tasks"]["sample"]["updates"].append("current")
    tracker.save(state)
    native["current"]["mentions"] = [{"id":"cli_other"}]
    with pytest.raises(ValueError, match="同一收件应用"):
        tracker.link("sample", "current")
    native["orig"]["mentions"] = [{"id":"cli_other"}]
    with pytest.raises(ValueError, match="同一收件应用"):
        tracker.link("sample", "current")
    native["orig"]["mentions"] = [{"id":"cli_worker"}]
    native["current"]["mentions"] = [{"id":"cli_worker"}]
    tracker.link("sample", "current")
    tracker.link("sample", "current")
    row = tracker.read()["tasks"]["sample"]
    assert row["updates"] == ["query", "current"]
    assert row["mention_aliases"]["current"]["address"] == "ou_first"
    assert tracker.check("sample")["state"] == "同群同一收件应用已投递，待回"
    inbound.append(dict(message_id="receipt", source="feishu-ws", ts=4, chat_id="oc_group",
                        sender=dict(is_bot=True, open_id="ou_second"),
                        raw_text="结论 [飞书_from_Worker_to_desk reply_to=current]"))
    native["receipt"]["sender"]["id"] = "cli_other"
    assert not tracker.check("sample")["reply_mid"]
    native["receipt"]["sender"]["id"] = "cli_worker"
    native["receipt"]["mentions"] = [{"id":"cli_other"}]
    assert not tracker.check("sample")["reply_mid"]
    native["receipt"]["mentions"] = [{"id":"cli_desk"}]
    assert tracker.check("sample")["reply_mid"] == "receipt"
    inbound[0]["raw_text"] = "结论 [飞书_from_Worker_to_desk reply_to=orig]"
    checked = tracker.check("sample")
    assert not checked["reply_mid"] and checked["late_original_replies"] == ["receipt"]
    assert not tracker.read()["tasks"]["sample"]["closed"]


def test_only_a_verified_receipt_query_can_attach_an_answer_to_a_closed_parent(tmp_path, monkeypatch):
    rows, original, query, reply = native_addresses_fixture(monkeypatch)
    board = follow.Followup("desk",tmp_path)
    board.register([dict(id="parent",peer="worker",request_mid="orig",next_check="2020-01-01T00:00:00+00:00"),
                    dict(id="clock",peer="ou_second",request_mid="query",next_check="2020-01-01T00:00:00+00:00")])
    state=board.read()
    parent=state["tasks"]["parent"]
    parent.update(closed=True,acceptance_receipt="old-receipt",conclusion="prior scope")
    board.save(state)
    def finish():
        board.close("clock","alias and remaining evidence only","https://example.org/preview","receipt","parent")
    original_query=copy.deepcopy(query)
    query["text"]="another question"
    with pytest.raises(ValueError): finish()
    query.update(original_query)
    reply["ts"]=1
    with pytest.raises(ValueError): finish()
    reply["ts"]=3
    rows["receipt"]["sender"]["id"]="cli_else"
    with pytest.raises(ValueError): finish()
    rows["receipt"]["sender"]["id"]="cli_worker"
    finish()
    tasks=board.read()["tasks"]
    assert tasks["clock"]["next_check"] is None and tasks["clock"]["parent_task"]=="parent"
    assert tasks["parent"]["conclusion"]=="prior scope" and tasks["parent"]["acceptance_receipt"]=="old-receipt"
    assert tasks["parent"]["supplemental_receipts"]["clock"]["receipt"]=="receipt"


def test_verify_named_control_reply_across_addresses_without_earlier_alias(tmp_path, monkeypatch):
    native, original, query, _ = native_addresses_fixture(monkeypatch)
    query["route"] = {**original["route"], "coordination":"control"}
    tracker = follow.Followup("desk", tmp_path)
    tracker.register([dict(record(), request_mid="orig")])
    tracker.link("sample", "query")
    assert not tracker.read()["tasks"]["sample"].get("mention_aliases")
    native["query"]["mentions"] = [{"id":"cli_else"}]
    with pytest.raises(ValueError, match="同一收件应用"):
        tracker.link("sample", "query", verify_app=True)
    native["query"]["mentions"] = [{"id":"cli_worker"}]
    tracker.link("sample", "query", verify_app=True)
    evidence = dict(message_id="receipt", source="feishu-ws", ts=4, chat_id="oc_group",
                    sender=dict(is_bot=True, open_id="ou_second"),
                    raw_text="恢复 [飞书_from_Worker_to_desk reply_to=query]")
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound, "read_records", lambda *a: [evidence])
    monkeypatch.setattr(follow.dispatch, "progress", lambda *a, **kw: {"state":"已向目标发送"})
    assert tracker.check("sample")["reply_mid"] == "receipt"
    tracker.close("sample", "原生恢复", "https://example.org/readback", "receipt")
    assert tracker.read()["tasks"]["sample"]["next_check"] is None


def test_first_named_request_corroborates_receiver_sdk_envelope_and_both_apps(tmp_path, monkeypatch):
    import send_feishu_msg

    native, original, _, _ = native_addresses_fixture(monkeypatch)
    tracker = follow.Followup("desk", tmp_path)
    tracker.register([dict(record(), request_mid="orig")])
    envelope = dict(message_id="orig", chat_id="oc_group", bot="worker", source="feishu-ws",
                    mentioned_bot=True, ts=2, sender={"is_bot":True, "open_id":"ou_receiver_scope"})
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound, "read_records",
                        lambda state, peer: [envelope] if peer == "worker" else [])
    monkeypatch.setattr(send_feishu_msg, "resolve_app_id", lambda bot:"cli_" + bot)
    envelope["mentioned_bot"] = False
    with pytest.raises(ValueError):
        tracker.link("sample", "orig", verify_app=True)
    envelope["mentioned_bot"] = True
    native["orig"]["mentions"] = [{"id":"cli_other"}]
    with pytest.raises(ValueError):
        tracker.link("sample", "orig", verify_app=True)
    native["orig"]["mentions"] = [{"id":"cli_worker"}]
    tracker.link("sample", "orig", verify_app=True)
    monkeypatch.setattr(follow.dispatch, "progress", lambda *a, **kw: {"state":"未找到匹配派单"})
    monkeypatch.setattr(follow, "local_addressed_receipt", lambda *args:None)
    observed = tracker.check("sample")
    assert observed["state"] == "目标桥已收件" and observed["last_signal_at"] == 2
    assert not observed["reply_mid"] and not tracker.read()["tasks"]["sample"].get("updates")


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


def test_external_wait_suppresses_only_reviewed_receipt_and_wakes_for_new_evidence(tmp_path, monkeypatch):
    tracker = follow.Followup("desk", tmp_path)
    tracker.register([record()])
    observation = dict(state="执行者原单已回", request_mid="om_a", reply_mid="receipt1")
    monkeypatch.setattr(tracker, "observe", lambda row: dict(observation))
    now = 1700000000
    monkeypatch.setattr(follow.time, "time", lambda: now)
    assert tracker.tick(wake=False)["attention"] == ["sample"]
    with pytest.raises(ValueError, match="回执"):
        tracker.await_input("sample", "unrelated", "等待侧拍原片")
    tracker.await_input("sample", "receipt1", "等待侧拍原片")
    assert all(a["ids"] == a["reviewed_ids"] == ["sample"] for a in tracker.read()["alarms"].values())
    now += 3600
    assert tracker.tick(wake=False)["attention"] == []
    assert not tracker.read()["tasks"]["sample"]["closed"]
    observation["late_original_replies"] = ["later-on-earlier-request"]
    now += 61
    assert tracker.tick(wake=False)["attention"] == ["sample"]
    assert "awaiting_input" not in tracker.read()["tasks"]["sample"]


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
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger", lambda name:[])
    monkeypatch.setattr(follow.bridge_activity, "read_activity", lambda *a:{"state":"active"})
    assert follow.local_receipt("owner", "worker", "new", "chat")["session"]["state"] == "active"
    assert follow.local_receipt("other-owner", "worker", "new", "chat") is None
    original["mid"] = "another-turn"
    sent = {"message_id":"signed", "ts":2, "route":{"kind":"a2a", "peer":"owner", "dest":"chat", "reply_to":"new"}}
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger", lambda name:[sent])
    assert follow.local_receipt("owner", "worker", "new", "chat")["reply_mid"] == "signed"
    original["mid"] = "new"
    assert follow.local_receipt("owner", "worker", "new", "chat")["reply_mid"] == "signed"
    original["mid"] = "another-turn"
    sent["route"]["dest"] = "other-chat"
    assert follow.local_receipt("owner", "worker", "new", "chat") is None
    original["mid"] = "new"
    inbound["mentioned_bot"] = False
    assert follow.local_receipt("owner", "worker", "new", "chat") is None


def test_one_dispatch_can_deliver_two_previews_with_distinct_earlier_receipts(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record("left"), record("right")])
    monkeypatch.setattr(tracker, "observe", lambda row: {
        "state":"已回信", "reply_mid":"r2", "reply_mids":["r2","r1"], "request_mid":"om_a"})
    tracker.check("left")
    tracker.check("right")
    tracker.close("left", "左件已验", "https://example.org/left", "r1")
    with pytest.raises(ValueError, match="各有"):
        tracker.close("right", "右件已验", "https://example.org/right", "r1")
    with pytest.raises(ValueError, match="各有"):
        tracker.close("right", "右件已验", "https://example.org/left", "r2")
    tracker.close("right", "右件已验", "https://example.org/right", "r2")


def test_authentic_final_on_original_after_supplement_can_close_legacy_registration(tmp_path, monkeypatch):
    tracker = follow.Followup("sender", tmp_path)
    tracker.register([record()])
    requests = [dict(message_id=mid, ts=ts, route={"peer":"worker", "at":"addr", "dest":"group", "kind":"a2a"})
                for mid, ts in [("om_a", 1), ("om_extra", 10)]]
    route = {"reply_to":"om_a", "peer":"sender", "dest":"group", "kind":"a2a"}
    replies = [dict(message_id="stage", ts=2, route=route), dict(message_id="done", ts=12, route=route),
               dict(message_id="wrong", ts=13, route={**route, "dest":"another-group"})]
    monkeypatch.setattr(follow.dispatch.bridge_history, "_outbound_from_ledger",
                        lambda who: requests if who == "sender" else replies)
    monkeypatch.setattr(follow.dispatch, "progress", lambda *a, **kw: {"state":"目标桥已收件"})
    tracker.link("sample", "om_extra")
    legacy = tracker.read()
    del legacy["tasks"]["sample"]["dispatch_chat"]
    tracker.save(legacy)
    evidence = tracker.check("sample")
    assert evidence["late_original_replies"] == ["done"]
    for receipt in ["stage", "wrong"]:
        with pytest.raises(ValueError, match="作者回执"):
            tracker.close("sample", "验收通过", "https://example.org/review", receipt)
    tracker.close("sample", "验收通过", "https://example.org/review", "done")
