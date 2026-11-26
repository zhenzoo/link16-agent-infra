"""Business arrangement is opt-in, verified against registered identities and independent dispatch."""
import copy
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
