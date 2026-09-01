import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_runtime  # noqa: E402
import codex_app_server_worker  # noqa: E402
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

    def test_discovers_repo_skill_from_nested_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            repo = Path(tmp) / "repo"
            nested = repo / "src" / "feature"
            skill = repo / ".agents" / "skills" / "feishu"
            nested.mkdir(parents=True)
            (repo / ".git").mkdir()
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: feishu\ndescription: test\n---\n", encoding="utf-8"
            )
            with patch.object(Path, "home", return_value=home):
                actual = agent_runtime.codex_skill_invocation(
                    {"agent": "codex", "codex_home": str(home / ".codex-work")},
                    "/feishu status", cwd=nested,
                )
        self.assertEqual(actual, "$feishu status")

    def test_duplicate_skill_names_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for folder in ("feishu", "claude-compat-feishu"):
                skill = home / ".agents" / "skills" / folder
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: feishu\ndescription: test\n---\n", encoding="utf-8"
                )
            with patch.object(Path, "home", return_value=home):
                self.assertIsNone(agent_runtime.codex_skill_invocation(
                    {"agent": "codex", "codex_home": str(home / ".codex-work")},
                    "/feishu status",
                ))


class AgentProfileTests(unittest.TestCase):
    def test_registry_has_nine_profiles_and_cxp_is_codex_default(self):
        profiles = agent_runtime.profile_specs()
        self.assertEqual(len(profiles), 9)
        self.assertEqual(agent_runtime.default_profile("codex"), "cxp")
        self.assertEqual(agent_runtime.profile_spec("cxp").home, "~/.codex-personal")
        self.assertEqual(agent_runtime.profile_spec("cck").launcher, "launch-sh")
        preferred = {profile.name for profile in profiles if profile.session_search_preferred}
        self.assertEqual(preferred, {"cc", "ccp", "ccp2", "cx", "cxp"})
        self.assertTrue(agent_runtime.profile_public_dict(agent_runtime.profile_spec("cxp"))["session_search_preferred"])
        self.assertFalse(agent_runtime.profile_public_dict(agent_runtime.profile_spec("ccw"))["session_search_preferred"])

    def test_session_search_policy_rejects_unknown_or_duplicate_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "agent-profiles.local.json"
            base = {
                "version": 1,
                "default_profiles": {"claude": "cc", "codex": "cx"},
                "profiles": {
                    "cc": {"runtime": "claude", "home": "~/.claude", "launcher": "direct"},
                    "cx": {"runtime": "codex", "home": "~/.codex", "launcher": "direct"},
                },
            }
            for preferred, error in ((["cc", "missing"], "未知 profile"), (["cc", "cc"], "不得重复")):
                document = dict(base)
                document["session_search"] = {"preferred_profiles": preferred}
                registry.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, error):
                    agent_runtime.profile_specs(registry)

    def test_local_registry_wins_and_invalid_local_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            committed = root / "agent-profiles.json"
            local = root / "agent-profiles.local.json"
            committed.write_text((ROOT / "feishu" / "agent-profiles.json").read_text(encoding="utf-8"),
                                 encoding="utf-8")
            local.write_text(json.dumps({
                "version": 1,
                "default_profiles": {"claude": "claude-work", "codex": "codex-work"},
                "profiles": {
                    "claude-work": {"runtime": "claude", "home": "~/.claude-work", "launcher": "direct"},
                    "codex-work": {"runtime": "codex", "home": "~/.codex-work", "launcher": "direct"},
                },
            }), encoding="utf-8")
            with patch.object(agent_runtime, "PROFILE_REGISTRY_PATH", committed), \
                 patch.object(agent_runtime, "PROFILE_REGISTRY_LOCAL_PATH", local):
                self.assertEqual(agent_runtime.profile_registry_path(), local)
                self.assertEqual([p.name for p in agent_runtime.profile_specs()],
                                 ["claude-work", "codex-work"])
                local.write_text("not-json", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "读失败"):
                    agent_runtime.profile_specs()

    def test_missing_local_uses_explicit_legacy_transition_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            committed = root / "agent-profiles.json"
            local = root / "agent-profiles.local.json"
            committed.write_text((ROOT / "feishu" / "agent-profiles.json").read_text(encoding="utf-8"),
                                 encoding="utf-8")
            with patch.object(agent_runtime, "PROFILE_REGISTRY_PATH", committed), \
                 patch.object(agent_runtime, "PROFILE_REGISTRY_LOCAL_PATH", local):
                self.assertEqual(agent_runtime.profile_registry_path(), committed)
                self.assertEqual(agent_runtime.default_profile("codex"), "cxp")

    def test_single_runtime_registry_is_valid_and_other_default_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "agent-profiles.local.json"
            registry.write_text(json.dumps({
                "version": 1,
                "default_profiles": {"claude": "claude-work"},
                "profiles": {
                    "claude-work": {
                        "runtime": "claude", "home": "~/.claude-work", "launcher": "direct",
                    },
                },
            }), encoding="utf-8")
            self.assertEqual([p.name for p in agent_runtime.profile_specs(registry)], ["claude-work"])
            self.assertEqual(agent_runtime.default_profile("claude", registry), "claude-work")
            with self.assertRaises(KeyError):
                agent_runtime.default_profile("codex", registry)

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
        self.assertIn("unset CLAUDE_CODE_CHILD_SESSION;", kimi)
        self.assertNotIn("API_KEY", kimi)
        self.assertIn('LINK16_AGENT_PROFILE="cxp"', codex)
        self.assertIn(".codex-personal", codex)
        self.assertIn("CODEX_HOME=", codex)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex)
        self.assertIn("--dangerously-bypass-hook-trust", codex)
        self.assertIn('"resume" "abc 123"', codex)
        self.assertNotIn("CLAUDE_CONFIG_DIR", codex)
        self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", codex)

    def test_bridge_claude_worker_clears_parent_harness_marker(self):
        bot = {"name": "claude-bot", "profile": "ccp"}
        with patch.object(agent_runtime, "_require_profile_available"):
            command = agent_runtime.worker_cmd(bot, ROOT, ROOT / "feishu" / "_state")
        self.assertTrue(command.startswith("unset CLAUDE_CODE_CHILD_SESSION;"))

    def test_standalone_profile_is_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "LINK16_AGENT_PROFILE 未设置"):
            agent_runtime.profile_from_env({})
        with self.assertRaises(KeyError):
            agent_runtime.profile_spec("does-not-exist")

    def test_public_ready_probe_supports_claude_and_codex(self):
        cli = ROOT / "feishu" / "agent_profile_cli.py"
        # 屏幕样本带 `❯` / `›`：子进程按本机 locale 解 stdin（tuf19 是 GBK）会直接崩，
        # 探针看起来「判未就绪」其实是根本没读到屏。钉死编码，这条测试才跨机器成立。
        utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
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
                env=utf8_env,
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
                    cwd="C:\\work\\new-bot",
                )
            self.assertEqual(row["profile"], "cxp")
            self.assertEqual(row["cwd"], "C:/work/new-bot")
            self.assertNotIn("agent", row)
            self.assertNotIn("codex_home", row)
            persisted = json.loads(local.read_text(encoding="utf-8"))["bots"][0]
            self.assertEqual(persisted, row)

    def test_switching_to_claude_removes_stale_codex_transport_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "bridge-bots.local.json"
            committed = Path(tmp) / "bridge-bots.json"
            local.write_text(json.dumps({"bots": [{
                "name": "baseball",
                "profile": "cxp",
                "codex_transport": "app-server-canary",
                "delivery_contract": "milestone-v1",
            }]}), encoding="utf-8")
            committed.write_text('{"bots":[]}', encoding="utf-8")
            with (
                patch.object(agent_runtime, "ROSTER_LOCAL_PATH", local),
                patch.object(agent_runtime, "ROSTER_COMMITTED_PATH", committed),
            ):
                agent_runtime.persist_account("baseball", "ccp2")
            row = json.loads(local.read_text(encoding="utf-8"))["bots"][0]
            self.assertEqual(row["profile"], "ccp2")
            self.assertNotIn("codex_transport", row)
            self.assertNotIn("delivery_contract", row)

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


