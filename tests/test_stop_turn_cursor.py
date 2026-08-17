"""Stop hook「一轮只发一轮」的两道闸（2026-08-16 tb24-voiceover 全量重发事故的回归闸）。

事故形状：`/loop` 定时开火与 a2a 注入被 `_is_real_user_message` 一并当成「不是 turn 边界」
→ anchor 冻在几十轮以前 → 每轮 Stop 把这期间**所有**终结态收尾拼成一张越滚越大的卡重发一遍
（实证：收尾卡 1099 → 21176 字·连发 21 轮·主人只发了一句话）。

两道闸各测各的（异构·互不替代）：
  ① 语义闸 `_is_real_user_message`：定时/a2a 注入 = 真 turn 边界；带 sourceToolUseID 的技能注入不是。
  ② 结构闸 turn cursor：**哪怕 anchor 完全冻死**（构造一个判据认不出的边界），
     上一轮已取走的正文也绝不会被下一轮再取一遍——这条不依赖任何「什么算边界」的判断。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "feishu"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "feishu", "hooks"))
import bridge_stop as bs                                   # noqa: E402
from jsonl_reply_extract import _assistant_texts, _is_real_user_message  # noqa: E402

LONG = "这是一段实质正文。" * 40                            # 远超 _SUBSTANTIVE_MIN


def _u(text, **kw):
    return dict({"type": "user", "message": {"content": text}}, **kw)


def _a(text, stop="end_turn"):
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": text}], "stop_reason": stop}}


def _write(recs):
    p = Path(tempfile.mkdtemp()) / "t.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    return p


def _reply(path, floor=0, pred=_is_real_user_message):
    old = bs._POLL_TRIES
    bs._POLL_TRIES = 1                                      # 不等竞态轮询（单测无并发写）
    try:
        return bs._final_turn_reply(str(path), pred, _assistant_texts, floor_line=floor)
    finally:
        bs._POLL_TRIES = old


class TurnBoundaryPredicateTest(unittest.TestCase):
    """① 语义闸：谁算 turn 边界。"""

    def test_human_message_is_boundary(self):
        self.assertTrue(_is_real_user_message(_u("你好")))

    def test_scheduled_loop_fire_is_boundary(self):
        # /loop 定时开火：isMeta + promptSource=system + queuePriority · 无 sourceToolUseID
        self.assertTrue(_is_real_user_message(
            _u("继续推进", isMeta=True, promptSource="system", queuePriority="later")))

    def test_a2a_injection_is_boundary(self):
        self.assertTrue(_is_real_user_message(
            _u("Another Claude session sent a message: <agent-message from=\"peer\">…</agent-message>",
               isMeta=True, promptSource="system")))

    def test_skill_injection_is_not_boundary(self):
        # 技能/工具注入（Base directory for this skill: …）带 sourceToolUseID → 不是边界
        self.assertFalse(_is_real_user_message(
            _u("Base directory for this skill: …", isMeta=True, sourceToolUseID="toolu_1")))

    def test_tool_result_and_compact_summary_are_not_boundaries(self):
        self.assertFalse(_is_real_user_message(
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}))
        self.assertFalse(_is_real_user_message(_u("续上下文", isCompactSummary=True)))


class TurnCursorTest(unittest.TestCase):
    """② 结构闸：cursor 让「已取走的正文」再也够不着。"""

    def test_loop_driven_turns_do_not_pile_up(self):
        """三轮由定时开火驱动 → 每轮只发本轮收尾（旧版会把三轮拼成一张越滚越大的卡）。"""
        recs = [_u("开工")]
        floor, seen = 0, []
        for i in range(3):
            recs += [_a(f"第 {i} 轮收尾 {LONG}")]
            r = _reply(_write(recs), floor)
            self.assertTrue(r["complete"])
            self.assertEqual(len(r["cards"]), 1, f"第 {i} 轮应只有一张收尾卡")
            self.assertIn(f"第 {i} 轮收尾", r["cards"][0])
            for prev in seen:                               # 本轮卡里不得夹带任何往轮正文
                self.assertNotIn(prev, r["cards"][0])
            seen.append(f"第 {i} 轮收尾")
            floor = r["consumed_line"]
            recs += [_u("继续推进", isMeta=True, promptSource="system", queuePriority="later")]

    def test_cursor_holds_even_when_anchor_is_frozen(self):
        """anchor 冻死（判据认不出后续边界）时，cursor 仍然把重复正文挡住 —— 这条不靠边界判据。"""
        frozen = lambda rec: rec.get("type") == "user" and rec.get("message", {}).get("content") == "唯一锚点"  # noqa: E731
        recs = [_u("唯一锚点"), _a("第 0 轮收尾 " + LONG)]
        r0 = _reply(_write(recs), 0, pred=frozen)
        self.assertEqual(len(r0["cards"]), 1)
        recs += [_u("看不见的边界"), _a("第 1 轮收尾 " + LONG)]
        r1 = _reply(_write(recs), r0["consumed_line"], pred=frozen)
        self.assertEqual(r1["anchor_line"], r0["anchor_line"], "前提：anchor 确实冻住了")
        self.assertEqual(len(r1["cards"]), 1)
        self.assertNotIn("第 0 轮收尾", r1["cards"][0])      # 旧版这里会把两轮拼在一起
        self.assertIn("第 1 轮收尾", r1["cards"][0])

    def test_repeated_stop_on_same_transcript_sends_nothing_new(self):
        """同一 transcript 上 Stop 重复开火（background 唤醒等）→ 第二次不再重发。"""
        p = _write([_u("开工"), _a("收尾 " + LONG)])
        r0 = _reply(p, 0)
        r1 = _reply(p, r0["consumed_line"])
        self.assertTrue(r0["cards"])
        self.assertEqual(r1["cards"], [])

    def test_timeout_path_does_not_advance_cursor_past_unsent_text(self):
        """竞态超时（没等到终结态）→ complete=False，cursor 只停在已取走正文那行 → 下轮自愈补发。"""
        p = _write([_u("开工"),                               # L1
                    _a("中段实质正文 " + LONG, stop="tool_use"),  # L2 ← 取走正文的最后一行
                    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}])  # L3
        r = _reply(p, 0)
        self.assertFalse(r["complete"])
        self.assertTrue(r["cards"])                          # 正文必达：抓到了就发
        self.assertEqual(r["scan_line"], 3)
        self.assertEqual(r["consumed_line"], 2)              # 只停在 L2·不吃掉 L3 之后可能晚落的 wrap-up


if __name__ == "__main__":
    unittest.main()
