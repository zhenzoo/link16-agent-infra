#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import windows_bootstrap as wb  # noqa: E402


class ComponentPlanTests(unittest.TestCase):
    def test_python_utf8_is_persisted_without_system_locale_change(self):
        with mock.patch.object(wb, "_user_env_value", return_value=None), \
             mock.patch.object(wb, "_set_user_env_value") as set_value, \
             mock.patch.object(wb, "_broadcast_environment_change") as broadcast, \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYTHONUTF8", None)
            preview = wb.configure_python_utf8(apply=False)
            applied = wb.configure_python_utf8(apply=True)
        self.assertEqual(preview["status"], "missing")
        self.assertEqual(applied["status"], "applied")
        set_value.assert_called_once_with("PYTHONUTF8", "1")
        broadcast.assert_called_once_with()
        self.assertIn("重开终端", applied["detail"])

    def test_python_reprobe_accepts_new_interpreter_not_bootstrap_process(self):
        fresh = Path("C:/Users/test/AppData/Local/Programs/Python/Python313/python.exe")
        with mock.patch.object(wb, "_python312_executable", return_value=fresh):
            self.assertEqual(wb.detect_component("python"), fresh)

    def test_default_plan_has_eight_and_providers_use_official_native_installers(self):
        self.assertEqual([row.key for row in wb.COMPONENTS],
                         ["git", "gh", "python", "node", "wmux", "claude", "codex", "kimi"])
        serialized = json.dumps([row.install_command for row in wb.COMPONENTS]).lower()
        self.assertNotIn("gstack", serialized)
        self.assertNotIn("npm", serialized)
        self.assertIn("https://claude.ai/install.ps1", serialized)
        self.assertIn("https://chatgpt.com/codex/install.ps1", serialized)
        self.assertIn("https://code.kimi.com/kimi-code/install.ps1", serialized)
        for row in wb.COMPONENTS:
            if not row.required:
                self.assertEqual(row.install_command[0], "powershell.exe")
                self.assertNotIn("winget", " ".join(row.install_command).lower())
                self.assertNotIn("uv", row.install_command)

    def test_only_provider_components_can_be_skipped(self):
        self.assertEqual(wb.parse_skips(["claude,codex"]), {"claude", "codex"})
        self.assertEqual(wb.parse_skips(["kimi"]), {"kimi"})
        with self.assertRaisesRegex(ValueError, "核心依赖"):
            wb.parse_skips(["git"])

    def test_existing_install_is_detected_and_not_scheduled_again(self):
        fake = Path("C:/tools/node.exe")
        with mock.patch.object(wb, "detect_component", return_value=fake):
            rows = wb.installation_plan()
        self.assertTrue(all(row["status"] == "installed" for row in rows))

    def test_blank_machine_installs_three_providers_and_skips_have_no_side_effects(self):
        decision = wb.network_route.RouteDecision("direct", True, "reachable", "direct", ())
        for skipped in ((), ("kimi",), ("claude", "codex")):
            with self.subTest(skipped=skipped), \
                 mock.patch.object(wb, "detect_component", return_value=None), \
                 mock.patch.object(wb.network_route, "proxy_url", return_value=None), \
                 mock.patch.object(wb.network_route, "detect", return_value=decision) as probe, \
                 mock.patch.object(wb.network_route, "child_environment", side_effect=lambda *_: {}), \
                 mock.patch.object(wb.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                rows = wb.installation_plan(skipped)
                results = wb.apply_missing(rows)
            selected = [component for component in wb.COMPONENTS if component.key not in skipped]
            self.assertEqual([row["key"] for row in results], [row.key for row in selected])
            self.assertEqual([call.args[0] for call in probe.call_args_list], [row.probe_url for row in selected])
            for call, row in zip(run.call_args_list, selected):
                self.assertEqual(call.args[0], list(row.install_command))
                self.assertEqual(call.kwargs["env"].get("CODEX_NON_INTERACTIVE"),
                                 "1" if row.key == "codex" else None)

    def test_installed_kimi_is_never_reinstalled(self):
        with mock.patch.object(wb, "detect_component", return_value=Path("existing/kimi.exe")), \
             mock.patch.object(wb.network_route, "detect") as probe, \
             mock.patch.object(wb.subprocess, "run") as run:
            rows = wb.installation_plan()
            self.assertEqual(wb.apply_missing(rows), [])
        probe.assert_not_called()
        run.assert_not_called()

    def test_official_default_paths_are_detected_when_parent_path_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            local = home / "AppData" / "Local"
            paths = {
                "claude": home / ".local" / "bin" / "claude.exe",
                "codex": local / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe",
                "kimi": home / ".kimi-code" / "bin" / "kimi.exe",
            }
            for path in paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with mock.patch.object(wb.preflight, "_fresh_which", return_value=None), \
                 mock.patch.object(wb.Path, "home", return_value=home), \
                 mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local)}):
                for key, path in paths.items():
                    self.assertEqual(wb.detect_component(key), path)

    def test_existing_path_entry_is_preserved_instead_of_implicitly_migrated(self):
        installed = "C:/existing-manager/claude.exe"
        with mock.patch.object(wb.preflight, "_fresh_which", return_value=installed):
            self.assertEqual(wb.detect_component("claude"), Path(installed))

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

    def test_software_plan_uses_only_user_facing_status_vocabulary(self):
        rows = [
            {"key": "git", "status": "installed"},
            {"key": "claude", "status": "installed"},
            {"key": "codex", "status": "skipped"},
            {"key": "node", "status": "missing"},
            {"key": "kimi", "status": "installed"},
        ]
        result = wb.software_user_plan(rows, runtime_health={"profiles": {"runtimes": {}}})
        self.assertEqual([row["user_status"] for row in result], [
            "已存在跳过", "需要登录", "本次不安装", "将安装", "需要登录",
        ])
        self.assertTrue(all(row["user_status"] in wb.USER_STATUSES for row in result))

    def test_link16_plan_has_fixed_ten_items_and_preserves_technical_evidence(self):
        raw = {
            "profiles": {"selected": ["work"], "runtimes": {"work": "codex"},
                         "skills": [{"status": "ok"}]},
            "roster": {"exists": True, "error": "", "names": ["bot1"],
                       "credential_missing": {}},
        }
        component = lambda **layers: {"layers": layers, "evidence": "proof", "fix": "fix"}
        health = {"components": {
            "profiles_skill": component(configured="pass"),
            "hooks_transport": component(configured="pass"),
            "bridge": component(file_present="pass", running="pass", real_io="pass"),
            "cron": component(file_present="pass", running="pass", real_io="na"),
            "watchdog": component(file_present="pass", running="pass", real_io="na"),
            "registration_monitor": component(file_present="pass", running="na", real_io="pass"),
            "history_ledger": component(file_present="pass", running="na", real_io="pass"),
        }}
        rows = wb.link16_user_plan(raw, health)
        self.assertEqual([row["key"] for row in rows], [key for key, _ in wb.LINK16_ITEMS])
        self.assertTrue(all(row["user_status"] in wb.USER_STATUSES for row in rows))
        self.assertTrue(all("technical" in row for row in rows))


