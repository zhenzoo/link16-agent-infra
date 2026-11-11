import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import agent_update


class OfficialUpdateCommandTests(unittest.TestCase):
    def test_every_runtime_calls_its_official_updater(self):
        cases = [("ccp", "claude", ("update",)), ("cxp", "codex", ("update",)),
                 ("kp", "kimi", ("update", "--yes"))]
        for name, runtime, update_args in cases:
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                profile = SimpleNamespace(name=name, runtime=runtime, home_path=home)
                completed = {"status": None, "return_code": 0, "response_elapsed_sec": 0.01}
                with patch.object(agent_update.agent_runtime, "profile_spec", return_value=profile), \
                        patch.object(agent_update, "_executable", return_value=f"{runtime}.exe"), \
                        patch.object(agent_update, "_centralize_codex_updates"), \
                        patch.object(agent_update, "_codex_latest", return_value="1.1.0"), \
                        patch.object(agent_update, "_version", side_effect=["1.0.0", "1.0.0"]), \
                        patch.object(agent_update, "_run_update_command", return_value=completed) as run:
                    result = agent_update.ensure(name, state_dir=home / "state")
                self.assertEqual(result["status"], "unverified" if runtime == "codex" else "current")
                self.assertEqual(run.call_args.args[:2], (f"{runtime}.exe", update_args))
                self.assertEqual(run.call_args.kwargs["response_timeout"], 5)
                self.assertEqual(result["mode"], "official-foreground")

    def test_emergency_switch_does_not_invoke_an_updater(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"LINK16_AGENT_UPDATE": "0"}):
            profile = SimpleNamespace(name="kp", runtime="kimi", home_path=Path(temp))
            with patch.object(agent_update.agent_runtime, "profile_spec", return_value=profile), \
                    patch.object(agent_update.subprocess, "Popen") as popen:
                result = agent_update.ensure("kp")
            self.assertEqual(result["status"], "disabled")
            popen.assert_not_called()


