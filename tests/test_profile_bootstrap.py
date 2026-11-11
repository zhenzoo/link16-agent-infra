#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import shutil
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import profile_bootstrap as pb  # noqa: E402


class ProfileBootstrapTests(unittest.TestCase):
    def test_selected_account_uses_native_login_then_launch_probe_for_all_runtimes(self):
        for runtime, arguments in (("claude", ["auth", "login"]), ("codex", ["login"]), ("kimi", ["login"])):
            with self.subTest(runtime=runtime), \
                 mock.patch.object(pb.agent_runtime, "profile_spec"), \
                 mock.patch.object(pb.agent_runtime, "profile_login_state", return_value={"status": "missing"}), \
                 mock.patch.object(pb.subprocess, "call", return_value=0) as call:
                selected = {"name": runtime + "-work", "runtime": runtime, "registry": "chosen-local.json"}
                self.assertEqual(pb.login_account(selected), 0)
                self.assertEqual(call.call_args_list[0].args[0][-len(arguments):], arguments)
                self.assertEqual(call.call_args_list[1].args[0][-3:], ["selftest", "--profile", selected["name"]])
                self.assertEqual(call.call_args_list[0].kwargs["env"][pb.agent_runtime.PROFILE_REGISTRY_ENV], "chosen-local.json")
                self.assertNotIn("timeout", call.call_args_list[0].kwargs)

    def test_failed_login_stops_before_launch_probe(self):
        with mock.patch.object(pb.agent_runtime, "profile_spec"), \
             mock.patch.object(pb.agent_runtime, "profile_login_state", return_value={"status": "missing"}), \
             mock.patch.object(pb.subprocess, "call", return_value=1) as call:
            self.assertEqual(pb.login_account({"name": "chosen", "runtime": "codex", "registry": "local.json"}), 1)
            call.assert_called_once()

    def test_blank_user_choices_have_three_runtimes_without_private_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = pb.account_choices(root / "profiles.json", root)
            self.assertEqual({r["runtime"] for r in rows}, {"claude", "codex", "kimi"})
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(not r["existing"] for r in rows))
            self.assertEqual(list(root.iterdir()), [])

    def test_choices_keep_all_accounts_and_offer_next_unused_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "profiles.json"
            pb.initialize_registry(registry, [
                {"name": "a", "runtime": "codex", "home": "~/.codex-personal"},
                {"name": "b", "runtime": "codex", "home": "~/.codex-personal2"},
                {"name": "c", "runtime": "codex", "home": "~/.codex-personal3"},
            ], apply=True)
            rows = pb.account_choices(registry, root)
            self.assertEqual([r["name"] for r in rows if r["existing"]], ["a", "b", "c"])
            self.assertEqual(rows[1]["label"], "Codex-personal 2")
            self.assertIn("codex-personal4", [r["name"] for r in rows])

    def test_selection_creates_only_chosen_account_and_reuses_it(self):
        for runtime in ("claude", "codex", "kimi"):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                registry = root / "profiles.json"
                name = runtime + "-personal"
                selected, rows = pb.select_account(name, home=root, registry_path=registry, apply=False)
                self.assertFalse(registry.exists())
                first, _ = pb.select_account(name, home=root, registry_path=registry, apply=True)
                before = registry.read_bytes()
                second, _ = pb.select_account(name, home=root, registry_path=registry, apply=True)
                self.assertTrue(second["existing"])
                self.assertEqual(before, registry.read_bytes())
                self.assertEqual(list(json.loads(before)["profiles"]), [name])
                self.assertFalse(list(root.rglob("auth.json")))
                self.assertFalse(list(root.rglob("CLAUDE.md")))

    def test_occupied_unregistered_home_is_never_adopted_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codex-personal").mkdir()
            registry = root / "profiles.json"
            with self.assertRaisesRegex(ValueError, "选项已变化"):
                pb.select_account("codex-personal", home=root, registry_path=registry, apply=True)
            self.assertFalse(registry.exists())

    def test_apply_creates_three_homes_launcher_and_shell_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pb.bootstrap(home, apply=True, registry_path=ROOT / "tests" / "fixtures" / "agent-profiles.legacy.json")
            self.assertTrue((home / ".claude-personal").is_dir())
            self.assertTrue((home / ".claude-personal2" / "launch.sh").is_file())
            self.assertTrue((home / ".codex-personal").is_dir())
            self.assertTrue((home / ".claude-personal" / "skills" / "feishu" / "SKILL.md").is_file())
            self.assertTrue((home / ".agents" / "skills" / "feishu" / "SKILL.md").is_file())
            self.assertTrue((home / ".claude-personal" / "skills" / "feishu-workline" / "SKILL.md").is_file())
            self.assertTrue((home / ".agents" / "skills" / "feishu-workline" / "SKILL.md").is_file())
            hooks = json.loads((home / ".codex-personal" / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(set(hooks["hooks"]), {"Stop", "PostToolUse", "UserPromptSubmit"})
            bashrc = (home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn("agent_profile_cli.py", bashrc)
            self.assertIn("list --names", bashrc)
            self.assertNotIn("cc ccp ccp2", bashrc)
            self.assertIn("__link16_run_profile", bashrc)
            self.assertNotIn("alias ccp", bashrc)
            ps5 = home / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
            self.assertIn("Function:global:$profileName", ps5.read_text(encoding="utf-8"))

    def test_never_creates_or_copies_auth_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pb.bootstrap(home, apply=True)
            self.assertEqual(list(home.rglob("auth.json")), [])
            self.assertEqual(list(home.rglob("credentials.json")), [])

    def test_custom_codex_hooks_preserve_user_hook_and_conflicts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "user"
            registry = root / "profiles.json"
            pb.initialize_registry(registry, [
                {"name": "codex-work", "runtime": "codex", "home": "~/.codex-work"},
                {"name": "claude-work", "runtime": "claude", "home": "~/.claude-work"},
            ], apply=True)
            codex_home = home / ".codex-work"
            codex_home.mkdir(parents=True)
            hooks_path = codex_home / "hooks.json"
            hooks_path.write_text(json.dumps({
                "hooks": {"Stop": [{"hooks": [
                    {"type": "command", "command": "play-sound"},
                ]}]},
            }), encoding="utf-8")
            rows = pb.bootstrap(home, apply=True, profiles=("codex-work", "claude-work"),
                                registry_path=registry)
            hook_row = next(row for row in rows if row["kind"] == "codex-bridge-hooks")
            self.assertEqual(hook_row["status"], "ok")
            installed = json.loads(hooks_path.read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for entries in installed["hooks"].values()
                for entry in entries
                for hook in entry.get("hooks") or []
            ]
            self.assertIn("play-sound", commands)
            before = hooks_path.read_bytes()
            second = pb.bootstrap(home, apply=True, profiles=("codex-work", "claude-work"),
                                  registry_path=registry)
            self.assertEqual(next(row for row in second if row["kind"] == "codex-bridge-hooks")["status"], "ok")
            self.assertEqual(hooks_path.read_bytes(), before)
            self.assertFalse((home / ".claude-work" / "hooks.json").exists())

            hooks_path.write_bytes(b"{broken")
            broken = hooks_path.read_bytes()
            conflict = pb.bootstrap(home, apply=True, profiles=("codex-work",),
                                    registry_path=registry)
            self.assertEqual(next(row for row in conflict if row["kind"] == "codex-bridge-hooks")["status"], "conflict")
            self.assertEqual(hooks_path.read_bytes(), broken)

    def test_unselected_codex_profile_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "user"
            registry = root / "profiles.json"
            pb.initialize_registry(registry, [
                {"name": "codex-one", "runtime": "codex", "home": "~/.codex-one"},
                {"name": "codex-two", "runtime": "codex", "home": "~/.codex-two"},
            ], apply=True)
            pb.bootstrap(home, apply=True, profiles=("codex-one",), registry_path=registry)
            self.assertTrue((home / ".codex-one" / "hooks.json").is_file())
            self.assertFalse((home / ".codex-two" / "hooks.json").exists())

    def test_nested_profile_home_keeps_the_full_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "user"
            registry = root / "profiles.json"
            pb.initialize_registry(registry, [
                {"name": "claude-nested", "runtime": "claude",
                 "home": "~/.profiles/claude-work", "launcher": "launch-sh"},
                {"name": "codex-nested", "runtime": "codex",
                 "home": "~/.profiles/codex-work"},
            ], apply=True)
            pb.bootstrap(home, apply=True, profiles=("claude-nested", "codex-nested"),
                         registry_path=registry)
            claude_home = home / ".profiles" / "claude-work"
            codex_home = home / ".profiles" / "codex-work"
            self.assertTrue((claude_home / "skills" / "feishu" / "SKILL.md").is_file())
            self.assertTrue((codex_home / "hooks.json").is_file())
            self.assertFalse((home / "claude-work").exists())
            self.assertFalse((home / "codex-work").exists())
            self.assertIn(
                "$HOME/.profiles/claude-work",
                (claude_home / "launch.sh").read_text(encoding="utf-8"),
            )

    def test_managed_block_replaces_itself_and_preserves_user_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bashrc = home / ".bashrc"
            bashrc.write_text("# my setting\n", encoding="utf-8")
            pb.bootstrap(home, apply=True)
            pb.bootstrap(home, apply=True)
            text = bashrc.read_text(encoding="utf-8")
            self.assertEqual(text.count(pb.BASH_BEGIN), 1)
            self.assertIn("# my setting", text)

    def test_skill_install_is_idempotent_and_user_drift_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pb.bootstrap(home, apply=True, profiles=("ccp", "cxp"))
            target = home / ".agents" / "skills" / "feishu"
            skill = target / "SKILL.md"
            manifest = target / pb.SKILL_MANIFEST
            before = (skill.read_bytes(), manifest.read_bytes(), skill.stat().st_mtime_ns)
            pb.bootstrap(home, apply=True, profiles=("ccp", "cxp"))
            self.assertEqual((skill.read_bytes(), manifest.read_bytes(), skill.stat().st_mtime_ns), before)

            skill.write_text(skill.read_text(encoding="utf-8") + "\nuser edit\n", encoding="utf-8")
            edited = skill.read_bytes()
            rows = pb.bootstrap(home, apply=True, profiles=("ccp", "cxp"))
            codex = next(row for row in rows if row["kind"] == "skill" and ".agents" in row["path"])
            self.assertEqual(codex["status"], "drift")
            self.assertEqual(skill.read_bytes(), edited)

    def test_unmanaged_same_hash_is_adopted_but_different_content_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / ".agents" / "skills" / "feishu"
            # 技能目录现在不止 SKILL.md（还有 references/），同 hash 的判据是整棵树，整目录拷贝才算「同内容」。
            shutil.copytree(pb.FEISHU_SKILL_SOURCE, target)
            rows = pb.bootstrap(home, apply=True, profiles=("cxp",))
            self.assertEqual(next(row for row in rows if row["kind"] == "skill")["status"], "ok")
            self.assertTrue((target / pb.SKILL_MANIFEST).is_file())

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / ".agents" / "skills" / "feishu"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("---\nname: feishu\n---\nprivate\n", encoding="utf-8")
            original = (target / "SKILL.md").read_bytes()
            rows = pb.bootstrap(home, apply=True, profiles=("cxp",))
            self.assertEqual(next(row for row in rows if row["kind"] == "skill")["status"], "conflict")
            self.assertEqual((target / "SKILL.md").read_bytes(), original)

    def test_duplicate_frontmatter_name_fails_closed_and_marker_adapter_moves_recoverably(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            legacy = home / ".agents" / "skills" / pb.LEGACY_CODEX_ADAPTER
            legacy.mkdir(parents=True)
            (legacy / "SKILL.md").write_text(
                "---\nname: feishu\n---\n" + pb.LEGACY_ADAPTER_MARKER + "\n",
                encoding="utf-8",
            )
            rows = pb.bootstrap(home, apply=False, profiles=("cxp",))
            self.assertEqual(next(row for row in rows if row["kind"] == "skill")["status"], "name-conflict")
            pb.bootstrap(home, apply=True, profiles=("cxp",), migrate_legacy_feishu_adapter=True)
            self.assertFalse(legacy.exists())
            self.assertTrue((home / ".agents" / "link16-disabled-skills" /
                             pb.LEGACY_CODEX_ADAPTER / "SKILL.md").is_file())
            self.assertTrue((home / ".agents" / "skills" / "feishu" / "SKILL.md").is_file())

    def test_document_vendor_skills_are_retired_but_other_and_foreign_skills_stay(self):
        """lark-cli update reinstalls lark-doc & co.; bootstrap moves them out recoverably."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skills = home / ".agents" / "skills"
            vendor = 'requires:\n  bins: ["lark-cli"]'
            for name, body in (("lark-doc", vendor), ("lark-calendar", vendor),
                               ("lark-wiki", "hand written, not the vendor copy")):
                (skills / name).mkdir(parents=True)
                (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n{body}\n---\n", encoding="utf-8")
            rows = [r for r in pb.bootstrap(home, apply=False, profiles=("cxp",))
                    if r["kind"] == "retired-vendor-skill"]
            self.assertEqual({Path(r["path"]).name: r["status"] for r in rows},
                             {"lark-doc": "ready-to-retire", "lark-wiki": "conflict"})
            pb.bootstrap(home, apply=True, profiles=("cxp",))
            self.assertFalse((skills / "lark-doc").exists())
            self.assertTrue((skills / "lark-calendar" / "SKILL.md").is_file())   # 非读文档类保留
            self.assertTrue((skills / "lark-wiki" / "SKILL.md").is_file())       # 非 lark-cli 生成物不动
            backups = list((home / ".agents" / "link16-disabled-skills").glob("lark-doc-*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue((backups[0] / "SKILL.md").is_file())

    @unittest.skipUnless(sys.platform == "win32", "directory junctions are Windows-only")
    def test_dangling_junction_is_retired_without_path_is_junction(self):
        """Python 3.11 has no Path.is_junction; TB25 kept 5 dangling lark-* junctions."""
        import _winapi
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / ".agents" / "skills" / "lark-doc"
            target.mkdir(parents=True)
            claude_skills = home / ".claude-personal" / "skills"
            claude_skills.mkdir(parents=True)
            link = claude_skills / "lark-doc"
            _winapi.CreateJunction(str(target), str(link))
            target.rmdir()
            saved = {cls: vars(cls)["is_junction"]
                     for cls in type(link).__mro__ if "is_junction" in vars(cls)}
            for cls in saved:
                delattr(cls, "is_junction")     # behave like Python 3.11
            try:
                rows = pb.retired_vendor_skill_rows(home, [claude_skills], apply=False)
                self.assertEqual([r["status"] for r in rows], ["ready-to-retire"])
                rows = pb.retired_vendor_skill_rows(home, [claude_skills], apply=True)
            finally:
                for cls, method in saved.items():
                    setattr(cls, "is_junction", method)
            self.assertEqual([r["status"] for r in rows], ["retired"])
            self.assertFalse(os.path.lexists(link))

    def test_new_registry_accepts_selected_runtimes_and_requires_isolated_unique_homes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "agent-profiles.local.json"
            result = pb.initialize_registry(target, [
                {"name": "claude-work", "runtime": "claude", "home": "~/.claude-work"},
                {"name": "codex-work2", "runtime": "codex", "home": "~/.codex-work2"},
            ], apply=True)
            self.assertEqual(result["status"], "ok")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["default_profiles"], {
                "claude": "claude-work", "codex": "codex-work2",
            })
            claude_only = Path(tmp) / "claude-only.json"
            single = pb.initialize_registry(claude_only, [
                {"name": "claude-solo", "runtime": "claude", "home": "~/.claude-solo"},
            ], apply=True)
            self.assertEqual(single["status"], "ok")
            self.assertEqual(
                json.loads(claude_only.read_text(encoding="utf-8"))["default_profiles"],
                {"claude": "claude-solo"},
            )
            with self.assertRaisesRegex(ValueError, "至少提供一个"):
                pb.initialize_registry(Path(tmp) / "empty.json", [], apply=True)
            for forbidden in ("~/.claude", "~\\.CODEX\\", "~/.kimi-code"):
                with self.subTest(forbidden=forbidden):
                    with self.assertRaisesRegex(ValueError, "禁止"):
                        pb._new_profile("bad", "claude", forbidden)

    def test_kimi_only_cli_initialization_bootstraps_isolated_home_with_native_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "new-user"
            registry = root / "agent-profiles.local.json"
            args = ["--init-registry", "--kimi-profile", "kimi-work", "--kimi-home", "~/.kimi-work", "--json"]
            with mock.patch.object(pb.agent_runtime, "PROFILE_REGISTRY_LOCAL_PATH", registry):
                self.assertEqual(pb.main(args), 0)
                self.assertFalse(registry.exists())
                self.assertEqual(pb.main(args + ["--apply"]), 0)
                first = registry.read_bytes()
                self.assertEqual(pb.main(args + ["--apply"]), 0)
                self.assertEqual(registry.read_bytes(), first)
            doc = json.loads(first)
            self.assertEqual(doc["default_profiles"], {"kimi": "kimi-work"})
            pb.bootstrap(home, apply=True, profiles=("kimi-work",), registry_path=registry)
            self.assertTrue((home / ".kimi-work").is_dir())
            self.assertTrue((home / ".agents" / "skills" / "feishu" / "SKILL.md").is_file())
            self.assertFalse((home / ".kimi-work" / "hooks.json").exists())
            config = (home / ".kimi-work" / "config.toml").read_text(encoding="utf-8")
            self.assertIn("[[hooks]]", config)
            self.assertIn('event = "UserPromptSubmit"', config)
            self.assertIn("bridge_userprompt.py", config)
            self.assertFalse((home / ".kimi-code").exists())
            self.assertEqual(list(home.rglob("auth.json")), [])
            bashrc = (home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn("list --names", bashrc)
            self.assertNotIn("kimi-work", bashrc)
            checked = pb.bootstrap(home, profiles=("kimi-work",), registry_path=registry)
            self.assertTrue(all(row["status"] == "ok" for row in checked))

    def test_kimi_initialization_requires_both_name_and_home_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "profiles.json"
            with mock.patch.object(pb.agent_runtime, "PROFILE_REGISTRY_LOCAL_PATH", registry):
                for option, value in (("--kimi-profile", "kimi-work"), ("--kimi-home", "~/.kimi-work")):
                    with self.subTest(option=option), self.assertRaises(SystemExit) as error:
                        pb.main(["--init-registry", option, value, "--apply"])
                    self.assertEqual(error.exception.code, 2)
                    self.assertFalse(registry.exists())

    def test_legacy_registry_migration_is_exact_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "agent-profiles.json"
            target = root / "agent-profiles.local.json"
            shutil.copy2(ROOT / "tests" / "fixtures" / "agent-profiles.legacy.json", source)
            first = pb.migrate_registry(source, target, roster=root / "missing-roster.json", apply=True)
            second = pb.migrate_registry(source, target, roster=root / "missing-roster.json", apply=True)
            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "ok")
            self.assertEqual(json.loads(source.read_text(encoding="utf-8")),
                             json.loads(target.read_text(encoding="utf-8")))
            changed = json.loads(target.read_text(encoding="utf-8"))
            changed["profiles"]["cxp"]["home"] = "~/.codex-other"
            target.write_text(json.dumps(changed), encoding="utf-8")
            conflict = pb.migrate_registry(source, target, roster=root / "missing-roster.json", apply=True)
            self.assertEqual(conflict["status"], "conflict")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), changed)

    def test_real_git_bash_loads_all_three_as_functions(self):
        candidates = [
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files\Git\bin\bash.exe"),
        ]
        bash = next((path for path in candidates if path.is_file()), None)
        if not bash:
            self.skipTest("Git for Windows bash not installed")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pb.bootstrap(home, apply=True)
            rc = (home / ".bashrc").as_posix()
            done = subprocess.run(
                [str(bash), "--noprofile", "--norc", "-c",
                 f"source '{rc}'; type -t ccp; type -t ccp2; type -t cxp"],
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "LINK16_AGENT_INFRA_ROOT": str(ROOT),
                     pb.agent_runtime.PROFILE_REGISTRY_ENV: str(ROOT / "tests" / "fixtures" / "agent-profiles.legacy.json")},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.split(), ["function", "function", "function"])

    def test_git_bash_resolver_prefers_python313_over_python39(self):
        candidates = [
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files\Git\bin\bash.exe"),
        ]
        bash = next((path for path in candidates if path.is_file()), None)
        if not bash:
            self.skipTest("Git for Windows bash not installed")
        if sys.version_info < (3, 11):
            # Both test installations delegate to this interpreter; it must
            # qualify before we can test ordering among eligible installations.
            self.skipTest("需要 Python 3.11+ 才能演示「优先挑更新的解释器」")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            local = root / "Local App Data"
            old_python = local / "Programs" / "Python" / "Python39" / "python.exe"
            new_python = local / "Programs" / "Python" / "Python313" / "python.exe"
            old_python.parent.mkdir(parents=True)
            new_python.parent.mkdir(parents=True)
            # Use supported venv redirectors: a naked copy of python.exe
            # cannot find its DLLs; a shell script named .exe is not a PE file.
            import venv
            probe_env = root / "probe-venv"
            venv.EnvBuilder(with_pip=False).create(probe_env)
            for executable in (old_python, new_python):
                shutil.copy2(probe_env / "Scripts" / "python.exe", executable)
                shutil.copy2(probe_env / "pyvenv.cfg", executable.parent / "pyvenv.cfg")
            # This case checks interpreter selection only; wrapper discovery
            # (and its second interpreter scan) is covered by the preceding test.
            home.mkdir()
            rc_path = home / ".bashrc"
            rc_path.write_text(pb._bash_block().split("\n__link16_cli=", 1)[0], encoding="utf-8", newline="\n")
            done = subprocess.run(
                [str(bash), "--noprofile", "--norc", "-c",
                 f"export LOCALAPPDATA='{local.as_posix()}'; source '{rc_path.as_posix()}'; __link16_python"],
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env={**os.environ, "LINK16_AGENT_INFRA_ROOT": str(ROOT),
                     pb.agent_runtime.PROFILE_REGISTRY_ENV: str(ROOT / "tests" / "fixtures" / "agent-profiles.legacy.json")},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("Python313/python.exe", done.stdout.replace("\\", "/"))

    def test_powershell_resolver_uses_semantic_version_and_requires_311(self):
        block = pb._powershell_block()
        self.assertIn("[version]", block)
        self.assertIn("sys.version_info >= (3, 11)", block)
        self.assertNotIn("Sort-Object Name -Descending", block)

    @unittest.skipUnless(shutil.which("powershell.exe"), "requires PowerShell")
    def test_powershell_wrapper_forwards_provider_flags_without_binding_them(self):
        # Exercise PowerShell's actual argument binder, without launching an agent.
        block = pb._powershell_block(["cxp"])
        begin = block.index("function Invoke-Link16AgentProfile")
        end = block.index("\ntry {", begin)
        harness = (
            "function Resolve-Link16AgentProfileCli { 'agent_profile_cli.py' }; "
            "function Resolve-Link16Python { 'Capture-Arguments' }; "
            "function Capture-Arguments { ConvertTo-Json -Compress -InputObject @($args) }; "
            + block[begin:end]
            + "\nInvoke-Link16AgentProfile cxp --model sample-model -c model_reasoning_effort=low --version -p /model"
        )
        done = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-Command", harness],
                              capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        actual = json.loads(done.stdout)
        self.assertEqual(actual[actual.index("--profile") + 1], "cxp")
        self.assertEqual(actual[-7:], [
            "--model", "sample-model", "-c", "model_reasoning_effort=low", "--version", "-p", "/model",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
