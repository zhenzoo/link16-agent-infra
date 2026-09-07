import json
import pathlib
import queue
import sys
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import codex_app_server_worker as worker  # noqa: E402
from codex_app_server_worker import MilestoneObserver  # noqa: E402


class _FakeRpc:
    def __init__(self, messages):
        self.notifications = queue.Queue()
        for message in messages:
            self.notifications.put(message)


class AppServerFinalDeliveryTests(unittest.TestCase):
    def test_app_server_child_gets_root_even_with_missing_or_stale_parent_variable(self):
        import os
        for inherited in ({}, {"LINK16_AGENT_INFRA_ROOT": "/stale/root"}):
            with self.subTest(inherited=inherited), mock.patch.dict(os.environ, inherited, clear=True):
                env = worker.worker_environment("test", "/selected/home", "/business/state")
            self.assertEqual(env["LINK16_AGENT_INFRA_ROOT"], ROOT.as_posix())
            self.assertEqual(env["FEISHU_BRIDGE_SESSION"], "test")
            self.assertEqual(env["CODEX_HOME"], str(Path("/selected/home")))

    def test_typed_final_is_written_as_answer_without_stop_hook(self):
        final = {
            "method": "item/completed",
            "params": {
                "threadId": "root",
                "turnId": "turn-1",
                "item": {
                    "id": "final-1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "任务完成。",
                },
            },
        }
        # Replay the same notification once: reconnect/replay must not double-send.
        messages = [
            final,
            json.loads(json.dumps(final)),
            {"method": "_transport_error", "params": {"message": "test end"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_owner"}
            (state / "bridge-turn-route-test-bot.json").write_text(
                json.dumps(route), encoding="utf-8"
            )
            observer = MilestoneObserver(
                _FakeRpc(messages),
                bot="test-bot",
                root_thread="root",
                state_dir=state,
                workspace_root=ROOT,
            )

            observer._run()

            records = [
                json.loads(line)
                for line in (state / "bridge-outbox-test-bot.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["kind"], "answer")
            self.assertEqual(records[0]["session"], "root")
            self.assertEqual(records[0]["anchor"], "turn-1")
            self.assertEqual(records[0]["route"], route)
            self.assertIn("任务完成。", records[0]["text"])
            self.assertIn("✅ 已完成", records[0]["text"])


class LoopbackTransportTests(unittest.TestCase):
    """对端是本机 app-server 的连接，永远不许经过代理。

    websockets 默认 proxy=True（读 HTTPS_PROXY），而本机把 HTTPS_PROXY 设在【用户级】，
    于是桥起的 worker 也继承 —— 一条本该走 loopback 的 ws 被第三方代理进程转发，
    结果就是 2026-08-29 那次「观察者断线」：loopback 不会无缘无故断，闲置的代理隧道会。
    """

    def test_rpc_connection_never_goes_through_a_proxy(self):
        import websockets.sync.client as ws_client

        captured = {}

        class _FakeWs:
            def __iter__(self):
                return iter(())

            def close(self):
                pass

        def _fake_connect(url, **kwargs):
            captured.update(kwargs)
            captured["url"] = url
            return _FakeWs()

        original = ws_client.connect
        ws_client.connect = _fake_connect
        try:
            worker.RpcConnection("ws://127.0.0.1:5588")
        finally:
            ws_client.connect = original
        self.assertIn("proxy", captured)
        self.assertIsNone(captured["proxy"])


class ObserverSlotTests(unittest.TestCase):
    """速记员席位 = 硬闸。两个速记员同时抄同一条 thread → 同一条 final 写两遍 outbox →
    主人收到重复消息。所以第二个必须【拒绝启动】，而不是启动后再发现撞车。"""

    def test_second_observer_is_refused_while_first_holds_the_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = worker.acquire_observer_slot(tmp, "bot-a")
            try:
                with self.assertRaises(worker.ObserverSlotTaken):
                    worker.acquire_observer_slot(tmp, "bot-a")
            finally:
                first.release()

    def test_slot_frees_itself_after_the_holder_lets_go(self):
        """进程崩了/退了席位要自动腾出 —— 否则每次异常都留一个要人工清理的残留。"""
        with tempfile.TemporaryDirectory() as tmp:
            worker.acquire_observer_slot(tmp, "bot-a").release()
            again = worker.acquire_observer_slot(tmp, "bot-a")
            again.release()

    def test_slots_are_per_bot(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = worker.acquire_observer_slot(tmp, "bot-a")
            b = worker.acquire_observer_slot(tmp, "bot-b")
            a.release()
            b.release()

    def test_observe_refuses_to_start_and_writes_nothing_when_slot_is_taken(self):
        """拒绝发生在【连 app-server 之前】：url 给一个根本没人听的端口，它仍必须立刻返回，
        且一个字节都没写 —— 这就是「拿不到席位就不启动」与「启动后再发现」的机械差别。"""
        with tempfile.TemporaryDirectory() as tmp:
            held = worker.acquire_observer_slot(tmp, "bot-a")
            try:
                args = worker.build_parser().parse_args([
                    "observe", "--bot", "bot-a", "--cwd", tmp, "--state-dir", tmp,
                    "--url", "ws://127.0.0.1:1", "--thread", "t1",
                ])
                self.assertEqual(worker.observe(args), worker.EXIT_SLOT_TAKEN)
            finally:
                held.release()
            self.assertFalse((pathlib.Path(tmp) / "bridge-outbox-bot-a.jsonl").exists())
            self.assertFalse((pathlib.Path(tmp) / "bridge-event-ledger-bot-a.jsonl").exists())


class ObserverSupervisorTests(unittest.TestCase):
    """『杀掉速记员 = 升级』这条路的机械保证：子进程非预期退出要被拉回来（新进程从磁盘重读代码），
    但席位被占那种退出必须罢手，否则就是无限重启刷屏。"""

    @staticmethod
    def _counting_command(marker, code):
        return [sys.executable, "-c",
                f"open({str(marker)!r}, 'a', encoding='utf-8').write('x'); raise SystemExit({code})"]

    def test_unexpected_exit_is_respawned(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = pathlib.Path(tmp) / "spawns.txt"
            stop = threading.Event()
            thread = threading.Thread(
                target=worker._run_observer_child,
                args=(self._counting_command(marker, 1), None,
                      pathlib.Path(tmp) / "observer.log", stop, {}, 0.01),
                daemon=True,
            )
            thread.start()
            deadline = time.time() + 20
            while time.time() < deadline:
                if marker.exists() and len(marker.read_text(encoding="utf-8")) >= 2:
                    break
                time.sleep(0.05)
            stop.set()
            thread.join(timeout=10)
            self.assertGreaterEqual(len(marker.read_text(encoding="utf-8")), 2)

    def test_slot_taken_exit_stops_the_supervisor(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = pathlib.Path(tmp) / "spawns.txt"
            stop = threading.Event()
            worker._run_observer_child(
                self._counting_command(marker, worker.EXIT_SLOT_TAKEN), None,
                pathlib.Path(tmp) / "observer.log", stop, {}, 0.01,
            )
            self.assertEqual(len(marker.read_text(encoding="utf-8")), 1)


class ObserverCommandTests(unittest.TestCase):
    def test_command_round_trips_through_the_real_parser(self):
        """速记员怎么起只有 observer_command 说了算；这里锁死它拼出来的命令能被本模块自己解析回来。"""
        command = worker.observer_command(
            bot="bot-a", url="ws://127.0.0.1:5588", thread_id="t1",
            cwd="D:/repo", state_dir="D:/state",
        )
        self.assertEqual(pathlib.Path(command[2]).name, "codex_app_server_worker.py")
        args = worker.build_parser().parse_args(command[3:])
        self.assertEqual(args.mode, "observe")
        self.assertEqual(args.bot, "bot-a")
        self.assertEqual(args.url, "ws://127.0.0.1:5588")
        self.assertEqual(args.thread, "t1")

    def test_worker_invocation_without_mode_still_means_run(self):
        """agent_runtime 拼的那条老命令（纯 flag、无位置参数）语义不能变。"""
        args = worker.build_parser().parse_args(
            ["--bot", "bot-a", "--cwd", "D:/repo", "--state-dir", "D:/state"]
        )
        self.assertEqual(args.mode, "run")


if __name__ == "__main__":
    unittest.main()
