import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import codex_startup as startup
import codex_app_server_worker as worker
import feishu_bridge as bridge
from websockets.sync.server import serve
from websockets.sync.client import connect


class ReadyContractTests(unittest.TestCase):
    def record(self):
        return dict(contract=startup.CONTRACT, bot="bot", startup_id="attempt", ts=20,
                    stage="ready", source="thread/start", thread_id="root", worker_pid=os.getpid(),
                    tui_pid=os.getpid(), observer_pid=os.getpid())

    def test_ready_requires_attempt_source_thread_and_live_processes(self):
        record = self.record()
        self.assertTrue(startup.ready(record, bot="bot", startup_id="attempt"))
        for key, value in (("startup_id", "old"), ("bot", "other"), ("contract", "v1"),
                           ("source", "process_spawned"), ("thread_id", ""), ("stage", "connecting"),
                           ("worker_pid", 0), ("tui_pid", 0), ("observer_pid", 0)):
            with self.subTest(key=key):
                self.assertFalse(startup.ready({**record, key: value}, bot="bot", startup_id="attempt"))
        self.assertFalse(startup.ready(record, bot="bot", since=21))
        self.assertFalse(startup.ready({"worker_pid": os.getpid(), "ts": 999}, bot="bot"))

    def test_structured_failure_returns_immediately_without_reading_or_reinjecting(self):
        bot = {"name": "bot", "agent": "codex", "_startup_id": "attempt"}
        record = {**self.record(), "stage": "failed", "detail": "thread/resume denied"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bridge, "STATE_DIR", Path(tmp)), \
                mock.patch.object(bridge, "read_screen", return_value="shell") as screen, \
                mock.patch.object(bridge, "wmux") as send:
            startup.atomic_write_json(startup.state_path(tmp, "bot"), record)
            start = time.monotonic()
            self.assertFalse(bridge._finish_worker_startup(bot, "ws", "pty", tmp))
            self.assertLess(time.monotonic() - start, 1)
            send.assert_not_called()
            self.assertIn("thread/resume denied", bridge._load_startup_failure("bot")["reason"])

    def test_ready_never_reads_or_requires_terminal_words(self):
        bot = {"name": "bot", "agent": "codex", "_startup_id": "attempt"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bridge, "STATE_DIR", Path(tmp)), \
                mock.patch.object(bridge, "read_screen", side_effect=AssertionError("must not read screen")):
            startup.atomic_write_json(startup.state_path(tmp, "bot", "ready"), self.record())
            self.assertTrue(bridge._wait_agent_ready(bot, "pty", timeout=.2))

    def test_fresh_session_never_starts_a_model_turn_or_reads_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            rpc = mock.Mock()
            self.assertIsNone(worker._start_or_resume_thread(rpc, state_dir=Path(tmp), bot="bot", cwd=Path(tmp)))
            rpc.request.assert_not_called()

    def test_resume_failure_is_not_silently_replaced_with_an_empty_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            worker._thread_state_path(state, "bot").write_text('{"thread_id":"existing"}')
            rpc = mock.Mock()
            rpc.request.side_effect = [{"config": {}}, RuntimeError("thread/resume: access denied")]
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                worker._start_or_resume_thread(rpc, state_dir=state, bot="bot", cwd=state)
            self.assertEqual([x.args[0] for x in rpc.request.call_args_list], ["config/read", "thread/resume"])

    def test_tui_loopback_ignores_proxy_without_changing_backend_environment(self):
        env = {"http_proxy": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:2",
               "ALL_PROXY": "socks5://127.0.0.1:3", "NO_PROXY": "internal.test", "CODEX_HOME": "home"}
        original = env.copy()
        tui = worker.tui_environment(env)
        self.assertEqual(env, original)
        self.assertFalse(any(k.lower() in {"http_proxy", "https_proxy", "all_proxy"} for k in tui))
        self.assertIn("internal.test", tui["NO_PROXY"])
        self.assertIn("127.0.0.1", tui["NO_PROXY"])
        self.assertEqual(tui["CODEX_HOME"], "home")

    def test_known_server_exit_does_not_wait_for_listen_timeout(self):
        server = mock.Mock(returncode=7)
        server.poll.return_value = 7
        with mock.patch.object(worker, "RpcConnection") as connect_rpc:
            with self.assertRaisesRegex(RuntimeError, "退出码 7"):
                worker._wait_rpc("ws://127.0.0.1:1", server=server)
            connect_rpc.assert_not_called()

    def test_disconnected_rpc_wakes_pending_request_immediately(self):
        def upstream(ws):
            ws.recv()
            ws.close()
        with serve(upstream, "127.0.0.1", 0) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            rpc = worker.RpcConnection(f"ws://127.0.0.1:{server.socket.getsockname()[1]}")
            try:
                started = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, "initialize"):
                    rpc.request("initialize", {}, timeout=30)
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(rpc.pending)
            finally:
                rpc.close()


