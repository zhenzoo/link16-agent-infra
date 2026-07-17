import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

from bridge_events import MilestoneAccumulator, normalize_codex_notification  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "plan915"
SECURITY_FIXTURE = ROOT / "tests" / "fixtures" / "plan916" / "codex_security_surfaces.json"
SECURITY_MARKERS = (
    "PLAN916_RAW_COMMAND_X", "PLAN916_RAW_OUTPUT_X", "PLAN916_RAW_STDOUT_X",
    "PLAN916_RAW_STDERR_X", "PLAN916_RAW_ARGUMENT_X", "PLAN916_RAW_ACTION_X",
    "PLAN916_RAW_QUERY_X", "PLAN916_RAW_DIFF_X", "PLAN916_OUTSIDE_PATH_X",
    "PLAN916_SECRET_PATH_X", "PLAN916_SECRET_TOOL_X",
)


def security_messages():
    messages = json.loads(SECURITY_FIXTURE.read_text(encoding="utf-8"))
    workspace = str(ROOT).replace("\\", "/")
    outside = str(ROOT.parent / "plan916-outside").replace("\\", "/")

    def hydrate(value):
        if isinstance(value, str):
            return value.replace("__WORKSPACE__", workspace).replace("__OUTSIDE__", outside)
        if isinstance(value, list):
            return [hydrate(item) for item in value]
        if isinstance(value, dict):
            return {key: hydrate(item) for key, item in value.items()}
        return value

    return hydrate(messages)


