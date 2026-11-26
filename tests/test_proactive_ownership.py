from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_ownership as ownership
from proactive_followup import Followup


def test_product_brief_is_shared_by_preview_and_dispatch_and_skips_nonproduct_work():
    from proactive_dispatch import dispatch_prompt
    source = "只改支脚。\r\n原图照旧。"
    prompt = dispatch_prompt("desk","task",source,["docs/PRD-100-demo.html"])
    assert source in prompt and prompt.count(ownership.PRODUCT_STYLE)==1
    assert ownership.product_brief(prompt)==prompt
    assert "原功能格" in ownership.product_brief("改PRD-210")
    assert ownership.PRODUCT_STYLE not in dispatch_prompt("desk","other","核例会人员的时间。",[])


def test_local_stopped_author_answer_is_handoff_evidence_not_an_external_receipt(tmp_path, monkeypatch):
    import proactive_followup as follow
    monkeypatch.setattr(follow.dispatch, "STATE", tmp_path)
    route = dict(peer="desk",dest="oc_group",at="ou_desk",mid="om_stop")
    (tmp_path/"bridge-turn-route-writer.json").write_text(json.dumps(route))
    inbound = dict(message_id="om_stop",source="feishu-ws",bot="writer",chat_id="oc_group",mentioned_bot=True,
                   sender={"is_bot":True, "open_id":"ou_desk"},ts=100)
    monkeypatch.setattr(follow.dispatch.bridge_history.bridge_inbound,"read_records",lambda *args:[inbound])
    monkeypatch.setattr(follow.dispatch.bridge_history,"_outbound_from_ledger",lambda who:[])
    answer = dict(kind="answer",turn_key="unique",session="session",anchor="turn",text="停笔，已封存。",
                  ts=101,route=route)
    outbox=tmp_path/"bridge-outbox-writer.jsonl"
    outbox.write_text(json.dumps(answer)+"\n")
    evidence=follow.verified_local_turn("desk",{"name":"writer"},"om_stop")
    assert evidence["handoff_receipt"]=="native:session:turn" and not evidence.get("reply_mid")
    answer["route"]={**route,"mid":"different"}
    outbox.write_text(json.dumps(answer)+"\n")
    monkeypatch.setattr(follow.bridge_activity, "read_activity", lambda *args:{"state":"idle"})
    assert not follow.verified_local_turn("desk",{"name":"writer"},"om_stop").get("handoff_receipt")


def test_single_owner_across_managers_parallel_items_and_same_task_updates(tmp_path):
    registry = ownership.ItemOwners(tmp_path)
    def take(peer):
        try:
            with registry.author("desk-"+peer, "product", peer, "work-"+peer):
                return peer
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        won = [peer for peer in pool.map(take, ["first","second"]) if peer]
    assert len(won) == 1
    peer = won[0]
    for _ in range(2):
        with registry.author("desk-"+peer, "product", peer, "work-"+peer, "ou_actor"):
            pass
    with registry.author("desk-"+peer, "product","ou_actor","supplement", "ou_actor"):
        pass
    with pytest.raises(ValueError, match="唯一责任人"):
        with registry.author("unverified", "product","ou_actor","elsewhere", "ou_actor"):
            pass
    with registry.author("another-desk", "other-product", "other-worker","other-task"):
        pass
    with pytest.raises(ValueError, match="原任务"):
        with registry.author("desk-"+peer, "other-product", peer, "work-"+peer):
            pass


def test_transfer_requires_stop_receipt_and_handoff_before_releasing_former_writer(tmp_path, monkeypatch):
    registry = ownership.ItemOwners(tmp_path)
    tracker = Followup("desk", tmp_path)
    tracker.register([dict(id="old", peer="old", request_mid="om_stop", next_check="2020-01-01T00:00:00+00:00"),
                      dict(id="new", peer="new", request_mid="om_next", next_check="2020-01-01T00:00:00+00:00")])
    with registry.author("desk", "product", "old","old"):
        pass
    registry.stop("product","old","om_stop")
    for peer in ("old","new"):
        with pytest.raises(ValueError, match="停笔"):
            with registry.author("desk","product",peer,peer):
                pass
    handoff = tmp_path/"handoff.json"
    handoff.write_text("actual handoff", encoding="utf8")
    monkeypatch.setattr(Followup, "observe", lambda self, row:
                        {"state":"已回", "request_mid":row["request_mid"],"reply_mid":"om_stopped"})
    with pytest.raises(ValueError, match="回执"):
        registry.transfer("product","old","new","om_stop","wrong",str(handoff),"desk")
    registry.transfer("product","old","new","om_stop","om_stopped",str(handoff),"desk")
    tracker.retire("old","new","om_stopped",str(handoff))
    rows = tracker.read()["tasks"]
    assert rows["old"]["retired_to"] == "new" and not rows["old"]["closed"] and rows["old"]["next_check"] is None
    assert not tracker.delivered(rows, "old") and rows["new"]["inherited_work"][0]["id"]=="old"
    with registry.author("desk","product","new","new"):
        pass
    with pytest.raises(ValueError, match="唯一"):
        with registry.author("desk","product","old","old"):
            pass
