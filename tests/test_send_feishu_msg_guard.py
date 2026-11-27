import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import send_feishu_msg as sender  # noqa: E402
import turn_delivery_guard as guard  # noqa: E402
import proactive_dispatch as dispatch  # noqa: E402


def test_same_active_group_is_rejected_before_send_api(tmp_path, monkeypatch):
    guard.activate(tmp_path, "bot", {"kind": "p2a-ext", "dest": "oc_group"})
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", "bot")
    with mock.patch.object(sender, "STATE_DIR", tmp_path), \
         mock.patch.object(sender, "assert_sender_identity"), \
         mock.patch.object(sender, "send_msg") as send, \
         mock.patch.object(sys, "argv", [
             "send_feishu_msg.py", "--bot", "bot", "--to", "oc_group", "--text", "重复",
         ]):
        with pytest.raises(SystemExit, match="自动回原处"):
            sender.main()
    send.assert_not_called()


def test_proactive_override_sends_once_and_records_history(tmp_path, monkeypatch):
    guard.activate(tmp_path, "bot", {"kind": "p2a-ext", "dest": "oc_group"})
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", "bot")
    with mock.patch.object(sender, "STATE_DIR", tmp_path), \
         mock.patch.object(sender, "assert_sender_identity"), \
         mock.patch.object(sender, "send_msg", return_value=(True, "om_1")) as send, \
         mock.patch.object(sender.bridge_outbound, "append_delivery", return_value=True) as append, \
         mock.patch.object(sys, "argv", [
             "send_feishu_msg.py", "--bot", "bot", "--to", "oc_group",
             "--text", "额外通知", "--proactive", "--json",
         ]):
        with pytest.raises(SystemExit) as stopped:
            sender.main()
    assert stopped.value.code == 0
    send.assert_called_once()
    assert append.call_args.kwargs["proactive_override"] is True


