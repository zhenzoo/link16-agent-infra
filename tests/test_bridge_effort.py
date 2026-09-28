"""The Feishu effort command must affect new Codex turns, not an active turn."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_effort  # noqa: E402
import bridge_effort_control  # noqa: E402
import bridge_effort_auto  # noqa: E402


def test_override_is_scoped_to_bot_and_profile(tmp_path):
    raw = json.dumps({"id": 1, "method": "turn/start", "params": {"threadId": "t", "input": []}})
    assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp") == raw
    bridge_effort.save(tmp_path, "one", "cxp", "extra high", "gpt-6-sol")
    assert bridge_effort.status(tmp_path, "one", "cxp")["accepted"] is False
    changed = json.loads(bridge_effort.apply_turn(raw, tmp_path, "one", "cxp"))
    assert changed["params"]["effort"] == "xhigh"
    assert changed["params"]["model"] == "gpt-6-sol"
    bridge_effort.record_accepted(tmp_path, "one", "cxp", "xhigh", "turn-1")
    assert bridge_effort.status(tmp_path, "one", "cxp") == {
        "model": "gpt-6-sol", "effort": "xhigh", "accepted": True, "turn_id": "turn-1"}
    assert bridge_effort.apply_turn(raw, tmp_path, "two", "cxp") == raw
    assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp2") == raw
    bridge_effort.save(tmp_path, "one", "cxp", "medium", "gpt-6-sol")
    assert bridge_effort.status(tmp_path, "one", "cxp")["accepted"] is False
    assert json.loads(bridge_effort.apply_turn(raw, tmp_path, "one", "cxp"))["params"]["effort"] == "medium"


def test_profile_model_is_the_saved_profile_choice_not_catalog_default(tmp_path):
    (tmp_path / "config.toml").write_text('model = "gpt-6-sol"\nmodel_reasoning_effort = "xhigh"\n',
                                          encoding="utf-8")
    assert bridge_effort.profile_model(tmp_path) == "gpt-6-sol"
    (tmp_path / "config.toml").write_text('model_reasoning_effort = "xhigh"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="未保存默认模型"):
        bridge_effort.profile_model(tmp_path)


def test_new_session_launch_uses_the_bot_override_without_changing_profile(tmp_path):
    bridge_effort.save(tmp_path, "one", "cxp", "medium", "gpt-6-sol")
    assert bridge_effort.launch_args(tmp_path, "one", "cxp") == [
        "--model", "gpt-6-sol", "-c", 'model_reasoning_effort="medium"']
    assert bridge_effort.launch_args(tmp_path, "two", "cxp") == []
    assert bridge_effort.launch_args(tmp_path, "one", "cxp2") == []


def test_legacy_effort_only_record_applies_to_new_session_without_pinning_model(tmp_path):
    bridge_effort.save(tmp_path, "legacy", "cxp", "medium")
    assert bridge_effort.launch_args(tmp_path, "legacy", "cxp") == [
        "-c", 'model_reasoning_effort="medium"']
    assert bridge_effort.launch_args(tmp_path, "legacy", "cxp2") == []


def test_live_picker_parses_medium_with_default_and_current_markers():
    screen = """  Select Reasoning Level for gpt-6-sol
  1. Low                   Fast
› 2. Medium (default) (current)  Balanced
  3. High                  Deep
  4. Extra high            Extra deep
"""
    assert bridge_effort_control._selected_index(screen) == 2
    assert any(label.startswith("Medium") and "(current)" in label
               for _, label, _ in bridge_effort_control._rows(screen))


def test_autonomous_request_needs_an_explicit_self_selection_instruction():
    assert bridge_effort_auto.requested("选用你合适的 effort，自己去调整，然后再执行")
    assert bridge_effort_auto.requested("请自己判断推理档位并调整")
    assert not bridge_effort_auto.requested("介绍一下 medium 和 xhigh 的区别")
    assert not bridge_effort_auto.requested("这个任务是否需要 medium？先讨论")


def test_preflight_must_return_one_supported_level_and_live_footer_must_agree():
    event = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "xhigh"}})
    assert bridge_effort_auto._last_agent_message(event) == "xhigh"
    with pytest.raises(RuntimeError):
        bridge_effort_auto._last_agent_message(event.replace("xhigh", "I prefer xhigh"))
    screen = "older startup gpt-6-sol xhigh\n\n› Ask Codex to do anything\n  gpt-6-sol medium · C:\\repo\n"
    assert bridge_effort_control._footer_matches(screen, "gpt-6-sol", "medium")
    assert not bridge_effort_control._footer_matches(screen, "gpt-6-sol", "xhigh")


def test_active_turn_and_other_rpc_frames_remain_untouched(tmp_path):
    bridge_effort.save(tmp_path, "one", "cxp", "medium")
    for method in ("turn/steer", "thread/start", "config/read"):
        raw = json.dumps({"id": 2, "method": method, "params": {"input": []}})
        assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp") == raw


def test_rejects_unclear_or_bad_effort_without_changing_saved_value(tmp_path):
    bridge_effort.save(tmp_path, "one", "cxp", "medium")
    for value in ("ultra", "auto", "这个任务用 medium", "medium high", ""):
        with pytest.raises(ValueError):
            bridge_effort.save(tmp_path, "one", "cxp", value)
    assert bridge_effort.load(tmp_path, "one", "cxp") == "medium"


def test_damaged_local_override_does_not_disconnect_turn(tmp_path):
    raw = json.dumps({"id": 3, "method": "turn/start", "params": {"input": []}})
    bridge_effort.path(tmp_path, "one").write_text("{broken", encoding="utf-8")
    assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp") == raw
    with pytest.raises(ValueError):
        bridge_effort.status(tmp_path, "one", "cxp")
