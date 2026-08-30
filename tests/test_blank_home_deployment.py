"""Blank-user acceptance: no real auth, registry, or Windows services touched."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import profile_bootstrap as pb  # noqa: E402
import service_installer as si  # noqa: E402


class MemoryServices:
    def __init__(self):
        self.run = {"exists": False, "value": "", "type": "REG_SZ"}
        self.tasks = {si.BRIDGE_TASK: {"exists": False}, si.LEGACY_TASK: {"exists": False}}

    def get_run(self):
        return copy.deepcopy(self.run)

    def set_run(self, state):
        self.run = copy.deepcopy(state)

    def delete_run(self):
        self.run = {"exists": False, "value": "", "type": "REG_SZ"}

    def get_task(self, name):
        return copy.deepcopy(self.tasks[name])

    def set_bridge_task(self, state):
        self.tasks[si.BRIDGE_TASK] = {**copy.deepcopy(state), "raw_xml": "<managed/>"}

    def restore_task(self, name, raw_xml, enabled):  # pragma: no cover - fresh rollback only
        self.tasks[name] = {"exists": True, "enabled": enabled, "raw_xml": raw_xml}

    def unregister_task(self, name):
        self.tasks[name] = {"exists": False}

    def set_task_enabled(self, name, enabled):
        self.tasks[name]["enabled"] = enabled


def test_blank_home_dry_run_apply_and_second_apply_are_safe(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    registry = tmp_path / "agent-profiles.local.json"
    specs = [
        {"name": "claude-work", "runtime": "claude", "home": "~/.claude-work"},
        {"name": "codex-work", "runtime": "codex", "home": "~/.codex-work"},
    ]

    preview = pb.initialize_registry(registry, specs, apply=False)
    assert preview["status"] == "missing"
    assert not registry.exists()
    pb.initialize_registry(registry, specs, apply=True)

    dry = pb.bootstrap(home, apply=False, profiles=("claude-work", "codex-work"),
                       registry_path=registry)
    assert any(row["status"] in {"missing", "would-create"} for row in dry)
    first = pb.bootstrap(home, apply=True, profiles=("claude-work", "codex-work"),
                         registry_path=registry)
    second = pb.bootstrap(home, apply=True, profiles=("claude-work", "codex-work"),
                          registry_path=registry)
    assert all(row["status"] == "ok" for row in second)
    assert (home / ".claude-work" / "skills" / "feishu" / "SKILL.md").is_file()
    assert (home / ".agents" / "skills" / "feishu" / "SKILL.md").is_file()
    hooks = json.loads((home / ".codex-work" / "hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"Stop", "PostToolUse", "UserPromptSubmit"}
    assert first
    assert list(home.rglob("auth.json")) == []
    assert list(home.rglob("credentials.json")) == []

    backend = MemoryServices()
    before = si.inspect_state(backend)
    raw_target = si.desired_state(tmp_path / "repo", "MACHINE\\user",
                                  tmp_path / "pythonw.exe", tmp_path / "wmux.exe", 0)
    target = si.materialize_desired(before, raw_target)
    plan = si.build_plan(before, target, machine="MACHINE", user="MACHINE\\user",
                         repo=tmp_path / "repo", pythonw=tmp_path / "pythonw.exe",
                         wmux_exe=tmp_path / "wmux.exe", bot_count=0)
    assert [row["operation"] for row in plan["actions"]] == ["create", "create", "none"]
    status, receipt_items = si.apply_plan(plan, before, target, backend)
    assert status == "applied"
    assert receipt_items
    after_plan = si.build_plan(si.inspect_state(backend), target, machine="MACHINE",
                               user="MACHINE\\user", repo=tmp_path / "repo",
                               pythonw=tmp_path / "pythonw.exe", wmux_exe=tmp_path / "wmux.exe",
                               bot_count=0)
    assert all(row["operation"] == "none" for row in after_plan["actions"])
    serialized = json.dumps({"plan": plan, "receipt": receipt_items}).casefold()
    assert "app_secret" not in serialized and "token=" not in serialized
