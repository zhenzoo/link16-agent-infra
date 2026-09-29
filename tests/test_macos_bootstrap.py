#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-1140 P2 · macos_bootstrap：组件清单 / detect / plan / zprofile 收尾，全部 mock 离线的 mac 语义。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import macos_bootstrap as mb  # noqa: E402


class ComponentPlanTests(unittest.TestCase):
    def test_component_keys_and_official_install_channels(self):
        self.assertEqual([row.key for row in mb.COMPONENTS],
                         ["brew", "git", "gh", "python", "node", "wmux", "claude", "codex", "kimi"])
        serialized = json.dumps([row.install_command for row in mb.COMPONENTS]).lower()
        self.assertNotIn("winget", serialized)
        self.assertNotIn("powershell", serialized)
        self.assertNotIn("npm", serialized)
        self.assertIn("https://raw.githubusercontent.com/homebrew/install/head/install.sh", serialized)
        self.assertIn("https://claude.ai/install.sh", serialized)
        self.assertIn("https://chatgpt.com/codex/install.sh", serialized)
        self.assertIn("https://code.kimi.com/kimi-code/install.sh", serialized)
        brew = mb.COMPONENT_BY_KEY
        for key in ("git", "gh", "python", "node"):
            self.assertEqual(brew[key].install_command[:2], ("brew", "install"))
        # wmux 没有包管理器渠道：apply 只能打开官方下载页
        self.assertEqual(brew["wmux"].install_command, ("open", mb.WMUX_RELEASES))

    def test_brew_is_the_first_task_when_missing(self):
        with mock.patch.object(mb, "detect_component", return_value=None):
            rows = mb.installation_plan()
        self.assertEqual(rows[0]["key"], "brew")
        self.assertEqual(rows[0]["status"], "missing")
        self.assertTrue(all(row["status"] == "missing" for row in rows))

    def test_only_provider_components_can_be_skipped(self):
        self.assertEqual(mb.parse_skips(["claude,codex"]), {"claude", "codex"})
        with self.assertRaisesRegex(ValueError, "核心依赖"):
            mb.parse_skips(["brew"])
        with self.assertRaisesRegex(ValueError, "核心依赖"):
            mb.parse_skips(["wmux"])

    def test_existing_install_is_detected_and_not_scheduled_again(self):
        fake = Path("/opt/homebrew/bin/gh")
        with mock.patch.object(mb, "detect_component", return_value=fake):
            rows = mb.installation_plan()
        self.assertTrue(all(row["status"] == "installed" for row in rows))
        self.assertTrue(all(row["detected_path"] == str(fake) for row in rows))


