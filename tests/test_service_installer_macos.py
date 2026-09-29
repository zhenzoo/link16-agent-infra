"""MacOSBackend（launchd LaunchAgent）测试（PLAN-1140 P1）。

覆盖：plist 渲染快照、launchctl 调用参数、plan 只读零副作用、
apply/rollback 全链路（mock launchctl）、get_backend 平台分派。
FakeBackend 契约见 test_service_installer.py；这里用真 MacOSBackend + tmp 目录。
"""
import os
import plistlib
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
from feishu import service_installer as si


@pytest.fixture
def launchctl():
    """拦截 launchctl；任何查询/计划路径调它都算事故。"""
    with mock.patch.object(si.subprocess, "run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        yield run


@pytest.fixture
def backend(tmp_path, launchctl):
    with mock.patch.object(si.sys, "platform", "darwin"), \
         mock.patch.object(si.os, "getuid", return_value=501, create=True):
        return si.MacOSBackend(agents_dir=tmp_path / "LaunchAgents")


def _commands(run):
    return [call.args[0] for call in run.call_args_list]


def test_wmux_plist_snapshot_and_launchctl_args(backend, launchctl, tmp_path):
    backend.set_run({"exists": True, "value": '"/Applications/wmux.app"', "type": "REG_SZ"})
    plist = plistlib.loads((backend.agents_dir / "com.link16.wmux.plist").read_bytes())
    assert plist == {
        "Label": "com.link16.wmux",
        "ProgramArguments": ["/usr/bin/open", "/Applications/wmux.app"],
        "RunAtLoad": True,
        "Link16State": {"value": '"/Applications/wmux.app"', "type": "REG_SZ"},
    }
    assert _commands(launchctl) == [
        ["launchctl", "bootout", "gui/501/com.link16.wmux"],
        ["launchctl", "bootstrap", "gui/501",
         str(backend.agents_dir / "com.link16.wmux.plist")],
        ["launchctl", "enable", "gui/501/com.link16.wmux"],
    ]
    assert backend.get_run() == {"exists": True, "value": '"/Applications/wmux.app"',
                                 "type": "REG_SZ"}


def test_bridge_plist_snapshot_round_trips_desired_state(backend, launchctl, tmp_path):
    desired = si.desired_state(tmp_path / "repo", "alice",
                               tmp_path / "python3", tmp_path / "wmux.app", 2)
    state = desired["bridge_task"]
    backend.set_bridge_task(state)
    plist = plistlib.loads((backend.agents_dir / "com.link16.feishu-bridge.plist").read_bytes())
    assert plist["Label"] == "com.link16.feishu-bridge"
    assert plist["ProgramArguments"] == [
        str((tmp_path / "python3").resolve()),
        str((tmp_path / "repo").resolve() / "feishu" / "feishu_bridge.py"),
        "start",
    ]
    assert plist["WorkingDirectory"] == str((tmp_path / "repo").resolve())
    assert plist["RunAtLoad"] is True
    assert plist["Link16State"] == state
    # apply 的写后回读靠的就是这个：回读状态与 desired 语义相等。
    assert si._state_equal(backend.get_task(si.BRIDGE_TASK), state)


def test_inspect_and_plan_are_read_only(backend, launchctl, tmp_path):
    before = si.inspect_state(backend)
    assert before["wmux_run"]["exists"] is False
    assert before["bridge_task"]["exists"] is False
    desired = si.materialize_desired(before, si.desired_state(
        tmp_path / "repo", "alice", tmp_path / "python3", tmp_path / "wmux.app", 2))
    plan = si.build_plan(before, desired, machine="mac", user="alice",
                         repo=tmp_path / "repo", pythonw=tmp_path / "python3",
                         wmux_exe=tmp_path / "wmux.app", bot_count=2)
    assert [row["operation"] for row in plan["actions"]] == ["create", "create", "none"]
    launchctl.assert_not_called()  # plan 纯只读：不碰 launchctl
    assert not backend.agents_dir.exists() or not list(backend.agents_dir.iterdir())


def test_apply_then_rollback_roundtrip(backend, launchctl, tmp_path):
    before = si.inspect_state(backend)
    desired = si.materialize_desired(before, si.desired_state(
        tmp_path / "repo", "alice", tmp_path / "python3", tmp_path / "wmux.app", 2))
    plan = si.build_plan(before, desired, machine="mac", user="alice",
                         repo=tmp_path / "repo", pythonw=tmp_path / "python3",
                         wmux_exe=tmp_path / "wmux.app", bot_count=2)
    status, items = si.apply_plan(plan, before, desired, backend)
    assert status == "applied"
    assert (backend.agents_dir / "com.link16.wmux.plist").is_file()
    assert (backend.agents_dir / "com.link16.feishu-bridge.plist").is_file()

    second_before = si.inspect_state(backend)
    second = si.build_plan(second_before, si.materialize_desired(second_before, desired),
                           machine="mac", user="alice", repo=tmp_path / "repo",
                           pythonw=tmp_path / "python3", wmux_exe=tmp_path / "wmux.app",
                           bot_count=2)
    assert all(row["operation"] == "none" for row in second["actions"])  # 幂等

    restored = si.rollback_receipt({"items": items}, backend)
    assert restored == ["bridge_task", "wmux_run"]
    assert list(backend.agents_dir.glob("*.plist")) == []


def test_set_task_enabled_rewrites_metadata(backend, launchctl, tmp_path):
    desired = si.desired_state(tmp_path / "repo", "alice",
                               tmp_path / "python3", tmp_path / "wmux.app", 2)
    backend.set_bridge_task(desired["bridge_task"])
    launchctl.reset_mock()
    backend.set_task_enabled(si.BRIDGE_TASK, False)
    assert _commands(launchctl) == [["launchctl", "disable", "gui/501/com.link16.feishu-bridge"]]
    assert backend.get_task(si.BRIDGE_TASK)["enabled"] is False


def test_get_backend_dispatch(monkeypatch):
    monkeypatch.setattr(si.os, "name", "posix")
    monkeypatch.setattr(si.sys, "platform", "darwin")
    assert isinstance(si.get_backend(), si.MacOSBackend)
    monkeypatch.setattr(si.sys, "platform", "linux")
    with pytest.raises(OSError, match="暂不支持"):
        si.get_backend()


def test_main_goes_through_platform_backend(monkeypatch, capsys, tmp_path):
    sentinel = object()
    called = []
    monkeypatch.setattr(si, "get_backend", lambda: called.append(1) or sentinel)
    plan = {"digest": "d", "machine": "m", "user": "u", "repo": "r",
            "pythonw": "p", "wmux_exe": "w", "bot_count": 0,
            "actions": [], "maintenance_required": False}
    monkeypatch.setattr(si, "make_plan", lambda backend: (plan, {}, {}))
    assert si.main(["plan", "--json"]) == 0
    assert called == [1]
    assert '"status": "planned"' in capsys.readouterr().out
