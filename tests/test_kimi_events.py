import json
import sys
import tempfile
import unittest
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
        self.assertIn("ETA 14:30", plan["label"])
        self.assertTrue(all(r["runtime"] == "kimi" for r in outputs))

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
        self.assertEqual(first.poll(), 1)
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

    def test_native_seed_requires_success_and_one_typed_session_hint(self):
        good = [{"role": "assistant", "content": "LINK16_KIMI_NATIVE_READY"},
                {"role": "meta", "type": "session.resume_hint", "session_id": "session_test"}]
        with patch.object(worker.agent_runtime, "standalone_worker_cmd", return_value="native"), \
                patch.object(worker.agent_runtime, "resolve_shell", return_value="shell"):
            for code, rows in [(0, good), (1, good), (0, good + good[-1:]), (0, good[:1]),
                               (0, [{**good[0], "content": "wrong"}, good[1]])]:
                result = SimpleNamespace(returncode=code, stdout="\n".join(map(json.dumps, rows)))
                with self.subTest(code=code, rows=rows), patch.object(worker.subprocess, "run", return_value=result):
                    if code == 0 and rows == good:
                        self.assertEqual(worker.new_session("kp", "."), "session_test")
                    else:
                        with self.assertRaises(RuntimeError):
                            worker.new_session("kp", ".")

    def test_exact_binding_resumes_and_handoff_rejects_closed_or_wrong_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wire = root / "sessions/wd_test_0123456789ab/session_test/agents/main/wire.jsonl"
            wire.parent.mkdir(parents=True)
            wire.write_text(json.dumps(fixture()[0]) + "\n", encoding="utf-8")
            spec = SimpleNamespace(runtime="kimi", home_path=root)
            with patch.object(worker.agent_runtime, "profile_spec", return_value=spec), \
                    patch.object(worker, "new_session", return_value="session_test") as create:
                binding, actual = worker.start_or_resume("test", "kp", root, root)
                self.assertEqual(actual, wire)
                self.assertEqual(binding["initial_offset"], wire.stat().st_size)
                worker.start_or_resume("test", "kp", root, root)
                self.assertEqual(create.call_count, 1)
                source = worker.handoff_source("test", "kp", root, root)
                self.assertEqual(source["session_id"], "session_test")
                self.assertEqual(source["transcript_runtime"], "kimi")
                with self.assertRaises(ValueError):
                    worker.handoff_source("test", "kp", root / "other", root)
                pointer = root / "bridge-kimi-thread-test.json"
                pointer.write_text(json.dumps({**binding, "closed": True}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    worker.handoff_source("test", "kp", root, root)
                worker.start_or_resume("test", "kp", root, root)
                self.assertEqual(create.call_count, 2)


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
