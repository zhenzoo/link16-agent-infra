import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_update


class OfficialUpdateCommandTests(unittest.TestCase):
    def test_every_runtime_calls_its_official_updater(self):
        cases = [("ccp", "claude", ["update"]), ("cxp", "codex", ["update"]),
                 ("kp", "kimi", ["update", "--yes"])]
        for name, runtime, args in cases:
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                profile = SimpleNamespace(name=name, runtime=runtime, home_path=home)
                with patch.object(agent_update.agent_runtime, "profile_spec", return_value=profile), \
                        patch.object(agent_update, "_executable", return_value=f"{runtime}.exe"), \
                        patch.object(agent_update, "_centralize_codex_updates"), \
                        patch.object(agent_update, "_version", side_effect=["1.0.0", "1.0.0"]), \
                        patch.object(agent_update.subprocess, "run",
                                     return_value=SimpleNamespace(returncode=0)) as run:
                    result = agent_update.ensure(name, state_dir=home / "state")
                self.assertEqual(result["status"], "current")
                self.assertEqual(run.call_args.args[0], [f"{runtime}.exe", *args])
                self.assertEqual(run.call_args.kwargs["timeout"], 60)
                self.assertEqual(result["mode"], "official-foreground")

    def test_emergency_switch_does_not_invoke_an_updater(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LINK16_AGENT_UPDATE": "0"}):
            profile = SimpleNamespace(name="kp", runtime="kimi", home_path=Path(temp))
            with patch.object(agent_update.agent_runtime, "profile_spec", return_value=profile), \
                    patch.object(agent_update.subprocess, "run") as run:
                result = agent_update.ensure("kp")
            self.assertEqual(result["status"], "disabled")
            run.assert_not_called()


class CodexUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = SimpleNamespace(name="cxp", runtime="codex", home_path=self.root)

    def tearDown(self):
        self.temp.cleanup()

    def ensure(self):
        with patch.object(agent_update.agent_runtime, "profile_spec", return_value=self.profile), \
                patch.object(agent_update, "_executable", return_value="codex.exe"):
            return agent_update.ensure("cxp", state_dir=self.root / "state")

    def test_official_update_and_version_readback(self):
        with patch.object(agent_update, "_version", side_effect=["1.0.0", "1.1.0"]), \
                patch.object(agent_update.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            result = self.ensure()
        self.assertEqual(result["status"], "updated")
        self.assertEqual((result["before"], result["after"]), ("1.0.0", "1.1.0"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["codex.exe", "update"])
        self.assertNotIn("stdout", json.dumps(result))
        config = (self.root / "config.toml").read_text(encoding="utf-8")
        self.assertIn("check_for_update_on_startup = false", config)

    def test_current_failed_and_timed_out_all_return_without_blocking_launch(self):
        cases = [
            (SimpleNamespace(returncode=0), ["2.0.0", "2.0.0"], "current"),
            (SimpleNamespace(returncode=1), ["2.0.0", "2.0.0"], "failed"),
            (subprocess.TimeoutExpired(["codex"], 120), ["2.0.0"], "timed_out"),
        ]
        for completed, versions, status in cases:
            with self.subTest(status=status), \
                    patch.object(agent_update, "_version", side_effect=versions), \
                    patch.object(agent_update.subprocess, "run",
                                 **({"side_effect": completed} if isinstance(completed, Exception)
                                    else {"return_value": completed})):
                result = self.ensure()
            self.assertEqual(result["status"], status)

    def test_concurrent_updater_does_not_run_twice(self):
        with patch.object(agent_update, "_centralize_codex_updates"), \
                patch.object(agent_update.bridge_injection, "ProcessFileLock",
                             side_effect=TimeoutError("busy")), \
                patch.object(agent_update.subprocess, "run") as run:
            result = self.ensure()
        self.assertEqual(result["status"], "busy")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
