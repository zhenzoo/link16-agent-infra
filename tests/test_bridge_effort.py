"""The Feishu effort command must affect new Codex turns, not an active turn."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_effort  # noqa: E402


def test_override_is_scoped_to_bot_and_profile(tmp_path):
    raw = json.dumps({"id": 1, "method": "turn/start", "params": {"threadId": "t", "input": []}})
    assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp") == raw
    bridge_effort.save(tmp_path, "one", "cxp", "x-high")
    assert bridge_effort.status(tmp_path, "one", "cxp")["accepted"] is False
    changed = json.loads(bridge_effort.apply_turn(raw, tmp_path, "one", "cxp"))
    assert changed["params"]["effort"] == "xhigh"
    bridge_effort.record_accepted(tmp_path, "one", "cxp", "xhigh", "turn-1")
    assert bridge_effort.status(tmp_path, "one", "cxp") == {
        "effort": "xhigh", "accepted": True, "turn_id": "turn-1"}
    assert bridge_effort.apply_turn(raw, tmp_path, "two", "cxp") == raw
    assert bridge_effort.apply_turn(raw, tmp_path, "one", "cxp2") == raw
    bridge_effort.save(tmp_path, "one", "cxp", "medium")
    assert bridge_effort.status(tmp_path, "one", "cxp")["accepted"] is False
    assert json.loads(bridge_effort.apply_turn(raw, tmp_path, "one", "cxp"))["params"]["effort"] == "medium"


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
