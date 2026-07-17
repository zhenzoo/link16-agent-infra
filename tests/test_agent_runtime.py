import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_runtime  # noqa: E402
import feishu_bridge  # noqa: E402


class CodexSkillInvocationTests(unittest.TestCase):
    def test_translates_installed_skill_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".agents" / "skills" / "claude-compat-envsync"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: envsync\ndescription: test\n---\n", encoding="utf-8"
            )
            with patch.object(Path, "home", return_value=home):
                actual = agent_runtime.codex_skill_invocation(
                    {"agent": "codex", "codex_home": str(home / ".codex-personal")},
                    "/envsync dry-run only",
                )
        self.assertEqual(actual, "$envsync dry-run only")

    def test_does_not_capture_native_or_missing_or_claude_commands(self):
        bot = {"agent": "codex", "codex_home": "~/.codex-personal"}
        self.assertIsNone(agent_runtime.codex_skill_invocation(bot, "/model gpt-5.6-sol"))
        self.assertIsNone(agent_runtime.codex_skill_invocation(bot, "/not-installed"))
        self.assertIsNone(agent_runtime.codex_skill_invocation({"agent": "claude"}, "/envsync"))


class CodexCanaryRuntimeTests(unittest.TestCase):
    def test_only_explicit_canary_uses_app_server_worker(self):
        project = ROOT
        state = ROOT / "feishu" / "_state"
        canary = agent_runtime.worker_cmd(
            {
                "name": "tb25-link16-codex",
                "agent": "codex",
                "codex_home": "~/.codex-personal",
                "codex_transport": "app-server-canary",
            },
            project,
            state,
        )
        ordinary = agent_runtime.worker_cmd(
            {"name": "another-codex", "agent": "codex", "codex_home": "~/.codex-personal"},
            project,
            state,
        )
        self.assertIn("codex_app_server_worker.py", canary)
        self.assertIn("FEISHU_CODEX_EVENT_STREAM=1", canary)
        self.assertNotIn("codex_app_server_worker.py", ordinary)
        self.assertIn("codex --dangerously-bypass", ordinary)

    def test_remote_tui_is_ready_without_normal_cli_banner(self):
        remote = {
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        screen = "status line\n› Use /skills to list available skills"
        self.assertTrue(agent_runtime.is_ready(remote, screen))
        self.assertFalse(agent_runtime.is_ready({"agent": "codex"}, screen))

    def test_remote_tui_bare_composer_is_ready_but_trust_prompt_is_not(self):
        remote = {
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        self.assertTrue(agent_runtime.is_ready(remote, "status\n›   \n"))
        trust = (
            "Do you trust the contents of this directory?\n"
            "Press enter to continue\n› Use /skills"
        )
        self.assertFalse(agent_runtime.is_ready(remote, trust))

    def test_remote_tui_warmup_marker_allows_rotating_composer_suggestion(self):
        remote = {
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        screen = (
            "OpenAI Codex\n"
            "• LINK16_APP_SERVER_READY\n"
            "› Summarize recent commits\n"
        )
        self.assertTrue(agent_runtime.is_ready(remote, screen))
        self.assertFalse(agent_runtime.is_ready(
            remote,
            "OpenAI Codex\n› a real user draft\n",
        ))


class BridgeProcessSnapshotTests(unittest.TestCase):
    def test_groups_exact_bot_names_from_one_snapshot(self):
        actual = feishu_bridge._parse_bridge_processes([
            {"ProcessId": 10, "CommandLine": "python feishu_bridge.py run --bot tb24-notes"},
            {"ProcessId": 11, "CommandLine": "python feishu_bridge.py run --bot tb24-notes-2"},
            {"ProcessId": 12, "CommandLine": 'python feishu_bridge.py run --bot "tb25-speech-codex"'},
            {"ProcessId": 13, "CommandLine": "python feishu_bridge.py status --bot tb24-notes"},
        ])
        self.assertEqual(actual, {
            "tb24-notes": ["10"],
            "tb24-notes-2": ["11"],
            "tb25-speech-codex": ["12"],
        })


class AppServerReadySignalTests(unittest.TestCase):
    def test_accepts_only_fresh_signal_for_explicit_app_server_bot(self):
        bot = {
            "name": "tb25-cartoonMV-codex",
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                path = Path(tmp) / "bridge-codex-app-ready-tb25-cartoonMV-codex.json"
                path.write_text(json.dumps({"worker_pid": 123, "ts": 20}), encoding="utf-8")
                self.assertTrue(feishu_bridge._app_server_ready_signal(bot, 10))
                self.assertFalse(feishu_bridge._app_server_ready_signal(bot, 30))
                self.assertFalse(feishu_bridge._app_server_ready_signal(
                    {"name": bot["name"], "agent": "codex"},
                    10,
                ))
            finally:
                feishu_bridge.STATE_DIR = previous


if __name__ == "__main__":
    unittest.main()
