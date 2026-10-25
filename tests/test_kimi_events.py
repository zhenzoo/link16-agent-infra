import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
from kimi_events import KimiEvents
import kimi_native_worker as worker
from kimi_native_worker import WireObserver, ensure_workspace_trust
import turn_delivery_guard
import bridge_outbox


def row(kind, **kwargs):
    return {"type": kind, "agentId": "main", "time": 100000, **kwargs}


def loop(kind, uuid, step="one", **kwargs):
    return row("context.append_loop_event", event={
        "type": kind, "uuid": uuid, "turnId": 0, "stepUuid": step, **kwargs,
    })


def text(uuid, content, step="one"):
    return loop("content.part", uuid, step, part={"type": "text", "text": content})


def fixture():
    return [
        {"type": "metadata", "protocol_version": "1.5", "created_at": 1},
        row("turn.prompt", input=[{"type": "text", "text": "start [飞书 route=p2a-ext dest=oc_group at=ou_owner]"}]),
        loop("content.part", "thought", part={"type": "think", "think": "PRIVATE_THOUGHT"}),
        text("comment", "🔄 Stage 1｜检查文件（ETA 14:30）"),
        loop("tool.call", "tool", name="Bash", args={"command": "PRIVATE_COMMAND"}),
        row("context.append_loop_event", event={"type": "tool.result", "parentUuid": "tool",
                                                 "result": {"output": "PRIVATE_OUTPUT"}}),
        loop("tool.call", "plan", "two", name="TodoList", args={"private": "PRIVATE_ARG"}),
        row("tools.update_store", key="todo", value=[
                 {"title": "Stage 1｜检查文件（实际 14:29）", "status": "done"},
                 {"title": "Stage 2｜核对结果（ETA 14:30）", "status": "in_progress"},
             ]),
        text("answer", "✅ 检查完成。", "three"),
        row("turn.ended", turnId=0, reason="completed"),
    ]


