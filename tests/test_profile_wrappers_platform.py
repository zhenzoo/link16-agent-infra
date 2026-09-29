"""profile_wrappers 平台分派测试（PLAN-1140 P1）。

darwin：target_plan 产出 ~/.zshrc + ~/.bashrc，不写 PowerShell profile；
__link16_python 补 python3 / Homebrew 探测。Windows 形状用 platform="nt" 钉住，
保证在 mac 上也能回归 Windows 行为。
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import profile_wrappers as pw  # noqa: E402


class TargetPlanPlatformTests(unittest.TestCase):
    def test_darwin_plan_writes_zshrc_and_bashrc_but_no_powershell(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = pw.target_plan(Path(tmp), platform="posix")
        paths = [row["path"].name for row in rows]
        self.assertEqual(paths, [".zshrc", ".bashrc"])
        self.assertTrue(all("PowerShell" not in str(row["path"]) for row in rows))
        for row in rows:
            self.assertIn("__link16_run_profile", row["desired"])
            self.assertEqual(row["status"], "missing")

    def test_nt_plan_keeps_powershell_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = pw.target_plan(Path(tmp), platform="nt")
        names = [row["path"].name for row in rows]
        self.assertEqual(names, [".bashrc", "Microsoft.PowerShell_profile.ps1",
                                 "Microsoft.PowerShell_profile.ps1"])
        self.assertIn("Resolve-Link16Python", rows[1]["desired"])

    def test_darwin_plan_marks_existing_zshrc_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".zshrc").write_text(pw.replace_block("", pw.bash_block()), encoding="utf-8")
            rows = pw.target_plan(home, platform="posix")
        zshrc = next(row for row in rows if row["path"].name == ".zshrc")
        self.assertEqual(zshrc["status"], "ok")

    def test_bash_block_finds_python3_and_homebrew(self):
        block = pw.bash_block()
        self.assertIn("command -v python3", block)
        self.assertIn("/opt/homebrew/bin/python3", block)
        self.assertIn("command -v powershell.exe", block)  # Windows 分支守卫保留


if __name__ == "__main__":
    unittest.main(verbosity=2)
