#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import subprocess
import shutil
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import profile_bootstrap as pb  # noqa: E402


class ProfileBootstrapTests(unittest.TestCase):
    def test_apply_creates_three_homes_launcher_and_shell_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pb.bootstrap(home, apply=True)
            self.assertTrue((home / ".claude-personal").is_dir())
            self.assertTrue((home / ".claude-personal2" / "launch.sh").is_file())
            self.assertTrue((home / ".codex-personal").is_dir())
            self.assertTrue((home / ".claude-personal" / "skills" / "feishu" / "SKILL.md").is_file())
            self.assertTrue((home / ".agents" / "skills" / "feishu" / "SKILL.md").is_file())
            hooks = json.loads((home / ".codex-personal" / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(set(hooks["hooks"]), {"Stop", "PostToolUse", "UserPromptSubmit"})
            bashrc = (home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn("cc ccp ccp2 cck ccw ccw2 ccw3 cx cxp", bashrc)
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
            target.mkdir(parents=True)
            shutil.copy2(pb.FEISHU_SKILL_SOURCE / "SKILL.md", target / "SKILL.md")
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
            for forbidden in ("~/.claude", "~\\.CODEX\\"):
                with self.subTest(forbidden=forbidden):
                    with self.assertRaisesRegex(ValueError, "禁止"):
                        pb._new_profile("bad", "claude", forbidden)

    def test_legacy_registry_migration_is_exact_and_conflict_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "agent-profiles.json"
            target = root / "agent-profiles.local.json"
            shutil.copy2(ROOT / "feishu" / "agent-profiles.json", source)
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
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
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
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.split(), ["function", "function", "function"])

    def test_git_bash_resolver_prefers_python313_over_python39(self):
        candidates = [
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ]
        bash = next((path for path in candidates if path.is_file()), None)
        if not bash:
            self.skipTest("Git for Windows bash not installed")
        if sys.version_info < (3, 12):
            # 本用例把 sys.executable 拷成 Python39/Python313 两个假解释器，靠它们【自报版本】
            # 来演示「在合格的解释器里挑最新的」。跑测试的解释器若本身 < 3.12，两个拷贝都不合格，
            # 解析器只会退到 PATH 上的 python —— 那不是被测行为，是环境不具备演示条件。
            # （tb24 是 3.10.10。2026-08-30 主人拍板把 PATH 兜底的硬闸放宽后，
            #   returncode 会变 0，但 stdout 仍不可能是 Python313 —— 断言不该为此改弱。）
            self.skipTest("需要 Python 3.12+ 才能演示「优先挑更新的解释器」")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            local = root / "Local App Data"
            old_python = local / "Programs" / "Python" / "Python39" / "python.exe"
            new_python = local / "Programs" / "Python" / "Python313" / "python.exe"
            old_python.parent.mkdir(parents=True)
            new_python.parent.mkdir(parents=True)
            shutil.copy2(sys.executable, old_python)
            shutil.copy2(sys.executable, new_python)
            pb.bootstrap(home, apply=True)
            rc = (home / ".bashrc").as_posix()
            done = subprocess.run(
                [str(bash), "--noprofile", "--norc", "-c",
                 f"export LOCALAPPDATA='{local.as_posix()}'; source '{rc}'; __link16_python"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("Python313/python.exe", done.stdout.replace("\\", "/"))

    def test_powershell_resolver_uses_semantic_version_and_requires_312(self):
        block = pb._powershell_block()
        self.assertIn("[version]", block)
        self.assertIn("sys.version_info >= (3, 12)", block)
        self.assertNotIn("Sort-Object Name -Descending", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