def test_history_failure_does_not_invite_duplicate_resend(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FEISHU_BRIDGE_SESSION", raising=False)
    with mock.patch.object(sender, "STATE_DIR", tmp_path), \
         mock.patch.object(sender, "assert_sender_identity"), \
         mock.patch.object(sender, "send_msg", return_value=(True, "om_sent")) as send, \
         mock.patch.object(sender.bridge_outbound, "append_delivery", return_value=False), \
         mock.patch.object(sys, "argv", [
             "send_feishu_msg.py", "--bot", "bot", "--to", "oc_group", "--text", "只发一次",
         ]):
        with pytest.raises(SystemExit) as stopped:
            sender.main()
    assert stopped.value.code == 0
    send.assert_called_once()
    output = capsys.readouterr().out
    assert "消息已发出" in output
    assert "不要重发" in output


@pytest.mark.parametrize("options,text", [
    ([], "继续写同一产品"),
    ([], "[Proactive stable] 改原件"),
    (["--item","example","--task-id","stable"], "改原件"),
])
def test_managed_dispatcher_cannot_bypass_product_owner_at_tenant_send(tmp_path, monkeypatch, options, text):
    from proactive_ownership import ItemOwners
    with ItemOwners(tmp_path).author("desk","example","elected","original"):
        pass
    monkeypatch.setattr(sender, "STATE_DIR", tmp_path)
    monkeypatch.setattr(dispatch, "check", lambda *_: {"ready": True, "reason": "就绪"})
    monkeypatch.setattr(sender, "local_name", lambda name:name)
    monkeypatch.setattr(sender, "assert_sender_identity", lambda name:None)
    monkeypatch.setattr(sender, "resolve_open_id", lambda name:"ou_other")
    monkeypatch.setattr(sender, "shared_group", lambda *a:"oc_group")
    monkeypatch.setattr(sender, "send_agent_message", lambda *a, **kw:pytest.fail("duplicate sent"))
    monkeypatch.setattr(sys, "argv",["send","--bot","desk","--to-agent","other","--in","oc_group",
                                     "--text",text,*options])
    with pytest.raises(SystemExit) as failed:
        sender.main()
    assert failed.value.code != 0


def test_same_author_can_update_original_task_through_tenant_with_product_format(tmp_path, monkeypatch):
    from proactive_ownership import ItemOwners, PRODUCT_STYLE
    with ItemOwners(tmp_path).author("desk","example","elected","original"):
        pass
    monkeypatch.setattr(sender,"STATE_DIR",tmp_path)
    monkeypatch.setattr(dispatch, "check", lambda *_: {"ready": True, "reason": "就绪"})
    monkeypatch.setattr(sender,"local_name",lambda n:n)
    monkeypatch.setattr(sender,"assert_sender_identity",lambda n:None)
    monkeypatch.setattr(sender,"resolve_open_id",lambda n:"ou_writer")
    monkeypatch.setattr(sender,"shared_group",lambda *a:"oc_group")
    monkeypatch.setattr(sender.bridge_outbound,"append_delivery",lambda *a,**kw:True)
    captured=[]
    def publish(bot, peer, text, **kwargs):
        captured.append((peer,text))
        return {"ok":True,"message_id":"om_receipt","mirror":None}
    monkeypatch.setattr(sender,"send_agent_message",publish)
    for text in ("改PRD-100 原表。","补充 PRD-100 对应视角。"):
        monkeypatch.setattr(sys,"argv",["send","--bot","desk","--to-agent","elected","--in","oc_group",
                                        "--item","example","--task-id","original","--text",text,"--json"])
        with pytest.raises(SystemExit) as sent:
            sender.main()
        assert sent.value.code == 0
    assert len(captured)==2 and all(peer=="elected" and PRODUCT_STYLE in text for peer,text in captured)


@pytest.mark.parametrize("args", [
    ["--item", "example", "--task-id", "stable"], ["--coordination", "compute"], ["--coordination", "publish"],
])
def test_direct_work_refuses_before_ownership_or_feishu(tmp_path, monkeypatch, capsys, args):
    from proactive_ownership import ItemOwners
    monkeypatch.setattr(sender, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sender, "assert_sender_identity", lambda _: None)
    def check(peer, size):
        assert (peer, size) == ("worker", "long")
        return dict(ready=False, reason="周额度已满", target=dict(profile="cxp"), bridge=dict(online=True))
    monkeypatch.setattr(dispatch, "check", check)
    monkeypatch.setattr(sender, "peer_address", lambda *_: pytest.fail("contacted Feishu"))
    monkeypatch.setattr(sys, "argv", ["send", "--bot", "desk", "--to-agent", "worker", "--text", "核对原件",
                                     "--json", *args])
    with pytest.raises(SystemExit) as stopped:
        sender.main()
    result = json.loads(capsys.readouterr().out)
    assert stopped.value.code == 2 and result["sent"] is False
    assert result["dispatch_check"]["profile"] == "cxp" and result["dispatch_check"]["bridge"]["online"]
    assert not ItemOwners(tmp_path).read()["items"]


def test_primary_seat_must_classify_first_dispatch(tmp_path, monkeypatch):
    import agent_grant
    monkeypatch.setattr(agent_grant, "primary_policy", lambda: {"desk": ("app", "group")})
    monkeypatch.setattr(sender, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sender, "assert_sender_identity", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["send", "--bot", "desk", "--to-agent", "worker", "--text", "工作"])
    with pytest.raises(SystemExit, match="--item"):
        sender.main()


def test_non_primary_cannot_send_control_with_delegated_identity(monkeypatch):
    import agent_grant
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", "worker")
    monkeypatch.setattr(agent_grant, "primary_policy", lambda: {"primary": ("cli_primary", "oc_team")})
    monkeypatch.setattr(sender, "local_name", lambda name: name)
    monkeypatch.setattr(sender, "send_msg", lambda *args: pytest.fail("control sent"))
    for identity in ("worker", "primary"):
        with pytest.raises(ValueError, match="0 号位"):
            sender.send_agent_message(identity, "recipient", "/close")
