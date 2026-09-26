import io
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import send_feishu_msg as sender  # noqa: E402

ENTERPRISE = "11eecc9c9e995740"
PERSONAL = "133cf707604f175e"


def fake_registry(agents, tenants):
    by_name = {a["name"]: a for a in agents}
    return types.SimpleNamespace(
        load_registry=lambda: {"agents": agents, "tenants": tenants},
        find=lambda name: by_name.get(name),
    )


TENANTS = [
    {"tenant_key": ENTERPRISE, "group_chat_id": "oc_enterprise", "group_name": "obsagent 大乱斗",
     "webhook_env": "FEISHU_XT_WEBHOOK_OBS_URL"},
    {"tenant_key": PERSONAL, "group_chat_id": "oc_personal", "group_name": "tb24-25交流水吧",
     "webhook_env": "FEISHU_XT_WEBHOOK_P_URL"},
]
AGENTS = [
    {"name": "tb26-link16", "tenant_key": ENTERPRISE, "open_id": "ou_tb26"},
    {"name": "tb25-link16", "tenant_key": PERSONAL, "open_id": "ou_tb25"},
    {"name": "tb24-link16", "tenant_key": PERSONAL, "open_id": "ou_tb24"},
    {"name": "tb26-multi-camera", "open_id": "ou_cam"},
]


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setitem(sys.modules, "registry", fake_registry(AGENTS, TENANTS))
    monkeypatch.setattr(sender, "_env", lambda *keys: {})


def test_same_tenant_keeps_shared_group_path(registry):
    assert sender.cross_tenant_route("tb24-link16", "tb25-link16") is None


def test_missing_tenant_key_never_guesses(registry):
    assert sender.cross_tenant_route("tb26-multi-camera", "tb25-link16") is None


def test_cross_tenant_picks_target_tenant_webhook(registry, monkeypatch):
    monkeypatch.setenv("FEISHU_XT_WEBHOOK_P_URL", "https://hook/p")
    route = sender.cross_tenant_route("tb26-link16", "tb25-link16")
    assert route == {"url": "https://hook/p", "tenant_key": PERSONAL, "chat_id": "oc_personal"}


def test_cross_tenant_without_local_webhook_fails_closed(registry, monkeypatch):
    monkeypatch.delenv("FEISHU_XT_WEBHOOK_OBS_URL", raising=False)
    with pytest.raises(SystemExit, match="FEISHU_XT_WEBHOOK_OBS_URL"):
        sender.cross_tenant_route("tb25-link16", "tb26-link16")


def test_webhook_posts_text_verbatim_with_real_at_tag():
    captured = {}

    def fake_urlopen(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return io.BytesIO(b'{"code":0,"msg":"success"}')

    with mock.patch.object(sender.urllib.request, "urlopen", side_effect=fake_urlopen):
        ok, info = sender.send_webhook("https://hook/p", "拉一下 link16", ["ou_tb25"])
    assert ok and info.startswith("webhook-")
    assert captured["body"] == {"msg_type": "text",
                                "content": {"text": '<at user_id="ou_tb25"></at> 拉一下 link16'}}


def test_webhook_error_code_is_reported_not_swallowed():
    with mock.patch.object(sender.urllib.request, "urlopen",
                           return_value=io.BytesIO(b'{"code":19024,"msg":"Key Words Not Found"}')):
        ok, info = sender.send_webhook("https://hook/p", "x", [])
    assert not ok and "19024" in info


def test_main_routes_cross_tenant_agent_through_webhook(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("FEISHU_XT_WEBHOOK_P_URL", "https://hook/p")
    monkeypatch.delenv("FEISHU_BRIDGE_SESSION", raising=False)
    with mock.patch.object(sender, "STATE_DIR", tmp_path), \
         mock.patch.object(sender, "assert_sender_identity"), \
         mock.patch.object(sender, "send_msg") as app_send, \
         mock.patch.object(sender, "shared_group") as shared, \
         mock.patch.object(sender, "send_webhook", return_value=(True, "webhook-1")) as hook, \
         mock.patch.object(sender.bridge_outbound, "append_delivery", return_value=True) as append, \
         mock.patch.object(sys, "argv", [
             "send_feishu_msg.py", "--bot", "tb26-link16", "--to-agent", "tb25-link16",
             "--text", "拉一下 link16", "--json",
         ]):
        with pytest.raises(SystemExit) as stopped:
            sender.main()
    assert stopped.value.code == 0
    app_send.assert_not_called()
    shared.assert_not_called()
    url, text, ats = hook.call_args.args
    assert url == "https://hook/p" and ats == ["ou_tb25"]
    assert text.endswith("[飞书_from_tb26-link16_to_tb25-link16]")
    assert append.call_args.kwargs["route"]["kind"] == "a2a-webhook"
    assert append.call_args.kwargs["target"] == "oc_personal"
