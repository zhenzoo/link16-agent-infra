import os
import json
import sys
import tempfile
import unittest
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

    async def new_card(self, text, route=None):
        self.new.append((text, route))
        return f"m{len(self.new)}"

    async def edit_card(self, mid, text):
        self.edits.append((mid, text))
        return self.edit_ok

    async def send_plain(self, text, route=None):
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