class GatewayTests(unittest.TestCase):
    def test_mirror_rejects_other_threads_and_private_raw_fields(self):
        gateway = startup.TuiGateway("unused", mock.Mock())
        gateway._set_session({"id": "root", "cwd": str(Path.cwd())})
        gateway._mirror({"method": "item/completed", "params": {"threadId": "child", "turnId": "turn",
                         "item": {"type": "agentMessage", "phase": "final_answer", "text": "child answer"}}})
        gateway._mirror({"method": "item/reasoning/textDelta", "params": {"threadId": "root", "delta": "private"}})
        self.assertFalse(gateway.events)
        gateway._mirror({"method": "item/completed", "params": {"threadId": "root", "turnId": "turn",
                         "privateExtra": "PRIVATE RAW FIELD",
                         "item": {"id": "f", "type": "agentMessage", "phase": "final_answer", "text": "answer"}}})
        self.assertEqual(len(gateway.events), 1)
        self.assertNotIn("PRIVATE RAW FIELD", json.dumps(list(gateway.events)))

    def test_actual_tui_frames_and_final_are_forwarded_without_a_warmup_turn(self):
        calls = []
        final = {"method": "item/completed", "params": {"threadId": "root", "turnId": "turn",
                  "item": {"type": "agentMessage", "id": "final", "phase": "final_answer", "text": "verified"}}}
        def upstream(ws):
            for raw in ws:
                msg = json.loads(raw)
                calls.append(msg)
                if msg.get("method") == "thread/start":
                    ws.send(json.dumps({"id": msg["id"], "result": {"thread": {"id": "root", "cwd": str(Path.cwd())}}}))
                elif msg.get("method") == "turn/start":
                    ws.send(json.dumps({"id": msg["id"], "result": {"turn": {"id": "turn"}}}))
                    ws.send(json.dumps(final))
        with serve(upstream, "127.0.0.1", 0, max_size=None) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            bound = []
            gateway = startup.TuiGateway(f"ws://127.0.0.1:{server.socket.getsockname()[1]}",
                                          lambda method, thread: bound.append((method, thread["id"])))
            url = gateway.start()
            try:
                with connect(url + "/events", proxy=None, max_size=None) as observer, connect(url, proxy=None, max_size=None) as tui:
                    self.assertEqual(json.loads(observer.recv(timeout=2))["method"], "_link16/listening")
                    tui.send(json.dumps({"id": 10, "method": "thread/start", "params": {}}))
                    self.assertEqual(json.loads(tui.recv(timeout=2))["id"], 10)
                    self.assertEqual(json.loads(observer.recv(timeout=2))["method"], "_link16/session")
                    self.assertTrue(gateway.attached.wait(1))
                    self.assertEqual([c["method"] for c in calls], ["thread/start"])
                    # Larger than the old websocket frame limit. Forward intact.
                    text = "x" * (1024 * 1024 + 1)
                    tui.send(json.dumps({"id": 11, "method": "turn/start", "params": {"input": text}}))
                    self.assertEqual(json.loads(tui.recv(timeout=2))["id"], 11)
                    self.assertEqual(json.loads(tui.recv(timeout=2)), final)
                    event = json.loads(observer.recv(timeout=2))
                    self.assertEqual(event["params"]["event"]["event_type"], "final")
                    self.assertEqual(calls[-1]["params"]["input"], text)
                    # Disconnect without ack: replacement must receive the final.
                with connect(url + "/events", proxy=None) as replacement:
                    replacement.recv(timeout=3)
                    replacement.recv(timeout=3)
                    replay = json.loads(replacement.recv(timeout=3))
                    self.assertEqual(replay, event)
                    replacement.send(json.dumps({"method": "_link16/ack", "params": {"seq": event["params"]["seq"]}}))
                self.assertEqual(bound, [("thread/start", "root")])
            finally:
                gateway.close()

    def test_missing_thread_fields_fail_instead_of_publishing_ready(self):
        def upstream(ws):
            msg = json.loads(ws.recv())
            ws.send(json.dumps({"id": msg["id"], "result": {"thread": {"id": "root"}}}))
        with serve(upstream, "127.0.0.1", 0) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            gateway = startup.TuiGateway(f"ws://127.0.0.1:{server.socket.getsockname()[1]}", mock.Mock())
            try:
                with connect(gateway.start(), proxy=None) as tui:
                    tui.send(json.dumps({"id": 1, "method": "thread/start", "params": {}}))
                    self.assertTrue(gateway.failed.wait(2))
                    self.assertFalse(gateway.attached.is_set())
                    gateway.before_session.assert_not_called()
            finally:
                gateway.close()


if __name__ == "__main__":
    unittest.main()
