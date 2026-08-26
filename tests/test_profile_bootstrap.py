#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import subprocess
import tempfile
import unittest
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
            bashrc = (home / ".bashrc").read_text(encoding="utf-8")
            self.assertIn("ccp ccp2 cxp", bashrc)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
