import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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


class AgentProfileTests(unittest.TestCase):
    def test_registry_has_nine_profiles_and_cxp_is_codex_default(self):
        profiles = agent_runtime.profile_specs()
        self.assertEqual(len(profiles), 9)
        self.assertEqual(agent_runtime.default_profile("codex"), "cxp")
        self.assertEqual(agent_runtime.profile_spec("cxp").home, "~/.codex-personal")
        self.assertEqual(agent_runtime.profile_spec("cck").launcher, "launch-sh")

    def test_profile_wins_over_conflicting_legacy_runtime(self):
        bot = {
            "profile": "cxp",
            "agent": "claude",
            "claude_config_dir": "~/.claude-kimi",
        }
        self.assertEqual(agent_runtime.runtime_name(bot), "codex")
        self.assertEqual(agent_runtime.current_account(bot), "cxp")

    def test_standalone_commands_cover_claude_backend_and_codex_home(self):
        with patch.object(agent_runtime, "_require_profile_available"):
            kimi = agent_runtime.standalone_worker_cmd(
                "cck", extra_env={"XHS_AUTOPILOT": "1"}
            )
            codex = agent_runtime.standalone_worker_cmd(
                "cxp", cwd=ROOT, provider_args=["resume", "abc 123"]
            )
        self.assertIn(".claude-kimi/launch.sh", kimi)
        self.assertIn('LINK16_AGENT_PROFILE="cck"', kimi)
        self.assertIn('CLAUDE_CONFIG_DIR=', kimi)
        self.assertIn('XHS_AUTOPILOT="1"', kimi)
        self.assertNotIn("API_KEY", kimi)
        self.assertIn('LINK16_AGENT_PROFILE="cxp"', codex)
        self.assertIn(".codex-personal", codex)
        self.assertIn("CODEX_HOME=", codex)
        self.assertIn('"resume" "abc 123"', codex)
        self.assertNotIn("CLAUDE_CONFIG_DIR", codex)

    def test_standalone_profile_is_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "LINK16_AGENT_PROFILE 未设置"):
            agent_runtime.profile_from_env({})
        with self.assertRaises(KeyError):
            agent_runtime.profile_spec("does-not-exist")

    def test_public_ready_probe_supports_claude_and_codex(self):
        cli = ROOT / "feishu" / "agent_profile_cli.py"
        samples = {
            "cck": "Claude Code\n❯",
            "cxp": "OpenAI Codex\npermissions: YOLO mode\n›",
        }
        for profile, screen in samples.items():
            result = subprocess.run(
                [sys.executable, str(cli), "ready", "--profile", profile],
                input=screen,
                text=True,
                capture_output=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        not_ready = subprocess.run(
            [sys.executable, str(cli), "ready", "--profile", "cxp"],
            input="Do you trust the contents of this directory?\nPress enter to continue",
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(not_ready.returncode, 1)
        trust = subprocess.run(
            [sys.executable, str(cli), "needs-trust", "--profile", "cxp"],
            input="Do you trust the contents of this directory?\nPress enter to continue",
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(trust.returncode, 0, trust.stderr)

    def test_machine_defaults_are_per_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "bridge-bots.local.json"
            committed = Path(tmp) / "bridge-bots.json"
            local.write_text(json.dumps({
                "defaults": {"profiles": {"claude": "ccp2", "codex": "cxp"}},
                "bots": [],
            }), encoding="utf-8")
            committed.write_text('{"bots":[]}', encoding="utf-8")
            with (
                patch.object(agent_runtime, "ROSTER_LOCAL_PATH", local),
                patch.object(agent_runtime, "ROSTER_COMMITTED_PATH", committed),
            ):
                self.assertEqual(agent_runtime.machine_default_profile("claude"), "ccp2")
                self.assertEqual(agent_runtime.machine_default_profile("codex"), "cxp")

    def test_roster_default_profile_does_not_override_bot_profile(self):
        defaults = {"profiles": {"claude": "ccp2", "codex": "cxp"}}
        self.assertEqual(
            feishu_bridge._apply_roster_defaults(
                {"name": "claude-bot", "agent": "claude"}, defaults
            )["profile"],
            "ccp2",
        )
        self.assertEqual(
            feishu_bridge._apply_roster_defaults(
                {"name": "codex-bot", "agent": "codex"}, defaults
            )["profile"],
            "cxp",
        )
        explicit = feishu_bridge._apply_roster_defaults(
            {"name": "codex-bot", "profile": "cx"}, defaults
        )
        self.assertEqual(explicit["profile"], "cx")

    def test_parallel_profile_writes_preserve_both_bots_and_remove_legacy_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "bridge-bots.local.json"
            committed = Path(tmp) / "bridge-bots.json"
            committed.write_text(json.dumps({
                "defaults": {"profiles": {"claude": "ccp2", "codex": "cxp"}},
                "bots": [
                    {
                        "name": "one",
                        "agent": "claude",
                        "account": "ccp",
                        "claude_config_dir": "~/.claude-personal",
                    },
                    {
                        "name": "two",
                        "agent": "codex",
                        "account": "cx",
                        "codex_home": "~/.codex",
                    },
                ],
            }), encoding="utf-8")
            with (
                patch.object(agent_runtime, "ROSTER_LOCAL_PATH", local),
                patch.object(agent_runtime, "ROSTER_COMMITTED_PATH", committed),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [
                    pool.submit(agent_runtime.persist_account, "one", "cck"),
                    pool.submit(agent_runtime.persist_account, "two", "cxp"),
                ]
                for future in futures:
                    future.result()
                data = json.loads(local.read_text(encoding="utf-8"))

            bots = {row["name"]: row for row in data["bots"]}
            self.assertEqual(bots["one"]["profile"], "cck")
            self.assertEqual(bots["two"]["profile"], "cxp")
            for row in bots.values():
                for legacy in (
                    "agent", "runtime", "account", "claude_config_dir", "codex_home"
                ):
                    self.assertNotIn(legacy, row)
            self.assertFalse(local.with_suffix(".json.lock").exists())
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_registration_upsert_uses_profile_as_only_runtime_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "bridge-bots.local.json"
            committed = Path(tmp) / "bridge-bots.json"
            committed.write_text(json.dumps({"bots": []}), encoding="utf-8")
            with (
                patch.object(agent_runtime, "ROSTER_LOCAL_PATH", local),
                patch.object(agent_runtime, "ROSTER_COMMITTED_PATH", committed),
            ):
                row = agent_runtime.upsert_runtime_bot(
                    "new-bot",
                    "FEISHU_BRIDGE_NEW_APP_ID",
                    "FEISHU_BRIDGE_NEW_APP_SECRET",
                    "@new-bot",
                    "cxp",
                )
            self.assertEqual(row["profile"], "cxp")
            self.assertNotIn("agent", row)
            self.assertNotIn("codex_home", row)
            persisted = json.loads(local.read_text(encoding="utf-8"))["bots"][0]
            self.assertEqual(persisted, row)

    def test_session_reuse_requires_exact_recorded_profile(self):
        bot = {"name": "codex-bot", "profile": "cxp"}
        base = {
            "workspace_id": "ws-1",
            "pty": "pty-1",
            "daemon_fp": "daemon-1",
        }
        with (
            patch.object(
                feishu_bridge.wmux_session,
                "pty_state",
                return_value=(True, "codex"),
            ),
            patch.object(
                feishu_bridge.wmux_session,
                "daemon_fingerprint",
                return_value="daemon-1",
            ),
        ):
            reusable, present, why = feishu_bridge._reuse_check(
                bot, {**base, "profile": "cxp"}
            )
            self.assertTrue(reusable)
            self.assertTrue(present)
            self.assertEqual(why, "")

            for recorded in (None, "cx"):
                rec = dict(base)
                if recorded:
                    rec["profile"] = recorded
                reusable, present, why = feishu_bridge._reuse_check(bot, rec)
                self.assertFalse(reusable)
                self.assertTrue(present)
                self.assertIn("profile 不一致", why)
                self.assertIn("cxp", why)


class CodexCanaryRuntimeTests(unittest.TestCase):
    def test_codex_defaults_to_app_server_worker(self):
        """主人 2026-07-23 拍板：codex bot 默认 typed-event，漏写字段也不掉回老路。"""
        project = ROOT
        state = ROOT / "feishu" / "_state"
        explicit = agent_runtime.worker_cmd(
            {
                "name": "tb25-link16-codex",
                "agent": "codex",
                "codex_home": "~/.codex-personal",
                "codex_transport": "app-server-canary",
            },
            project,
            state,
        )
        implicit = agent_runtime.worker_cmd(          # 名册没写 codex_transport = 也走 canary
            {"name": "another-codex", "agent": "codex", "codex_home": "~/.codex-personal"},
            project,
            state,
        )
        for cmd in (explicit, implicit):
            self.assertIn("codex_app_server_worker.py", cmd)
            self.assertIn("FEISHU_CODEX_EVENT_STREAM=1", cmd)

    def test_explicit_cli_legacy_still_falls_back_to_bare_codex(self):
        """应急回退口：只有显式写 cli-legacy 才回到已弃用的裸 CLI + hook 路。"""
        legacy = agent_runtime.worker_cmd(
            {
                "name": "old-codex",
                "agent": "codex",
                "codex_home": "~/.codex-personal",
                "codex_transport": "cli-legacy",
            },
            ROOT,
            ROOT / "feishu" / "_state",
        )
        self.assertNotIn("codex_app_server_worker.py", legacy)
        self.assertIn("codex --dangerously-bypass", legacy)
        self.assertFalse(agent_runtime.uses_app_server({"agent": "codex", "codex_transport": "bare-cli"}))
        self.assertTrue(agent_runtime.uses_app_server({"agent": "codex"}))
        self.assertFalse(agent_runtime.uses_app_server({"agent": "claude"}))

    def test_remote_tui_is_ready_without_normal_cli_banner(self):
        remote = {
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        screen = "status line\n› Use /skills to list available skills"
        self.assertTrue(agent_runtime.is_ready(remote, screen))
        self.assertTrue(agent_runtime.is_ready({"agent": "codex"}, screen))   # 默认即 canary
        # 只有显式回退老路的 bot 才仍要求普通 CLI 的 banner（remote TUI 没有它）
        self.assertFalse(
            agent_runtime.is_ready({"agent": "codex", "codex_transport": "cli-legacy"}, screen)
        )

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
