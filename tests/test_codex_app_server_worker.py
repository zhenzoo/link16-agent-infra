import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

from codex_app_server_worker import MilestoneObserver  # noqa: E402


class _FakeRpc:
    def __init__(self, messages):
        self.notifications = queue.Queue()
        for message in messages:
            self.notifications.put(message)


class AppServerFinalDeliveryTests(unittest.TestCase):
    def test_typed_final_is_written_as_answer_without_stop_hook(self):
        final = {
            "method": "item/completed",
            "params": {
                "threadId": "root",
                "turnId": "turn-1",
                "item": {
                    "id": "final-1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "任务完成。",
                },
            },
        }
        # Replay the same notification once: reconnect/replay must not double-send.
        messages = [
            final,
            json.loads(json.dumps(final)),
            {"method": "_transport_error", "params": {"message": "test end"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_owner"}
            (state / "bridge-turn-route-test-bot.json").write_text(
                json.dumps(route), encoding="utf-8"
            )
            observer = MilestoneObserver(
                _FakeRpc(messages),
                bot="test-bot",
                root_thread="root",
                state_dir=state,
                workspace_root=ROOT,
            )

            observer._run()

            records = [
                json.loads(line)
                for line in (state / "bridge-outbox-test-bot.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["kind"], "answer")
            self.assertEqual(records[0]["session"], "root")
            self.assertEqual(records[0]["anchor"], "turn-1")
            self.assertEqual(records[0]["route"], route)
            self.assertIn("任务完成。", records[0]["text"])
            self.assertIn("✅ 已完成", records[0]["text"])


if __name__ == "__main__":
    unittest.main()
