"""Business arrangement is opt-in, verified against registered identities and independent dispatch."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
from proactive_business_list import assignments, bullets, check, roster_names


NAMES = {"leader","worker-one","worker-two","remote-model"}
LEDGERS = ({"items":{},"tasks":{}}, {}, {}, {})


def plan():
    return {"mode":"arrange_business","dispatch_bot":"leader","previous":[],
            "items":[{"id":"new","name":"核对报价","agent":"worker-one","expected":"两份原报价"}]}


def arranged(p,ledgers=LEDGERS):
    return assignments(p,NAMES,ledgers)


def test_actual_sources():
    local=[{"name":"worker-one","display_name":"Worker No. 1"}]
    fleet=[{"name":"old-one","send_key":"worker-one"}, {"name":"remote-existing"}]
    members=[{"name":"Remote-Model","member_id":"ou_1","app_id":"cli_1"},
             {"name":"ambiguous","member_id":"ou_2","app_id":"cli_2"},
             {"name":"ambiguous","member_id":"ou_3","app_id":"cli_3"}]
    assert roster_names(local,fleet,members)=={"worker-one","remote-existing","remote-model"}


def test_new_business_and_correction():
    p=plan()
    wanted="- 核对报价｜worker-one｜两份原报价"
    assert bullets(arranged(p))==[wanted]
    with pytest.raises(ValueError):
        check(wanted.replace("worker-one","worker-two"),arranged(p))
    check(wanted+"\n",arranged(p))


@pytest.mark.parametrize("value",["0号位","各业务原作者","worker-ones","worker-one/worker-two","",["worker-one","worker-two"]])
def test_invalid_agent(value):
    p=plan()
    p["items"][0]["agent"]=value
    with pytest.raises(ValueError):
        arranged(p)


@pytest.mark.parametrize("key,value",[("name","核对\n报价"),("expected","两份｜报价"),("expected",""),("expected","待定")])
def test_invalid_field(key,value):
    p=plan()
    p["items"][0][key]=value
    with pytest.raises(ValueError):
        arranged(p)


@pytest.mark.parametrize("value",["核对报价｜worker-one｜两份原报价","- 核对报价 | worker-one | 两份原报价",
                                 "- 核对报价｜worker-one｜两份原报价\n\n","- 核对报价｜worker-one｜别的交付物"])
def test_invalid_pasted_structure(value):
    with pytest.raises(ValueError):
        check(value,arranged(plan()))


def test_tampered_truth_source_still_fails_and_unknown_address_fails_closed():
    p=plan()
    follow={"new":{"peer":"ou_native"}}
    data=({},follow,{},{"ou_native":"worker-one"})
    data=({"tasks":{},"items":{}},*data[1:])
    assert arranged(p,data)[0][2]=="worker-one"
    p["items"][0]["agent"]="worker-two"
    p["previous"]=[copy.deepcopy(p["items"][0])]
    with pytest.raises(ValueError,match="worker-one"):
        arranged(p,data)
    with pytest.raises(ValueError,match="收件地址"):
        arranged(p,(*data[:3],{}))


def test_bound_item_overrides_old_dispatch_only_with_confirmed_continuation():
    p=plan()
    p["items"][0]["agent"]="worker-two"
    p["items"][0]["author_task"]="continuation"
    ownership={"tasks":{"leader/continuation":"item"},"items":{"item":{"owner":"worker-two"}}}
    follow={"new":{"peer":"worker-one"},"continuation":{"peer":"worker-two","request_mid":"om_abc"}}
    links={"new":{"task":"continuation","request_mid":"om_abc"}}
    assert arranged(p,(ownership,follow,links,{}))[0][2]=="worker-two"
    with pytest.raises(ValueError):
        arranged(p,(ownership,follow,{},{}))
    follow["continuation"]["request_mid"]="om_other"
    with pytest.raises(ValueError):
        arranged(p,(ownership,follow,links,{}))


def test_similar_prefix_has_no_authority():
    p=plan()
    ownership={"tasks":{"leader/new-other":"item"},"items":{"item":{"owner":"worker-two"}}}
    assert arranged(p,(ownership,{}, {},{}))[0][2]=="worker-one"


def test_ordinary_reports_are_outside_this_mode():
    p=plan()
    p["mode"]="report"
    with pytest.raises(ValueError,match="仅在"):
        arranged(p)


def test_eight_part_round_checks_actual_inbound_dispatch_and_provider_receipts(tmp_path, monkeypatch):
    import bridge_env, bridge_history, bridge_inbound, feishu_bridge
    import proactive_business_list as business
    import send_feishu_msg as sender
    bot = "leader"
    parts = [f"第{i}项核对原表。\n" for i in range(8)]
    sections = [{"text": s, "items": [f"item{i}"]} for i, s in enumerate(parts)]
    p = dict(mode="arrange_business", dispatch_bot=bot, previous=[], source_mid="om_original",
             source_parts=sections, items=[dict(id=f"item{i}", name=f"事项{i}", agent=f"worker-{i}",
                                               expected="经核对的原表") for i in range(8)])
    monkeypatch.setattr(business, "registered_names", lambda bot: {f"worker-{i}" for i in range(8)})
    monkeypatch.setattr(business, "dispatch_ledgers", lambda bot: LEDGERS)
    monkeypatch.setattr(bridge_env, "assert_sender_identity", lambda bot: None)
    for module in (bridge_history, feishu_bridge, sender):
        monkeypatch.setattr(module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(feishu_bridge, "load_owner_record", lambda bot: {"open_id": "ou_owner"})
    original = dict(schema=bridge_inbound.SCHEMA, kind="inbound", source=bridge_inbound.NATIVE_SOURCE,
                    bot=bot, message_id="om_original", chat_type="p2p",
                    raw_text="".join(parts), sender={"open_id": "ou_owner", "is_bot": False})
    bridge_inbound.append_record(tmp_path, bot, original)
    path = tmp_path / "round.json"
    path.write_text(json.dumps(p), encoding="utf-8")
    for bad in (sections[:-1], list(reversed(sections)), [{}]):
        with pytest.raises(ValueError):
            business.dispatch_parts(dict(p, source_parts=bad), bot)
    business.main(["arrange", "--round-json", str(path)])
    path.write_text(json.dumps(dict(p, items=list(reversed(p["items"])))), encoding="utf-8")
    with pytest.raises(ValueError, match="已修改"):
        business.review_outgoing(path, bot, "item0", "worker-0", parts[0])
    path.write_text(json.dumps(p), encoding="utf-8")
    monkeypatch.setattr(sender, "local_name", lambda bot: bot)
    monkeypatch.setattr(sender, "assert_sender_identity", lambda bot: None)
    monkeypatch.setattr(sender, "peer_address", lambda *args: ("oc_group", "ou_worker"))
    calls = []
    def send(bot, peer, text, **options):
        calls.append(options["message_uuid"])
        return {"ok": True, "message_id": f"om_sent{len(calls)}", "mirror": None}
    monkeypatch.setattr(sender, "send_agent_message", send)
    def run(i, peer, text, *extra):
        monkeypatch.setattr(sys, "argv", ["send", "--bot", bot, "--to-agent", peer, "--item", f"item{i}",
                                         "--task-id", f"task{i}", "--in", "oc_group", "--text", text, *extra])
        with pytest.raises(SystemExit) as done:
            sender.main()
        return done.value.code
    assert run(0, "worker-1", parts[0]) != 0
    assert run(0, "worker-0", parts[1]) != 0
    assert not calls
    for i, part in enumerate(parts):
        assert run(i, f"worker-{i}", part) == 0
    assert run(0, "worker-0", parts[0], "--round-json", str(path)) != 0
    assert len(set(calls)) == 8
    business.main(["audit-sends", "--round-json", str(path)])
    monkeypatch.setattr(feishu_bridge, "load_owner_record", lambda bot: {"open_id": "ou_other"})
    with pytest.raises(ValueError, match="本人原文"):
        business.dispatch_parts(p, bot)


def test_remote_group_address_requires_same_live_application(tmp_path, monkeypatch):
    import proactive_business_list as business
    cache = tmp_path / "members.json"
    row = dict(name="Remote-Model-2", app_id="cli_worker", member_id="ou_member")
    stored = dict(bot="lead", chat_id="oc_group", members=[row])
    cache.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(business, "peer_cache", lambda bot: cache)
    monkeypatch.setattr(business, "read_members", lambda bot: stored)
    assert business.verified_member_address("lead", "remote-model-2") == ("oc_group", "ou_member")
    assert business.verified_member_address("lead", "remote-model") is None
    monkeypatch.setattr(business, "read_members", lambda bot: dict(stored, members=[dict(row, app_id="cli_other")]))
    with pytest.raises(ValueError, match="登记已变化"):
        business.verified_member_address("lead", "remote-model-2")
    assert json.loads(cache.read_text(encoding="utf-8")) == stored
