"""Exercise owner-only routing and the uncertain POST boundary without sending messages."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_report as pr


def incoming(chat="oc_owner", chat_type="p2p"):
    return {"source": "feishu-ws", "bot": "lead", "message_id": "om_in", "chat_id": chat,
            "chat_type": chat_type, "sender": {"type": "user", "is_bot": False, "open_id": "ou_owner"}}


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(pr.bridge, "STATE_DIR", tmp_path)
    monkeypatch.setattr(pr, "local_name", lambda bot, **_: bot)
    monkeypatch.delenv("FEISHU_BRIDGE_SESSION", raising=False)
    monkeypatch.setattr(pr.bridge, "load_owner_record", lambda _: {"open_id": "ou_owner"})
    monkeypatch.setattr(pr.bridge_inbound, "read_records", lambda *_, **__: [incoming()])
    monkeypatch.setattr(pr, "_bot_creds", lambda _: ("app_demo", "fixture"))
    monkeypatch.setattr(pr.bridge, "_tenant_token", lambda *_: "fixture")
    monkeypatch.setattr(pr.session_work, "apply_banner", lambda card, *_: card)
    log = {"posts": 0, "gets": 0, "chat": "oc_owner"}

    def post(_app, _secret, target, card, _uuid):
        assert target == "ou_owner"
        log.update(posts=log["posts"] + 1, card=card)
        if log.get("lost_ack"):
            raise TimeoutError("fixture")
        return "om_test"

    def fetch(_token, path, params):
        log["gets"] += 1
        if log.get("offline"):
            raise TimeoutError("fixture")
        msg = {"chat_id": log["chat"], "message_id": "om_test", "msg_type": log.get("type", "interactive"),
               "sender": {"sender_type": "app", "id": "app_demo"},
               "body": {"content": json.dumps(log.get("returned_card", log["card"]))}}
        if path.endswith("/om_test"):
            return {"items": [msg]}
        assert params["container_id"] == "oc_owner"
        return {"items": [msg] if not log.get("no_card") else [], "has_more": False}

    monkeypatch.setattr(pr.bridge, "_send_interactive_message", post)
    monkeypatch.setattr(pr, "fetch", fetch)
    return log


def test_preview_and_repeated_send(sandbox, tmp_path):
    body = "1. First item.\n2. Second item."
    preview = pr.report("lead", "batch", body)
    assert [e["content"] for e in preview["card"]["body"]["elements"]] == ["1. First item.", "2. Second item."]
    assert sandbox["posts"] == sandbox["gets"] == 0
    assert not (tmp_path / "proactive").exists()
    first = pr.report("lead", "batch", body, send=True)
    assert first["status"] == "delivered" and first["history_recorded"]
    assert pr.report("lead", "batch", body, send=True) == first
    with pytest.raises(ValueError, match="同一批次"):
        pr.report("lead", "batch", "Changed body", send=True)
    assert sandbox["posts"] == sandbox["gets"] == 1


def test_legacy_dm_requires_platform_user_identity_and_exact_chat(sandbox, monkeypatch):
    old = incoming()
    old["sender"]["type"] = None
    monkeypatch.setattr(pr.bridge_inbound, "read_records", lambda *_, **__: [old])
    sender = {"sender_type": "user", "id_type": "open_id", "id": "ou_owner"}
    row = {"message_id": "om_in", "chat_id": "oc_owner", "sender": sender}
    monkeypatch.setattr(pr, "fetch", lambda *_: {"items": [row]})
    assert pr.owner_dm("lead") == ("ou_owner", "oc_owner")
    for key, wrong in [("id", "ou_other"), ("id_type", "user_id"), ("sender_type", "app")]:
        with monkeypatch.context() as ctx:
            ctx.setitem(sender, key, wrong)
            with pytest.raises(ValueError):
                pr.owner_dm("lead")
    row["chat_id"] = "oc_other"
    with pytest.raises(ValueError):
        pr.owner_dm("lead")


def test_failed_get_does_not_trigger_another_post(sandbox, tmp_path):
    sandbox["offline"] = True
    with pytest.raises(TimeoutError):
        pr.report("lead", "batch", "done", send=True)
    saved, = (tmp_path / "proactive" / "lead" / "reports").glob("*.json")
    assert json.loads(saved.read_text())["message_id"] == "om_test"
    sandbox["offline"] = False
    assert pr.report("lead", "batch", "done", send=True)["status"] == "delivered"
    assert sandbox["posts"] == 1


def test_timeout_reconciles_in_known_dm_before_any_retry(sandbox):
    sandbox.update(lost_ack=True, no_card=True)
    with pytest.raises(TimeoutError):
        pr.report("lead", "batch", "done", send=True)
    with pytest.raises(RuntimeError, match="未确认"):
        pr.report("lead", "batch", "done", send=True)
    sandbox["no_card"] = False
    assert pr.report("lead", "batch", "done", send=True)["message_id"] == "om_test"
    assert sandbox["posts"] == 1


@pytest.mark.parametrize(("key", "value"), [("chat", "oc_group"), ("type", "text")])
def test_wrong_readback_remains_unverified(sandbox, key, value):
    sandbox[key] = value
    with pytest.raises(RuntimeError, match="不符"):
        pr.report("lead", "batch", "done", send=True)


def test_mid_for_different_card_in_same_dm_cannot_complete_batch(sandbox, tmp_path):
    sandbox["returned_card"] = {"body": {"elements": [{"tag": "markdown", "element_id": "other_batch"}]}}
    with pytest.raises(RuntimeError, match="批次标记不符"):
        pr.report("lead", "batch", "done", send=True)
    saved, = (tmp_path / "proactive" / "lead" / "reports").glob("*.json")
    assert json.loads(saved.read_text())["status"] == "awaiting_readback"
    assert not pr.bridge_outbound.read_records(tmp_path, "lead")
    with pytest.raises(RuntimeError, match="批次标记不符"):
        pr.report("lead", "batch", "done", send=True)
    assert sandbox["posts"] == 1


def test_group_only_and_ordinary_owner_reply_do_not_send(sandbox, monkeypatch):
    monkeypatch.setenv("FEISHU_BRIDGE_SESSION", "lead")
    monkeypatch.setattr(pr.turn_delivery_guard, "read_route", lambda *_: {"kind": "p2a", "active": True})
    with pytest.raises(ValueError, match="自动发"):
        pr.report("lead", "batch", "done", send=True)
    monkeypatch.setattr(pr.bridge_inbound, "read_records", lambda *_, **__: [incoming("oc_group", "group")])
    with pytest.raises(ValueError, match="尚|还未"):
        pr.report("lead", "batch", "done", send=True)
    assert sandbox["posts"] == 0
