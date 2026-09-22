import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import session_work  # noqa: E402
import turn_delivery_guard  # noqa: E402


class WorklineStopHookTests(unittest.TestCase):
    def run_hook(self, state: Path):
        env = os.environ.copy()
        env.update({
            "FEISHU_BRIDGE_SESSION": "test-bot",
            "FEISHU_BRIDGE_OUTBOX_DIR": str(state),
            "PYTHONIOENCODING": "utf-8",
        })
        return subprocess.run(
            [sys.executable, str(ROOT / "feishu/hooks/bridge_workline_stop.py")],
            input=json.dumps({"session_id": "s1"}), text=True,
            capture_output=True, env=env, check=False,
        )

    def test_blocks_once_then_marks_visible_failure_without_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            route = turn_delivery_guard.activate(
                state, "test-bot", {"kind": "p2a"}, turn_key="turn-1",
                metadata={"workline_gate": session_work.GATE_CONTRACT},
            )
            session_work.begin_turn(
                "test-bot", "turn-1", runtime="codex", project_hint="Link16",
                state_dir=state,
            )
            first = self.run_hook(state)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["decision"], "block")
            second = self.run_hook(state)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.strip(), "")
            work = session_work.delivery_work("test-bot", {"route": route}, state)
            self.assertIn("标题生成失败", work["task"])

    def test_ready_turn_passes_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            turn_delivery_guard.activate(
                state, "test-bot", {"kind": "p2a"}, turn_key="turn-1",
                metadata={"workline_gate": session_work.GATE_CONTRACT},
            )
            session_work.begin_turn("test-bot", "turn-1", state_dir=state)
            session_work.decide_work(
                "test-bot", "turn-1", "replace", project="Link16",
                task="验证工作标题闸", state_dir=state,
            )
            result = self.run_hook(state)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