class CodexEventContractTests(unittest.TestCase):
    def test_commentary_plan_and_tools_are_visible_but_reasoning_and_raw_io_are_not(self):
        messages = json.loads((FIXTURES / "codex_app_server.json").read_text(encoding="utf-8"))
        acc = MilestoneAccumulator()
        final = None
        for message in messages:
            event = normalize_codex_notification(message, "root")
            if event and event["event_type"] == "final":
                final = event
            elif event:
                acc.apply(event)
        record = acc.progress_record(session="root")
        rendered = "\n".join(step["label"] for step in record["steps"])
        self.assertIn("正在核对桥接契约", rendered)
        self.assertIn("当前计划", rendered)
        self.assertIn("工具活动 · 2 次", rendered)
        self.assertNotIn("private", rendered)
        self.assertNotIn("SECRET RAW COMMAND", json.dumps(record, ensure_ascii=False))
        self.assertNotIn("SECRET RAW OUTPUT", json.dumps(record, ensure_ascii=False))
        self.assertEqual(final["payload"]["text"], "完成。")

    def test_child_thread_is_ignored_and_root_collab_status_is_kept(self):
        messages = json.loads((FIXTURES / "interleaved_child.json").read_text(encoding="utf-8"))
        acc = MilestoneAccumulator()
        for message in messages:
            event = normalize_codex_notification(message, "root")
            if event:
                acc.apply(event)
        record = acc.progress_record(session="root")
        rendered = "\n".join(step["label"] for step in record["steps"])
        self.assertIn("父任务开始", rendered)
        self.assertIn("1/1 已完成", rendered)
        self.assertNotIn("不应进入", rendered)
        self.assertEqual(record["root_turn"], "parent-turn")

    def test_plan_revision_replaces_one_step_in_place(self):
        acc = MilestoneAccumulator()
        base = {"method": "turn/plan/updated", "params": {"threadId": "root", "turnId": "t", "plan": []}}
        first = json.loads(json.dumps(base))
        first["params"]["plan"] = [{"step": "A", "status": "inProgress"}]
        second = json.loads(json.dumps(base))
        second["params"]["plan"] = [{"step": "A", "status": "completed"}]
        acc.apply(normalize_codex_notification(first, "root"))
        acc.apply(normalize_codex_notification(second, "root"))
        steps = acc.progress_record(session="root")["steps"]
        self.assertEqual(len(steps), 1)
        self.assertIn("✅ A", steps[0]["label"])
        self.assertEqual(steps[0]["revision"], 2)

    def test_command_actions_publish_only_safe_program_and_workspace_paths(self):
        message = {
            "method": "item/completed",
            "params": {"threadId": "root", "turnId": "t", "item": {
                "id": "cmd", "type": "commandExecution", "status": "completed",
                "cwd": str(ROOT),
                "command": "rg -n SECRET_QUERY feishu tests",
                "aggregatedOutput": "SECRET OUTPUT",
                "commandActions": [
                    {"type": "search", "command": "SECRET RAW ACTION", "query": "SECRET_QUERY", "path": str(ROOT / "feishu")},
                    {"type": "listFiles", "command": "SECRET RAW ACTION", "path": str(ROOT / "tests")},
                ],
            }},
        }
        event = normalize_codex_notification(message, "root", workspace_root=ROOT)
        encoded = json.dumps(event, ensure_ascii=False)
        self.assertEqual(event["payload"]["tool_family"], "search")
        self.assertEqual(event["payload"]["program"], "rg")
        self.assertEqual(event["payload"]["access_paths"], ["feishu", "tests"])
        self.assertNotIn("SECRET", encoded)
        self.assertNotIn(str(ROOT), encoded)

    def test_paths_outside_workspace_variables_and_sensitive_files_are_hidden(self):
        message = {
            "method": "item/completed",
            "params": {"threadId": "root", "turnId": "t", "item": {
                "id": "cmd", "type": "commandExecution", "status": "completed",
                "cwd": str(ROOT),
                "command": "Get-Content '$HOME/.env' 'feishu/bridge_events.py'",
                "commandActions": [
                    {"type": "read", "command": "raw", "name": "outside", "path": str(Path.home() / "private.txt")},
                    {"type": "read", "command": "raw", "name": "env", "path": str(ROOT / ".env")},
                    {"type": "read", "command": "raw", "name": "safe", "path": str(ROOT / "feishu" / "bridge_events.py")},
                ],
            }},
        }
        event = normalize_codex_notification(message, "root", workspace_root=ROOT)
        self.assertEqual(event["payload"]["access_paths"], ["feishu/bridge_events.py"])

    def test_file_changes_split_created_and_written_paths_without_diff(self):
        message = {
            "method": "item/completed",
            "params": {"threadId": "root", "turnId": "t", "item": {
                "id": "patch", "type": "fileChange", "status": "completed",
                "changes": [
                    {"path": str(ROOT / "docs" / "new.md"), "kind": {"type": "add"}, "diff": "SECRET ADD"},
                    {"path": str(ROOT / "feishu" / "bridge_events.py"), "kind": {"type": "update"}, "diff": "SECRET UPDATE"},
                    {"path": str(ROOT / ".env"), "kind": {"type": "update"}, "diff": "SECRET ENV"},
                ],
            }},
        }
        event = normalize_codex_notification(message, "root", workspace_root=ROOT)
        encoded = json.dumps(event, ensure_ascii=False)
        self.assertEqual(event["payload"]["create_paths"], ["docs/new.md"])
        self.assertEqual(event["payload"]["write_paths"], ["feishu/bridge_events.py"])
        self.assertNotIn("SECRET", encoded)

    def test_twenty_adjacent_tools_form_one_structured_summary(self):
        acc = MilestoneAccumulator()
        for index in range(20):
            family, program = (("search", "rg") if index % 2 == 0 else ("read", "Get-Content"))
            acc.apply({
                "event_id": f"tool-{index}", "event_type": "tool", "turn": "t", "label": "tool",
                "payload": {
                    "tool_family": family, "program": program,
                    "access_paths": ["feishu/bridge_events.py", f"docs/sample-{index % 3}.md"],
                },
            })
        steps = acc.progress_record(session="root")["steps"]
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["event_id"], "tools:tool-0")
        self.assertEqual(step["revision"], 20)
        self.assertEqual(step["tool_count"], 20)
        self.assertEqual(step["tool_types"], [
            {"family": "search", "program": "rg", "count": 10},
            {"family": "read", "program": "Get-Content", "count": 10},
        ])
        self.assertEqual(step["access_paths"], [
            "feishu/bridge_events.py", "docs/sample-0.md", "docs/sample-1.md", "docs/sample-2.md",
        ])
        self.assertEqual(len(step["source_event_ids"]), 20)

    def test_commentary_splits_adjacent_tool_segments(self):
        acc = MilestoneAccumulator()
        acc.apply({"event_id": "a", "event_type": "tool", "turn": "t", "label": "tool",
                   "payload": {"tool_family": "search", "program": "rg"}})
        acc.apply({"event_id": "c", "event_type": "commentary", "turn": "t", "label": "💬 checkpoint"})
        acc.apply({"event_id": "b", "event_type": "tool", "turn": "t", "label": "tool",
                   "payload": {"tool_family": "read", "program": "Get-Content"}})
        acc.apply({"event_id": "d", "event_type": "tool", "turn": "t", "label": "tool",
                   "payload": {"tool_family": "python", "program": "Python"}})
        steps = acc.progress_record(session="root")["steps"]
        self.assertEqual([step["kind"] for step in steps], ["tool", "commentary", "tool"])
        self.assertEqual([steps[0]["event_id"], steps[2]["event_id"]], ["tools:a", "tools:b"])
        self.assertEqual([steps[0]["tool_count"], steps[2]["tool_count"]], [1, 2])

    def test_exact_tool_replay_is_ignored_but_safe_payload_revision_updates(self):
        acc = MilestoneAccumulator()
        event = {"event_id": "x", "event_type": "tool", "turn": "t", "label": "tool",
                 "payload": {"tool_family": "read", "program": "Get-Content", "access_paths": ["feishu"]}}
        self.assertTrue(acc.apply(event))
        self.assertFalse(acc.apply(json.loads(json.dumps(event))))
        changed = json.loads(json.dumps(event))
        changed["payload"]["access_paths"] = ["tests"]
        self.assertTrue(acc.apply(changed))
        step = acc.progress_record(session="root")["steps"][0]
        self.assertEqual(step["tool_count"], 1)
        self.assertEqual(step["revision"], 2)
        self.assertEqual(step["access_paths"], ["tests"])
        self.assertEqual(step["source_event_ids"], ["x"])

    def test_hostile_typed_items_are_safe_in_ledger_and_progress_surfaces(self):
        acc = MilestoneAccumulator()
        ledger = []
        for message in security_messages():
            event = normalize_codex_notification(message, "root", workspace_root=ROOT)
            self.assertIsNotNone(event)
            ledger.append({"kind": "event", "contract": "milestone-v1", **event})
            acc.apply(event)
        record = acc.progress_record(session="root")
        surfaces = {
            "ledger": json.dumps(ledger, ensure_ascii=False),
            "outbox": json.dumps(record, ensure_ascii=False),
        }
        for surface in surfaces.values():
            for marker in SECURITY_MARKERS:
                self.assertNotIn(marker, surface)
            self.assertNotIn(str(ROOT), surface)
            self.assertNotIn(str(ROOT.parent / "plan916-outside"), surface)
        rendered = "\n".join(step["label"] for step in record["steps"])
        self.assertIn("搜索 rg ×1", rendered)
        self.assertIn("Python ×1", rendered)
        self.assertIn("文件修改 ×1", rendered)
        self.assertIn("外部工具 ×1", rendered)
        self.assertIn("`feishu/bridge_events.py`", rendered)
        self.assertIn("`docs/PLAN-916-generated.md`", rendered)

    def test_path_display_is_limited_but_reports_exact_overflow(self):
        acc = MilestoneAccumulator()
        acc.apply({
            "event_id": "paths", "event_type": "tool", "turn": "t", "label": "tool",
            "payload": {
                "tool_family": "read", "program": "Get-Content",
                "access_paths": [f"docs/path-{index}.md" for index in range(8)],
            },
        })
        step = acc.progress_record(session="root")["steps"][0]
        self.assertEqual(step["access_path_total"], 8)
        self.assertIn("`docs/path-4.md`", step["label"])
        self.assertNotIn("`docs/path-5.md`", step["label"])
        self.assertIn("另有 3 个", step["label"])
        self.assertIn("修改：无", step["label"])


if __name__ == "__main__":
    unittest.main()