class ProfileLaunchIntegrityTests(unittest.TestCase):
    """PLAN-923 回归闸：账号解析不许猜，命令不许交给 WSL 的 bash。

    BUG-1/BUG-2 本身是 Windows 平台行为（CreateProcess 的 System32 优先、
    text-mode 的 \\r\\n 翻译），别的机器复现不了 —— 所以这里测的是**可移植的
    那一层逻辑**：解析顺序、fail closed、输出契约。
    """

    def test_shell_prefers_env_then_path_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "my-bash.exe"
            fake.write_text("", encoding="utf-8")

            # $SHELL 指向真实文件时优先它，不去碰 PATH（PATH 上第一个 bash 在
            # Windows 可能是 System32 的 WSL 存根）。
            with patch.dict("os.environ", {"SHELL": str(fake)}, clear=False):
                self.assertEqual(agent_runtime.resolve_shell(), str(fake))

            # $SHELL 缺失或指向不存在的文件 → 退回 PATH 查找（按 PATH 顺序，
            # 不是 CreateProcess 的 System32 优先）。
            env_without_shell = {k: v for k, v in os.environ.items() if k != "SHELL"}
            with patch.dict("os.environ", env_without_shell, clear=True):
                with patch.object(agent_runtime.shutil, "which",
                                  side_effect=lambda n: str(fake) if n == "bash" else None):
                    self.assertEqual(agent_runtime.resolve_shell(), str(fake))

                # 两条来源都没有 → 拒绝猜解释器，而不是退回裸名 "bash"。
                with patch.object(agent_runtime.shutil, "which", return_value=None):
                    with self.assertRaisesRegex(ValueError, "拒绝猜解释器"):
                        agent_runtime.resolve_shell()

    def test_doctor_flags_missing_shell(self):
        with patch.object(agent_runtime, "resolve_shell",
                          side_effect=ValueError("找不到可用 shell：测试")):
            result = agent_runtime.profile_doctor("cxp")
        self.assertFalse(result["ok"])
        self.assertTrue(any("找不到可用 shell" in e for e in result["errors"]))

    def test_bridge_worker_command_ignores_bridge_process_execution_env(self):
        """Scheduled Task env must not veto a command executed inside wmux."""
        bot = {
            "name": "test-codex-bot",
            "agent": "codex",
            "profile": "cxp",
        }
        with patch.object(agent_runtime.shutil, "which", return_value=None), \
             patch.object(agent_runtime, "resolve_shell",
                          side_effect=AssertionError("bridge must not resolve its own shell")):
            command = agent_runtime.worker_cmd(bot, ROOT, ROOT / "feishu" / "_state")

        self.assertIn('LINK16_AGENT_PROFILE="cxp"', command)
        self.assertIn("CODEX_HOME=", command)
        self.assertIn("codex_app_server_worker.py", command)

    def test_bridge_worker_command_still_rejects_missing_profile_assets(self):
        """Skipping bridge env checks must not skip registry/home validation."""
        bot = {"name": "test-codex-bot", "agent": "codex", "profile": "cxp"}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(Path, "home", return_value=Path(tmp)):
            with self.assertRaisesRegex(ValueError, "home 不存在"):
                agent_runtime.worker_cmd(bot, ROOT, ROOT / "feishu" / "_state")

    def test_doctor_static_only_survives_scheduled_task_env(self):
        """开机计划任务那份精简环境里，只查静态资产的体检必须仍然放行。

        这是 `/account` 闸和 `worker_cmd()` 共用的判据（PLAN-924）：桥不执行命令，
        所以「桥自己找不到 bash / CLI」不该判一个资产完好的 profile 死刑；
        而 direct-run 那条路（自己 exec）必须继续 fail closed。
        """
        with patch.object(agent_runtime.shutil, "which", return_value=None), \
             patch.object(agent_runtime, "resolve_shell",
                          side_effect=ValueError("找不到可用 shell：测试")):
            static_only = agent_runtime.profile_doctor("cxp", check_execution_env=False)
            direct_run = agent_runtime.profile_doctor("cxp")
        self.assertEqual(static_only["errors"], [])
        self.assertTrue(static_only["ok"])
        self.assertFalse(direct_run["ok"], "direct-run 那条路必须仍然 fail closed")

    def test_bridge_never_gates_on_its_own_execution_env(self):
        """桥里每一处 profile_doctor 都必须显式 check_execution_env=False。

        2026-08-06 tb24 实证：PLAN-924 修好了 `worker_cmd()`，却漏了 `/account` 的闸
        —— 同一个桥进程两条路一个放行一个卡死，主人 `/account ccp` 报「找不到可用
        shell」切不了账号。tb25 的桥碰巧起在有 bash 的环境里，所以一直看不出来。

        用 AST 扫调用点而不是驱动 `handle_slash`：它是嵌套闭包、无法单独导入。
        `asyncio.to_thread(profile_doctor, ...)` 这种转手形态也要认，否则等于没测。
        """
        import ast

        def _name(node):
            return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")

        source = (ROOT / "feishu" / "feishu_bridge.py").read_text(encoding="utf-8")
        seen, offenders = 0, []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            # 直接调 profile_doctor(...)，或把它当参数转手给 to_thread / partial。
            if not any(_name(t) == "profile_doctor" for t in [node.func, *node.args]):
                continue
            seen += 1
            flag = next((k for k in node.keywords if k.arg == "check_execution_env"), None)
            if flag is None or not (
                isinstance(flag.value, ast.Constant) and flag.value.value is False
            ):
                offenders.append(node.lineno)
        self.assertTrue(seen, "扫不到任何 profile_doctor 调用 —— 这条闸失效了，先修测试")
        self.assertEqual(
            offenders, [],
            f"feishu_bridge.py 第 {offenders} 行拿桥自己的 SHELL/PATH 当判据；"
            "桥只把命令写进 wmux 终端，不该用自己的执行环境否决 profile")

    def test_standalone_worker_still_requires_current_process_shell(self):
        """Direct launch keeps PLAN-923's fail-closed shell guarantee."""
        with patch.object(agent_runtime, "resolve_shell",
                          side_effect=ValueError("找不到可用 shell：测试")):
            with self.assertRaisesRegex(ValueError, "找不到可用 shell"):
                agent_runtime.standalone_worker_cmd("cxp", cwd=str(ROOT))

    def test_profile_name_has_no_runtime_default_tier(self):
        # 显式 profile / provider-home 反推 —— 两条有据可依的路仍然通。
        self.assertEqual(agent_runtime.profile_name({"profile": "cxp"}), "cxp")
        self.assertEqual(
            agent_runtime.profile_name({"agent": "claude", "claude_config_dir": "~/.claude-kimi"}),
            "cck",
        )
        # 只有 runtime、无任何账号证据 —— 以前会猜成 registry 的运行时默认号，
        # 现在必须交白卷（required 时报错并指名要补哪个字段）。
        bare = {"name": "tb25-bare", "agent": "claude"}
        self.assertIsNone(agent_runtime.profile_name(bare))
        with self.assertRaises(ValueError) as caught:
            agent_runtime.profile_name(bare, required=True)
        self.assertIn("tb25-bare", str(caught.exception))
        self.assertIn("profile", str(caught.exception))

    def test_local_roster_records_every_profile_explicitly(self):
        """本机名册不许再有『靠猜』的 bot（PLAN-923 · S2.1 的持续闸）。"""
        roster = ROOT / "feishu" / "bridge-bots.local.json"
        if not roster.is_file():
            self.skipTest("本机没有 bridge-bots.local.json")
        bots = json.loads(roster.read_text(encoding="utf-8"))["bots"]
        unresolvable = [b.get("name") for b in bots
                        if not agent_runtime.profile_name(b)]
        self.assertEqual(unresolvable, [], f"这些 bot 解析不出账号：{unresolvable}")

    def test_cli_stdout_is_lf_only(self):
        """CLI 输出必须是 LF：残留的 \\r 会打穿 shell wrapper 的 unalias。"""
        done = subprocess.run(
            [sys.executable, str(ROOT / "feishu" / "agent_profile_cli.py"), "list", "--names"],
            capture_output=True, check=True,
        )
        self.assertNotIn(b"\r", done.stdout)
        self.assertIn(b"cxp\n", done.stdout)


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


