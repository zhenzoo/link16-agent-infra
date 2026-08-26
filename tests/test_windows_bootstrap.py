#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import windows_bootstrap as wb  # noqa: E402


class ComponentPlanTests(unittest.TestCase):
    def test_default_plan_has_exactly_seven_and_never_installs_gstack_or_provider_via_npm(self):
        self.assertEqual([row.key for row in wb.COMPONENTS],
                         ["git", "gh", "python", "node", "wmux", "claude", "codex"])
        serialized = json.dumps([row.install_command for row in wb.COMPONENTS]).lower()
        self.assertNotIn("gstack", serialized)
        self.assertNotIn("npm", serialized)
        self.assertIn("https://claude.ai/install.ps1", serialized)
        self.assertIn("https://chatgpt.com/codex/install.ps1", serialized)

    def test_only_provider_components_can_be_skipped(self):
        self.assertEqual(wb.parse_skips(["claude,codex"]), {"claude", "codex"})
        with self.assertRaisesRegex(ValueError, "核心依赖"):
            wb.parse_skips(["git"])

    def test_existing_install_is_detected_and_not_scheduled_again(self):
        fake = Path("C:/tools/node.exe")
        with mock.patch.object(wb, "detect_component", return_value=fake):
            rows = wb.installation_plan()
        self.assertTrue(all(row["status"] == "installed" for row in rows))

    def test_apply_missing_stops_when_real_source_probe_is_unusable(self):
        row = {"key": "codex", "status": "missing", "probe_url": "https://example.invalid/",
               "install_command": ["never-run"]}
        decision = wb.network_route.RouteDecision(
            "direct", False, "两路失败", "direct", (),
        )
        with mock.patch.object(wb.network_route, "proxy_url", return_value=None), \
             mock.patch.object(wb.network_route, "detect", return_value=decision), \
             mock.patch.object(wb.subprocess, "run") as run:
            result = wb.apply_missing([row])
        run.assert_not_called()
        self.assertEqual(result[0]["returncode"], 2)


class WmuxPostInstallTests(unittest.TestCase):
    def test_closed_wmux_can_receive_git_bash_default_without_losing_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp)
            session = appdata / "wmux" / "session.json"
            session.parent.mkdir(parents=True)
            session.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
            bash = appdata / "Git" / "bin" / "bash.exe"
            bash.parent.mkdir(parents=True)
            bash.touch()
            with mock.patch.object(wb.preflight, "_git_bash_path", return_value=bash):
                row = wb.configure_wmux_git_bash(apply=True, appdata=appdata, running=False)
            saved = json.loads(session.read_text(encoding="utf-8"))
            self.assertEqual(row["status"], "applied")
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(saved["defaultShell"], str(bash))

    def test_running_wmux_requires_gui_instead_of_overwriting_its_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            bash = Path(tmp) / "Git" / "bin" / "bash.exe"
            bash.parent.mkdir(parents=True)
            bash.touch()
            with mock.patch.object(wb.preflight, "_git_bash_path", return_value=bash):
                row = wb.configure_wmux_git_bash(apply=True, appdata=tmp, running=True)
            self.assertEqual(row["status"], "needs-gui")
            self.assertFalse((Path(tmp) / "wmux" / "session.json").exists())

    def test_shortcut_dry_run_targets_stable_wmux_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "wmux" / "wmux.exe"
            exe.parent.mkdir()
            exe.touch()
            row = wb.ensure_wmux_desktop_shortcut(
                apply=False, desktop=root / "Desktop", executable=exe,
            )
            self.assertEqual(row["status"], "missing")
            self.assertIn("wmux.lnk", row["detail"])
            self.assertIn(str(exe), row["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
