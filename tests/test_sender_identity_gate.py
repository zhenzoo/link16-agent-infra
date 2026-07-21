"""PLAN-920 · 发送者身份闸（防跨-agent bot 冒用）单测 + 4 面集成。

覆盖（异构）：
  ① 复现事故→拒（me=professor 冒用 cv·带 --to-agent 指路）
  ② 自发→过（含 a2a 的 --bot=自己）
  ③ terminal（me 未设）→放行（可信操作者场景·零回归）
  ④ 归一化（`_`↔`-` + 大小写 视为同一身份）
  ⑤ 白名单：默认空=严格 / 命中=放行
  ⑥ cron-agent（me 已设 = 强制·非绕过后门）
  ⑦ 集成：4 个发送面各真接了闸（subprocess·冒用在【网络前】被挡）
"""
import contextlib
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

FEISHU = Path(__file__).resolve().parent.parent / "feishu"
sys.path.insert(0, str(FEISHU))
import bridge_env as be  # noqa: E402


@contextlib.contextmanager
def session(me):
    """临时把 FEISHU_BRIDGE_SESSION 设成 me（None=未设/terminal），退出还原。"""
    old = os.environ.get("FEISHU_BRIDGE_SESSION")
    if me is None:
        os.environ.pop("FEISHU_BRIDGE_SESSION", None)
    else:
        os.environ["FEISHU_BRIDGE_SESSION"] = me
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("FEISHU_BRIDGE_SESSION", None)
        else:
            os.environ["FEISHU_BRIDGE_SESSION"] = old


class GateUnit(unittest.TestCase):
    # ① 复现事故：me=professor 冒用 cv → 拒 + 指路 --to-agent ----------------
    def test_impersonation_blocked(self):
        with session("tb25-phd-taoci"):
            with self.assertRaises(SystemExit) as cm:
                be.assert_sender_identity("tb25-phd-taoci-3")
            msg = str(cm.exception)
            self.assertIn("身份越界", msg)
            self.assertIn("--to-agent", msg)

    # ② 自发（含 a2a 的 --bot=自己）→ 过（不抛） ----------------------------
    def test_self_send_passes(self):
        with session("tb25-phd-taoci"):
            be.assert_sender_identity("tb25-phd-taoci")

    # ③ terminal（me 未设）→ 放行（可信操作者·零回归） --------------------
    def test_terminal_allowed(self):
        with session(None):
            be.assert_sender_identity("any-bot-whatever")

    # ④ 归一化：`_`↔`-` + 大小写 视为同一身份·过 --------------------------
    def test_normalization(self):
        with session("tb25-phd-taoci"):
            be.assert_sender_identity("TB25_PHD_TAOCI")   # 归一化后 == 自己

    # ⑤a 白名单默认空 = 严格：不因白名单逻辑存在而放宽 ---------------------
    def test_whitelist_default_strict(self):
        with session("tb25-phd-taoci"):
            with self.assertRaises(SystemExit):
                be.assert_sender_identity("tb25-cartoonMV")   # 现状无 may_send_as → 拒

    # ⑤b 白名单命中 → 放行（将来编排者合法代发） -------------------------
    def test_whitelist_allows(self):
        with session("orchestrator-x"):
            with mock.patch.object(be, "_may_send_as", return_value={"tb25-phd-taoci-3"}):
                be.assert_sender_identity("tb25-phd-taoci-3")   # 白名单命中→过

    # ⑥ cron-agent：cron 注入的会话 me 已设 → 照样强制（非绕过后门） --------
    def test_cron_agent_still_enforced(self):
        # cron 到点注入 bot 现有会话，那会话 FEISHU_BRIDGE_SESSION 是设着的 → 冒用照样被拒
        with session("tb25-notes"):
            with self.assertRaises(SystemExit):
                be.assert_sender_identity("tb25-phd-taoci-3")

    # ⑤c _may_send_as 现状真名册 = 空（从严的锚点事实） --------------------
    def test_may_send_as_empty_now(self):
        self.assertEqual(be._may_send_as("tb25-phd-taoci"), set())
        self.assertEqual(be._may_send_as("tb25-link16"), set())


# 每个发送面：me≠--bot 时冒用应在【网络前】被闸挡（证明 4 面都真接了闸）
_TOOLS = {
    "send_feishu_msg.py": ["--text", "hi"],
    "send_feishu_media.py": ["--media", str(FEISHU / "_gate_nonexistent.png")],
    "send_feishu_file.py": ["--file", str(FEISHU / "_gate_nonexistent.txt")],
    "send_feishu_voice.py": ["--audio", str(FEISHU / "_gate_nonexistent.mp3")],
}


class GateIntegration(unittest.TestCase):
    def test_all_four_tools_block_impersonation(self):
        env = dict(os.environ)
        env["FEISHU_BRIDGE_SESSION"] = "tb25-link16"        # 我是 link16
        for tool, extra in _TOOLS.items():
            with self.subTest(tool=tool):
                r = subprocess.run(
                    [sys.executable, str(FEISHU / tool),
                     "--bot", "tb25-phd-taoci-3", *extra],   # 冒用 phd-taoci-3
                    capture_output=True, text=True, encoding="utf-8",
                    env=env, timeout=40,
                )
                blob = (r.stdout or "") + (r.stderr or "")
                self.assertNotEqual(r.returncode, 0, f"{tool} 冒用应非零退出·实得 0")
                self.assertIn("身份越界", blob, f"{tool} 应报身份越界·实得: {blob[:200]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