class ClaudeStartupPromptTests(unittest.TestCase):
    """PLAN-927 · Claude 首启目录信任弹窗把 ready 判据骗过去 → 第一条消息被吞。

    两段 screen 都是 2026-08-17 throwaway workspace 真机抓的原文（Claude Code v2.1.233）。
    """

    CLAUDE = {"name": "trustlab", "agent": "claude"}

    TRUST_SCREEN = (
        "───────────────────────────────────────────────────────────────\n"
        " Accessing workspace:\n\n"
        " C:\\Users\\ZhuZhen\\AppData\\Local\\Temp\\claude\\scratchpad\\trustlab-a\n\n"
        " Quick safety check: Is this a project you created or one you trust?\n\n"
        " Claude Code'll be able to read, edit, and execute files here.\n\n"
        " ❯ 1. Yes, I trust this folder\n"
        "   2. No, exit\n\n"
        " Enter to confirm · Esc to cancel"
    )

    COMPOSER_SCREEN = (
        "╭─── Claude Code v2.1.233 ──────────────────────────────╮\n"
        "│                Welcome back Zyu Zan!                  │\n"
        "╰───────────────────────────────────────────────────────╯\n"
        "                                     ◉ xhigh · /effort\n"
        "───────────────────────────────────────────────────────\n"
        "❯ "
    )

    def test_trust_prompt_is_not_ready_even_though_it_paints_the_ready_mark(self):
        # 这就是 bug 本体：弹窗的选择光标和空 composer 用的是同一个 `❯`。
        self.assertIn(agent_runtime.CLAUDE_READY_MARK, self.TRUST_SCREEN)
        self.assertFalse(agent_runtime.is_ready(self.CLAUDE, self.TRUST_SCREEN))
        self.assertTrue(agent_runtime.needs_trust_confirmation(self.CLAUDE, self.TRUST_SCREEN))

    def test_real_composer_after_trust_is_ready(self):
        # 按完回车之后的真屏：判就绪，且不再要求按回车（否则会往活会话里空按）。
        self.assertTrue(agent_runtime.is_ready(self.CLAUDE, self.COMPOSER_SCREEN))
        self.assertFalse(agent_runtime.needs_trust_confirmation(self.CLAUDE, self.COMPOSER_SCREEN))

    def test_older_trust_wording_is_also_caught(self):
        # 老版本文案换过一次；ready 判据不该跟着 Claude Code 的措辞漂。
        legacy = "Do you trust the files in this folder?\n ❯ 1. Yes, proceed\n   2. No, exit"
        self.assertFalse(agent_runtime.is_ready(self.CLAUDE, legacy))
        self.assertTrue(agent_runtime.needs_trust_confirmation(self.CLAUDE, legacy))

    def test_unknown_startup_modal_blocks_ready_but_never_auto_answers(self):
        # 未知弹窗（如 CLAUDE.md external includes）：不知道哪个选项安全 →
        # 判未就绪让它超时喊主人，绝不盲按回车、更不能当就绪把消息喂进去。
        unknown = (
            "Found external includes in CLAUDE.md\n"
            " ❯ 1. Approve\n   2. Reject\n\n Enter to confirm · Esc to cancel"
        )
        self.assertFalse(agent_runtime.is_ready(self.CLAUDE, unknown))
        self.assertFalse(agent_runtime.needs_trust_confirmation(self.CLAUDE, unknown))

    def test_trust_wording_in_scrollback_is_not_a_live_modal(self):
        # 跨机隐患：正在讨论这个 bug 的 bot，滚屏里就有这句话。只有文案、没有菜单结构
        # → 不是弹窗：既不能判未就绪，更不能往活会话里按回车。
        chatter = (
            "我刚查清楚了：弹窗那行写的是 Yes, I trust this folder，\n"
            "和空输入框共用同一个箭头，所以就绪判据被骗了。\n"
            "❯ "
        )
        self.assertFalse(agent_runtime.needs_trust_confirmation(self.CLAUDE, chatter))
        self.assertTrue(agent_runtime.is_ready(self.CLAUDE, chatter))

    def test_codex_and_plain_claude_composer_unchanged(self):
        # 回归：Codex 两条路和最朴素的 claude 就绪屏都不受影响。
        self.assertTrue(agent_runtime.is_ready(self.CLAUDE, "some output\n❯ "))
        self.assertFalse(agent_runtime.is_ready(self.CLAUDE, "$ bash\n"))
        self.assertFalse(agent_runtime.needs_trust_confirmation({"agent": "codex"}, self.TRUST_SCREEN))
        self.assertTrue(agent_runtime.is_ready({"agent": "codex"}, "status\n› Use /skills"))


