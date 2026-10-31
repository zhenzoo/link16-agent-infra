import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import agent_update
import official_install as oi
import preflight
import windows_bootstrap as wb


class OfficialInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"LOCALAPPDATA": str(self.root), "APPDATA": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.native = self.root / "Programs/OpenAI/Codex/bin/codex.exe"
        self.npm = self.root / "npm/codex.cmd"
        self.npm.parent.mkdir()
        self.npm.write_text('@node "%~dp0/node_modules/@openai/codex/bin/codex.js" %*')

    def install_native(self):
        self.native.parent.mkdir(parents=True)
        self.native.touch()

    def test_npm_is_reported_without_running_updater_or_touching_profile(self):
        before = self.npm.read_bytes()
        with patch.object(preflight, "_fresh_which", return_value=str(self.npm)):
            self.assertEqual(wb.detect_component("codex"), self.npm)
            check = preflight.check_codex_installation()
        self.assertEqual(check.status, preflight.FAIL)
        self.assertIn("npm", check.detail)
        profile = SimpleNamespace(runtime="codex", home_path=self.root, name="fixture")
        with patch.object(agent_update.shutil, "which", return_value=str(self.npm)), \
                patch.object(agent_update, "_centralize_codex_updates") as config, \
                patch.object(agent_update.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "官方迁移"):
                agent_update._update_official(profile, self.root, 60)
        config.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.npm.read_bytes(), before)

    def test_native_update_bypasses_npm_shadow_but_preflight_requires_shell_fix(self):
        self.install_native()
        with patch.object(agent_update.shutil, "which", return_value=str(self.npm)), \
                patch.object(preflight, "_fresh_which", return_value=str(self.npm)):
            self.assertEqual(agent_update._executable("codex"), str(self.native))
            self.assertEqual(wb.detect_component("codex"), self.native)
            self.assertEqual(preflight.check_codex_installation().status, preflight.FAIL)
        self.assertTrue(self.npm.exists())

    def test_migration_receipt_replaces_stale_success_without_changing_profile(self):
        profile = SimpleNamespace(runtime="codex", home_path=self.root, name="fixture")
        receipt = self.root / "fixture.json"
        receipt.write_text('{"status":"updated"}')
        with patch.object(agent_update.agent_runtime, "profile_spec", return_value=profile), \
                patch.object(agent_update.shutil, "which", return_value=str(self.npm)), \
                patch.object(agent_update, "_centralize_codex_updates") as config, \
                patch.object(agent_update.subprocess, "run") as run:
            record = agent_update.ensure("fixture", state_dir=self.root)
        self.assertEqual(record["status"], "migration_required")
        self.assertEqual(json.loads(receipt.read_text()), record)
        config.assert_not_called()
        run.assert_not_called()

    def test_native_and_existing_standalone_wrapper_pass_without_rewriting(self):
        self.install_native()
        wrapper = self.root / "codex"
        wrapper.write_text('#!/usr/bin/env bash\ncodex_exe="$LOCALAPPDATA/Programs/OpenAI/Codex/bin/codex.exe"\nexec "$codex_exe" "$@"\n')
        for entry in (self.native, wrapper):
            before = entry.read_bytes()
            with patch.object(preflight, "_fresh_which", return_value=str(entry)):
                self.assertEqual(preflight.check_codex_installation().status, preflight.OK)
            self.assertEqual(entry.read_bytes(), before)

    def test_unknown_wrapper_is_not_certified_and_missing_cli_is_optional(self):
        unknown = self.root / "custom-codex.cmd"
        unknown.write_text("@some-other-cli %*")
        self.assertEqual(oi.codex_windows_installation(unknown)["status"], "unknown")
        with patch.object(preflight, "_fresh_which", return_value=None):
            self.assertEqual(preflight.check_codex_installation().status, preflight.OK)
        self.install_native()
        self.assertEqual(oi.codex_windows_installation(unknown)["status"], "shadowed")
        unknown.write_text("# Programs/OpenAI/Codex/bin/codex.exe\n@some-other-cli %*")
        self.assertEqual(oi.codex_windows_installation(unknown)["status"], "shadowed")

    def test_msix_redirect_does_not_hide_native_installation(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "Packages/Claude_demo/LocalCache/Local"),
                                     "USERPROFILE": str(self.root)}):
            self.assertEqual(oi.local_appdata(), self.root / "AppData/Local")

    def test_wmux_update_check_is_read_only_for_on_off_and_bad_settings(self):
        session = self.root / "wmux/session.json"
        session.parent.mkdir()
        for content, expected in [(json.dumps({"autoUpdateEnabled": True}), preflight.OK),
                                  (json.dumps({"autoUpdateEnabled": False}), preflight.WARN),
                                  ("{}", preflight.WARN), ("bad", preflight.WARN)]:
            session.write_text(content)
            self.assertEqual(preflight.check_wmux_update_settings().status, expected)
            self.assertEqual(session.read_text(), content)

    def test_non_codex_provider_updaters_keep_their_existing_executable(self):
        for name in ("claude", "kimi"):
            with patch.object(agent_update.shutil, "which", return_value=name + ".exe"):
                self.assertEqual(agent_update._executable(name), name + ".exe")