class KimiEventsTests(unittest.TestCase):
    def test_native_events_keep_public_plan_and_final_without_private_payload(self):
        reducer = KimiEvents("session_test", Path.cwd())
        outputs = []
        for i, item in enumerate(fixture()):
            outputs.extend(reducer.consume(item, i))
        encoded = json.dumps(outputs, ensure_ascii=False)
        self.assertNotIn("PRIVATE", encoded)
        final = outputs[-1]
        self.assertEqual(final["text"], "✅ 检查完成。\n\n---\n✅ 已完成")
        self.assertEqual(final["route"]["dest"], "oc_group")
        self.assertNotIn("检查文件", final["text"])
        plan = next(s for s in outputs[-2]["steps"] if s["kind"] == "plan")
        self.assertEqual((plan["plan_completed"], plan["plan_total"]), (1, 2))
        self.assertEqual(
            "📋 当前计划\n"
            "✅ Stage 1｜检查文件（实际 14:29）\n"
            "🔄 Stage 2｜核对结果（ETA 14:30）",
            plan["label"],
        )
        self.assertTrue(all(r["runtime"] == "kimi" for r in outputs))
        self.assertNotIn("✅ 检查完成。", json.dumps([r for r in outputs if r["kind"] == "progress"], ensure_ascii=False))

    def test_text_is_progress_only_after_its_step_calls_a_tool(self):
        reducer = KimiEvents("session_test", Path.cwd())
        reducer.consume(fixture()[0], 0)
        reducer.consume(fixture()[1], 1)
        self.assertEqual(reducer.consume(text("c", "阶段说明"), 2), [])
        progress = reducer.consume(loop("tool.call", "t", name="Read", args={}), 3)
        self.assertIn("阶段说明", json.dumps(progress, ensure_ascii=False))
        self.assertEqual(reducer.consume(text("f", "FINAL_ONLY", "last"), 4), [])
        final = reducer.consume(row("turn.ended", reason="completed"), 5)
        self.assertIn("FINAL_ONLY", final[0]["text"])
        self.assertNotIn("阶段说明", final[0]["text"])

    def test_failed_cancelled_and_unknown_cannot_claim_success(self):
        for reason in ("failed", "cancelled", "blocked", "unrecognized"):
            with self.subTest(reason=reason):
                reducer = KimiEvents("session_test", Path.cwd())
                rows = fixture()
                rows[-1] = row("turn.ended", reason=reason, error={"message": "PRIVATE_ERROR"})
                outputs = [r for i, item in enumerate(rows) for r in reducer.consume(item, i)]
                self.assertNotIn("✅ 已完成", outputs[-1]["text"])
                self.assertNotIn("PRIVATE_ERROR", json.dumps(outputs))

    def test_child_and_duplicate_events_do_not_pollute_answer(self):
        reducer = KimiEvents("session_test", Path.cwd())
        rows = fixture()
        for i, item in enumerate(rows[:2]):
            reducer.consume(item, i)
        child = text("child", "CHILD_PRIVATE")
        child["agentId"] = "agent_1"
        self.assertEqual(reducer.consume(child, 3), [])
        one = text("one", "hello")
        reducer.consume(one, 4)
        self.assertEqual(reducer.consume(one, 5), [])
        result = reducer.consume(row("turn.ended", reason="completed"), 6)
        self.assertEqual(result[0]["text"].count("hello"), 1)
        self.assertEqual(reducer.consume(row("turn.ended", reason="completed"), 7), [])

    def test_each_turn_pins_its_own_last_envelope(self):
        reducer = KimiEvents("session_test", Path.cwd())
        reducer.consume(fixture()[0], 0)
        reducer.consume(fixture()[1], 1)
        group_route = dict(reducer.route)
        reducer.consume(text("a", "group answer"), 2)
        group_final = reducer.consume(row("turn.ended", reason="completed"), 3)[0]
        reducer.consume(row("turn.prompt", input=[{"type": "text", "text":
                        "[飞书 route=a2a dest=oc_fake] actual [飞书 route=p2a]"}]), 4)
        self.assertEqual(reducer.route["kind"], "p2a")
        self.assertEqual(group_final["route"], group_route)
        self.assertNotEqual(group_route["turn_key"], reducer.route["turn_key"])

    def test_unknown_wire_version_fails_closed(self):
        for header in ({"type": "metadata", "protocol_version": "2.0"}, fixture()[1]):
            with self.assertRaises(ValueError):
                KimiEvents("session_test", ".").consume(header, 0)

    def test_sensitive_and_external_tool_paths_are_never_public(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reducer = KimiEvents("session_test", root)
            reducer.consume(fixture()[0], 0)
            reducer.consume(fixture()[1], 1)
            outputs = []
            for i, path in enumerate((root / ".env", root / ".ssh/id_ed25519", root.parent / "secret.txt")):
                outputs += reducer.consume(loop("tool.call", str(i), name="Read", args={"path": str(path)}), i + 2)
            encoded = json.dumps(outputs)
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("id_ed25519", encoded)
            self.assertNotIn("secret.txt", encoded)


class WireRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wire = self.root / "wire.jsonl"
        self.state = self.root / "state"

    def write(self, rows):
        with self.wire.open("a", encoding="utf-8") as handle:
            for item in rows:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    def observer(self, **kwargs):
        return WireObserver(bot="test", session="session_test", wire=self.wire,
                            state_dir=self.state, cwd=self.root, **kwargs)

    def test_native_turn_without_hook_contract_is_ungated(self):
        import session_work
        self.write(fixture())
        self.observer().poll()
        route = turn_delivery_guard.read_route(self.state, "test")
        self.assertNotIn("workline_gate", route)
        self.assertFalse(session_work.gate_path("test", self.state).exists())
        first = json.loads(self.state.joinpath("bridge-outbox-test.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertNotIn("workline_gate", first["route"])

    def test_native_turn_with_hook_contract_reuses_pending_gate(self):
        import bridge_inbox
        import session_work
        prompt = fixture()[1]["input"][0]["text"]
        session_work.begin_turn(
            "test", "hook-turn", session="session_test", runtime="kimi",
            prompt_digest=bridge_inbox.prompt_digest(prompt), state_dir=self.state,
        )
        self.write(fixture())
        self.observer().poll()
        route = turn_delivery_guard.read_route(self.state, "test")
        self.assertEqual(route["turn_key"], "hook-turn")
        self.assertEqual(route["workline_gate"], session_work.GATE_CONTRACT)

    def test_restart_midturn_reconstructs_final_and_never_resends_history(self):
        self.write(fixture()[:-2])
        first = self.observer()
        first.poll()
        self.write(fixture()[-2:])
        second = self.observer()
        second.poll()
        path = self.state / "bridge-outbox-test.jsonl"
        contents = path.read_text(encoding="utf-8")
        self.assertEqual(sum(json.loads(s)["kind"] == "answer" for s in contents.splitlines()), 1)
        third = self.observer()
        self.assertEqual(third.poll(), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), contents)

    def test_outbox_append_before_cursor_crash_window_deduplicates(self):
        self.write(fixture())
        first = self.observer()
        first.poll()
        path = self.state / "bridge-outbox-test.jsonl"
        contents = path.read_text(encoding="utf-8")
        first.checkpoint.write_text(json.dumps({"session": "session_test", "offset": 0}), encoding="utf-8")
        self.assertEqual(self.observer().poll(), 0)
        self.assertEqual(path.read_text(encoding="utf-8"), contents)

    def test_partial_line_waits_and_truncation_is_an_error(self):
        self.write(fixture()[:2])
        first = self.observer()
        first.poll()
        part = json.dumps(text("a", "完整中文"), ensure_ascii=False).encode("utf-8")
        with self.wire.open("ab") as handle:
            handle.write(part[:15])
        self.assertEqual(first.poll(), 0)
        with self.wire.open("ab") as handle:
            handle.write(part[15:] + b"\n")
        self.assertEqual(first.poll(), 0)  # Text is pending until a tool identifies commentary.
        self.write([loop("tool.call", "t", name="Read", args={})])
        self.assertEqual(first.poll(), 2)
        self.wire.write_bytes(b"{}\n")
        with self.assertRaises(ValueError):
            first.poll()

    def test_seed_history_is_not_published(self):
        self.write(fixture())
        observer = self.observer(initial_offset=self.wire.stat().st_size)
        self.assertEqual(observer.poll(), 0)
        self.assertFalse(observer.outbox.exists())

    def test_terminal_clears_only_its_own_active_turn(self):
        self.write(fixture()[:-1])
        observer = self.observer()
        observer.poll()
        self.assertTrue(turn_delivery_guard.read_route(self.state, "test")["active"])
        turn_delivery_guard.activate(self.state, "test", {"kind": "p2a"}, turn_key="newer")
        self.write(fixture()[-1:])
        observer.poll()
        self.assertTrue(turn_delivery_guard.read_route(self.state, "test")["active"])
        self.assertEqual(turn_delivery_guard.read_route(self.state, "test")["turn_key"], "newer")

    def test_cancelled_turn_becomes_deliverable_failure_not_permanent_pending(self):
        rows = fixture()
        rows[-1] = row("turn.ended", turnId=0, reason="cancelled")
        self.write(rows)
        observer = self.observer()
        observer.poll()
        answer = next(json.loads(line) for line in observer.outbox.read_text(encoding="utf-8").splitlines()
                      if json.loads(line)["kind"] == "answer")
        turn_key = worker.session_work.record_turn_key(answer)
        gate = worker.session_work._load_gate("test", self.state)["turns"][turn_key]
        self.assertEqual(gate["status"], "failed-ready")
        self.assertIsInstance(worker.session_work.delivery_work("test", answer, self.state), dict)

    def test_failure_is_safe_pinned_and_idempotent_across_restart(self):
        self.write(fixture()[:4])
        observer = self.observer()
        observer.poll()
        observer.report_failure()
        observer.report_failure()
        restarted = self.observer()
        restarted.poll()
        restarted.report_failure()
        reports = [json.loads(s) for s in observer.outbox.read_text(encoding="utf-8").splitlines()
                   if "observer-failed" in json.loads(s)["source_event_id"]]
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["route"]["dest"], "oc_group")
        self.assertEqual(reports[0]["kind"], "progress")
        self.assertTrue(turn_delivery_guard.read_route(self.state, "test")["active"])
        restarted.report_failure(terminal=True)
        self.assertFalse(turn_delivery_guard.read_route(self.state, "test")["active"])

    def test_failure_without_a_new_turn_does_not_send_seed_or_stale_route(self):
        self.write(fixture()[:4])
        observer = self.observer(initial_offset=self.wire.stat().st_size)
        observer.poll()
        observer.report_failure()
        self.assertFalse(observer.outbox.exists())

    def test_workspace_trust_does_not_overwrite_existing_record(self):
        path = ensure_workspace_trust(self.root / "home", self.root / "workspace")
        before = path.read_bytes()
        self.assertEqual(path, ensure_workspace_trust(self.root / "home", self.root / "workspace"))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(path.parent.glob("wd_*"))), 1)


