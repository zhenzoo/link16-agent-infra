import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import agent_runtime as runtime


class KimiProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = self.root / "profiles.json"
        self.registry.write_text(json.dumps({
            "version": 1, "default_profiles": {"kimi": "kp"},
            "profiles": {"kp": {"runtime": "kimi", "home": "~/.kimi-personal",
                                  "launcher": "direct"}},
        }), encoding="utf-8")
        env = patch.dict(os.environ, {runtime.PROFILE_REGISTRY_ENV: str(self.registry)})
        env.start(); self.addCleanup(env.stop)
        availability = patch.object(runtime, "_require_profile_available")
        availability.start(); self.addCleanup(availability.stop)

    def test_terminal_uses_native_home_and_yolo(self):
        command = runtime.standalone_worker_cmd("kp")
        self.assertIn('LINK16_AGENT_PROFILE="kp"', command)
        self.assertIn('KIMI_CODE_HOME="', command)
        self.assertIn('/.kimi-personal" kimi --yolo', command)
        self.assertNotIn("CODEX_HOME=", command)
        self.assertNotIn("CLAUDE_CONFIG_DIR=", command)
        self.assertFalse(runtime.pins_jsonl({"profile": "kp"}))

    def test_login_acp_and_prompt_do_not_get_conflicting_yolo_flag(self):
        for args in (["login"], ["acp"], ["doctor"], ["-p", "hello"],
                     ["--prompt=hello"], ["--auto"], ["--plan"]):
            with self.subTest(args=args):
                self.assertNotIn("--yolo", runtime.standalone_worker_cmd(
                    "kp", provider_args=args))

    def test_model_override_is_cleared_in_the_child_shell(self):
        # Probe environment routing with a shell function, without invoking a model.
        with patch.dict(os.environ, {"KIMI_MODEL_NAME": "stale-provider"}):
            command = runtime.standalone_worker_cmd("kp")
            probe = ('kimi() { printf "%s|%s|%s" "$LINK16_AGENT_PROFILE" '
                     '"$KIMI_CODE_HOME" "${KIMI_MODEL_NAME-unset}"; }; ' + command)
            result = subprocess.run([runtime.resolve_shell(), "-lc", probe],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("kp|"), result.stdout)
            self.assertTrue(result.stdout.endswith("/.kimi-personal|unset"), result.stdout)
            self.assertEqual(os.environ["KIMI_MODEL_NAME"], "stale-provider")

    def test_native_bridge_launch_uses_one_profile_and_own_worker(self):
        bot = {"name": "test", "profile": "kp"}
        command = runtime.worker_cmd(bot, self.root, self.root)
        self.assertIn("kimi_native_worker.py", command)
        self.assertIn('LINK16_AGENT_PROFILE="kp"', command)
        self.assertNotIn("codex_app_server", command)
        self.assertEqual(runtime.account_aliases(), ["kp"])

    def test_native_ready_requires_composer_and_status_without_trust_dialog(self):
        bot = {"name": "test", "profile": "kp"}
        screen = "│ >                        │\nyolo K2.7 Coding\ncontext: 14% (35k/256k)"
        self.assertTrue(runtime.is_ready(bot, screen))
        self.assertFalse(runtime.is_ready(bot, "Trust this folder?\n" + screen))
        self.assertFalse(runtime.is_ready(bot, "LINK16_KIMI_NATIVE_READY"))
        self.assertFalse(runtime.is_ready(bot, "│ > unsent prompt │\nyolo K2.7 Coding\ncontext: 14%"))


if __name__ == "__main__":
    unittest.main()
