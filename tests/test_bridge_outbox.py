import asyncio
import hashlib
import os
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


def inclusive_right_split(text, capacity):
    """Pre-PLAN-991 splitter mutation: it can consume index=end."""
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + capacity)
        if end < len(text):
            newline = text.rfind("\n", start, end + 1)
            if newline >= start:
                end = newline + 1
        chunks.append(text[start:end])
        start = end
    return chunks or [text]


def guard_boundary_source():
    prefix = "**回复 3/3**\n\n"
    capacity = bridge_outbox.ANSWER_TARGET_BUDGET - len(prefix)
    return ("a" * capacity) + "\n" + ("中" * 2755) + "\n" + ("🙂" * 522)


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


class RoutedResultCards(FakeCards):
    """Production delivery callbacks return a structured receipt, not a bare ID."""

    async def new_card(self, text, route=None, purpose="answer", fragment=None):
        self.new.append((text, route))
        return {"ok": True, "message_id": f"m{len(self.new)}"}


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

    async def test_step_completion_plan_and_result_receipt_are_visible_without_tool_body(self):
        state, cards = fresh_state(), FakeCards()
        running = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 1, "kind": "plan",
             "label": "🔄 S7.2 全局交付开关（预计 17:50 完成)",
             "plan_completed": 1, "plan_total": 2},
            {"event_id": "tools:t", "revision": 1, "kind": "tool", "tool_count": 3,
             "label": "修改 C:/private/path.py"},
        ]}
        completed = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "plan:t", "revision": 2, "kind": "plan",
             "label": "✅ S7.2 全局交付开关（实际 17:37 完成)",
             "plan_completed": 2, "plan_total": 2},
            {"event_id": "tools:t", "revision": 2, "kind": "tool", "tool_count": 5,
             "label": "修改 C:/private/path.py"},
            {"event_id": "receipt:t:2", "revision": 1, "kind": "commentary",
             "label": "💬 🟢 S7.2 已完成；策略测试通过；下一 Step ETA 18:00"},
        ]}
        await self.drain([running], state, cards)
        await self.drain([completed], state, cards)
        self.assertEqual(len(cards.new), 1)
        self.assertEqual(len(cards.edits), 1)
        rendered = cards.edits[0][1]
        self.assertIn("✅ S7.2 全局交付开关", rendered)
        self.assertIn("🟢 S7.2 已完成", rendered)
        self.assertIn("下一 Step ETA 18:00", rendered)
        self.assertNotIn("private/path.py", rendered)

    async def test_structured_delivery_result_keeps_real_message_id_for_patch(self):
        state, cards = fresh_state(), RoutedResultCards()
        first = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "first"}
        ]}
        second = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "c1", "revision": 2, "kind": "commentary", "label": "second"}
        ]}
        await self.drain([first], state, cards)
        await self.drain([second], state, cards)
        self.assertEqual(state["v2_mid"], "m1")
        self.assertEqual([mid for mid, _text in cards.edits], ["m1"])
        self.assertEqual(len(cards.new), 1)

    async def test_legacy_progress_also_normalizes_structured_delivery_result(self):
        state, cards = fresh_state(), RoutedResultCards()
        await self.drain([{"kind": "progress", "turn": "t", "steps": [
            {"kind": "tool", "label": "first"}
        ]}], state, cards)
        await self.drain([{"kind": "progress", "turn": "t", "steps": [
            {"kind": "tool", "label": "first"}, {"kind": "tool", "label": "second"}
        ]}], state, cards)
        self.assertEqual(state["cur_mid"], "m1")
        self.assertEqual([mid for mid, _text in cards.edits], ["m1"])
        self.assertEqual(len(cards.new), 1)

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
        self.assertIn("📋 current plan", text)
        self.assertIn("checkpoint", text)
        self.assertNotIn("🟡 checkpoint", text)
        self.assertNotIn("类型：搜索 rg ×2", text)
        self.assertNotIn("修改：无", text)
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
        self.assertIn("checkpoint", text)
        self.assertNotIn("🟡 checkpoint", text)
        self.assertNotIn("\nA", text)
        self.assertNotIn("\nB", text)
        self.assertNotIn("计划", text)

    async def test_milestone_commentary_preserves_explicit_status_markers(self):
        state, cards = fresh_state(), FakeCards()
        record = {"kind": "progress", "contract": "milestone-v1", "root_turn": "t", "steps": [
            {"event_id": "c1", "revision": 1, "kind": "commentary", "label": "💬 🟢 done"},
            {"event_id": "c2", "revision": 1, "kind": "commentary", "label": "💬 🟡 running"},
            {"event_id": "c3", "revision": 1, "kind": "commentary", "label": "💬 🔴 blocked"},
        ]}
        await self.drain([record], state, cards)
        text = cards.new[0][0]
        self.assertIn("💬 🟢 done", text)
        self.assertIn("💬 🟡 running", text)
        self.assertIn("💬 🔴 blocked", text)
        self.assertNotIn("🟡 🟢", text)
        self.assertNotIn("🟡 🟡", text)
        self.assertNotIn("🟡 🔴", text)

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
        self.assertIn("_思考中…_", replacement)
        self.assertNotIn("NEW TOOL", replacement)
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
        self.assertNotIn("feishu/bridge_events.py", surfaces["card"])
        self.assertNotIn("修改：", surfaces["card"])
        self.assertNotIn("新增：", surfaces["card"])
        for name in ("outbox", "progress_state", "restored"):
            self.assertIn("feishu/bridge_events.py", surfaces[name])

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

    def test_poisoned_structured_message_id_is_discarded_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = bridge_outbox.progress_state_path(tmp, "bot")
            path.write_text(json.dumps({
                "contract": "milestone-v1",
                "turn": "t",
                "steps": [],
                "mid": {"ok": True, "message_id": "om_real"},
                "card_ids": [],
                "acked": {},
                "route": {"kind": "p2a"},
            }), encoding="utf-8")
            restored = bridge_outbox.load_progress_state(tmp, "bot")
        self.assertIsNone(restored["v2_mid"])

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

    def test_split_exact_newline_at_capacity_boundary_is_bounded_and_lossless(self):
        source = "abcd\nx"
        chunks = bridge_outbox._split_exact(source, 4)
        self.assertEqual("".join(chunks), source)
        self.assertTrue(all(len(chunk) <= 4 for chunk in chunks), chunks)

    def test_answer_fragment_newline_at_card_capacity_boundary_is_bounded(self):
        route = {"kind": "p2a"}
        record = {"session": "boundary", "anchor": "plan220"}
        prefix = "**回复 3/3**\n\n"
        capacity = bridge_outbox.CARD_BUDGET - len(prefix)
        source = ("a" * capacity) + "\n" + ("中" * 2765) + "\n" + ("🙂" * 522)

        first = bridge_outbox._answer_fragments(record, source, route)
        second = bridge_outbox._answer_fragments(record, source, route)

        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)
        self.assertEqual("".join(row["content"] for row in first), source)
        self.assertTrue(all(
            len(row["rendered"]) <= bridge_outbox.CARD_BUDGET for row in first
        ))

    def test_split_exact_boundary_matrix_handles_unicode_and_no_newline(self):
        samples = [
            ("abc\n中🙂", 4),       # newline is the last included character
            ("abcd\n中🙂", 4),      # newline is exactly at the right boundary
            ("abcde\n中🙂", 4),     # newline is just after the boundary
            ("中文🙂abcdef", 4),     # no newline fallback
            ("\nabcde", 4),         # preserve historical newline-at-start split
        ]
        for source, capacity in samples:
            with self.subTest(source=source, capacity=capacity):
                chunks = bridge_outbox._split_exact(source, capacity)
                self.assertEqual("".join(chunks), source)
                self.assertTrue(all(len(chunk) <= capacity for chunk in chunks), chunks)

    def test_answer_guard_band_is_unused_on_the_normal_path(self):
        record = {"session": "guard", "anchor": "normal"}
        source = "x" * 2795
        fragments = bridge_outbox._answer_fragments(
            record, source, {"kind": "p2a"},
            budget=bridge_outbox.ANSWER_TARGET_BUDGET,
            hard_budget=bridge_outbox.CARD_BUDGET,
            split_policy=bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
        )
        self.assertEqual("".join(row["content"] for row in fragments), source)
        self.assertGreater(len(fragments), 1)
        self.assertTrue(all(
            len(row["rendered"]) <= bridge_outbox.ANSWER_TARGET_BUDGET
            and row["guard_chars"] == 0 and not row["guard_used"]
            for row in fragments
        ))

    def test_old_off_by_one_uses_one_guard_character_without_losing_text(self):
        record = {"session": "guard", "anchor": "one-char"}
        source = guard_boundary_source()
        with mock.patch.object(bridge_outbox, "_split_exact", side_effect=inclusive_right_split):
            fragments = bridge_outbox._answer_fragments(
                record, source, {"kind": "p2a"},
                budget=bridge_outbox.ANSWER_TARGET_BUDGET,
                hard_budget=bridge_outbox.CARD_BUDGET,
                split_policy=bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
            )
        self.assertEqual("".join(row["content"] for row in fragments), source)
        self.assertEqual(max(row["guard_chars"] for row in fragments), 1)
        self.assertTrue(any(row["guard_used"] for row in fragments))
        self.assertTrue(all(
            len(row["rendered"]) <= bridge_outbox.CARD_BUDGET for row in fragments
        ))

    def test_guard_overrun_above_ten_characters_hits_hard_limit(self):
        def overrun(text, capacity):
            return [text[:capacity + 11], text[capacity + 11:]]

        with mock.patch.object(bridge_outbox, "_split_exact", side_effect=overrun):
            with self.assertRaisesRegex(ValueError, "CARD_BUDGET"):
                bridge_outbox._answer_fragments(
                    {"session": "guard", "anchor": "too-large"}, "x" * 6000,
                    {"kind": "p2a"}, budget=bridge_outbox.ANSWER_TARGET_BUDGET,
                    hard_budget=bridge_outbox.CARD_BUDGET,
                    split_policy=bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
                )

    async def test_outbox_drainer_replays_boundary_answer_to_eof_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = "boundary-bot"
            prefix = "**回复 3/3**\n\n"
            capacity = bridge_outbox.CARD_BUDGET - len(prefix)
            source = ("a" * capacity) + "\n" + ("中" * 2765) + "\n" + ("🙂" * 522)
            record = {
                "kind": "answer", "session": "boundary", "anchor": "plan220",
                "text": source, "route": {"kind": "p2a"},
            }
            self.assertTrue(bridge_outbox.append_record(tmp, bot, record))
            eof = Path(bridge_outbox.outbox_path(tmp, bot)).stat().st_size
            reached_eof = asyncio.Event()
            offsets = []
            cards = []
            fallbacks = []
            errors = []

            async def new_card(text, route=None, purpose="answer", fragment=None):
                cards.append((text, route, purpose, fragment))
                return {"ok": True, "message_id": f"om_{fragment['part']}"}

            async def edit_card(_mid, _text):
                return True

            async def send_plain(text, route=None, purpose="answer", fragment=None):
                fallbacks.append((text, route, purpose, fragment))
                return True

            async def asleep(_delay):
                await asyncio.sleep(0)

            def save_offset(offset):
                offsets.append(offset)
                if offset == eof:
                    reached_eof.set()

            task = asyncio.create_task(bridge_outbox.outbox_drainer(
                bot, state_dir=tmp, new_card=new_card, edit_card=edit_card,
                send_plain=send_plain, asleep=asleep, hwm_load=lambda: 0,
                hwm_save=save_offset, on_error=errors.append, poll=0,
                coalesce_sec=0,
            ))
            try:
                await asyncio.wait_for(reached_eof.wait(), timeout=1)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertEqual(offsets[-1], eof)
            self.assertEqual(len(cards), 3)
            self.assertEqual([row[3]["part"] for row in cards], [1, 2, 3])
            self.assertEqual(fallbacks, [])
            self.assertEqual(errors, [])

    async def test_outbox_drainer_guard_survives_old_off_by_one_and_reaches_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = "guard-bot"
            source = guard_boundary_source()
            record = {
                "kind": "answer", "session": "guard", "anchor": "old-splitter",
                "text": source, "route": {"kind": "p2a"},
            }
            self.assertTrue(bridge_outbox.append_record(tmp, bot, record))
            eof = Path(bridge_outbox.outbox_path(tmp, bot)).stat().st_size
            reached_eof = asyncio.Event()
            offsets, cards, errors = [], [], []

            async def new_card(text, route=None, purpose="answer", fragment=None):
                cards.append((text, fragment))
                return {"ok": True, "message_id": f"om_{fragment['part']}"}

            async def edit_card(_mid, _text):
                return True

            async def send_plain(*_args, **_kwargs):
                self.fail("guarded interactive delivery must not fall back")

            async def asleep(_delay):
                await asyncio.sleep(0)

            def save_offset(offset):
                offsets.append(offset)
                if offset == eof:
                    reached_eof.set()

            with mock.patch.object(
                bridge_outbox, "_split_exact", side_effect=inclusive_right_split,
            ):
                task = asyncio.create_task(bridge_outbox.outbox_drainer(
                    bot, state_dir=tmp, new_card=new_card, edit_card=edit_card,
                    send_plain=send_plain, asleep=asleep, hwm_load=lambda: 0,
                    hwm_save=save_offset, on_error=errors.append, poll=0,
                    coalesce_sec=0,
                ))
                try:
                    await asyncio.wait_for(reached_eof.wait(), timeout=1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

            self.assertEqual(offsets[-1], eof)
            self.assertEqual(len(cards), 3)
            self.assertEqual(max(row[1]["guard_chars"] for row in cards), 1)
            self.assertEqual(errors, [])

    async def test_outbox_drainer_reports_logic_error_without_advancing_hwm(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = "error-bot"
            record = {
                "kind": "answer", "session": "s", "anchor": "a",
                "text": "answer", "route": {"kind": "p2a"},
            }
            self.assertTrue(bridge_outbox.append_record(tmp, bot, {"kind": "noop"}))
            self.assertTrue(bridge_outbox.append_record(tmp, bot, record))
            error_seen = asyncio.Event()
            failed_twice = asyncio.Event()
            block_after_error = asyncio.Event()
            offsets = []
            errors = []
            failures = []

            async def no_send(*_args, **_kwargs):
                self.fail("delivery callback must not run after injected splitter failure")

            async def asleep(delay):
                if delay >= 1 and len(failures) >= 2:
                    await block_after_error.wait()
                else:
                    await asyncio.sleep(0)

            def on_error(event):
                errors.append(event)
                error_seen.set()

            def injected_failure(*_args, **_kwargs):
                failures.append(1)
                if len(failures) >= 2:
                    failed_twice.set()
                raise RuntimeError("SECRET_MARKER token=supersecret injected failure")

            with mock.patch.object(
                bridge_outbox, "_answer_fragments",
                side_effect=injected_failure,
            ):
                task = asyncio.create_task(bridge_outbox.outbox_drainer(
                    bot, state_dir=tmp, new_card=no_send, edit_card=no_send,
                    send_plain=no_send, asleep=asleep, hwm_load=lambda: 0,
                    hwm_save=offsets.append, on_error=on_error, poll=0,
                    coalesce_sec=0,
                ))
                try:
                    await asyncio.wait_for(error_seen.wait(), timeout=1)
                    await asyncio.wait_for(failed_twice.wait(), timeout=1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["bot"], bot)
            self.assertEqual(errors[0]["offset"], 0)
            self.assertEqual(errors[0]["kind"], "answer")
            self.assertEqual(errors[0]["error_type"], "RuntimeError")
            self.assertIn("internal error digest=", errors[0]["error"])
            self.assertNotIn("SECRET_MARKER", errors[0]["error"])
            self.assertNotIn("supersecret", errors[0]["error"])
            self.assertEqual(offsets, [])

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
            restored = bridge_outbox.load_answer_state(tmp, "bot")
            saved = next(iter(restored["answers"].values()))
            self.assertEqual(saved["split_policy"], bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10)
            self.assertEqual(saved["render_target"], bridge_outbox.ANSWER_TARGET_BUDGET)
            self.assertEqual(
                [row["fragment_id"] for row in bridge_outbox._answer_fragments(
                    record, text.strip(), record["route"],
                    budget=bridge_outbox.ANSWER_TARGET_BUDGET,
                    hard_budget=bridge_outbox.CARD_BUDGET,
                    split_policy=bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
                )][1],
                second.calls[0][2],
            )

    async def test_guard_used_manifest_freezes_boundaries_across_restart(self):
        class Cards:
            def __init__(self, fail_parts=()):
                self.fail_parts = set(fail_parts)
                self.calls = []

            async def new_card(self, text, route=None, purpose="answer", fragment=None):
                self.calls.append((text, dict(fragment)))
                return None if fragment["part"] in self.fail_parts else f"m{fragment['part']}"

            async def edit_card(self, _mid, _text):
                return True

            async def send_plain(self, text, route=None, purpose="answer", fragment=None):
                return False if fragment["part"] in self.fail_parts else f"t{fragment['part']}"

        with tempfile.TemporaryDirectory() as tmp:
            source = guard_boundary_source()
            record = {"kind": "answer", "session": "guard", "anchor": "manifest",
                      "text": source, "route": {"kind": "p2a"}}
            first_state = fresh_state()
            first_state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
            first = Cards({2})
            with mock.patch.object(
                bridge_outbox, "_split_exact", side_effect=inclusive_right_split,
            ):
                with self.assertRaises(bridge_outbox.RetrySend):
                    await bridge_outbox.drain_batch(
                        [record], new_card=first.new_card, edit_card=first.edit_card,
                        send_plain=first.send_plain, state=first_state, coalesce_sec=0,
                        clock=lambda: 100, force_flush=True,
                        persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
                    )

            persisted = bridge_outbox.load_answer_state(tmp, "bot")
            saved = next(iter(persisted["answers"].values()))
            self.assertEqual(saved["manifest"][0]["guard_chars"], 1)
            manifest_ids = [row["fragment_id"] for row in saved["manifest"]]

            second_state = fresh_state()
            second_state["answer_delivery"] = persisted
            second = Cards()
            await bridge_outbox.drain_batch(
                [record], new_card=second.new_card, edit_card=second.edit_card,
                send_plain=second.send_plain, state=second_state, coalesce_sec=0,
                clock=lambda: 101, force_flush=True,
                persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
            )
            self.assertEqual([row[1]["part"] for row in second.calls], [2, 3])
            self.assertEqual([row[1]["fragment_id"] for row in second.calls], manifest_ids[1:])
            self.assertEqual(second.calls[0][1]["guard_chars"], 0)

    async def test_manifest_persist_failure_blocks_network_and_rolls_back_memory(self):
        state = fresh_state()
        state["answer_delivery"] = {"version": 1, "answers": {}}
        sends = []

        async def new_card(*args, **kwargs):
            sends.append((args, kwargs))
            return "m1"

        async def edit_card(_mid, _text):
            return True

        async def send_plain(*args, **kwargs):
            sends.append((args, kwargs))
            return "t1"

        record = {"kind": "answer", "session": "guard", "anchor": "durable",
                  "text": "x" * 3000, "route": {"kind": "p2a"}}
        with self.assertRaisesRegex(OSError, "durable"):
            await bridge_outbox.drain_batch(
                [record], new_card=new_card, edit_card=edit_card,
                send_plain=send_plain, state=state, coalesce_sec=0,
                clock=lambda: 100, force_flush=True,
                persist_answer=lambda _value: False,
            )
        self.assertEqual(sends, [])
        self.assertEqual(state["answer_delivery"]["answers"], {})

    async def test_legacy_answer_state_backfills_manifest_without_resending_ack(self):
        class Cards:
            def __init__(self):
                self.calls = []

            async def new_card(self, text, route=None, purpose="answer", fragment=None):
                self.calls.append(dict(fragment))
                return f"m{fragment['part']}"

            async def edit_card(self, _mid, _text):
                return True

            async def send_plain(self, *_args, **_kwargs):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            source = ("旧分片\n" * 1200).strip()
            record = {"kind": "answer", "session": "legacy", "anchor": "a",
                      "text": source, "route": {"kind": "p2a"}}
            legacy = bridge_outbox._answer_fragments(record, source, record["route"])
            first = legacy[0]
            delivery = {"version": 1, "answers": {first["answer_id"]: {
                "text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "route": record["route"], "total": len(legacy),
                "fragments": {first["fragment_id"]: {
                    "acked": True, "message_id": "old-m1", "part": 1,
                    "content_sha256": first["content_sha256"], "acked_at": 1,
                }}, "completed_at": None,
            }}}
            self.assertTrue(bridge_outbox.save_answer_state(tmp, "bot", delivery))
            state = fresh_state()
            state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
            cards = Cards()
            await bridge_outbox.drain_batch(
                [record], new_card=cards.new_card, edit_card=cards.edit_card,
                send_plain=cards.send_plain, state=state, coalesce_sec=0,
                clock=lambda: 100, force_flush=True,
                persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
            )
            self.assertNotIn(1, [row["part"] for row in cards.calls])
            restored = bridge_outbox.load_answer_state(tmp, "bot")["answers"][first["answer_id"]]
            self.assertEqual(restored["split_policy"], bridge_outbox.ANSWER_SPLIT_POLICY_LEGACY)
            self.assertEqual(restored["render_target"], bridge_outbox.CARD_BUDGET)
            self.assertEqual(
                [row["fragment_id"] for row in restored["manifest"]],
                [row["fragment_id"] for row in legacy],
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


class HWM崩溃安全(unittest.TestCase):
    """锁死 2026-08-30 tb24-voiceover 洪水事故的三条修复。

    事故链：机器断电重启(Kernel-Power 41) → NTFS 把没落盘的 HWM 文件还成 21 个 0x00
    → load_hwm 旧实现 except→return 0（0 的语义是「一条都没发过」）→ drainer 从头重放
    800MB/20127 条历史 → 医生见水位不推进每 97 秒重启它一次 → 同一批最老消息被重发 40 余轮，
    42 分钟砸出 3667 条。
    """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.bot = "botx"
        self.obx = bridge_outbox.outbox_path(self.d, self.bot)
        with open(self.obx, "w", encoding="utf-8") as f:
            for i in range(50):
                f.write(json.dumps({"kind": "progress", "n": i}) + "\n")
        self.size = Path(self.obx).stat().st_size

    def test_书签全NUL时必须fail_closed不得重放历史(self):
        """断电后的真实形态：文件长度对、内容全 0x00。"""
        p = bridge_outbox.hwm_path(self.d, self.bot)
        with open(p, "wb") as f:
            f.write(b"\x00" * 21)
        off = bridge_outbox.load_hwm(self.d, self.bot)
        self.assertEqual(off, self.size, "损坏必须退到 outbox 末尾，绝不能回 0（回 0 = 重放全部历史）")
        recs, _ = bridge_outbox.read_new_records(self.obx, off)
        self.assertEqual(recs, [], "fail-closed 之后不应再读出任何待发记录")

    def test_书签是垃圾JSON时同样fail_closed(self):
        p = bridge_outbox.hwm_path(self.d, self.bot)
        Path(p).write_text("{不是合法 json", encoding="utf-8")
        self.assertEqual(bridge_outbox.load_hwm(self.d, self.bot), self.size)

    def test_损坏会被就地钉死并留痕(self):
        p = bridge_outbox.hwm_path(self.d, self.bot)
        with open(p, "wb") as f:
            f.write(b"\x00" * 21)
        bridge_outbox.load_hwm(self.d, self.bot)
        self.assertEqual(json.loads(Path(p).read_text(encoding="utf-8"))["offset"], self.size,
                         "损坏应被立刻改写成安全值，免得每次重启都再踩一次")
        log = Path(self.d) / f"bridge-hwm-corrupt-{self.bot}.log"
        self.assertTrue(log.exists() and log.read_text(encoding="utf-8").strip(),
                        "损坏必须留痕，不能再静默发生一次")

    def test_书签不存在才允许从0开始(self):
        """『文件没有』(真新 bot) 和『文件读不出来』(损坏) 必须是两码事。"""
        self.assertEqual(bridge_outbox.load_hwm(self.d, "从没跑过的bot"), 0)

    def test_读取切分边界不得被改动(self):
        """2026-08-30 反向闸：曾试过给 read_new_records 加 max_bytes 封顶，差分测试证明
        它会改变卡片标题计数器、多发卡片（8MB 样本 +2 张卡 / +1187 字符）。
        切分边界属于消息语义的一部分，任何人再想加封顶，这条测试必须先红。"""
        import inspect
        sig = inspect.signature(bridge_outbox.read_new_records)
        self.assertEqual(list(sig.parameters), ["path", "offset"],
                         "read_new_records 只能有 (path, offset)；加封顶参数会改变发出的消息")
        recs, new_off = bridge_outbox.read_new_records(self.obx, 0)
        self.assertEqual(new_off, self.size, "一次必须读到文件尾，保持整批累计语义")
        self.assertEqual(len(recs), 50)

    def test_写书签必须落盘(self):
        """save_hwm 少了 fsync 就是这次事故的成因，锁死它。"""
        import inspect
        src = inspect.getsource(bridge_outbox.save_hwm)
        self.assertIn("fsync", src, "save_hwm 必须 fsync，否则断电就还你一个全 NUL 文件")
