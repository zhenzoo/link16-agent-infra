"""PLAN-918 Part B / B6：_stop_clear_composer 状态机单测（mock 读屏·CI 可跑·无需真会话）。

异构覆盖（§4.5）：cleared（见残留→清掉）/ empty（整窗无残留）/ residual（顽固清不掉）/ 无 marker。
真机 e2e(含慢会话晚退回 RED→GREEN)另在手动实验里跑过·此处补 CI 回归。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "feishu"))
import feishu_bridge as fb  # noqa: E402

MARK = "被打断的消息内容 abc [envelope · route=p2a]"
DRAFT_SCR = "some scrollback line\n──────❯ " + MARK      # composer 里卡着残留（rfind ❯ 后含 marker 尾段）
EMPTY_SCR = 'some scrollback line\n──────❯ Try "…"'       # composer 空（只有占位·无 marker）


class StopClearComposerTest(unittest.TestCase):
    def _run(self, screens):
        seq = list(screens)

        def fake_read(pty, n):
            return seq.pop(0) if len(seq) > 1 else seq[0]   # 耗尽后重复最后一个

        with mock.patch.object(fb, "read_screen", side_effect=fake_read), \
             mock.patch.object(fb, "wmux"), \
             mock.patch("time.sleep"):
            return fb._stop_clear_composer("pty", "ws", MARK)

    def test_no_marker_returns_empty(self):
        with mock.patch.object(fb, "read_screen"), mock.patch.object(fb, "wmux"), \
             mock.patch("time.sleep"):
            self.assertEqual(fb._stop_clear_composer("pty", "ws", ""), "empty")

    def test_never_seen_returns_empty(self):
        self.assertEqual(self._run([EMPTY_SCR]), "empty")

    def test_draft_then_cleared(self):
        self.assertEqual(self._run([DRAFT_SCR, DRAFT_SCR, EMPTY_SCR]), "cleared")

    def test_stubborn_residual(self):
        self.assertEqual(self._run([DRAFT_SCR]), "residual")

    def test_detector_sanity(self):
        # 保证夹具的两个屏对检测函数确实是 True/False（否则上面全是假绿）
        self.assertTrue(fb._composer_holds_paste(DRAFT_SCR, MARK))
        self.assertFalse(fb._composer_holds_paste(EMPTY_SCR, MARK))


if __name__ == "__main__":
    unittest.main(verbosity=2)
