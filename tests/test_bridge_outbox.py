import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_outbox  # noqa: E402
from bridge_events import MilestoneAccumulator, normalize_codex_notification  # noqa: E402


SECURITY_FIXTURE = ROOT / "tests" / "fixtures" / "plan916" / "codex_security_surfaces.json"
SECURITY_MARKERS = (
    "PLAN916_RAW_COMMAND_X", "PLAN916_RAW_OUTPUT_X", "PLAN916_RAW_STDOUT_X",
    "PLAN916_RAW_STDERR_X", "PLAN916_RAW_ARGUMENT_X", "PLAN916_RAW_ACTION_X",
    "PLAN916_RAW_QUERY_X", "PLAN916_RAW_DIFF_X", "PLAN916_OUTSIDE_PATH_X",
    "PLAN916_SECRET_PATH_X", "PLAN916_SECRET_TOOL_X",
)


def security_progress_record():
    messages = json.loads(SECURITY_FIXTURE.read_text(encoding="utf-8"))
    serialized = json.dumps(messages)
    serialized = serialized.replace("__WORKSPACE__", str(ROOT).replace("\\", "/"))
    serialized = serialized.replace("__OUTSIDE__", str(ROOT.parent / "plan916-outside").replace("\\", "/"))
    acc = MilestoneAccumulator()
    for message in json.loads(serialized):
        event = normalize_codex_notification(message, "root", workspace_root=ROOT)
        if event:
            acc.apply(event)
    return acc.progress_record(session="root")


def fresh_state():
    return {
        "turn": None,
        "steps": [],
        "usage": {},
        "seg_start": 0,
        "cur_mid": None,
        "flushed": 0,
        "last_flush": 0,
        "sent": set(),
        "picker_active": False,
    }


class FakeCards:
    def __init__(self):
        self.new = []
        self.edits = []
        self.edit_ok = True

    async def new_card(self, text, route=None, purpose="answer", fragment=None):
        self.new.append((text, route))
        return f"m{len(self.new)}"

    async def edit_card(self, mid, text):
        self.edits.append((mid, text))
        return self.edit_ok

    async def send_plain(self, text, route=None, purpose="answer", fragment=None):
        return True


class BridgeOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, records, state, cards):
        return await bridge_outbox.drain_batch(
            records,
            new_card=cards.new_card,
            edit_card=cards.edit_card,
            send_plain=cards.send_plain,
            state=state,
            coalesce_sec=0,
            clock=lambda: 100,
            force_flush=True,
        )

    async def test_legacy_edit_failure_continues_with_only_unseen_delta(self):
        state, cards = fresh_state(), FakeCards()
        await self.drain([{"kind": "progress", "turn": "t", "steps": [{"kind": "tool", "label": "OLD"}]}], state, cards)
        cards.edit_ok = False
        await self.drain([{"kind": "progress", "turn": "t", "steps": [
            {"kind": "tool", "label": "OLD"}, {"kind": "tool", "label": "NEW"}
        ]}], state, cards)
        replacement = cards.new[-1][0]
        self.assertIn("NEW", replacement)
        self.assertNotIn("OLD", replacement)

    async def test_milestone_revision_patches_same_card(self):
        state, cards = fresh_state(), FakeCards()
        first = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 1, "kind": "plan", "label": "🔄 A"}
        ]}
        second = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 2, "kind": "plan", "label": "✅ A"}
        ]}
        await self.drain([first], state, cards)
        await self.drain([second], state, cards)
        self.assertEqual(len(cards.new), 1)
        self.assertEqual(len(cards.edits), 1)
        self.assertIn("✅ A", cards.edits[0][1])

    async def test_milestone_edit_failure_sends_dirty_event_only(self):
        state, cards = fresh_state(), FakeCards()
        first = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "OLD COMMENTARY"}
        ]}
        second = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "OLD COMMENTARY"},
            {"event_id": "c2", "revision": 1, "kind": "commentary", "label": "NEW COMMENTARY"},
        ]}
        await self.drain([first], state, cards)
        cards.edit_ok = False
        await self.drain([second], state, cards)
        replacement = cards.new[-1][0]
        self.assertIn("NEW COMMENTARY", replacement)
        self.assertNotIn("OLD COMMENTARY", replacement)

    async def test_milestone_header_uses_plan_progress_and_actual_tool_calls(self):
        state, cards = fresh_state(), FakeCards()
        record = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 1, "kind": "plan", "label": "📋 current plan",
             "plan_completed": 2, "plan_total": 3},
            {"event_id": "tools:a", "revision": 2, "kind": "tool",
             "tool_count": 2, "label": "🔧 **工具活动 · 2 次**\n类型：搜索 rg ×2"},
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "💬 checkpoint"},
            {"event_id": "tools:b", "revision": 6, "kind": "tool",
             "tool_count": 6, "label": "🔧 **工具活动 · 6 次**\n修改：无"},
        ]}
        await self.drain([record], state, cards)
        text = cards.new[0][0]
        self.assertIn("🤖 **进行中** · 计划 2/3 · 工具 8 次", text)
        self.assertIn("类型：搜索 rg ×2", text)
        self.assertIn("修改：无", text)
        self.assertNotIn("里程碑", text)
        self.assertNotIn("工具段", text)

    async def test_milestone_header_without_plan_sums_segments_not_segment_count(self):
        state, cards = fresh_state(), FakeCards()
        record = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "tools:a", "revision": 2, "kind": "tool", "tool_count": 2, "label": "A"},
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "checkpoint"},
            {"event_id": "tools:b", "revision": 4, "kind": "tool", "tool_count": 4, "label": "B"},
        ]}
        await self.drain([record], state, cards)
        text = cards.new[0][0]
        self.assertIn("🤖 **进行中** · 工具 6 次", text)
        self.assertNotIn("计划", text)

    async def test_edit_failure_keeps_full_snapshot_header_but_dirty_body_only(self):
        state, cards = fresh_state(), FakeCards()
        first = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 1, "kind": "plan", "label": "OLD PLAN",
             "plan_completed": 1, "plan_total": 2},
            {"event_id": "tools:a", "revision": 1, "kind": "tool", "tool_count": 1, "label": "OLD TOOL"},
        ]}
        second = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            *first["steps"],
            {"event_id": "tools:b", "revision": 3, "kind": "tool", "tool_count": 3, "label": "NEW TOOL"},
        ]}
        await self.drain([first], state, cards)
        cards.edit_ok = False
        await self.drain([second], state, cards)
        replacement = cards.new[-1][0]
        self.assertIn("计划 1/2 · 工具 4 次", replacement)
        self.assertIn("NEW TOOL", replacement)
        self.assertNotIn("OLD PLAN", replacement)
        self.assertNotIn("OLD TOOL", replacement)

    async def test_legacy_progress_header_is_unchanged(self):
        state, cards = fresh_state(), FakeCards()
        await self.drain([{"kind": "progress", "turn": "t", "steps": [
            {"kind": "tool", "label": "legacy tool"},
            {"kind": "thinking", "label": "legacy thinking"},
        ]}], state, cards)
        text = cards.new[0][0]
        self.assertIn("🤖 **进行中** · 🔧1 💭1", text)
        self.assertNotIn("计划", text)

    async def test_safe_producer_record_stays_safe_in_card_and_restart_state(self):
        state, cards = fresh_state(), FakeCards()
        record = security_progress_record()
        await self.drain([record], state, cards)
        with tempfile.TemporaryDirectory() as tmp:
            bridge_outbox.save_progress_state(tmp, "bot", state)
            state_path = bridge_outbox.progress_state_path(tmp, "bot")
            state_text = state_path.read_text(encoding="utf-8")
            restored = bridge_outbox.load_progress_state(tmp, "bot")
        surfaces = {
            "outbox": json.dumps(record, ensure_ascii=False),
            "card": "\n".join(text for text, _route in cards.new) + "\n" + "\n".join(text for _mid, text in cards.edits),
            "progress_state": state_text,
            "restored": json.dumps(restored, ensure_ascii=False),
        }
        for surface in surfaces.values():
            for marker in SECURITY_MARKERS:
                self.assertNotIn(marker, surface)
            self.assertNotIn(str(ROOT), surface)
            self.assertNotIn(str(ROOT.parent / "plan916-outside"), surface)
        self.assertIn("工具 4 次", surfaces["card"])
        self.assertIn("feishu/bridge_events.py", surfaces["card"])
        self.assertIn("修改：", surfaces["card"])
        self.assertIn("新增：", surfaces["card"])

    def test_milestone_delivery_cursor_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = fresh_state()
            state.update({
                "v2_turn": "t",
                "v2_steps": [{"event_id": "c1", "revision": 1, "label": "hello"}],
                "v2_mid": "message-id",
                "v2_card_ids": ["c1"],
                "v2_acked": {"c1": 1},
                "v2_route": {"kind": "p2a"},
            })
            bridge_outbox.save_progress_state(tmp, "bot", state)
            restored = bridge_outbox.load_progress_state(tmp, "bot")
        self.assertEqual(restored["v2_mid"], "message-id")
        self.assertEqual(restored["v2_acked"], {"c1": 1})
        self.assertEqual(restored["v2_route"], {"kind": "p2a"})

    def test_answer_fragments_are_stable_bounded_and_lossless(self):
        route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_user"}
        record = {"session": "s", "anchor": "a"}
        samples = [
            "a" * 2799,
            "中" * 2800,
            "🙂" * 2801,
            ("第一段\n" * 900) + "结尾",
            "```text\n" + ("x" * 6000) + "\n```",
        ]
        for text in samples:
            with self.subTest(size=len(text)):
                first = bridge_outbox._answer_fragments(record, text, route)
                second = bridge_outbox._answer_fragments(record, text, route)
                self.assertEqual(first, second)
                self.assertEqual("".join(row["content"] for row in first), text)
                self.assertTrue(all(len(row["rendered"]) <= bridge_outbox.CARD_BUDGET for row in first))
                self.assertEqual(len({row["fragment_id"] for row in first}), len(first))
                if len(first) > 1:
                    self.assertTrue(all(
                        row["rendered"].startswith(f"**回复 {row['part']}/{row['total']}**")
                        for row in first
                    ))

    async def test_fragment_ack_survives_restart_and_only_missing_parts_retry(self):
        class FragmentCards:
            def __init__(self, fail_parts=()):
                self.fail_parts = set(fail_parts)
                self.calls = []

            async def new_card(self, text, route=None, purpose="answer", fragment=None):
                self.calls.append(("card", fragment["part"], fragment["fragment_id"], text))
                return None if fragment["part"] in self.fail_parts else f"m{fragment['part']}"

            async def edit_card(self, _mid, _text):
                return True

            async def send_plain(self, text, route=None, purpose="answer", fragment=None):
                self.calls.append(("plain", fragment["part"], fragment["fragment_id"], text))
                return False if fragment["part"] in self.fail_parts else f"t{fragment['part']}"

        with tempfile.TemporaryDirectory() as tmp:
            text = "段落\n" * 1700
            record = {"kind": "answer", "session": "s", "anchor": "a", "text": text,
                      "route": {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_user"}}
            first_state = fresh_state()
            first_state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
            first = FragmentCards({2})
            with self.assertRaises(bridge_outbox.RetrySend):
                await bridge_outbox.drain_batch(
                    [record], new_card=first.new_card, edit_card=first.edit_card,
                    send_plain=first.send_plain, state=first_state, coalesce_sec=0,
                    clock=lambda: 100, force_flush=True,
                    persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
                )
            self.assertEqual([part for kind, part, _fid, _text in first.calls if kind == "card"], [1, 2])

            second_state = fresh_state()
            second_state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
            second = FragmentCards()
            await bridge_outbox.drain_batch(
                [record], new_card=second.new_card, edit_card=second.edit_card,
                send_plain=second.send_plain, state=second_state, coalesce_sec=0,
                clock=lambda: 101, force_flush=True,
                persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
            )
            self.assertNotIn(1, [part for _kind, part, _fid, _text in second.calls])
            self.assertEqual(
                [row["fragment_id"] for row in bridge_outbox._answer_fragments(
                    record, text.strip(), record["route"]
                )][1],
                second.calls[0][2],
            )

    def test_same_text_on_different_routes_has_different_answer_identity(self):
        record = {"session": "s", "anchor": "a"}
        text = "完成"
        dm = bridge_outbox._answer_fragments(record, text, {"kind": "p2a"})[0]["answer_id"]
        group = bridge_outbox._answer_fragments(
            record, text, {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_user"}
        )[0]["answer_id"]
        self.assertNotEqual(dm, group)

    def test_malformed_answer_state_root_is_empty_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = bridge_outbox.answer_state_path(tmp, "bot")
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(
                bridge_outbox.load_answer_state(tmp, "bot"),
                {"version": 1, "answers": {}},
            )


if __name__ == "__main__":
    unittest.main()
