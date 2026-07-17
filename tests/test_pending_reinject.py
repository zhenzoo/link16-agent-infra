"""PLAN-918 Part B 验证：pending 投递账本生命周期 + 控制命令清账（B1）。

异构覆盖（§4.5·≥3 种不同覆盖）：
  1. pending_status 六态矩阵（none/active/stuck/waiting）—— 我的修复所依赖的状态机没被带坏。
  2. B1 病根复现 → 修复：造一笔【已超时·outbox 零活动】的 stuck 账（= /stop 留的雷·旧行为 doctor 会重投），
     调 pending_clear（= /stop 现在做的）→ 状态变 none → doctor 的 _recover_pending 会 early-return 不重投。
  3. 回归：正常 active 路径（outbox 涨了）→ 判 active·不误投。
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "feishu"))
import bridge_outbox as ob  # noqa: E402


class PendingReinjectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pending-test-")
        self.bot = "tb-test"
        # 造一个空 outbox（活动基线）
        open(ob.outbox_path(self.dir, self.bot), "w", encoding="utf-8").close()

    def _outbox_write(self, nbytes):
        with open(ob.outbox_path(self.dir, self.bot), "a", encoding="utf-8") as f:
            f.write("x" * nbytes)

    # 1) 六态矩阵 ----------------------------------------------------------
    def test_status_none_when_no_pending(self):
        self.assertEqual(ob.pending_status(self.dir, self.bot)[0], "none")

    def test_status_active_when_outbox_grew(self):
        sz0 = os.path.getsize(ob.outbox_path(self.dir, self.bot))
        ob.pending_write(self.dir, self.bot, text="hi", size0=sz0)
        self._outbox_write(50)                     # outbox 涨 = turn 发生
        self.assertEqual(ob.pending_status(self.dir, self.bot)[0], "active")

    def test_status_waiting_when_zero_activity_within_timeout(self):
        sz0 = os.path.getsize(ob.outbox_path(self.dir, self.bot))
        ob.pending_write(self.dir, self.bot, text="hi", size0=sz0)   # 刚记·没超时
        self.assertEqual(ob.pending_status(self.dir, self.bot, timeout=120)[0], "waiting")

    def test_status_stuck_when_zero_activity_and_timed_out(self):
        sz0 = os.path.getsize(ob.outbox_path(self.dir, self.bot))
        ob.pending_write(self.dir, self.bot, text="hi", size0=sz0,
                         now=time.time() - 999)      # ts 造成 999s 前 → 超时
        self.assertEqual(ob.pending_status(self.dir, self.bot, timeout=120)[0], "stuck")

    # 2) B1 病根复现 → 修复 -------------------------------------------------
    def test_stop_clears_the_stuck_landmine(self):
        """核心：/stop 留下的 stuck 雷，被 pending_clear 拆掉后 → doctor 判 none·不重投。"""
        sz0 = os.path.getsize(ob.outbox_path(self.dir, self.bot))
        # 造雷：一笔早就超时、outbox 零活动的账（= M 被 /stop 杀了·永不回传）
        ob.pending_write(self.dir, self.bot, text="被停掉的任务", size0=sz0,
                         now=time.time() - 999)
        # 修复前：doctor 会判 stuck（→ 旧行为放行重投）
        self.assertEqual(ob.pending_status(self.dir, self.bot, timeout=120)[0], "stuck",
                         "前提：这确实是一颗会被重投的 stuck 雷")
        # /stop 现在做的：清账
        ob.pending_clear(self.dir, self.bot)
        # 修复后：doctor 看到 none → _recover_pending 的 `if st in ("none","waiting"): return` 触发 → 绝不重投
        self.assertEqual(ob.pending_status(self.dir, self.bot, timeout=120)[0], "none",
                         "/stop 清账后·不该再有任何待重投的账")

    # 3) 回归：清账幂等 + 不误伤正常路径 ------------------------------------
    def test_clear_is_idempotent(self):
        ob.pending_clear(self.dir, self.bot)        # 本来就没账·清也不报错
        self.assertEqual(ob.pending_status(self.dir, self.bot)[0], "none")

    def test_active_path_not_broken_by_clear_semantics(self):
        sz0 = os.path.getsize(ob.outbox_path(self.dir, self.bot))
        ob.pending_write(self.dir, self.bot, text="正常任务", size0=sz0)
        self._outbox_write(10)                       # 正常回传 → 涨
        self.assertEqual(ob.pending_status(self.dir, self.bot)[0], "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