class UpdateResponseGateTests(unittest.TestCase):
    def run_command(self, script, response_timeout=0.3, on_response=None, **kwargs):
        return agent_update._run_update_command(
            sys.executable, ("-c", script), env=os.environ.copy(),
            response_timeout=response_timeout, on_response=on_response, **kwargs,
        )

    def test_no_response_is_abandoned_after_the_initial_timeout(self):
        on_response = Mock()
        started = time.monotonic()
        result = self.run_command("import time; time.sleep(30)", on_response=on_response)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result["status"], "no_response")
        self.assertIsNone(result["return_code"])
        self.assertIsNone(result["response_elapsed_sec"])
        on_response.assert_not_called()

    def test_banner_does_not_remove_the_inactivity_deadline(self):
        on_response = Mock()
        result = self.run_command(
            "import time; print('downloading', flush=True); time.sleep(30)",
            response_timeout=.5, on_response=on_response,
        )
        self.assertEqual(result["status"], "stalled")
        self.assertIsNone(result["return_code"])
        self.assertIsNotNone(result["response_elapsed_sec"])
        on_response.assert_called_once()

    def test_stderr_banner_also_cannot_hold_the_queue(self):
        result = self.run_command(
            "import sys, time; print('downloading', file=sys.stderr, flush=True); time.sleep(30)",
            response_timeout=.5,
        )
        self.assertEqual(result["status"], "stalled")
        self.assertIsNotNone(result["response_elapsed_sec"])

    def test_silent_file_growth_continues_beyond_the_inactivity_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = ("import time; from pathlib import Path; "
                      f"p=Path({tmp!r})/'download'; "
                      "[(p.write_bytes(b'x'*(i+1)),time.sleep(.1)) for i in range(12)]")
            started = time.monotonic()
            result = self.run_command(script, response_timeout=.5, progress_dir=tmp)
        self.assertGreater(time.monotonic() - started, 1)
        self.assertIsNone(result["status"])
        self.assertEqual(result["return_code"], 0)

    def test_cancellation_ends_the_entire_owned_installer_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_pid = Path(tmp) / 'child'
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                      f"Path({str(child_pid)!r}).write_text(str(p.pid)); time.sleep(30)")
            result = self.run_command(script, response_timeout=5, cancelled=child_pid.exists)
            import codex_startup
            pid = int(child_pid.read_text())
            deadline = time.monotonic() + 2
            while codex_startup.process_alive(pid) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(codex_startup.process_alive(pid))
        self.assertEqual(result['status'], 'cancelled')

    @unittest.skipUnless(os.name == 'nt', 'Windows official installer staging')
    def test_installer_child_staging_writes_count_as_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = ("import os,time; from pathlib import Path; "
                     f"p=Path({tmp!r})/('.staging.fixture.'+str(os.getpid())); p.mkdir(); "
                     "[( (p/'archive').write_bytes(b'x'*(i+1)),time.sleep(.1)) for i in range(12)]")
            script = ("import subprocess,sys; "
                      f"subprocess.run([sys.executable,'-c',{child!r}])")
            result = self.run_command(script, response_timeout=.5, staging_root=tmp)
        self.assertIsNone(result['status'])
        self.assertEqual(result['return_code'], 0)

    def test_repeated_banner_does_not_masquerade_as_progress(self):
        result = self.run_command("import time; [(print('Checking...',flush=True),time.sleep(.05)) for _ in range(100)]",
                                  response_timeout=.5)
        self.assertEqual(result['status'], 'stalled')

    def test_advancing_stdout_progress_and_fast_failure(self):
        result = self.run_command("import time; [(print(str(i)+'%',flush=True),time.sleep(.1)) for i in range(12)]",
                                  response_timeout=.5)
        self.assertEqual(result['return_code'], 0)
        self.assertIsNone(result['status'])
        started = time.monotonic()
        result = self.run_command('raise SystemExit(7)', response_timeout=5)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result['return_code'], 7)

    def test_fast_exit_without_output_does_not_wait_for_the_response_timeout(self):
        started = time.monotonic()
        result = self.run_command("pass", response_timeout=5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(result["status"])
        self.assertEqual(result["return_code"], 0)
        self.assertIsNone(result["response_elapsed_sec"])


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
        completed = {"status": None, "return_code": 0, "response_elapsed_sec": 0.01}
        with patch.object(agent_update, "_version", side_effect=["1.0.0", "1.1.0"]), \
                patch.object(agent_update, "_codex_latest", return_value="1.1.0"), \
                patch.object(agent_update, "_run_update_command", return_value=completed) as run:
            result = self.ensure()
        self.assertEqual(result["status"], "updated")
        self.assertEqual((result["before"], result["after"]), ("1.0.0", "1.1.0"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[:2], ("codex.exe", ("update",)))
        self.assertNotIn("stdout", json.dumps(result))
        self.assertEqual(run.call_args.kwargs["env"]["PATH"].split(os.pathsep)[0],
                         str(Path(os.environ["SystemRoot"]) / "System32"))
        config = (self.root / "config.toml").read_text(encoding="utf-8")
        self.assertIn("check_for_update_on_startup = false", config)

    def test_current_failed_and_no_response_all_return_without_blocking_launch(self):
        cases = [
            ({"status": None, "return_code": 0, "response_elapsed_sec": 0.01},
             ["2.0.0", "2.0.0"], "unverified"),
            ({"status": None, "return_code": 1, "response_elapsed_sec": 0.01},
             ["2.0.0", "2.0.0"], "failed"),
            ({"status": "no_response", "return_code": None, "response_elapsed_sec": None},
             ["2.0.0"], "no_response"),
        ]
        for completed, versions, status in cases:
            with self.subTest(status=status), \
                    patch.object(agent_update, "_version", side_effect=versions), \
                    patch.object(agent_update, "_codex_latest", return_value="2.1.0"), \
                    patch.object(agent_update, "_run_update_command", return_value=completed):
                result = self.ensure()
            self.assertEqual(result["status"], status)

    def test_current_version_skips_installer(self):
        with patch.object(agent_update, '_version', return_value='2.0.0'), \
                patch.object(agent_update, '_codex_latest', return_value='2.0.0'), \
                patch.object(agent_update, '_run_update_command') as run:
            result = self.ensure()
        self.assertEqual(result['status'], 'current')
        run.assert_not_called()

    def test_unavailable_check_is_not_reported_as_latest(self):
        with patch.object(agent_update, '_version', return_value='2.0.0'), \
                patch.object(agent_update, '_codex_latest', return_value=None), \
                patch.object(agent_update, '_run_update_command') as run:
            result = self.ensure()
        self.assertEqual(result['status'], 'check_failed')
        self.assertNotIn('最新版', agent_update.status_text(result))
        run.assert_not_called()

    def test_concurrent_updater_does_not_run_twice(self):
        with patch.object(agent_update, "_centralize_codex_updates"), \
                patch.object(agent_update.bridge_injection, "ProcessFileLock",
                             side_effect=TimeoutError("busy")), \
                patch.object(agent_update.subprocess, "Popen") as popen:
            result = self.ensure()
        self.assertEqual(result["status"], "busy")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
