"""兜底留痕 + DM 坐标不被群污染的回归测试（2026-08-02 · taoci-7 刷群事故）。

事故形状：新 bot 只在群里被 peer @ 过 → 会话 open_id 被存成【peer bot 的 open_id】→
route=p2a 回信把 peer 当主人发私聊 → 飞书 230013（非 retryable）→ 退 webhook 投【群】→ 刷屏。
而兜底【成功】时上层只看到 True，outbox/receipts 一片干净 —— 「兜底成功了，所以没人知道 DM 是坏的」。
这里锁死两件事：① 降级必留痕（含真实原因）② 群消息绝不写进 DM 坐标。
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_bridge  # noqa: E402


class 未送达必须如实记账(unittest.TestCase):
    """2026-08-30 主人拍板：兜底全部拆除，DM 发不到就是发不到，绝不改投任何通道。

    为什么拆（三次同形状事故）：
      · 2026-08-02 taoci-7：DM 坏了几十小时没人知道，因为兜底一直「成功」刷群 767 条；
      · 2026-07-06 主人已砍掉【瞬时错走 webhook】那半；
      · 2026-08-30 洪水：医生朝着早被关键词校验拒收的群喇叭喊「需人工」（748 次失败），
        主人 42 分钟一无所知。
    兜底给失败开了一条特殊通道，让「没送到」长得像「送到了」。留痕要求（承自 2026-08-02）
    不变，但只留痕、不改投。
    """

    def _run(self, tmp, **kw):
        feishu_bridge.STATE_DIR = Path(tmp)
        ok = feishu_bridge._record_undelivered("正文", "bot", **kw)
        path = Path(tmp) / "bridge-receipts-bot.jsonl"
        recs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return ok, recs

    def test_未送达要写回执且标明没送到(self):
        prev = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ok, recs = self._run(
                    tmp, reason="code=UNKNOWN raw=230013 hint=Bot has NO availability to this user.",
                    intended="ou_owner")
        finally:
            feishu_bridge.STATE_DIR = prev
        self.assertFalse(ok, "没送到就必须返回 False —— 上层据此判失败，不许有假绿灯")
        self.assertEqual(len(recs), 1, "必须留痕（这正是 2026-08-02 事故里缺的那条）")
        self.assertFalse(recs[0]["delivered"])
        self.assertIsNone(recs[0]["via"], "没有任何替代通道 → via 必须是 None")
        self.assertIn("230013", recs[0]["reason"], "真实报错要带上，不许变成『未知原因』")
        self.assertEqual(recs[0]["intended"], "ou_owner", "本该投的目标要一起留痕")

    def test_真失败返回failed而不是某个替代通道(self):
        """端到端：真实 API 报错 → guaranteed_send 只能回 'failed'，
        绝不能再出现 'webhook' 这种「投到别处也算成功」的返回值。"""
        seen = {}

        def rec(text, name, reason=None, intended=None):
            seen.update(reason=reason, intended=intended)
            return False

        async def fake_checked(_ch, _cid, _payload, _name, _kind):
            return False, "code=UNKNOWN raw=230013 hint=Bot has NO availability to this user.", False

        with mock.patch.object(feishu_bridge, "_send_checked", fake_checked),                 mock.patch.object(feishu_bridge, "_record_undelivered", rec):
            via = asyncio.run(feishu_bridge.guaranteed_send(None, "ou_peer_bot", "答案", "bot"))

        self.assertEqual(via, "failed")
        self.assertIn("230013", seen["reason"] or "")
        self.assertEqual(seen["intended"], "ou_peer_bot")

    def test_瞬时错交重投_不记账也不改投(self):
        """瞬时网络错要交给耐心重投【对的目标】，不该当成永久失败记账。"""
        called = []

        async def fake_checked(_ch, _cid, _payload, _name, _kind):
            return False, "getaddrinfo failed", True          # transient=True

        with mock.patch.object(feishu_bridge, "_send_checked", fake_checked),                 mock.patch.object(feishu_bridge, "_record_undelivered",
                                  lambda *a, **k: called.append(1)):
            via = asyncio.run(feishu_bridge.guaranteed_send(None, "ou_x", "答案", "bot"))
        self.assertEqual(via, "failed")
        self.assertEqual(called, [], "瞬时错不该记成永久未送达")

    def test_反向闸_代码里不许再有任何改投别处的通道(self):
        """任何人再想加「发不出去就投到别处」（群喇叭 / 借别的 bot / 任何第二通道），
        这条测试必须先红。兜底本身就是被否掉的方案，不是实现细节。"""
        src = Path(feishu_bridge.__file__).read_text(encoding="utf-8")
        # tokenize 剥掉注释和字符串（含 docstring）再判 —— 只按行首 # 过滤会把文档里的
        # 历史说明误判成活代码，那种尺子会一直红、久了就被人注释掉。
        import io as _io, tokenize as _tk
        code = [t.string for t in _tk.generate_tokens(_io.StringIO(src).readline)
                if t.type not in (_tk.COMMENT, _tk.STRING)]
        live = [t for t in code if "webhook" in t.lower()]
        self.assertEqual(live, [], f"出现了活的 webhook 代码符号：{live}")
        for banned in ("def _webhook_fallback", "def _peer_dm_fallback",
                       "import notify", "_notify.send_feishu", "find_webhook_url"):
            self.assertNotIn(banned, src, f"{banned!r} 已被拍板拆除，不许复活")
        self.assertIn("_record_undelivered", src, "记账函数必须在")
        self.assertFalse((Path(feishu_bridge.__file__).parent / "notify.py").exists(),
                         "feishu/notify.py 是群喇叭本体，已随兜底一起删除")


class DmCoordinateTest(unittest.TestCase):
    """mirror_target 的兜底链不许凭空造出一个 DM 目标。"""

    def test_no_owner_and_no_dm_session_yields_no_target(self):
        previous = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                feishu_bridge.STATE_DIR = Path(tmp)
                # 群消息不再写 DM 坐标 ⇒ 新 bot 在被主人私聊前，会话里就是空的
                self.assertIsNone(feishu_bridge.mirror_target("brand-new-bot"))
        finally:
            feishu_bridge.STATE_DIR = previous

    def test_owner_file_wins_over_stale_session_open_id(self):
        """主人私聊认主后，owner 文件必须盖过历史上被群污染的 open_id（这就是热修的原理）。"""
        previous = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                feishu_bridge.STATE_DIR = Path(tmp)
                feishu_bridge.save_session("b", {"open_id": "ou_peer_bot_poisoned"})
                self.assertEqual(feishu_bridge.mirror_target("b"), "ou_peer_bot_poisoned")
                feishu_bridge.save_owner("b", "ou_real_owner")
                self.assertEqual(feishu_bridge.mirror_target("b"), "ou_real_owner")
        finally:
            feishu_bridge.STATE_DIR = previous


class ProvisionalOwnerTest(unittest.TestCase):
    """工具补写的 owner 绝不能把真主人锁在门外（2026-08-02 全舰队补 owner 时引入的风险）。"""

    BOT = {"name": "b"}

    def setUp(self):
        self._prev = feishu_bridge.STATE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        feishu_bridge.STATE_DIR = Path(self._tmp.name)

    def tearDown(self):
        feishu_bridge.STATE_DIR = self._prev
        self._tmp.cleanup()

    def test_wrong_provisional_owner_is_corrected_by_a_real_dm(self):
        """补写的 open_id 猜错时，主人的第一条真 DM 必须被放行并【纠正】它，而不是被拒。"""
        feishu_bridge.save_owner("b", "ou_guessed_wrong", provisional=True)
        with mock.patch.object(feishu_bridge, "ALLOWED_OPEN_IDS", set()):
            self.assertTrue(feishu_bridge.is_allowed(self.BOT, "ou_real_owner"),
                            "真主人被临时 owner 挡在门外了——正是要避免的回归")
        self.assertEqual(feishu_bridge.load_owner("b"), "ou_real_owner")
        self.assertNotIn("provisional", feishu_bridge.load_owner_record("b"))

    def test_correct_provisional_owner_is_promoted(self):
        feishu_bridge.save_owner("b", "ou_owner", provisional=True)
        with mock.patch.object(feishu_bridge, "ALLOWED_OPEN_IDS", set()):
            self.assertTrue(feishu_bridge.is_allowed(self.BOT, "ou_owner"))
        self.assertNotIn("provisional", feishu_bridge.load_owner_record("b"),
                         "被真 DM 证实后应转正，不再是临时的")

    def test_confirmed_owner_still_rejects_strangers(self):
        """转正后的 owner 必须恢复原有的严格性——别把安全性一起放宽了。"""
        feishu_bridge.save_owner("b", "ou_owner")
        with mock.patch.object(feishu_bridge, "ALLOWED_OPEN_IDS", set()):
            self.assertFalse(feishu_bridge.is_allowed(self.BOT, "ou_stranger"))
        self.assertEqual(feishu_bridge.load_owner("b"), "ou_owner")


if __name__ == "__main__":
    unittest.main()

