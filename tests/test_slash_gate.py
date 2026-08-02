"""破坏性斜杠命令授权闸 + Git-Bash 改写复原 的回归测试（PLAN-930 · 2026-08-03）。

背景：`agent 关别的 agent` 早就能做且**零鉴权**——群消息跳过 is_allowed，任何 `/` 文本直达 handle_slash，
6 个破坏性命令(close/clear/cd/account/new/stop)全裸奔。这里锁死装上去的闸 + 那条静默失效的改写 bug。
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import agent_grant  # noqa: E402
import feishu_bridge  # noqa: E402


class UnmangleSlashTest(unittest.TestCase):
    """Git-Bash 把 /close 改写成 C:/Program Files/Git/close → 静默失效。收信侧复原。"""

    def test_restores_mangled_command(self):
        self.assertEqual(feishu_bridge._unmangle_slash("C:/Program Files/Git/close"), "/close")

    def test_restores_and_keeps_trailing_envelope(self):
        got = feishu_bridge._unmangle_slash("C:/Program Files/Git/close [飞书_from_a_to_b]")
        self.assertEqual(got, "/close [飞书_from_a_to_b]")

    def test_leaves_normal_text_alone(self):
        for s in ("/close", "把文件放到 C:/Users/x/close 目录", "hello", ""):
            self.assertEqual(feishu_bridge._unmangle_slash(s), s)

    def test_does_not_touch_unknown_trailing_word(self):
        # 不是我们认识的命令名 → 一律不动（防止误伤真实路径）
        self.assertEqual(feishu_bridge._unmangle_slash("D:/repo/deploy"), "D:/repo/deploy")


class GateVerdictTest(unittest.TestCase):
    BOT = {"name": "victim"}

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        feishu_bridge.STATE_DIR = Path(self._tmp.name)
        feishu_bridge.save_owner("victim", "ou_owner")

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def _grant(self, grantee, caps):
        p = Path(self._tmp.name) / f"bridge-grant-{grantee}.json"
        p.write_text(json.dumps({"grantee": grantee, "caps": caps,
                                 "expires_at": int(time.time()) + 3600}), encoding="utf-8")

    def test_owner_in_group_is_allowed(self):
        ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_owner", "/close")
        self.assertTrue(ok)
        self.assertIn("主人", why)

    def test_unknown_sender_is_denied(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_stranger", "/close")
        self.assertFalse(ok, "认不出的发信人必须拒绝(fail closed)")

    def test_peer_without_grant_is_denied(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")
        self.assertFalse(ok)
        self.assertIn("没有", why)

    def test_peer_with_grant_is_allowed(self):
        self._grant("peer-bot", ["close"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")
        self.assertTrue(ok)

    def test_grant_is_per_capability(self):
        """拿到 close 权 ≠ 拿到 clear 权（clear 抹光上下文、比 close 更狠）。"""
        self._grant("peer-bot", ["close"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            self.assertTrue(feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/close")[0])
            self.assertFalse(feishu_bridge._gate_verdict(self.BOT, "ou_peer", "/clear")[0])

    def test_agent_closing_itself_always_allowed(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "victim")}):
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_self", "/close")
        self.assertTrue(ok, "agent 关自己必须放行（主人 2026-08-03 拍板）")

    def test_account_aliases_map_to_same_capability(self):
        self._grant("peer-bot", ["account"])
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: "peer-bot")}):
            for c in ("/account", "/acc", "/账号"):
                self.assertTrue(feishu_bridge._gate_verdict(self.BOT, "ou_peer", c)[0], c)


class GrantClaimTest(unittest.TestCase):
    """claim 必须有【主人刚私聊过】做凭据——agent 不能自己给自己发权限。"""

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        self.sd = Path(self._tmp.name)
        feishu_bridge.STATE_DIR = self.sd

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def test_refuses_without_owner(self):
        ok, why = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertFalse(ok)
        self.assertIn("认主", why)

    def test_refuses_when_last_dm_is_not_owner(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_someone_else",
                                         "chat_updated": int(time.time())})
        ok, _ = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertFalse(ok)

    def test_refuses_when_owner_dm_too_old(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_owner",
                                         "chat_updated": int(time.time()) - 99999})
        ok, why = agent_grant.owner_dm_evidence("b", window_sec=1800, state_dir=self.sd)
        self.assertFalse(ok)
        self.assertIn("窗口", why)

    def test_accepts_recent_owner_dm(self):
        feishu_bridge.save_owner("b", "ou_owner")
        feishu_bridge.save_session("b", {"open_id": "ou_owner", "chat_updated": int(time.time())})
        ok, why = agent_grant.owner_dm_evidence("b", state_dir=self.sd)
        self.assertTrue(ok, why)

    def test_expired_grant_is_not_honored(self):
        p = self.sd / "bridge-grant-x.json"
        p.write_text(json.dumps({"caps": ["close"], "expires_at": int(time.time()) - 10}),
                     encoding="utf-8")
        self.assertFalse(agent_grant.has_cap("x", "close", self.sd))


if __name__ == "__main__":
    unittest.main()


class GateIdentifiesPeerViaStampTest(unittest.TestCase):
    """open_id 是 per-app 的 → 名册按 open_id 恒查不到 peer；必须退 a2a 戳认人。

    2026-08-03 上线当晚自查发现：初版闸只用 name_for_open_id，而名册里存的 open_id
    是【另一个应用视角】下的值（实证 tb25-phd-taoci：名册 ou_acd4e2d4… vs 实际发来 ou_3d12b059…）
    ⇒ 持有授权的 peer 也会被拒 = 闸装了等于谁都关不了。
    """
    BOT = {"name": "victim"}

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        feishu_bridge.STATE_DIR = Path(self._tmp.name)
        feishu_bridge.save_owner("victim", "ou_owner")
        (Path(self._tmp.name) / "bridge-grant-tb25-phd-taoci.json").write_text(
            json.dumps({"caps": ["close"], "expires_at": int(time.time()) + 3600}), encoding="utf-8")

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def test_peer_recognized_by_stamp_when_open_id_lookup_fails(self):
        text = "/close [飞书_from_tb25-phd-taoci_to_victim]"
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):   # 模拟 per-app 查不到
            ok, why = feishu_bridge._gate_verdict(self.BOT, "ou_unmatched", "/close", text)
        self.assertTrue(ok, f"持授权的 peer 应放行，实际被拒：{why}")
        self.assertIn("a2a戳", why)

    def test_still_denied_without_stamp_and_without_lookup(self):
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, _ = feishu_bridge._gate_verdict(self.BOT, "ou_unmatched", "/close", "/close")
        self.assertFalse(ok, "既查不到又没戳 → 必须 fail closed")

    def test_stamp_from_ungranted_peer_still_denied(self):
        text = "/close [飞书_from_tb25-somebody-else_to_victim]"
        with mock.patch.dict(sys.modules, {"registry": mock.MagicMock(
                name_for_open_id=lambda *a, **k: None)}):
            ok, _ = feishu_bridge._gate_verdict(self.BOT, "ou_x", "/close", text)
        self.assertFalse(ok, "戳能认人 ≠ 有授权")
