"""§2.12b 修复：_inject 忙时注入不再误报「没提交成功」（2026-07-20）。

病根：往【正忙(生成中)】的会话注入消息 → Claude Code 把它【排队】(等这轮干完再处理·正常)，
但注入校验读屏 6 次都见消息还在 → 当成「卡死没提交」→ 误报「请重发」(还重按回车·可能重复入队)。
真机(zz-busy-test)证：干净场景已 True；误报是【负载相关的偶发竞态】(读屏正撞上「消息还在活 composer、
尚未落进排队」的瞬间)——干净环境复现不出。故本单测在【逻辑层】红→绿：脚本化「忙时消息仍在」的屏序列。

异构覆盖：① 竞态(忙+消息仍在)→ 不误报 + 不重按(红→绿) ② 回归:空闲卡死 → 照旧喊人 + 重按到上限
③ 干净提交 → 立即 True ④ _busy_or_queued 检测器 sanity。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "feishu"))
import feishu_bridge as fb  # noqa: E402

MARK = "被注入的消息内容 [飞书 from=host to=zz via=DM · route=p2a]"
STUCK = "some scrollback\n──────❯ " + MARK          # 消息仍卡在【活 composer】(rfind ❯ 后含 marker)
BUSY_STUCK = "✶ Ideating… (12s · ↓ 1.1k tokens)\n──────❯ " + MARK   # 同上·但屏上有【生成中】状态
CLEAR = 'some scrollback\n──────❯ Try "…"'          # composer 已空(无 marker)


class InjectBusyGuardTest(unittest.TestCase):
    def _run(self, screens):
        """mock 掉 wmux/read_screen/sleep，跑真 _inject，返回 (结果, enter 次数)。"""
        seq = list(screens)
        calls = {"enter": 0}

        def fake_wmux(action, *a, **k):
            if action == "enter":
                calls["enter"] += 1

        def fake_read(pty, n):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with mock.patch.object(fb, "wmux", side_effect=fake_wmux), \
             mock.patch.object(fb, "read_screen", side_effect=fake_read), \
             mock.patch("time.sleep"):
            res = fb._inject("pty", "ws", MARK)
        return res, calls["enter"]

    # ① 竞态红→绿：忙 + 消息仍在 → 不误报·且不重按(防重复入队) --------------
    def test_busy_stuck_not_false_escalated(self):
        res, enters = self._run([BUSY_STUCK])       # 每次读都是「忙+消息还在」
        self.assertTrue(res, "忙时消息仍在=排队(正常)·不该返回 False 误报")
        self.assertEqual(enters, 1, "忙时不该重按回车(只有最初那一次)·防重复入队")

    # ② 回归：空闲卡死 → 照旧喊人(False) + 重按到上限 ----------------------
    def test_idle_stuck_still_escalates(self):
        res, enters = self._run([STUCK])            # 每次读都是「消息还在·但不忙」
        self.assertFalse(res, "空闲卡死必须照旧返回 False 喊人·绝不能被修法咽掉")
        self.assertEqual(enters, 1 + fb.INJECT_VERIFY_TRIES, "空闲卡死应重按到上限")

    # ③ 干净提交：composer 空 → 立即 True ---------------------------------
    def test_clean_submit(self):
        res, enters = self._run([CLEAR])
        self.assertTrue(res)
        self.assertEqual(enters, 1, "已提交·不重按")

    # ④ 检测器 sanity（防夹具假绿）----------------------------------------
    def test_detector(self):
        self.assertTrue(fb._busy_or_queued(BUSY_STUCK))                       # 有生成中耗时锚
        self.assertTrue(fb._busy_or_queued("✻ Actioning…\n❯"))                # 有 spinner
        self.assertTrue(fb._busy_or_queued("❯ Press up to edit queued messages"))  # 排队指示
        self.assertFalse(fb._busy_or_queued(STUCK))                           # 纯卡死·无忙信号
        self.assertFalse(fb._busy_or_queued(""))                             # 空屏


if __name__ == "__main__":
    unittest.main(verbosity=2)
