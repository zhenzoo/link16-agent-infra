"""Exercise the actual /effort handler without connecting to Feishu."""

import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
import feishu_bridge as bridge
import bridge_effort
import bridge_effort_control


def handler(home, *, alive=True, screen="busy", runtime="codex"):
    (home / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_reasoning_effort = "xhigh"\n', encoding="utf-8")
    tree = ast.parse((ROOT / "feishu/feishu_bridge.py").read_text(encoding="utf-8"))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "handle_slash")
    rec = {"pty": "pty", "workspace_id": "ws"}
    environment = dict(vars(bridge))
    environment.update(
        bot={"name": "unit"}, STATE_DIR=home, reply=mock.AsyncMock(), load_session=lambda _: rec,
        _gate_verdict=lambda *args: (True, "authorized agent"), receipt=mock.Mock(), blog=mock.Mock(),
        load_owner=lambda _: "owner", ALLOWED_OPEN_IDS=set(),
        wmux_session=SimpleNamespace(pty_alive=lambda _: alive),
        bridge_activity=SimpleNamespace(read_activity=lambda *args: {"state": "idle"}),
        agent_runtime=SimpleNamespace(runtime_name=lambda _: runtime, current_account=lambda _: "cxp",
                                      profile_spec=lambda _: SimpleNamespace(home_path=home)),
        codex_startup=SimpleNamespace(
            read_state=lambda _: {"effort_control": True, "profile": "cxp", "worker_pid": 10},
            process_alive=lambda _: True),
        read_screen=mock.Mock(return_value=screen), wmux=mock.Mock(),
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "effort-command-test", "exec"), environment)
    return environment["handle_slash"], environment


@pytest.mark.parametrize("alive", [False, True])
def test_missing_or_busy_session_saves_default_without_keyboard_input(tmp_path, alive):
    command, env = handler(tmp_path, alive=alive)
    asyncio.run(command("chat", "/effort high", sender="owner"))
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "high"
    env["wmux"].assert_not_called()
    assert "新会话默认" in env["reply"].call_args.args[1]
    assert not (tmp_path / "bridge-effort-unit.json").exists()


def test_idle_session_switch_does_not_restore_old_default(tmp_path):
    command, env = handler(tmp_path, screen="› Ask Codex to do anything")
    with mock.patch.object(bridge_effort_control, "switch_current",
                           return_value={"before": ("gpt-6-astra", "xhigh")}) as switch:
        asyncio.run(command("chat", "/effort medium", sender="owner"))
    switch.assert_called_once()
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "medium"
    assert "当前会话也已切换" in env["reply"].call_args.args[1]


def test_switch_failure_reports_saved_default_and_unverified_live_state(tmp_path):
    command, env = handler(tmp_path, screen="› Ask Codex to do anything")
    with mock.patch.object(bridge_effort_control, "switch_current", side_effect=RuntimeError("picker failed")):
        asyncio.run(command("chat", "/effort high", sender="owner"))
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "high"
    assert "未通过核验" in env["reply"].call_args.args[1]


def test_group_agent_cannot_change_human_profile_default(tmp_path):
    command, env = handler(tmp_path)
    asyncio.run(command("chat", "/effort medium", sender="agent", from_group=True))
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "xhigh"
    assert "只有主人" in env["reply"].call_args.args[1]


def test_active_native_turn_with_empty_composer_is_not_switched(tmp_path):
    command, env = handler(tmp_path, screen="› Ask Codex to do anything")
    env["bridge_activity"].read_activity = lambda *args: {"state": "active"}
    with mock.patch.object(bridge_effort_control, "switch_current") as switch:
        asyncio.run(command("chat", "/effort high", sender="owner"))
    switch.assert_not_called()
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "high"
    assert "未确认空闲" in env["reply"].call_args.args[1]


def test_autonomous_injection_rechecks_activity_after_classification(tmp_path):
    with mock.patch.object(bridge, "STATE_DIR", tmp_path), \
            mock.patch.object(bridge.bridge_activity, "read_activity", return_value={"state": "active"}), \
            mock.patch.object(bridge, "_inject_unlocked") as inject:
        with pytest.raises(RuntimeError, match="未注入"):
            bridge._inject("pty", "ws", "task", idle_bot={"name": "unit"})
    inject.assert_not_called()


@pytest.mark.parametrize("runtime", ["claude", "kimi"])
def test_other_runtimes_do_not_write_codex_defaults(tmp_path, runtime):
    command, env = handler(tmp_path, runtime=runtime)
    asyncio.run(command("chat", "/effort medium", sender="owner"))
    assert bridge_effort.profile_defaults(tmp_path)["effort"] == "xhigh"
    if runtime == "claude":
        assert env["wmux"].call_count == 2
    else:
        env["wmux"].assert_not_called()
