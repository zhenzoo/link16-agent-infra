import copy
import json
from pathlib import Path

import pytest

from feishu import service_installer as si


class FakeBackend:
    def __init__(self, run=None, bridge=None, legacy=None, fail_on=None):
        self.run = copy.deepcopy(run or {"exists": False, "value": "", "type": "REG_SZ"})
        self.tasks = {
            si.BRIDGE_TASK: copy.deepcopy(bridge or {"exists": False}),
            si.LEGACY_TASK: copy.deepcopy(legacy or {"exists": False}),
        }
        self.calls = []
        self.fail_on = fail_on

    def _call(self, name):
        self.calls.append(name)
        if self.fail_on == name:
            raise RuntimeError(f"injected {name}")

    def get_run(self):
        return copy.deepcopy(self.run)

    def set_run(self, state):
        self._call("set_run")
        self.run = copy.deepcopy(state)

    def delete_run(self):
        self._call("delete_run")
        self.run = {"exists": False, "value": "", "type": "REG_SZ"}

    def get_task(self, name):
        return copy.deepcopy(self.tasks[name])

    def set_bridge_task(self, state):
        self._call("set_bridge_task")
        self.tasks[si.BRIDGE_TASK] = {**copy.deepcopy(state), "raw_xml": "<new/>"}

    def restore_task(self, name, raw_xml, enabled):
        self._call(f"restore_task:{name}")
        restored = json.loads(raw_xml)
        restored["raw_xml"] = raw_xml
        restored["enabled"] = enabled
        self.tasks[name] = restored

    def unregister_task(self, name):
        self._call(f"unregister_task:{name}")
        self.tasks[name] = {"exists": False}

    def set_task_enabled(self, name, enabled):
        self._call(f"set_task_enabled:{name}:{enabled}")
        self.tasks[name]["enabled"] = enabled


def desired(tmp_path, before):
    raw = si.desired_state(
        tmp_path / "repo", "MACHINE\\user", tmp_path / "pythonw.exe",
        tmp_path / "wmux.exe", 3,
    )
    return si.materialize_desired(before, raw)


def plan_for(tmp_path, backend):
    before = si.inspect_state(backend)
    target = desired(tmp_path, before)
    plan = si.build_plan(
        before, target, machine="MACHINE", user="MACHINE\\user",
        repo=tmp_path / "repo", pythonw=tmp_path / "pythonw.exe",
        wmux_exe=tmp_path / "wmux.exe", bot_count=3,
    )
    return plan, before, target


def existing_bridge(target, **changes):
    row = {**copy.deepcopy(target["bridge_task"]), **changes}
    serializable = {key: value for key, value in row.items() if key != "raw_xml"}
    row["raw_xml"] = json.dumps(serializable, sort_keys=True)
    return row


def test_fresh_plan_is_read_only_and_digest_is_stable(tmp_path):
    backend = FakeBackend()
    first, _before, _target = plan_for(tmp_path, backend)
    second, *_ = plan_for(tmp_path, backend)
    assert [row["operation"] for row in first["actions"]] == ["create", "create", "none"]
    assert first["digest"] == second["digest"]
    assert backend.calls == []


def test_apply_is_idempotent_and_rollback_removes_new_entries(tmp_path):
    backend = FakeBackend()
    plan, before, target = plan_for(tmp_path, backend)
    status, items = si.apply_plan(plan, before, target, backend)
    assert status == "applied"
    assert {item["key"] for item in items} == {"wmux_run", "bridge_task"}
    second, *_ = plan_for(tmp_path, backend)
    assert all(row["operation"] == "none" for row in second["actions"])

    receipt = {"items": items}
    assert si.rollback_receipt(receipt, backend) == ["bridge_task", "wmux_run"]
    assert backend.run["exists"] is False
    assert backend.tasks[si.BRIDGE_TASK]["exists"] is False


def test_managed_drift_updates_but_unrelated_values_fail_closed(tmp_path):
    empty = si.inspect_state(FakeBackend())
    target = desired(tmp_path, empty)
    managed = existing_bridge(target, disallow_start_on_batteries=True)
    backend = FakeBackend(
        run={"exists": True, "value": '"C:\\Users\\u\\AppData\\Local\\wmux\\app-1\\wmux.exe"',
             "type": "REG_SZ"},
        bridge=managed,
    )
    plan, *_ = plan_for(tmp_path, backend)
    assert [row["operation"] for row in plan["actions"][:2]] == ["update", "update"]

    conflict = FakeBackend(
        run={"exists": True, "value": '"C:\\other.exe"', "type": "REG_SZ"},
        bridge={"exists": True, "arguments": "unrelated.py", "raw_xml": "<other/>"},
    )
    blocked, before, target = plan_for(tmp_path, conflict)
    assert [row["status"] for row in blocked["actions"][:2]] == ["conflict", "conflict"]
    with pytest.raises(ValueError, match="conflict"):
        si.apply_plan(blocked, before, target, conflict)
    assert conflict.calls == []


def test_plan_drift_and_apply_failure_roll_back_completed_items(tmp_path):
    backend = FakeBackend(fail_on="set_bridge_task")
    plan, before, target = plan_for(tmp_path, backend)
    with pytest.raises(RuntimeError, match="injected"):
        si.apply_plan(plan, before, target, backend)
    assert backend.run["exists"] is False
    assert "delete_run" in backend.calls

    backend = FakeBackend()
    plan, before, target = plan_for(tmp_path, backend)
    backend.run = {"exists": True, "value": '"C:\\manual.exe"', "type": "REG_SZ"}
    with pytest.raises(RuntimeError, match="发生漂移"):
        si.apply_plan(plan, before, target, backend)
    assert backend.calls == []


def test_legacy_task_is_only_disabled_and_can_be_restored(tmp_path):
    legacy = {"exists": True, "enabled": True, "raw_xml": "legacy"}
    backend = FakeBackend(legacy=legacy)
    plan, before, target = plan_for(tmp_path, backend)
    assert plan["maintenance_required"] is True
    assert plan["actions"][2]["operation"] == "disable"
    _status, items = si.apply_plan(plan, before, target, backend)
    assert backend.tasks[si.LEGACY_TASK]["enabled"] is False
    legacy_calls = [call for call in backend.calls if si.LEGACY_TASK in call]
    assert legacy_calls == [f"set_task_enabled:{si.LEGACY_TASK}:False"]
    si.rollback_receipt({"items": items}, backend)
    assert backend.tasks[si.LEGACY_TASK]["enabled"] is True


def test_rollback_compare_and_swap_refuses_later_manual_change(tmp_path):
    backend = FakeBackend()
    plan, before, target = plan_for(tmp_path, backend)
    _status, items = si.apply_plan(plan, before, target, backend)
    backend.run["value"] = '"C:\\manual-new.exe"'
    with pytest.raises(RuntimeError, match="rollback-conflict"):
        si.rollback_receipt({"items": items}, backend)


def test_plan_and_receipt_shapes_never_contain_credentials(tmp_path):
    backend = FakeBackend()
    plan, before, target = plan_for(tmp_path, backend)
    _status, items = si.apply_plan(plan, before, target, backend)
    text = json.dumps({"plan": plan, "items": items}, ensure_ascii=False).casefold()
    for forbidden in ("app_secret", "auth.json", "session.json", "token="):
        assert forbidden not in text