class NativeBindingTests(unittest.TestCase):
    def test_observer_failure_restarts_without_stopping_native(self):
        native = Mock()
        native.poll.side_effect = [None, None, None, 0]
        first, second = Mock(), Mock()
        first.poll.return_value = 1
        second.poll.return_value = None
        with patch.object(worker.subprocess, "Popen", side_effect=[first, second]) as launch, \
                patch.object(worker.time, "sleep"), \
                patch.object(worker.time, "monotonic", side_effect=[0, 0, 10, 10]):
            worker.supervise(native, ["observer"], None)
        self.assertEqual(launch.call_count, 2)
        native.terminate.assert_not_called()
        second.terminate.assert_called_once()

    def test_lazy_binding_starts_observer_only_after_session_exists(self):
        native = Mock()
        native.poll.side_effect = [None, None, None, 0]
        child = Mock()
        child.poll.return_value = None
        with patch.object(worker.subprocess, "Popen", return_value=child) as launch, \
                patch.object(worker.time, "sleep"), \
                patch.object(worker.time, "monotonic", side_effect=[0, 1, 2]):
            worker.supervise(native, ["observer"], None,
                             bind=Mock(side_effect=[False, True]))
        launch.assert_called_once()
        child.terminate.assert_called_once()

    def test_native_exit_without_lazy_session_never_starts_observer(self):
        native = Mock()
        native.poll.side_effect = [None, 0]
        with patch.object(worker.subprocess, "Popen") as launch, \
                patch.object(worker.time, "sleep"), \
                patch.object(worker.time, "monotonic", return_value=0):
            worker.supervise(native, ["observer"], None, bind=Mock(return_value=False))
        launch.assert_not_called()

    def test_native_session_listing_does_not_make_a_model_request(self):
        result = SimpleNamespace(returncode=0, stdout=json.dumps([
            {"id": "session_test"}, {"id": "session_other"},
        ]))
        with patch.object(worker.agent_runtime, "standalone_worker_cmd", return_value="native") as command, \
                patch.object(worker.agent_runtime, "resolve_shell", return_value="shell"), \
                patch.object(worker.subprocess, "run", return_value=result):
            self.assertEqual(worker.native_session_ids("kp", "."),
                             {"session_test", "session_other"})
        provider_args = command.call_args.kwargs["provider_args"]
        self.assertEqual(provider_args[:2], ["session", "list"])
        self.assertNotIn("-p", provider_args)

    def test_new_native_binding_requires_one_new_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wire = root / "sessions/wd_test_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text("{}\n", encoding="utf-8")
            profile = SimpleNamespace(name="kp", home_path=root)
            with patch.object(worker, "native_session_ids", return_value=set()):
                self.assertIsNone(worker.bind_new_session("test", profile, root, root, set()))
            with patch.object(worker, "native_session_ids", return_value={"session_test"}):
                binding, actual = worker.bind_new_session("test", profile, root, root, set())
            self.assertEqual(actual, wire)
            self.assertEqual(binding["session"], "session_test")
            self.assertEqual(binding["initial_offset"], 0)
            with patch.object(worker, "native_session_ids",
                              return_value={"session_test", "session_other"}):
                with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                    worker.bind_new_session("other", profile, root, root, set())

    def test_worker_persists_sanitized_failure_before_native_tui_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(bot="test", cwd=str(root), state_dir=str(root))
            profile = SimpleNamespace(name="kp", home_path=root)
            with patch.dict(os.environ, {worker.agent_runtime.PROFILE_ENV: "kp"}), \
                    patch.object(worker.agent_runtime, "profile_from_env", return_value=profile), \
                    patch.object(worker.bridge_injection, "ProcessFileLock",
                                 return_value=nullcontext()), \
                    patch.object(worker, "start_or_resume",
                                 side_effect=RuntimeError("Kimi native session listing failed; secret output")):
                with self.assertRaises(RuntimeError):
                    worker.run(args)
            record = json.loads(
                (root / "bridge-kimi-ready-test.json").read_text(encoding="utf-8"))
            self.assertEqual(record["contract"], worker.agent_runtime.KIMI_STARTUP_CONTRACT)
            self.assertEqual(record["stage"], "failed")
            self.assertIn("原生会话未就位", record["detail"])
            self.assertNotIn("secret output", record["detail"])

    def test_native_exit_restarts_same_session_and_resets_after_stable_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cwd = root / "repo"
            cwd.mkdir()
            wire = root / "sessions/wd_repo_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text(json.dumps(fixture()[0]) + "\n", encoding="utf-8")
            binding = {"session": "session_test", "profile": "kp", "cwd": str(cwd),
                       "initial_offset": wire.stat().st_size}
            args = SimpleNamespace(bot="test", cwd=str(cwd), state_dir=str(root))
            profile = SimpleNamespace(name="kp", home_path=root)
            natives = [Mock(pid=456 + i, returncode=0) for i in range(5)]
            observer = Mock()
            observer.reducer.closed = True
            clock = {"now": 0.0}
            uptimes = [0, 0, 301, 0, 0]

            def native_runtime(*_args, **_kwargs):
                clock["now"] += uptimes.pop(0)

            with patch.dict(os.environ, {worker.agent_runtime.PROFILE_ENV: "kp"}), \
                    patch.object(worker.agent_runtime, "profile_from_env", return_value=profile), \
                    patch.object(worker.bridge_injection, "ProcessFileLock",
                                 return_value=nullcontext()), \
                    patch.object(worker, "start_or_resume", return_value=(binding, wire, set())), \
                    patch.object(worker, "ensure_workspace_trust"), \
                    patch.object(worker.agent_runtime, "standalone_worker_cmd", return_value="kimi") as command, \
                    patch.object(worker.agent_runtime, "resolve_shell", return_value="bash"), \
                    patch.object(worker.agent_runtime, "cli_version", return_value="2.3.1"), \
                    patch.object(worker.subprocess, "Popen", side_effect=natives) as launch, \
                    patch.object(worker, "supervise", side_effect=native_runtime), \
                    patch.object(worker.time, "monotonic", side_effect=lambda: clock["now"]), \
                    patch.object(worker.time, "sleep"), \
                    patch.object(worker, "WireObserver", return_value=observer):
                self.assertEqual(worker.run(args), 1)
            self.assertEqual(launch.call_count, 5)
            self.assertTrue(all(call.kwargs["provider_args"] == ["--session", "session_test"]
                                for call in command.call_args_list))
            self.assertEqual(launch.call_args_list[0].kwargs["stderr"].name,
                             str(root / "bridge-kimi-native-test.log"))
            rows = [json.loads(line) for line in
                    (root / "bridge-kimi-exits-test.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["exit_code"] for row in rows], [0, 0, 0, 0, 0])
            self.assertEqual([row["attempt"] for row in rows], [1, 2, 1, 2, 3])
            self.assertEqual([row["outcome"] for row in rows],
                             ["restart_scheduled"] * 4 + ["crash_loop_stopped"])
            self.assertFalse(any(row["recovery"] for row in rows))

    def test_native_exit_without_lazy_session_restarts_without_observer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(bot="test", cwd=str(root), state_dir=str(root))
            profile = SimpleNamespace(name="kp", home_path=root)
            natives = [Mock(pid=456 + i, returncode=0) for i in range(3)]
            with patch.dict(os.environ, {worker.agent_runtime.PROFILE_ENV: "kp"}), \
                    patch.object(worker.agent_runtime, "profile_from_env", return_value=profile), \
                    patch.object(worker.bridge_injection, "ProcessFileLock",
                                 return_value=nullcontext()), \
                    patch.object(worker, "start_or_resume", return_value=(None, None, set())), \
                    patch.object(worker, "ensure_workspace_trust"), \
                    patch.object(worker.agent_runtime, "standalone_worker_cmd",
                                 return_value="kimi") as command, \
                    patch.object(worker.agent_runtime, "resolve_shell", return_value="bash"), \
                    patch.object(worker.agent_runtime, "cli_version", return_value="2.3.1"), \
                    patch.object(worker.subprocess, "Popen", side_effect=natives), \
                    patch.object(worker, "supervise") as supervise, \
                    patch.object(worker.time, "sleep"), \
                    patch.object(worker, "WireObserver") as observer:
                self.assertEqual(worker.run(args), 1)
            self.assertEqual(command.call_count, 3)
            self.assertTrue(all(call.kwargs["extra_env"]["KIMI_CODE_WATCH"] == "0"
                                for call in command.call_args_list))
            self.assertTrue(all(call.kwargs["bind"] is not None for call in supervise.call_args_list))
            observer.assert_not_called()

    def test_active_native_exit_recovery_prompt_forbids_tool_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cwd = root / "repo"
            cwd.mkdir()
            wire = root / "sessions/wd_repo_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text(json.dumps(fixture()[0]) + "\n", encoding="utf-8")
            binding = {"session": "session_test", "profile": "kp", "cwd": str(cwd),
                       "initial_offset": wire.stat().st_size}
            args = SimpleNamespace(bot="test", cwd=str(cwd), state_dir=str(root))
            profile = SimpleNamespace(name="kp", home_path=root)
            first, second, third = (Mock(pid=456 + i, returncode=0) for i in range(3))
            observer = Mock()
            observer.reducer.closed = False
            observer.reducer.turn = "turn-old"
            observer.reducer.route = {
                "kind": "a2a", "dest": "oc_group", "at": "ou_peer", "peer": "peer-bot",
                "mid": "om_old", "turn_key": "turn-old",
            }
            with patch.dict(os.environ, {worker.agent_runtime.PROFILE_ENV: "kp"}), \
                    patch.object(worker.agent_runtime, "profile_from_env", return_value=profile), \
                    patch.object(worker.bridge_injection, "ProcessFileLock",
                                 return_value=nullcontext()), \
                    patch.object(worker, "start_or_resume", return_value=(binding, wire, set())), \
                    patch.object(worker, "ensure_workspace_trust"), \
                    patch.object(worker.agent_runtime, "standalone_worker_cmd", return_value="kimi"), \
                    patch.object(worker.agent_runtime, "resolve_shell", return_value="bash"), \
                    patch.object(worker.agent_runtime, "cli_version", return_value="2.3.1"), \
                    patch.object(worker.subprocess, "Popen", side_effect=[first, second, third]), \
                    patch.object(worker, "supervise"), \
                    patch.object(worker.time, "sleep"), \
                    patch.object(worker.bridge_injection, "inject_bot_prompt",
                                 side_effect=[{"ok": True}, {"ok": False, "error": "not ready"}]) as inject, \
                    patch.object(worker, "WireObserver", return_value=observer):
                self.assertEqual(worker.run(args), 1)
            self.assertEqual(inject.call_count, 2)
            prompts = [call.args[1] for call in inject.call_args_list]
            self.assertNotEqual(prompts[0], prompts[1])
            self.assertIn("不要重放最后一个工具调用或命令", prompts[0])
            self.assertIn("只读检查", prompts[0])
            self.assertIn("exit=0", prompts[0])
            self.assertIn("route=a2a", prompts[0])
            self.assertIn("dest=oc_group", prompts[0])
            self.assertTrue(all(call.kwargs["wait_ready"] for call in inject.call_args_list))
            third.terminate.assert_not_called()
            observer.report_failure.assert_called_with(terminal=True)

    def test_recovery_injection_requires_native_ready_before_paste(self):
        bot = {"name": "test", "agent": "kimi"}
        with patch("feishu_bridge.load_bots", return_value=[bot]), \
                patch("feishu_bridge.ensure_session", return_value=("ws", "pty", False, None)), \
                patch("feishu_bridge._wait_agent_ready", return_value=False) as ready, \
                patch("feishu_bridge._inject") as inject:
            result = worker.bridge_injection.inject_bot_prompt("test", "recovery", wait_ready=True)
        self.assertFalse(result["ok"])
        self.assertIn("未就绪", result["error"])
        ready.assert_called_once_with(bot, "pty", "ws", since=0, require_screen_change=True)
        inject.assert_not_called()

    def test_recovery_ready_rejects_stale_screen_and_dead_native(self):
        import feishu_bridge as fb
        bot = {"name": "test", "agent": "kimi", "profile": "kp"}
        old_screen = "│ > │"
        record = {
            "stage": "ready", "native_pid": 123,
            "previous_screen_sha256": worker.hashlib.sha256(old_screen.encode()).hexdigest(),
        }
        with patch.object(fb, "_kimi_startup_record", return_value=record), \
                patch.object(fb, "_kimi_ready_signal", return_value=True), \
                patch.object(fb.codex_startup, "process_alive", return_value=True), \
                patch.object(fb, "read_screen", return_value=old_screen), \
                patch.object(fb.time, "sleep"):
            self.assertFalse(fb._wait_agent_ready(
                bot, "pty", "ws", timeout=0.01, since=0, require_screen_change=True,
            ))
        with patch.object(fb, "_kimi_startup_record", return_value=record), \
                patch.object(fb, "_kimi_ready_signal", return_value=True), \
                patch.object(fb.codex_startup, "process_alive", return_value=True), \
                patch.object(fb, "read_screen", return_value=old_screen + "\nnew native"):
            self.assertTrue(fb._wait_agent_ready(
                bot, "pty", "ws", timeout=0.1, since=0, require_screen_change=True,
            ))
        with patch.object(fb, "_kimi_startup_record", return_value=record), \
                patch.object(fb.codex_startup, "process_alive", return_value=False):
            self.assertFalse(fb._wait_agent_ready(
                bot, "pty", "ws", timeout=0.1, since=0, require_screen_change=True,
            ))

    def test_real_native_process_exit_restarts_and_preserves_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cwd = root / "repo"
            cwd.mkdir()
            wire = root / "sessions/wd_repo_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text(json.dumps(fixture()[0]) + "\n", encoding="utf-8")
            binding = {"session": "session_test", "profile": "kp", "cwd": str(cwd),
                       "initial_offset": wire.stat().st_size}
            args = SimpleNamespace(bot="test", cwd=str(cwd), state_dir=str(root))
            profile = SimpleNamespace(name="kp", home_path=root)
            observer = Mock()
            observer.reducer.closed = True
            real_popen = worker.subprocess.Popen
            launches = []

            def launch_native(_argv, *, cwd, stderr):
                code = [0, 23, 0][len(launches)]
                process = real_popen([sys.executable, "-c", f"raise SystemExit({code})"],
                                     cwd=cwd, stdin=worker.subprocess.DEVNULL,
                                     stdout=worker.subprocess.DEVNULL, stderr=stderr)
                launches.append(process)
                return process

            def wait_native(native, *_args, **_kwargs):
                return native.wait(timeout=5)

            with patch.dict(os.environ, {worker.agent_runtime.PROFILE_ENV: "kp"}), \
                    patch.object(worker.agent_runtime, "profile_from_env", return_value=profile), \
                    patch.object(worker.bridge_injection, "ProcessFileLock",
                                 return_value=nullcontext()), \
                    patch.object(worker, "start_or_resume", return_value=(binding, wire, set())), \
                    patch.object(worker, "ensure_workspace_trust"), \
                    patch.object(worker.agent_runtime, "standalone_worker_cmd", return_value="kimi"), \
                    patch.object(worker.agent_runtime, "resolve_shell", return_value="bash"), \
                    patch.object(worker.agent_runtime, "cli_version", return_value="2.3.1"), \
                    patch.object(worker.subprocess, "Popen", side_effect=launch_native), \
                    patch.object(worker, "supervise", side_effect=wait_native), \
                    patch.object(worker.time, "sleep"), \
                    patch.object(worker, "WireObserver", return_value=observer):
                self.assertEqual(worker.run(args), 1)
            self.assertEqual([process.returncode for process in launches], [0, 23, 0])
            rows = [json.loads(line) for line in
                    (root / "bridge-kimi-exits-test.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["exit_code"] for row in rows], [0, 23, 0])
            self.assertEqual([row["native_pid"] for row in rows],
                             [process.pid for process in launches])

    def test_exact_binding_resumes_and_handoff_rejects_closed_or_wrong_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wire = root / "sessions/wd_test_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text(json.dumps(fixture()[0]) + "\n", encoding="utf-8")
            spec = SimpleNamespace(runtime="kimi", home_path=root)
            with patch.object(worker.agent_runtime, "profile_spec", return_value=spec), \
                    patch.object(worker, "native_session_ids", return_value=set()) as list_sessions:
                binding, actual, before = worker.start_or_resume("test", "kp", root, root)
                self.assertIsNone(binding)
                self.assertIsNone(actual)
                self.assertEqual(before, set())
                pointer = root / "bridge-kimi-thread-test.json"
                binding = {"session": "session_test", "profile": "kp", "cwd": str(root),
                           "initial_offset": wire.stat().st_size}
                pointer.write_text(json.dumps(binding), encoding="utf-8")
                binding, actual, before = worker.start_or_resume("test", "kp", root, root)
                self.assertEqual(actual, wire)
                self.assertEqual(binding["initial_offset"], wire.stat().st_size)
                self.assertEqual(list_sessions.call_count, 1)
                source = worker.handoff_source("test", "kp", root, root)
                self.assertEqual(source["session_id"], "session_test")
                self.assertEqual(source["transcript_runtime"], "kimi")
                with self.assertRaises(ValueError):
                    worker.handoff_source("test", "kp", root / "other", root)
                pointer.write_text(json.dumps({**binding, "closed": True}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    worker.handoff_source("test", "kp", root, root)
                worker.start_or_resume("test", "kp", root, root)
                self.assertEqual(list_sessions.call_count, 2)


class KimiDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_plan_reaches_shared_card_pipeline_and_final_once(self):
        reducer = KimiEvents("session_test", Path.cwd())
        outputs = [public for offset, item in enumerate(fixture())
                   for public in reducer.consume(item, offset)]
        cards, edits = [], []

        async def new_card(text, route=None, **kwargs):
            cards.append((text, route, kwargs.get("purpose")))
            return {"ok": True, "message_id": f"m{len(cards)}"}

        async def edit_card(mid, text):
            edits.append(text)
            return True

        async def send_plain(*args, **kwargs):
            self.fail("interactive owner/group delivery should use the shared card callback")

        state = {"turn": None, "steps": [], "usage": {}, "seg_start": 0, "cur_mid": None,
                 "flushed": 0, "last_flush": 0, "sent": set(), "picker_active": False}
        for public in outputs:
            await bridge_outbox.drain_batch([public], new_card=new_card, edit_card=edit_card,
                                           send_plain=send_plain, state=state, coalesce_sec=0,
                                           clock=lambda: 100, force_flush=True)
        rendered = "\n".join([c[0] for c in cards] + edits)
        self.assertIn("当前计划", rendered)
        self.assertIn("实际 14:29", rendered)
        self.assertIn("ETA 14:30", rendered)
        self.assertNotIn("PRIVATE", rendered)
        answers = [c for c in cards if c[2] == "answer"]
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0][1]["dest"], "oc_group")


def test_kimi_handoff_prompt_preserves_runtime_format():
    import bridge_watchdog
    pack = {"transcript_runtime": "kimi", "transcript": "wire.jsonl", "session_id": "session_test"}
    for prompt in (bridge_watchdog.build_handoff_prompt(pack, "cxp"), bridge_watchdog.build_align_prompt(pack)):
        assert "Kimi Wire 1.5" in prompt
        assert "turn.prompt" in prompt
        assert "wire.jsonl" in prompt


if __name__ == "__main__":
    unittest.main()