class WmuxPostInstallTests(unittest.TestCase):
    def test_windows_terminal_can_be_safely_set_without_losing_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            settings = local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"theme": "system", "profiles": {"list": []}}), encoding="utf-8")
            bash = local / "Git" / "bin" / "bash.exe"
            bash.parent.mkdir(parents=True)
            bash.touch()
            with mock.patch.object(wb.preflight, "_git_bash_path", return_value=bash):
                row = wb.configure_windows_terminal_git_bash(
                    apply=True, localappdata=local, running=False,
                )
            saved = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(row["status"], "applied")
            self.assertEqual(saved["theme"], "system")
            self.assertEqual(saved["defaultProfile"], saved["profiles"]["list"][0]["guid"])
            self.assertIn("bash.exe", saved["profiles"]["list"][0]["commandline"])

    def test_running_windows_terminal_requires_visible_gui_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp)
            settings = local / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"profiles": {"list": []}}), encoding="utf-8")
            bash = local / "Git" / "bin" / "bash.exe"
            bash.parent.mkdir(parents=True)
            bash.touch()
            with mock.patch.object(wb.preflight, "_git_bash_path", return_value=bash):
                row = wb.configure_windows_terminal_git_bash(
                    apply=True, localappdata=local, running=True,
                )
            self.assertEqual(row["status"], "needs-gui")

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

    def test_zero_exit_installer_is_not_success_when_recheck_still_missing(self):
        rows = [{"key": "node", "status": "missing"}]
        installs = [{"key": "node", "returncode": 0}]
        self.assertEqual(wb.deployment_failures(rows, installs, []), ["missing:node"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
