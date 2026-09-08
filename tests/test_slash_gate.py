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
    是【另一个应用视角】下的值（实证 tb25-phd-taoci：名册 ou_xxxxxxx3… vs 实际发来 ou_xxxxxxx4…）
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


class GrantCliEntrypointTest(unittest.TestCase):
    """堵「测了功能、没测入口」那个洞（2026-08-03 · TB24 抓到）。

    我原来所有单测都显式传 `state_dir=`，**恰好避开了唯一会崩的那条路径**：
    `_state_dir()` 里 `from bridge_env import resolve_project_root` 引的是个**不存在的函数**，
    于是 CLI 一跑就 ImportError —— 形状是「闸装上了，但开锁的钥匙是坏的」：
    6 个破坏性命令被拦住，而唯一能解锁的 claim 跑不起来。桥的运行时闸反而没事
    （has_cap 显式传 STATE_DIR → `state_dir or _state_dir()` 短路，压根不调）。
    """

    def test_state_dir_resolves_without_explicit_arg(self):
        """判据①：不传 state_dir 也必须能解析（这正是 CLI 走的路）。"""
        d = agent_grant._state_dir()          # 不传参 —— 修复前这里直接 ImportError
        self.assertTrue(str(d), "解析不出 state_dir")

    def test_state_dir_matches_the_bridge_state_dir(self):
        """判据②（比①更要紧）：CLI 写授权的目录，必须就是桥读授权的目录。

        若两者不一致，CLI 不报错、claim 也显示成功，但闸去另一个目录读 → 授权永远不生效。
        这是比「CLI 崩了」更隐蔽的坏法：**看起来成功，实际没生效**。"""
        self.assertEqual(
            Path(agent_grant._state_dir()).resolve(),
            Path(feishu_bridge.STATE_DIR).resolve(),
            "claim 写到 A、闸去 B 读 —— 授权会静默失效")

    def test_cli_status_runs_bare(self):
        """判据①的端到端形态：真的把 CLI 当命令跑一遍，不带任何 --state-dir。"""
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "feishu" / "agent_grant.py"), "status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
        self.assertEqual(r.returncode, 0, f"CLI 裸跑失败：{r.stderr[-400:]}")
        self.assertNotIn("ImportError", r.stderr)


class RosterProfileResolvesAfterLoadTest(unittest.TestCase):
    """TB24 的建议：光测「名册显式写死」不够，还要测「装载后真能解析」。

    PLAN-923 把 profile_name() 改成 fail-closed（删掉 runtime 默认兜底），
    `_reuse_check` 是带 required=True 调它的 ⇒ 解析不出 = 那个 bot 起不了会话。
    而 `_apply_roster_defaults()` 会在 load_bots() 时注入 profile —— 只读裸 JSON 判断会误报
    （我给 TB24 的检查方法就栽在这），所以这条闸必须跑**真 loader**。
    本机名册 gitignored、每台机不同 → 没有名册就跳过。

    ⚠️ **这条闸必须两台机各跑一次才算覆盖住**（2026-08-03 TB24 交叉验证发现）：
    它要防的是「装载注入之后还能不能解析」，而**能不能跑到那条路径取决于本机名册的形状**——
      · TB25：顶层 `defaults` 为空、32 个 bot 全部显式写 profile ⇒ 走的是「本来就显式」这条路，
        **根本没验到注入**（单看 TB25 绿灯会误以为这条测试在起作用）。
      · TB24：名册有 `defaults.profiles(claude→ccp2)`、9 个 bot 的 profile 靠
        `_apply_roster_defaults()` 装载时注入 ⇒ 在那儿才真正验到盲区。
    同理 PLAN-923 那条「名册显式写死」的持续闸，在 TB24 是唯一 fail、补完才绿，在 TB25 一直是绿的。
    ⇒ 两台机的名册形状不同，**互为对方的盲区补全**。以后凡是跟本机 roster 相关的闸，
    别只看一台机绿就下结论。
    """

    def test_every_loaded_bot_resolves_a_profile(self):
        import agent_runtime
        if not (ROOT / "feishu" / "bridge-bots.local.json").exists():
            self.skipTest("本机没有 bridge-bots.local.json（每机各自维护·gitignored）")
        bad = []
        for b in feishu_bridge.load_bots():
            try:
                agent_runtime.profile_name(b, required=True)
            except Exception as e:  # noqa: BLE001
                bad.append(f"{b.get('name')}: {e}")
        self.assertEqual(bad, [], "这些 bot 解析不出 profile → 它们起不了会话")
