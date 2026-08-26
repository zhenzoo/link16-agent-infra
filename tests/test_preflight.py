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

import preflight  # noqa: E402


class GitBashChecks(unittest.TestCase):
    def test_runtime_signature_must_be_mingw(self):
        bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        with mock.patch.object(preflight, "_git_bash_path", return_value=bash), \
             mock.patch.object(preflight, "_run", return_value="BASH_VERSION=5.3\nMSYSTEM=MINGW64"):
            self.assertEqual(preflight.check_git_bash().status, preflight.OK)
        with mock.patch.object(preflight, "_git_bash_path", return_value=bash), \
             mock.patch.object(preflight, "_run", return_value="BASH_VERSION=5.3\nMSYSTEM="):
            self.assertEqual(preflight.check_git_bash().status, preflight.FAIL)


class TerminalDefaultsChecks(unittest.TestCase):
    def test_windows_terminal_default_guid_resolves_to_git_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({
                "defaultProfile": "{git}",
                "profiles": {"list": [
                    {"guid": "{git}", "name": "Git Bash",
                     "commandline": r"C:\Program Files\Git\bin\bash.exe --login -i"},
                ]},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                self.assertEqual(preflight.check_windows_terminal_default().status, preflight.OK)

    def test_windows_terminal_rejects_powershell_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "Packages" / "Microsoft.WindowsTerminal_8wekyb3d8bbwe" / "LocalState" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({
                "defaultProfile": "{ps}",
                "profiles": {"list": [
                    {"guid": "{ps}", "name": "PowerShell", "commandline": "powershell.exe"},
                ]},
            }), encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": tmp}, clear=False):
                self.assertEqual(preflight.check_windows_terminal_default().status, preflight.FAIL)

    def test_wmux_store_must_point_to_existing_git_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = Path(tmp)
            session = appdata / "wmux" / "session.json"
            session.parent.mkdir(parents=True)
            fake_bash = appdata / "Git" / "bin" / "bash.exe"
            fake_bash.parent.mkdir(parents=True)
            fake_bash.touch()
            session.write_text(json.dumps({"defaultShell": str(fake_bash)}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"APPDATA": tmp}, clear=False):
                self.assertEqual(preflight.check_wmux_default_shell().status, preflight.OK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
