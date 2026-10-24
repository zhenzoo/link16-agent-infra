"""Absent WMUX recovery, including concurrent bots and unsafe-to-replay failures."""
from concurrent.futures import ThreadPoolExecutor
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_process
import windows_bootstrap
import wmux_session as ws
import bridge_inbox

UNAVAILABLE = RuntimeError("wmux-rpc rpc workspace.list failed: ERR: connect ECONNREFUSED 127.0.0.1:5912")


class WmuxRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.patches = [
            patch.object(ws, "os", SimpleNamespace(name="nt")),
            patch.object(bridge_process, "LOCK_DIR", self.root),
            patch.object(ws, "_windows_shutting_down", return_value=False),
            patch.object(ws, "_desktop_wmux_state", return_value={"session": 1, "running": False}),
            patch.object(windows_bootstrap, "wmux_executable", return_value=self.root / "wmux.exe"),
            patch.object(ws.subprocess, "STARTUPINFO", create=True,
                         side_effect=lambda: SimpleNamespace(dwFlags=0, wShowWindow=0)),
            patch.object(ws.subprocess, "STARTF_USESHOWWINDOW", 1, create=True),
            patch.object(ws.subprocess, "Popen"),
        ]
        mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.shutdown, self.state, self.exe, self.launch = mocks[2], mocks[3], mocks[4], mocks[7]

    def test_missing_process_launches_once_and_does_not_claim_delivery(self):
        result = ws.recover_transport(UNAVAILABLE)
        self.assertIn("已发起启动", result)
        self.assertNotIn("已送达", result)
        self.launch.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0], [str(self.root / "wmux.exe")])
        self.assertEqual(self.launch.call_args.kwargs["startupinfo"].wShowWindow, 0)
        self.assertIn("避免重复", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_called_once()

    def test_concurrent_bots_share_one_launch(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(ws.recover_transport, [UNAVAILABLE] * 6))
        self.assertEqual(sum("已发起启动" in result for result in results), 1)
        self.launch.assert_called_once()

    def test_launch_budget_stops_after_two_attempts_until_real_handoff(self):
        state = self.root / "wmux-recovery.json"
        state.write_text(json.dumps({"at": 0, "attempts": 2}), encoding="utf-8")
        self.assertIn("停止自动启动", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_not_called()
        ws.mark_transport_recovered()
        self.assertIn("已发起启动", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_called_once()

    def test_running_but_unreachable_process_is_not_restarted(self):
        self.state.return_value = {"session": 1, "running": True}
        self.assertIn("已在运行", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_not_called()

    def test_unknown_process_state_does_not_launch(self):
        self.state.side_effect = RuntimeError("query failed")
        self.assertIn("尚未完成", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_not_called()

    def test_shutdown_and_noninteractive_session_do_not_launch(self):
        self.shutdown.return_value = True
        self.assertIn("正在关机", ws.recover_transport(UNAVAILABLE))
        self.shutdown.return_value = False
        self.state.return_value = {"session": 0, "running": False}
        self.assertIn("没有交互式桌面", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_not_called()

    def test_missing_executable_remains_pending(self):
        self.exe.return_value = None
        self.assertIn("未找到", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_not_called()

    def test_launch_failure_remains_pending_and_is_throttled(self):
        self.launch.side_effect = OSError("launch failed")
        self.assertIn("尚未完成", ws.recover_transport(UNAVAILABLE))
        self.assertIn("避免重复", ws.recover_transport(UNAVAILABLE))
        self.launch.assert_called_once()

    def test_timeout_auth_and_network_failures_never_launch_wmux(self):
        for error in ["wmux-rpc RPC timeout", "wmux-rpc closed before response",
                      "wmux plugin is unconfirmed", "HTTPS ECONNREFUSED", "login required"]:
            with self.subTest(error=error):
                self.assertIsNone(ws.recover_transport(RuntimeError(error)))
        self.state.assert_not_called()
        self.launch.assert_not_called()

    def test_nonwindows_does_not_try_windows_launcher(self):
        with patch.object(ws, "os", SimpleNamespace(name="posix")):
            self.assertIn("等待重试", ws.recover_transport(UNAVAILABLE))
        self.state.assert_not_called()
        self.launch.assert_not_called()


class InboundRecoveryBoundaryTests(unittest.TestCase):
    def dispatch(self, row):
        # Run the actual nested dispatcher with a controlled channel/inbox. No
        # real bot connection, terminal, or GUI is touched by these probes.
        source = Path(ws.__file__).with_name("feishu_bridge.py").read_text(encoding="utf-8")
        node = next(n for n in ast.walk(ast.parse(source))
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == "_dispatch")
        recover = Mock(return_value="检测到 WMUX 未运行，已发起启动；待终端就绪后自动继续原消息。")
        card = AsyncMock()
        context = dict(
            asyncio=asyncio, bridge_inbox=bridge_inbox,
            wmux_session=SimpleNamespace(recover_transport=recover),
            control=Mock(), durable_inbox=Mock(get=Mock(return_value=row)),
            ch=SimpleNamespace(dispatch_saved=AsyncMock(side_effect=UNAVAILABLE)),
            card_send=card, bname="unit", blog=Mock(), inbound_handler=Mock(),
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), "dispatcher", "exec"), context)
        payload = {"message": {"message_id": "om_unit", "chat_id": "oc_unit"}}
        with self.assertRaisesRegex(RuntimeError, "ECONNREFUSED"):
            asyncio.run(context["_dispatch"](payload, 0))
        return recover, card

    def test_preparation_failure_repairs_then_preserves_retry_exception(self):
        recover, card = self.dispatch({"state": "processing", "attempts": 1})
        recover.assert_called_once_with(UNAVAILABLE)
        self.assertIn("尚未送达终端", card.call_args.args[2])
        self.assertIn("已发起启动", card.call_args.args[2])

    def test_submission_uncertainty_never_launches_or_replays(self):
        for state in ("submitting", "uncertain", "awaiting_confirmation"):
            with self.subTest(state=state):
                recover, card = self.dispatch({"state": state, "attempts": 1})
                recover.assert_not_called()
                self.assertIn("不重复提交", card.call_args.args[2])

    def test_later_retry_checks_recovery_without_repeating_notice(self):
        recover, card = self.dispatch({"state": "processing", "attempts": 7})
        recover.assert_called_once()
        card.assert_not_called()


if __name__ == "__main__":
    unittest.main()
