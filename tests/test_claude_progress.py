import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

from jsonl_reply_extract import progress
from bridge_outbox import drain_batch
from feishu_bridge import _card_payload


def user(text):
    return {"type": "user", "message": {"content": text}}


def call(name, args, tool_id="call"):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": args},
    ]}}


def result(tool_id="call", error=False, task_id=None):
    record = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id,
         "is_error": error, "content": "PRIVATE TOOL OUTPUT"},
    ]}}
    if task_id is not None:
        record["toolUseResult"] = {"task": {"id": task_id}}
    return record


def extract(records):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
        return progress(path)["milestone"]


class ClaudeProgressTests(unittest.IsolatedAsyncioTestCase):
    def test_real_posttool_hook_emits_safe_milestone_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.jsonl"
            records = [user("start"), call("TodoWrite", {"todos": [
                {"content": "验收（预计 13:20 完成）", "status": "in_progress"},
            ]}), result()]
            path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(ROOT / "feishu/hooks/bridge_posttool.py")],
                input=json.dumps({"session_id": "test-session", "transcript_path": str(path),
                                  "tool_name": "TodoWrite", "tool_input": records[1]["message"]["content"][0]["input"]}),
                text=True, capture_output=True,
                env={**os.environ, "CLAUDE_PROJECT_DIR": tmp, "FEISHU_BRIDGE_SESSION": "test-bot",
                     "FEISHU_BRIDGE_OUTBOX_DIR": tmp},
                timeout=15,
            )
            self.assertEqual(0, run.returncode, run.stderr)
            record = json.loads((Path(tmp) / "bridge-outbox-test-bot.jsonl").read_text(encoding="utf-8"))
        self.assertEqual("milestone-v1", record["contract"])
        self.assertEqual("claude", record["runtime"])
        self.assertEqual("test-session:1", record["root_turn"])
        self.assertEqual("test-session", record["session"])
        self.assertIn("1. 🔄 验收", next(s["label"] for s in record["steps"] if s["kind"] == "plan"))
        self.assertNotIn("PRIVATE", json.dumps(record))

    def test_todo_plan_preserves_lines_indentation_icons_and_eta(self):
        record = extract([user("start"), call("TodoWrite", {"todos": [
            {"content": "1. ✅ 调研（实际完成 13:00）", "status": "completed"},
            {"content": "2. 🔄 验证（预计 13:20 完成）\n  2.1 🔄 卡片换行（预计 13:15 完成）", "status": "in_progress"},
            {"content": "发布（预计 13:25 完成）", "status": "pending"},
        ]}), result()])
        plan = next(s for s in record["steps"] if s["kind"] == "plan")
        self.assertIn("当前计划**\n\n1. ✅ 调研", plan["label"])
        self.assertIn("\n    2.1 🔄 卡片换行（预计 13:15 完成）", plan["label"])
        self.assertIn("\n3. ⏳ 发布（预计 13:25 完成）", plan["label"])
        self.assertNotIn("✅ ✅", plan["label"])
        self.assertEqual((1, 3), (plan["plan_completed"], plan["plan_total"]))

    def test_failed_or_unfinished_tool_does_not_publish_plan(self):
        for tail in ([], [result(error=True)]):
            with self.subTest(tail=tail):
                record = extract([user("start"), call("TodoWrite", {"todos": [
                    {"content": "must not appear", "status": "completed"},
                ]}), *tail])
                self.assertFalse(any(s["kind"] == "plan" for s in record["steps"]))

    def test_task_update_recovers_known_id_from_previous_turn(self):
        record = extract([
            user("old"), call("TaskCreate", {"subject": "验收（预计 14:00 完成）"}), result(task_id="7"),
            user("continue"), call("TaskUpdate", {"taskId": "7", "status": "completed", "subject": "验收（实际完成 13:50）"}, "update"), result("update"),
        ])
        plan = next(s for s in record["steps"] if s["kind"] == "plan")
        self.assertIn("1. ✅ 验收（实际完成 13:50）", plan["label"])
        self.assertEqual(1, plan["revision"])
        self.assertNotIn("14:00", plan["label"])

    def test_textual_create_receipt_and_update_replace_one_plan(self):
        created = result()
        created["message"]["content"][0]["content"] = "Task #7 created successfully: 验收"
        record = extract([user("start"), call("TaskCreate", {"subject": "验收"}), created,
                          call("TaskUpdate", {"taskId": "7", "status": "completed"}, "update"), result("update")])
        plans = [s for s in record["steps"] if s["kind"] == "plan"]
        self.assertEqual(1, len(plans))
        self.assertEqual(2, plans[0]["revision"])
        self.assertEqual(1, plans[0]["plan_completed"])

    def test_task_deletion_and_unknown_id_do_not_invent_titles(self):
        record = extract([user("start"), call("TaskCreate", {"subject": "验收"}), result(task_id="7"),
                          call("TaskUpdate", {"taskId": "7", "status": "deleted"}, "delete"), result("delete"),
                          call("TaskUpdate", {"taskId": "unknown", "status": "completed"}, "unknown"), result("unknown")])
        plan = next(s for s in record["steps"] if s["kind"] == "plan")
        self.assertEqual(0, plan["plan_total"])
        self.assertNotIn("unknown", plan["label"])

    async def test_public_text_survives_extractor_outbox_and_card_payload(self):
        public = "正在核对计划。\n\n1. 🔄 排版（预计 13:20 完成）\n    1.1 验证换行\n" + "可见说明" * 50
        record = extract([user("start"), {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "PRIVATE REASONING"},
            {"type": "text", "text": public},
        ]}}, call("Bash", {"command": "PRIVATE COMMAND"}), result()])
        cards = []

        async def new_card(text, **kwargs):
            cards.append(_card_payload(text))
            return "message"

        state = {"turn": None, "steps": [], "seg_start": 0, "flushed": 0,
                 "last_flush": 0, "sent": set(), "picker_active": False}
        await drain_batch([record], new_card=new_card, edit_card=None, send_plain=None,
                          state=state, coalesce_sec=0, clock=lambda: 1, force_flush=True)
        self.assertEqual(1, len(cards))
        rendered = cards[0]["body"]["elements"][0]["content"]
        self.assertIn(public, rendered)
        self.assertNotIn("PRIVATE", json.dumps(record) + json.dumps(cards))

    def test_final_text_is_not_repeated_as_progress(self):
        record = extract([user("start"), {"type": "assistant", "message": {
            "stop_reason": "end_turn", "content": [{"type": "text", "text": "FINAL"}],
        }}])
        self.assertEqual([], record["steps"])


if __name__ == "__main__":
    unittest.main()
