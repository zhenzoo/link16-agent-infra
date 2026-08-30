import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import send_feishu_msg as sender  # noqa: E402
import turn_delivery_guard as guard  # noqa: E402


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
