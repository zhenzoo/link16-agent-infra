import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_outbox  # noqa: E402
import turn_delivery_guard as guard  # noqa: E402


def test_p2a_aliases_and_group_route_are_blocked_before_network(tmp_path):
    (tmp_path / "bridge-owner-bot.json").write_text(
        json.dumps({"open_id": "ou_owner"}), encoding="utf-8"
    )
    (tmp_path / "bridge-session-bot.json").write_text(
        json.dumps({"open_id": "ou_owner", "chat_id": "oc_dm"}), encoding="utf-8"
    )
    guard.activate(tmp_path, "bot", {"kind": "p2a"}, turn_key="dm-turn")
    for target in ("ou_owner", "oc_dm"):
        with pytest.raises(RuntimeError, match="自动回原处"):
            guard.guard_outbound(tmp_path, "bot", target, bridge_session="bot")

    guard.activate(tmp_path, "bot", {
        "kind": "p2a-ext", "dest": "oc_group", "at": "ou_human",
    }, turn_key="group-turn")
    with pytest.raises(RuntimeError, match="自动回原处"):
        guard.guard_outbound(tmp_path, "bot", "oc_group", bridge_session="bot")
    allowed = guard.guard_outbound(tmp_path, "bot", "oc_other", bridge_session="bot")
    assert allowed["reason"] == "different-target"


def test_proactive_override_and_outside_bridge_session_remain_available(tmp_path):
    guard.activate(tmp_path, "bot", {"kind": "p2a-ext", "dest": "oc_group"})
    assert guard.guard_outbound(
        tmp_path, "bot", "oc_group", bridge_session="bot", proactive=True,
    )["reason"] == "proactive-override"
    assert guard.guard_outbound(
        tmp_path, "bot", "oc_group", bridge_session=None,
    )["reason"] == "outside-bridge-session"


def test_compare_and_clear_never_clears_a_newer_turn(tmp_path):
    guard.activate(tmp_path, "bot", {"kind": "p2a"}, turn_key="old")
    guard.activate(tmp_path, "bot", {"kind": "p2a"}, turn_key="new")
    assert guard.compare_and_clear(tmp_path, "bot", "old") is False
    assert guard.read_route(tmp_path, "bot")["active"] is True
    assert guard.compare_and_clear(tmp_path, "bot", "new") is True
    assert guard.read_route(tmp_path, "bot")["active"] is False


def test_missing_active_route_fails_closed_only_inside_bridge_session(tmp_path):
    with pytest.raises(RuntimeError, match="状态缺失"):
        guard.guard_outbound(tmp_path, "bot", "oc_x", bridge_session="bot")
    assert guard.guard_outbound(
        tmp_path, "bot", "oc_x", bridge_session="other",
    )["reason"] == "outside-bridge-session"

    guard.activate(tmp_path, "bot", {"kind": "p2a"})
    with pytest.raises(RuntimeError, match="无法解析"):
        guard.guard_outbound(tmp_path, "bot", "oc_x", bridge_session="bot")


def test_claude_userprompt_hook_is_synchronous_for_guard(tmp_path):
    path = bridge_outbox.write_hooks_settings(tmp_path, ROOT / "feishu" / "hooks")
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert hook.get("async") is not True
