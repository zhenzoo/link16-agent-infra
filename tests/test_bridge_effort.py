"""Defaults are shared per profile; temporary effort is one accepted turn."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_effort
import bridge_effort_control
import bridge_effort_auto
import codex_startup


def profile(home, effort="xhigh"):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        f'# keep this comment\nmodel = "gpt-6-astra"\nmodel_reasoning_effort = "{effort}"\n'
        '[notice]\nhide_rate_limit_model_nudge = true\n', encoding="utf-8")
    return home


def ready(state, bot="one", profile="cxp", startup="attempt", thread="root"):
    record = dict(contract=codex_startup.CONTRACT, effort_contract=bridge_effort.CONTRACT,
                  profile=profile, bot=bot, startup_id=startup, thread_id=thread,
                  stage="ready", source="thread/start", worker_pid=os.getpid(),
                  tui_pid=os.getpid(), observer_pid=os.getpid())
    codex_startup.atomic_write_json(state / f"bridge-codex-app-ready-{bot}.json", record)
    return record


def request(id=1, thread="root", message="task", method="turn/start"):
    return json.dumps({"id": id, "method": method, "params": {
        "threadId": thread, "model": "gpt-6-astra", "effort": "xhigh",
        "collaborationMode": {"mode": "default", "settings": {
            "model": "gpt-6-astra", "reasoning_effort": "xhigh", "developer_instructions": "keep"}},
        "input": [{"type": "text", "text": message}]}})


@pytest.mark.parametrize("level", ["medium", "high", "xhigh"])
def test_human_default_is_shared_only_with_same_profile(tmp_path, level):
    first, other = profile(tmp_path / "cxp"), profile(tmp_path / "cxp2", "high")
    before = (other / "config.toml").read_bytes()
    assert bridge_effort.save_default(first, level) == {"model": "gpt-6-astra", "effort": level}
    for bot in ("one", "two"):
        assert bridge_effort.status(tmp_path, bot, "cxp", first)["effort"] == level
    assert (other / "config.toml").read_bytes() == before
    assert "# keep this comment" in (first / "config.toml").read_text()
    assert "hide_rate_limit_model_nudge = true" in (first / "config.toml").read_text()


def test_native_latest_save_wins_over_old_bot_state(tmp_path):
    home = profile(tmp_path / "home", "high")
    (tmp_path / "bridge-effort-one.json").write_text(
        '{"profile":"cxp","effort":"medium","model":"obsolete"}', encoding="utf-8")
    profile(home, "xhigh")  # Native picker saved a newer value.
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    assert controller.apply(request()) == request()
    assert bridge_effort.status(tmp_path, "one", "cxp", home)["effort"] == "xhigh"


def test_concurrent_profile_save_is_not_reverted(tmp_path, monkeypatch):
    profile(tmp_path)
    original_write = Path.write_text
    def writing(path, text, *args, **kwargs):
        result = original_write(path, text, *args, **kwargs)
        if path.name.startswith(".config.toml.link16-effort.") and path.suffix == ".tmp":
            original_write(tmp_path / "config.toml", 'model = "new-model"\nmodel_reasoning_effort = "high"\n')
        return result
    monkeypatch.setattr(Path, "write_text", writing)
    with pytest.raises(RuntimeError, match="另一终端"):
        bridge_effort.save_default(tmp_path, "medium")
    assert bridge_effort.profile_defaults(tmp_path) == {"model": "new-model", "effort": "high"}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("level", ["medium", "high", "xhigh"])
def test_temporary_effort_both_fields_consumed_only_after_acceptance(tmp_path, level):
    home = profile(tmp_path / "home")
    before = (home / "config.toml").read_bytes()
    ready(tmp_path)
    bridge_effort.select_next(tmp_path, "one", "cxp", level)
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    raw = request()
    forwarded = json.loads(controller.apply(raw))
    assert forwarded["params"]["effort"] == level
    assert forwarded["params"]["collaborationMode"]["settings"]["reasoning_effort"] == level
    assert forwarded["params"]["collaborationMode"]["settings"]["developer_instructions"] == "keep"
    assert forwarded["params"]["model"] == "gpt-6-astra"
    assert controller.apply(request(id=2)) == request(id=2)  # In flight; cannot apply twice.
    controller.result(forwarded, None)
    assert bridge_effort.status(tmp_path, "one", "cxp", home)["pending"]
    forwarded = json.loads(controller.apply(request(id=3)))
    controller.result(forwarded, "turn-3")
    state = bridge_effort.status(tmp_path, "one", "cxp", home)
    assert state["pending"] is None
    assert state["receipt"]["effort"] == level
    assert state["receipt"]["turn_id"] == "turn-3"
    assert controller.apply(request(id=4)) == request(id=4)
    assert (home / "config.toml").read_bytes() == before


def test_selection_scope_and_message_binding(tmp_path):
    ready(tmp_path)
    bridge_effort.select_next(tmp_path, "one", "cxp", "medium", message_id="om_task")
    for bot, profile_name, attempt, thread in [
        ("two", "cxp", "attempt", "root"), ("one", "cxp2", "attempt", "root"),
        ("one", "cxp", "next-worker", "root"), ("one", "cxp", "attempt", "new-thread")]:
        controller = bridge_effort.TurnEffort(tmp_path, bot, profile_name, attempt)
        raw = request(thread=thread, message="task [飞书 mid=om_task]")
        assert controller.apply(raw) == raw
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    for text in ("other", "task [飞书 mid=om_task_extra]"):
        assert controller.apply(request(message=text)) == request(message=text)
    forwarded = json.loads(controller.apply(request(message="task [飞书 mid=om_task]")))
    assert forwarded["params"]["effort"] == "medium"
    controller.result(forwarded, "turn")
    assert controller.apply(request(id=2, message="task [飞书 mid=om_task]")) == request(
        id=2, message="task [飞书 mid=om_task]")


def test_acceptance_does_not_consume_a_newer_selection(tmp_path):
    ready(tmp_path)
    bridge_effort.select_next(tmp_path, "one", "cxp", "medium")
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    forwarded = json.loads(controller.apply(request()))
    newer = bridge_effort.select_next(tmp_path, "one", "cxp", "high")
    controller.result(forwarded, "turn")
    pending = bridge_effort.selection_record(tmp_path, "one", "cxp", "attempt", "root")
    assert pending["selection_id"] == newer["selection_id"]


def test_receipt_uses_collaboration_setting_and_rejects_legacy_receipt(tmp_path):
    home = profile(tmp_path / "home")
    ready(tmp_path)
    (tmp_path / "bridge-effort-applied-one.json").write_text(
        '{"profile":"cxp","effort":"medium","accepted_at":99999999999}')
    assert bridge_effort.status(tmp_path, "one", "cxp", home)["receipt"] is None
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    raw = json.loads(request())
    raw["params"]["effort"] = "medium"
    controller.result(raw, "turn")
    assert bridge_effort.status(tmp_path, "one", "cxp", home)["receipt"]["effort"] == "xhigh"
    ready(tmp_path, startup="next-worker")
    assert bridge_effort.status(tmp_path, "one", "cxp", home)["receipt"] is None


def test_old_or_dead_worker_cannot_queue_temporary_effort(tmp_path):
    record = ready(tmp_path)
    record.pop("effort_contract")
    codex_startup.atomic_write_json(tmp_path / "bridge-codex-app-ready-one.json", record)
    with pytest.raises(RuntimeError, match="worker"):
        bridge_effort.select_next(tmp_path, "one", "cxp", "medium")
    assert not bridge_effort.next_path(tmp_path, "one").exists()


def test_active_turn_and_other_rpc_frames_remain_untouched(tmp_path):
    ready(tmp_path)
    bridge_effort.select_next(tmp_path, "one", "cxp", "medium")
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    for method in ("turn/steer", "thread/start", "config/read"):
        raw = request(method=method)
        assert controller.apply(raw) == raw


def test_rejects_bad_effort_without_changing_default(tmp_path):
    profile(tmp_path, "high")
    for value in ("ultra", "auto", "这个任务用 medium", "medium high", ""):
        with pytest.raises(ValueError):
            bridge_effort.save_default(tmp_path, value)
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "high"


def test_damaged_temporary_record_does_not_disconnect_turn(tmp_path):
    bridge_effort.next_path(tmp_path, "one").write_text("{broken", encoding="utf-8")
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    assert controller.apply(request()) == request()

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
    assert bridge_effort_control._footer_matches(
        "› Ask Codex to do anything\n  GPT-6-Astra xhigh · C:\\repo\n", "gpt-6-astra", "xhigh")


def test_receipt_write_failure_cannot_reapply_consumed_effort(tmp_path, monkeypatch):
    ready(tmp_path)
    bridge_effort.select_next(tmp_path, "one", "cxp", "medium")
    controller = bridge_effort.TurnEffort(tmp_path, "one", "cxp", "attempt")
    forwarded = json.loads(controller.apply(request()))
    monkeypatch.setattr(bridge_effort, "_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        controller.result(forwarded, "turn")
    assert controller.apply(request(id=2)) == request(id=2)


def test_manual_picker_keeps_new_default_even_when_later_verification_fails(tmp_path, monkeypatch):
    profile(tmp_path)
    from unittest.mock import Mock
    monkeypatch.setattr(bridge_effort_control, "_status", Mock(
        side_effect=[("gpt-6-astra", "xhigh"), RuntimeError("verification interrupted")]))
    monkeypatch.setattr(bridge_effort_control, "_navigate", lambda *args: None)
    monkeypatch.setattr(bridge_effort_control, "_wait", Mock(side_effect=[
        "› 1. gpt-6-astra (current)",
        "› 2. Medium",
        "Model changed to gpt-6-astra medium"]))
    count = 0
    def key(value):
        nonlocal count
        if value == "enter":
            count += 1
        if count == 3:
            bridge_effort.save_default(tmp_path, "medium")  # Native picker save.
    with pytest.raises(RuntimeError, match="verification interrupted"):
        bridge_effort_control.switch_current(
            model="gpt-6-astra", effort="medium", profile_home=tmp_path,
            read_screen=lambda: "› Ask Codex to do anything", send=lambda text: None, key=key)
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "medium"