class CodexTrustPreseedTests(unittest.TestCase):
    """2026-08-25 · Codex 首启 trust 弹窗会在 app-server warmup 上游挡死会话
    （刷屏自动回车来不及）→ spawn 前把目录信任预写进 profile 的 config.toml。
    Codex 自己持久化的就是小写 key，且查找大小写不敏感（当天 throwaway 目录实测）。"""

    CODEX = {"name": "trustlab-codex", "agent": "codex"}
    CLAUDE = {"name": "trustlab-claude", "agent": "claude"}

    def _profile(self, home: Path, runtime: str):
        from types import SimpleNamespace
        return SimpleNamespace(home_path=home, runtime=runtime, name="test")

    def test_seeds_lowercase_key_into_missing_config(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(agent_runtime, "resolve_profile", return_value=self._profile(home, "codex")):
                agent_runtime.ensure_codex_trust(self.CODEX, "C:/410_VibeCoding/Post/Some-Repo")
            text = (home / "config.toml").read_text(encoding="utf-8")
            self.assertIn("[projects.'c:\\410_vibecoding\\post\\some-repo']", text)
            self.assertIn('trust_level = "trusted"', text)

    def test_existing_key_is_not_duplicated_case_insensitively(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config = home / "config.toml"
            config.write_text("[projects.'C:\\410_VibeCoding\\Post\\Some-Repo']\ntrust_level = \"trusted\"\n", encoding="utf-8")
            with patch.object(agent_runtime, "resolve_profile", return_value=self._profile(home, "codex")):
                agent_runtime.ensure_codex_trust(self.CODEX, "c:/410_vibecoding/post/some-repo")
            self.assertEqual(config.read_text(encoding="utf-8").count("projects."), 1)

    def test_claude_runtime_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(agent_runtime, "resolve_profile", return_value=self._profile(home, "claude")):
                agent_runtime.ensure_codex_trust(self.CLAUDE, "C:/whatever")
            self.assertFalse((home / "config.toml").exists())


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
    def test_runtime_specific_ready_timeout_contract_and_overrides(self):
        default_codex = {"name": "codex", "agent": "codex"}
        self.assertGreater(
            feishu_bridge._ready_timeout(default_codex),
            codex_app_server_worker.WARMUP_TIMEOUT_SEC,
        )
        self.assertEqual(feishu_bridge._ready_timeout({"agent": "claude"}), 90)
        self.assertEqual(feishu_bridge._ready_timeout({
            "agent": "codex", "codex_transport": "cli-legacy",
        }), 30)
        self.assertEqual(feishu_bridge._ready_timeout({
            **default_codex, "ready_timeout_sec": 77,
        }), 77)
        self.assertEqual(feishu_bridge._ready_timeout({
            **default_codex, "ready_timeout_sec": 77,
        }, 0.25), 0.25)

    def _with_ready_file(self, bot, callback):
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                path = Path(tmp) / f"bridge-codex-app-ready-{bot['name']}.json"
                path.write_text(json.dumps({"worker_pid": 123, "ts": 20}), encoding="utf-8")
                callback()
            finally:
                feishu_bridge.STATE_DIR = previous

    def test_default_app_server_accepts_fresh_but_rejects_stale_signal(self):
        bot = {"name": "test-default-codex", "agent": "codex"}
        self._with_ready_file(bot, lambda: (
            self.assertTrue(feishu_bridge._app_server_ready_signal(bot, 10)),
            self.assertFalse(feishu_bridge._app_server_ready_signal(bot, 30)),
        ))

    def test_explicit_app_server_accepts_fresh_signal(self):
        bot = {
            "name": "test-explicit-codex",
            "agent": "codex",
            "codex_transport": "app-server-canary",
        }
        self._with_ready_file(
            bot,
            lambda: self.assertTrue(feishu_bridge._app_server_ready_signal(bot, 10)),
        )

    def test_legacy_and_non_codex_runtimes_reject_ready_signal(self):
        legacy = {
            "name": "test-legacy-codex",
            "agent": "codex",
            "codex_transport": "cli-legacy",
        }
        claude = {
            "name": "test-claude",
            "agent": "claude",
            "codex_transport": "app-server-canary",
        }
        for bot in (legacy, claude):
            self._with_ready_file(
                bot,
                lambda bot=bot: self.assertFalse(
                    feishu_bridge._app_server_ready_signal(bot, 10)
                ),
            )

    def test_wait_ready_accepts_default_app_server_resume_handshake(self):
        bot = {"name": "test-resumed-codex", "agent": "codex"}
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                path = Path(tmp) / "bridge-codex-app-ready-test-resumed-codex.json"
                path.write_text(
                    json.dumps({"worker_pid": 123, "ts": time.time() + 1}),
                    encoding="utf-8",
                )
                with patch.object(
                    feishu_bridge,
                    "read_screen",
                    return_value="OpenAI Codex\n› Summarize recent commits\n",
                ):
                    self.assertTrue(
                        feishu_bridge._wait_agent_ready(bot, "test-pty", timeout=0.1)
                    )
            finally:
                feishu_bridge.STATE_DIR = previous


class BridgeStartupRecoveryTests(unittest.TestCase):
    BOT = {"name": "test-claude-startup", "agent": "claude"}

    def _with_state_dir(self, callback):
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                callback(Path(tmp))
            finally:
                feishu_bridge.STATE_DIR = previous

    def test_slow_live_agent_is_never_given_duplicate_launcher(self):
        def check(_tmp):
            with (
                patch.object(feishu_bridge, "_wait_agent_ready", return_value=False),
                patch.object(feishu_bridge, "read_screen", return_value="Claude Code is starting…"),
                patch.object(feishu_bridge.wmux_session, "pty_state", return_value=(True, "Claude Code")),
                patch.object(feishu_bridge, "wmux") as send,
            ):
                self.assertFalse(feishu_bridge._finish_worker_startup(
                    self.BOT, "ws-test", "pty-test", "C:/repo",
                ))
            send.assert_not_called()
            failure = feishu_bridge._load_startup_failure(self.BOT["name"])
            self.assertEqual(failure["stage"], "agent-started-not-ready")
            self.assertIn("未补发启动命令", failure["reason"])
            self.assertIn("Claude Code is starting", failure["screen_tail"])
            self.assertIn("最近一次启动失败现场", feishu_bridge._startup_failure_markdown(
                self.BOT["name"]
            ))

        self._with_state_dir(check)

    def test_proven_bare_shell_gets_one_retry_and_clears_failure(self):
        def check(tmp):
            feishu_bridge._record_startup_failure(
                self.BOT,
                workspace_id="old-ws",
                pty="old-pty",
                cwd="C:/repo",
                stage="old",
                reason="old",
                screen="old",
            )
            with (
                patch.object(feishu_bridge, "_wait_agent_ready", side_effect=(False, True)),
                patch.object(
                    feishu_bridge,
                    "read_screen",
                    return_value="remo@host MINGW64 /c/repo\n$ ",
                ),
                patch.object(feishu_bridge.wmux_session, "pty_state", return_value=(True, "")),
                patch.object(feishu_bridge, "_worker_cmd", return_value="launch-claude"),
                patch.object(feishu_bridge, "blog"),
                patch.object(feishu_bridge.time, "sleep"),
                patch.object(feishu_bridge, "wmux") as send,
            ):
                self.assertTrue(feishu_bridge._finish_worker_startup(
                    self.BOT, "ws-test", "pty-test", "C:/repo",
                ))
            self.assertEqual(send.call_count, 2)
            send.assert_any_call(
                "send", "pty-test", "launch-claude", "--allow-ws", "ws-test"
            )
            send.assert_any_call("enter", "pty-test", "--allow-ws", "ws-test")
            self.assertFalse((tmp / "bridge-startup-failure-test-claude-startup.json").exists())

        self._with_state_dir(check)

    def test_unknown_or_unreadable_screen_fails_closed(self):
        with patch.object(
            feishu_bridge.wmux_session, "pty_state", return_value=(True, "")
        ):
            self.assertFalse(feishu_bridge._startup_retryable_shell(
                self.BOT, "pty-test", ""
            ))

    def test_recent_unfinished_manual_handoff_is_retryable_exactly_until_complete(self):
        def check(tmp):
            pack = {
                "bot": self.BOT["name"],
                "reason": "主人手动 /handoff",
                "session_id": "old-session",
                "cwd": "C:/repo",
            }
            path = tmp / f"watchdog-handoff-{self.BOT['name']}.json"
            path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")

            # Backward compatibility: the production failure predates the
            # attempt sidecar, so an absent sidecar is explicitly retryable.
            self.assertEqual(
                feishu_bridge._load_retryable_handoff(self.BOT["name"]), pack
            )
            pending = feishu_bridge._record_handoff_attempt(
                self.BOT["name"], pack, "pending"
            )
            self.assertEqual(pending["attempts"], 1)
            feishu_bridge._record_handoff_attempt(
                self.BOT["name"], pack, "failed", "startup timed out"
            )
            self.assertEqual(
                feishu_bridge._load_retryable_handoff(self.BOT["name"]), pack
            )
            second = feishu_bridge._record_handoff_attempt(
                self.BOT["name"], pack, "pending"
            )
            self.assertEqual(second["attempts"], 2)
            feishu_bridge._record_handoff_attempt(
                self.BOT["name"], pack, "complete"
            )
            self.assertIsNone(
                feishu_bridge._load_retryable_handoff(self.BOT["name"])
            )

        self._with_state_dir(check)


if __name__ == "__main__":
    unittest.main()
