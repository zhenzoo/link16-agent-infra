import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import agent_runtime as runtime
import feishu_bridge


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

    def test_native_ready_ignores_permission_mode_vocabulary(self):
        """0.41.0 renamed the status-bar modes; readiness must not care."""
        bot = {"name": "test", "profile": "kp"}
        # Captured from a real Kimi Code 0.41.0 idle composer on this machine.
        idle = (
            " ╭────────╮ " \
            + "\n │ >        │ \n"
            + " ╰────────╯ \n"
            + " Ask When Needed  K3 thinking: high  main [+9 -6] \n"
            + "        context: 0% (0/1M) "
        )
        self.assertTrue(runtime.is_ready(bot, idle))
        for mode in ("Never Ask", "Always Ask", "Plan Mode", "yolo", "whatever-comes-next"):
            self.assertTrue(runtime.is_ready(bot, idle.replace("Ask When Needed", mode)))
        # A busy turn paints the spinner instead of the composer box: not ready.
        self.assertFalse(runtime.is_ready(
            bot, " Ask When Needed  K3 \ncontext: 3% \n ⠹ thinking…"))

    def test_startup_handshake_accepts_fresh_and_rejects_stale_or_foreign(self):
        bot = {"name": "test", "profile": "kp"}
        previous = feishu_bridge.STATE_DIR
        feishu_bridge.STATE_DIR = self.root
        try:
            path = self.root / "bridge-kimi-ready-test.json"
            path.write_text(json.dumps(
                {"native_pid": 123, "ts": 20, "profile": "kp"}), encoding="utf-8")
            self.assertTrue(feishu_bridge._kimi_ready_signal(bot, 10))
            self.assertFalse(feishu_bridge._kimi_ready_signal(bot, 30))   # 上一次 spawn 的残留
            path.write_text(json.dumps(
                {"native_pid": 123, "ts": 20, "profile": "other"}), encoding="utf-8")
            self.assertFalse(feishu_bridge._kimi_ready_signal(bot, 10))   # 别的账号的会话
            path.write_text(json.dumps({"ts": 20, "profile": "kp"}), encoding="utf-8")
            self.assertFalse(feishu_bridge._kimi_ready_signal(bot, 10))   # 没起出 TUI
        finally:
            feishu_bridge.STATE_DIR = previous

    def test_wait_ready_survives_a_status_bar_rewrite(self):
        """The whole 2026-09-07 outage in one test: chrome changes, start still works."""
        bot = {"name": "test", "profile": "kp"}
        previous = feishu_bridge.STATE_DIR
        feishu_bridge.STATE_DIR = self.root
        try:
            (self.root / "bridge-kimi-ready-test.json").write_text(
                json.dumps({"native_pid": 123, "ts": time.time() + 1, "profile": "kp"}),
                encoding="utf-8")
            # No "context:", no mode word — nothing left but the composer box.
            future_screen = " ╭────────╮ \n │ >        │ \n ╰────────╯ \n 全新状态栏 "
            with patch.object(feishu_bridge, "read_screen", return_value=future_screen):
                self.assertTrue(
                    feishu_bridge._wait_agent_ready(bot, "test-pty", timeout=0.1))
            self.assertFalse(runtime.is_ready(bot, future_screen))  # 读屏这条确实已失效
        finally:
            feishu_bridge.STATE_DIR = previous

    def test_version_drift_only_fires_on_positive_evidence(self):
        verified = runtime.VERIFIED_CLI_VERSIONS["kimi"]
        with patch.object(runtime, "cli_version", return_value="99.0.0"):
            drift = runtime.cli_version_drift("kp")
            self.assertTrue(drift["drifted"])
            self.assertEqual(drift["verified"], verified)
            self.assertIn("99.0.0", runtime.version_drift_note("kp"))
        for installed in (verified, None):   # 同版本 / 读不出版本 → 绝不误报
            with patch.object(runtime, "cli_version", return_value=installed):
                self.assertFalse(runtime.cli_version_drift("kp")["drifted"])
                self.assertIsNone(runtime.version_drift_note("kp"))
        with patch.dict(runtime.VERIFIED_CLI_VERSIONS, {}, clear=True):
            with patch.object(runtime, "cli_version", return_value="99.0.0"):
                self.assertFalse(runtime.cli_version_drift("kp")["drifted"])

    def test_doctor_reports_version_without_turning_drift_into_failure(self):
        with patch.object(runtime, "cli_version", return_value="99.0.0"):
            report = runtime.profile_doctor("kp")
        self.assertEqual(report["version"]["runtime"], "kimi")
        self.assertTrue(report["version"]["drifted"])
        self.assertNotIn("99.0.0", "；".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
