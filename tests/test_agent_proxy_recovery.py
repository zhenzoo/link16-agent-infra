"""Local proxy recovery must be bounded, verified, and independent of a model."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import network_route as nr
import bridge_process
import wmux_session


class AgentProxyRecoveryTests(unittest.TestCase):
    EXPECTED = "http://127.0.0.1:7897"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = self.root / "CATVPN.exe"
        self.client.write_bytes(b"fixture")
        self.port = 7890
        self.rows = [
            {"ProcessId": 10, "Name": "CATVPN.exe", "ExecutablePath": str(self.client), "ListenPorts": []},
            {"ProcessId": 11, "ParentProcessId": 10, "Name": "CATVPNCore.exe", "CreationDate": "one",
             "ListenPorts": [7890, 8426]},
        ]

        def request(core, method="GET", payload=None):
            if method == "PATCH":
                self.port = payload["mixed-port"]
                return {}
            return {"mixed-port": self.port, "allow-lan": False}

        self.patches = [
            patch.object(nr, "os", SimpleNamespace(name="nt")),
            patch.object(bridge_process, "LOCK_DIR", self.root),
            patch.object(nr, "_proxy_processes", side_effect=lambda: self.rows),
            patch.object(nr, "_core_config", side_effect=lambda row: {"url": "http://127.0.0.1:8426", "secret": "hidden"}
                         if row["Name"] == "CATVPNCore.exe" else None),
            patch.object(nr, "_core_request", side_effect=request),
            patch.object(nr, "_listening", side_effect=lambda url: url.endswith(":" + str(self.port))),
            patch.object(nr, "probe_route", side_effect=lambda *a, **k: nr.ProbeResult("proxy", True, 10, 200)),
            patch.object(nr, "_sync_system_proxy"),
            patch.object(wmux_session, "_windows_shutting_down", return_value=False),
            patch.object(nr.subprocess, "STARTUPINFO", create=True,
                         side_effect=lambda: SimpleNamespace(dwFlags=0, wShowWindow=0)),
            patch.object(nr.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(nr.subprocess, "Popen"),
        ]
        mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.query, self.request, self.probe, self.sync = mocks[2], mocks[4], mocks[6], mocks[7]
        self.shutdown, self.launch = mocks[8], mocks[11]

    def run_repair(self, apply=True):
        return nr.ensure_agent_proxy(apply=apply, expected=self.EXPECTED)

    def saved(self):
        return json.loads((self.root / "agent-proxy-recovery.json").read_text(encoding="utf-8"))

    def test_dry_run_does_not_mutate_port_or_launch(self):
        self.assertEqual(self.run_repair(False)["status"], "would_align")
        self.assertEqual(self.port, 7890)
        self.assertFalse((self.root / "agent-proxy-recovery.json").exists())
        self.sync.assert_not_called()
        self.launch.assert_not_called()

    def test_verified_hot_port_repair_keeps_client_alive_and_secret_out_of_state(self):
        self.assertEqual(self.run_repair()["status"], "recovered")
        self.assertEqual(self.port, 7897)
        self.sync.assert_called_once_with(7890, 7897)
        self.launch.assert_not_called()
        self.assertEqual(self.saved()["client"], str(self.client))
        self.assertNotIn("hidden", json.dumps(self.saved()))

    def test_same_core_cannot_be_changed_repeatedly_even_after_success(self):
        self.run_repair()
        self.port = 7890
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "停止重复"):
            self.run_repair()
        self.assertEqual(self.port, 7890)

    def test_failed_exit_verification_rolls_back_once_and_stops(self):
        self.probe.side_effect = lambda url, route, proxy, **kw: nr.ProbeResult("proxy", proxy.endswith(":7890"), 10)
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "回退"):
            self.run_repair()
        self.assertEqual(self.port, 7890)
        self.sync.assert_not_called()
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "停止重复"):
            self.run_repair()

    def test_broken_exit_is_not_misdiagnosed_as_port_mismatch(self):
        self.probe.side_effect = lambda *a, **k: nr.ProbeResult("proxy", False, 10)
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "出口验证失败"):
            self.run_repair()
        self.assertEqual(self.port, 7890)
        self.assertFalse((self.root / "agent-proxy-recovery.json").exists())

    def test_absent_verified_client_launches_only_once_until_port_recovers(self):
        self.port = 7897
        self.rows[1]["ListenPorts"] = [7897, 8426]
        self.assertEqual(self.run_repair()["status"], "listening")
        self.port, self.rows = 0, []
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "发起一次启动"):
            self.run_repair()
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "停止自动启动"):
            self.run_repair()
        self.launch.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0], [str(self.client)])
        self.assertEqual(self.launch.call_args.kwargs["startupinfo"].wShowWindow, 0)

    def test_unknown_or_multiple_cores_do_not_change_anything(self):
        self.query.side_effect = RuntimeError("inventory unavailable")
        with self.assertRaises(RuntimeError):
            self.run_repair()
        self.query.side_effect = lambda: self.rows
        self.rows.append({**self.rows[1], "ProcessId": 12})
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "无法唯一"):
            self.run_repair()
        self.assertEqual(self.port, 7890)
        self.launch.assert_not_called()

    def test_running_gui_without_core_is_not_restarted(self):
        self.rows = self.rows[:1]
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "无法唯一"):
            self.run_repair()
        self.launch.assert_not_called()

    def test_shutdown_does_not_change_port(self):
        self.shutdown.return_value = True
        with self.assertRaisesRegex(nr.AgentProxyUnavailable, "正在关机"):
            self.run_repair()
        self.assertEqual(self.port, 7890)

    def test_nonlocal_or_authenticated_proxy_is_not_rewritten(self):
        for proxy in ["http://corp.example:8080", "http://user:password@127.0.0.1:7897"]:
            self.assertEqual(nr.ensure_agent_proxy(apply=True, expected=proxy)["status"], "unmanaged")
        self.query.assert_not_called()

    def test_healthy_new_client_replaces_old_launch_target(self):
        self.port = 7897
        self.rows[1]["ListenPorts"] = [7897, 8426]
        self.run_repair()
        replacement = self.root / "clash-verge.exe"
        replacement.write_bytes(b"fixture")
        self.rows[0].update(Name="clash-verge.exe", ExecutablePath=str(replacement))
        with patch.object(nr.time, "time", return_value=self.saved()["observed_at"] + 301):
            self.run_repair()
        self.assertEqual(self.saved()["client"], str(replacement))
        self.rows = []
        with patch.object(nr.time, "time", return_value=self.saved()["observed_at"] + 301):
            self.run_repair()
        self.assertNotIn("client", self.saved())

    def test_registry_proxy_not_hidden_by_stale_terminal_environment(self):
        with patch.object(nr.urllib.request, "getproxies", return_value={"https": self.EXPECTED}), \
             patch.object(nr.urllib.request, "getproxies_registry", create=True,
                          return_value={"https": "http://127.0.0.1:7890"}):
            rows = nr.discover_proxy_candidates(self.EXPECTED, common_ports=())
        self.assertEqual([r.url for r in rows], [self.EXPECTED, "http://127.0.0.1:7890"])


if __name__ == "__main__":
    unittest.main()
