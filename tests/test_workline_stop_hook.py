import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
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


class NestedAgentTests(unittest.TestCase):
    """2026-09-25: two `codex exec` runs launched from inside tb25-link16 inherited
    FEISHU_BRIDGE_SESSION, opened turns as the bot and left its card on
    「工作标题生成失败」. They carried the bot's Claude session stamp."""

    BOT_SESSION = "bot-claude-session"

    def run_as(self, hook, state, payload, stamp):
        env = os.environ.copy()
        env.update({
            "FEISHU_BRIDGE_SESSION": "test-bot",
            "FEISHU_BRIDGE_OUTBOX_DIR": str(state),
            "PYTHONIOENCODING": "utf-8",
            "CLAUDE_CODE_SESSION_ID": stamp,
        })
        return subprocess.run(
            [sys.executable, str(ROOT / "feishu/hooks" / hook)],
            input=json.dumps(payload), text=True, capture_output=True, env=env, check=False,
        )

    def test_rule(self):
        import bridge_env
        cases = [
            ({}, {"session_id": "s1"}, False),                               # no agent stamp
            ({"CLAUDE_CODE_SESSION_ID": "s1"}, {"session_id": "s1"}, False),  # the bot's own Claude
            ({"CLAUDE_CODE_SESSION_ID": "bot"}, {"session_id": "exec"}, True),  # Codex inside a Claude bot
            ({"CODEX_THREAD_ID": "bot"}, {"thread_id": "exec"}, True),          # Codex inside a Codex bot
            ({"CLAUDE_CODE_SESSION_ID": "bot"}, {}, False),                    # no session: keep old behaviour
        ]
        for env, payload, nested in cases:
            with self.subTest(env=env, payload=payload), \
                    unittest.mock.patch.dict(os.environ, env):
                self.assertIs(bridge_env.nested_agent(payload), nested)

    def test_nested_agent_neither_opens_a_turn_nor_fails_the_bot_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            route = turn_delivery_guard.activate(
                state, "test-bot", {"kind": "p2a"}, turn_key="bot-turn",
                metadata={"workline_gate": session_work.GATE_CONTRACT},
            )
            session_work.begin_turn("test-bot", "bot-turn", session=self.BOT_SESSION, state_dir=state)
            session_work.decide_work("test-bot", "bot-turn", "replace", project="Link16",
                                     task="修复卡片标题", state_dir=state)
            nested = {"session_id": "nested-codex-thread", "prompt": "Reply with exactly: OK"}
            for hook in ("bridge_userprompt.py", "bridge_workline_stop.py", "bridge_workline_stop.py"):
                result = self.run_as(hook, state, nested, self.BOT_SESSION)
                self.assertEqual((result.returncode, result.stdout.strip()), (0, ""), result.stderr)
            self.assertEqual(turn_delivery_guard.read_route(state, "test-bot")["turn_key"], "bot-turn")
            work = session_work.delivery_work("test-bot", {"route": route}, state)
            self.assertEqual(work["task"], "修复卡片标题")
            # The bot's own next prompt still opens its turn.
            own = self.run_as("bridge_userprompt.py", state,
                              {"session_id": self.BOT_SESSION, "prompt": "hi"}, self.BOT_SESSION)
            self.assertIn("feishu-workline", own.stdout)
            self.assertNotEqual(turn_delivery_guard.read_route(state, "test-bot")["turn_key"], "bot-turn")


if __name__ == "__main__":
    unittest.main()