class DetectTests(unittest.TestCase):
    def test_brew_detects_path_then_canonical_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            brew = Path(tmp) / "brew"
            brew.touch()
            with mock.patch.object(mb.preflight, "_fresh_which", return_value=None), \
                 mock.patch.object(mb, "BREW_CANDIDATES", (brew,)):
                self.assertEqual(mb.detect_component("brew"), brew)
        with mock.patch.object(mb.preflight, "_fresh_which", return_value=None), \
             mock.patch.object(mb, "BREW_CANDIDATES", (Path("/nonexistent/brew"),)):
            self.assertIsNone(mb.detect_component("brew"))

    def test_git_accepts_xcode_clt_git_without_git_bash(self):
        with mock.patch.object(mb.preflight, "_fresh_which", return_value="/usr/bin/git"):
            self.assertEqual(mb.detect_component("git"), Path("/usr/bin/git"))

    def test_node_falls_back_to_keg_only_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            keg = Path(tmp) / "opt" / "node@22" / "bin"
            (keg / "node").parent.mkdir(parents=True)
            (keg / "node").touch()
            with mock.patch.object(mb.preflight, "_fresh_which", return_value=None), \
                 mock.patch.object(mb, "NODE_KEG_BINS", (keg,)):
                self.assertEqual(mb.detect_component("node"), keg / "node")

    def test_wmux_detects_applications_app_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "wmux.app"
            app.mkdir()
            with mock.patch.object(mb, "_wmux_candidates", return_value=[app]):
                self.assertEqual(mb.detect_component("wmux"), app)
            with mock.patch.object(mb, "_wmux_candidates", return_value=[Path(tmp) / "nope.app"]):
                self.assertIsNone(mb.detect_component("wmux"))

    def test_python_requires_312_or_newer(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "python3.11", Path(tmp) / "python3.13"
            old.touch()
            new.touch()
            versions = {old: (3, 11, 9), new: (3, 13, 1)}
            with mock.patch.object(mb, "_python_candidates", return_value=[old, new]), \
                 mock.patch.object(mb, "_python_version", side_effect=lambda p: versions.get(Path(p))):
                self.assertEqual(mb.detect_component("python"), new)
            with mock.patch.object(mb, "_python_candidates", return_value=[old]), \
                 mock.patch.object(mb, "_python_version", return_value=(3, 11, 9)):
                self.assertIsNone(mb.detect_component("python"))

    def test_provider_clis_detect_path_then_official_user_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude = home / ".local" / "bin" / "claude"
            codex = home / ".codex" / "bin" / "codex"
            kimi = home / ".kimi-code" / "bin" / "kimi"
            for path in (claude, codex, kimi):
                path.parent.mkdir(parents=True)
                path.touch()
            with mock.patch.object(mb.preflight, "_fresh_which", return_value=None), \
                 mock.patch.object(mb.Path, "home", return_value=home):
                self.assertEqual(mb.detect_component("claude"), claude)
                self.assertEqual(mb.detect_component("codex"), codex)
                self.assertEqual(mb.detect_component("kimi"), kimi)
        with mock.patch.object(mb.preflight, "_fresh_which", return_value="/usr/local/bin/codex"):
            self.assertEqual(mb.detect_component("codex"), Path("/usr/local/bin/codex"))


class PostInstallTests(unittest.TestCase):
    def test_python_utf8_is_always_ok_on_default_macos_locale(self):
        row = mb.configure_python_utf8(apply=False, preferred="UTF-8")
        self.assertEqual(row["status"], "ok")
        row = mb.configure_python_utf8(apply=True, preferred="utf8")
        self.assertEqual(row["status"], "ok")

    def test_non_utf8_locale_is_written_to_zprofile_only_on_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview = mb.configure_python_utf8(apply=False, home=tmp, preferred="POSIX")
            applied = mb.configure_python_utf8(apply=True, home=tmp, preferred="POSIX")
            text = (Path(tmp) / ".zprofile").read_text(encoding="utf-8")
        self.assertEqual(preview["status"], "missing")
        self.assertEqual(applied["status"], "applied")
        self.assertIn("export LC_ALL=en_US.UTF-8", text)
        self.assertIn(mb.ZPROFILE_MARKER, text)

    def test_user_path_appends_missing_dirs_to_zprofile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            brew_bin = home / "homebrew" / "bin"
            local_bin = home / ".local" / "bin"
            brew_bin.mkdir(parents=True)
            local_bin.mkdir(parents=True)
            with mock.patch.object(mb, "detect_component",
                                   side_effect=lambda key: {"brew": brew_bin / "brew"}.get(key)), \
                 mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                preview = mb.configure_user_path(apply=False, home=home)
                applied = mb.configure_user_path(apply=True, home=home)
                again = mb.configure_user_path(apply=False, home=home)
            text = (home / ".zprofile").read_text(encoding="utf-8")
        self.assertEqual(preview["status"], "missing")
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(again["status"], "ok")  # 幂等：已写进 zprofile 不再追加
        self.assertIn(f'export PATH="{brew_bin}:$PATH"', text)
        self.assertIn(f'export PATH="{local_bin}:$PATH"', text)

    def test_user_path_ok_when_nothing_to_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mb, "detect_component", return_value=None), \
                 mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
                row = mb.configure_user_path(apply=False, home=tmp)
        self.assertEqual(row["status"], "ok")


class ApplyFlowTests(unittest.TestCase):
    def test_blank_machine_installs_in_component_order_and_stops_on_failure(self):
        decision = mb.network_route.RouteDecision("direct", True, "reachable", "direct", ())
        with mock.patch.object(mb, "detect_component", return_value=None), \
             mock.patch.object(mb.network_route, "proxy_url", return_value=None), \
             mock.patch.object(mb.network_route, "detect", return_value=decision), \
             mock.patch.object(mb.network_route, "child_environment", side_effect=lambda *_: {}), \
             mock.patch.object(mb.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            rows = mb.installation_plan()
            results = mb.apply_missing(rows)
        self.assertEqual([row["key"] for row in results], [row.key for row in mb.COMPONENTS])
        for call, row in zip(run.call_args_list, mb.COMPONENTS):
            self.assertEqual(call.args[0], list(row.install_command))
            self.assertEqual(call.kwargs["env"].get("CODEX_NON_INTERACTIVE"),
                             "1" if row.key == "codex" else None)

    def test_apply_requires_yes_and_preview_never_installs(self):
        rows = [{"key": "git", "label": "Git", "required": True, "status": "missing",
                 "official_source": "s", "probe_url": "https://example.invalid/",
                 "install_command": ["never-run"], "detected_path": ""}]
        with mock.patch.object(mb, "installation_plan", return_value=rows), \
             mock.patch.object(mb, "apply_missing") as apply_mock, \
             mock.patch.object(mb, "configure_python_utf8", return_value={"task": "t", "status": "ok", "detail": ""}), \
             mock.patch.object(mb, "configure_user_path", return_value={"task": "t", "status": "ok", "detail": ""}), \
             mock.patch.object(mb.service_doctor, "collect_raw", return_value={}), \
             mock.patch.object(mb.service_doctor, "evaluate", return_value={}), \
             mock.patch.object(mb, "software_user_plan", side_effect=lambda rows, **_: rows), \
             mock.patch.object(mb, "link16_user_plan", return_value=[]):
            exit_code = mb.main(["--json"])
            with self.assertRaises(SystemExit):
                mb.main(["--apply"])
        self.assertEqual(exit_code, 0)
        apply_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
