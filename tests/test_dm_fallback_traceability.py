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


class FakeNotify:
    """假的 webhook 喇叭：记下发出去的正文，好断言「降级原因印在群消息头上」。"""

    def __init__(self, ok=True):
        self.ok, self.bodies = ok, []

    def find_webhook_url(self):
        return "https://open.feishu.cn/hook/fake"

    def send_feishu(self, _url, body):
        self.bodies.append(body)
        return (True, "ok") if self.ok else (False, "Key Words Not Found")


class WebhookFallbackTraceTest(unittest.TestCase):
    def _run_fallback(self, tmp, notify, **kw):
        feishu_bridge.STATE_DIR = Path(tmp)
        with mock.patch.object(feishu_bridge, "_notify", notify):
            ok = feishu_bridge._webhook_fallback("正文", "bot", **kw)
        path = Path(tmp) / "bridge-receipts-bot.jsonl"
        lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return ok, lines

    def test_successful_fallback_still_leaves_a_receipt(self):
        """核心：兜底【成功】也必须留痕——否则降级投递长得跟正常送达一模一样。"""
        previous = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ok, recs = self._run_fallback(
                    tmp, FakeNotify(ok=True),
                    reason="code=UNKNOWN raw=230013 hint=Bot has NO availability to this user.",
                    intended="ou_owner")
        finally:
            feishu_bridge.STATE_DIR = previous
        self.assertTrue(ok)
        self.assertEqual(len(recs), 1, "兜底成功也要写回执（这正是事故里缺的那条）")
        self.assertTrue(recs[0]["delivered"])
        self.assertEqual(recs[0]["via"], "webhook-group")
        self.assertIn("230013", recs[0]["reason"])
        self.assertEqual(recs[0]["intended"], "ou_owner")

    def test_reason_is_printed_into_the_group_message(self):
        """降级原因要印在群里那条消息上——看到刷屏的人当场知道为什么，不用翻日志反推。"""
        previous = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                notify = FakeNotify(ok=True)
                self._run_fallback(tmp, notify, reason="raw=230013 Bot has NO availability",
                                   intended="ou_owner")
        finally:
            feishu_bridge.STATE_DIR = previous
        body = notify.bodies[0]
        self.assertIn("DM 回传失败转群兜底", body)
        self.assertIn("230013", body, "旧版只说『兜底』不说原因 → 主人只能肉眼发现")
        self.assertIn("ou_owner", body)

    def test_failed_fallback_also_leaves_a_receipt(self):
        previous = feishu_bridge.STATE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ok, recs = self._run_fallback(tmp, FakeNotify(ok=False), reason="raw=230013")
        finally:
            feishu_bridge.STATE_DIR = previous
        self.assertFalse(ok)
        self.assertEqual(len(recs), 1)
        self.assertFalse(recs[0]["delivered"])

    def test_guaranteed_send_passes_the_real_error_into_the_fallback(self):
        """端到端：真实 API 报错要一路带进兜底，而不是在中间被丢掉变成『未知原因』。"""
        seen = {}

        def fake_fallback(text, name, reason=None, intended=None):
            seen.update(text=text, name=name, reason=reason, intended=intended)
            return True

        async def fake_checked(_ch, _cid, _payload, _name, _kind):
            return False, "code=UNKNOWN raw=230013 hint=Bot has NO availability to this user.", False

        with mock.patch.object(feishu_bridge, "_send_checked", fake_checked), \
                mock.patch.object(feishu_bridge, "_webhook_fallback", fake_fallback):
            via = asyncio.run(feishu_bridge.guaranteed_send(None, "ou_peer_bot", "答案", "bot"))

        self.assertEqual(via, "webhook")
        self.assertIn("230013", seen["reason"] or "")
        self.assertEqual(seen["intended"], "ou_peer_bot", "本该投的目标要一起留痕")


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


class Peer私聊兜底(unittest.TestCase):
    """锁死 2026-08-30 新增的兜底第一顺位：借另一个 bot 的凭据私聊主人。

    背景：原来唯一兜底是群喇叭 webhook，2026-07 有人在飞书后台给它加了关键词校验，
    此后 748 次兜底全被拒(19024)。8-30 洪水那天医生喊「需人工」喊的就是这条死通道，
    主人 42 分钟一无所知。peer DM 不依赖任何后台可变设置。
    """

    ROSTER = [
        {"name": "坏了的bot", "app_id_env": "A_ID", "app_secret_env": "A_SEC"},
        {"name": "没认过主的", "app_id_env": "B_ID", "app_secret_env": "B_SEC"},
        {"name": "只是推测的", "app_id_env": "C_ID", "app_secret_env": "C_SEC"},
        {"name": "好用的peer", "app_id_env": "D_ID", "app_secret_env": "D_SEC"},
    ]
    OWNERS = {
        "坏了的bot": {"open_id": "ou_自己"},
        "没认过主的": {},
        "只是推测的": {"open_id": "ou_猜的", "provisional": True},
        "好用的peer": {"open_id": "ou_peer专属"},
    }

    def _run(self, api_impl, roster=None):
        calls = []
        def api(method, url, token=None, body=None, **kw):
            calls.append({"url": url, "body": body, "token": token})
            return api_impl(url, body)
        fake_rest = mock.Mock(api=api)
        env = {k: "v" for b in self.ROSTER for k in (b["app_id_env"], b["app_secret_env"])}
        with mock.patch.dict(sys.modules, {"feishu_rest": fake_rest}), \
             mock.patch.dict(feishu_bridge.os.environ, env, clear=False), \
             mock.patch.object(feishu_bridge, "load_bots", lambda: roster or self.ROSTER), \
             mock.patch.object(feishu_bridge, "load_owner_record", lambda n: self.OWNERS.get(n, {})):
            got = feishu_bridge._peer_dm_fallback("正文", "坏了的bot", "230013")
        return got, calls

    @staticmethod
    def _ok(url, body):
        if "tenant_access_token" in url:
            return {"code": 0, "tenant_access_token": "tok"}
        return {"code": 0}

    def test_跳过自己_跳过没认主_跳过provisional_用好的peer(self):
        got, calls = self._run(self._ok)
        self.assertEqual(got, "好用的peer")
        sends = [c for c in calls if "im/v1/messages" in c["url"]]
        self.assertEqual(len(sends), 1, "只该发一次")
        self.assertEqual(sends[0]["body"]["receive_id"], "ou_peer专属",
                         "必须用【peer 自己记的】主人 open_id —— open_id 按飞书应用隔离，"
                         "拿失败 bot 的 id 配 peer 的 token 会 230013 找不到人")

    def test_正文里带上是谁代发的(self):
        _, calls = self._run(self._ok)
        sent = json.loads([c for c in calls if "im/v1/messages" in c["url"]][0]["body"]["content"])
        self.assertIn("好用的peer", sent["text"], "主人要一眼看出是谁代发的")
        self.assertIn("230013", sent["text"], "降级原因必须带上")
        self.assertNotIn("{peer}", sent["text"], "占位符必须被替换")

    def test_全部失败返回None而不是抛异常(self):
        got, _ = self._run(lambda u, b: {"code": 99, "msg": "boom"})
        self.assertIsNone(got)

    def test_底层抛异常也不许逃逸(self):
        def boom(u, b): raise RuntimeError("网络炸了")
        got, _ = self._run(boom)
        self.assertIsNone(got, "兜底路径自己绝不能抛 —— 它是最后一道防线")

    def test_名册读不了也不许抛(self):
        with mock.patch.object(feishu_bridge, "load_bots",
                               side_effect=RuntimeError("名册坏了")):
            self.assertIsNone(feishu_bridge._peer_dm_fallback("x", "b", "why"))

    def test_绝不调用会sys_exit的feishu_rest高层函数(self):
        """feishu_rest.tenant_token / send_msg 失败时 sys.exit(2)/(1)。
        在兜底路径里调它们 = 一次 token 失败就杀掉整个桥进程，比没有兜底更糟。"""
        import inspect
        src = inspect.getsource(feishu_bridge._peer_dm_fallback)
        self.assertNotIn("tenant_token(", src)
        self.assertNotIn("send_msg(", src)

    def test_peer成功时不再走群喇叭(self):
        notify = FakeNotify()
        with mock.patch.object(feishu_bridge, "_peer_dm_fallback", lambda *a: "某peer"), \
             mock.patch.object(feishu_bridge, "_notify", notify), \
             mock.patch.object(feishu_bridge, "receipt", lambda *a, **k: None), \
             mock.patch.object(feishu_bridge, "blog", lambda *a, **k: None):
            ok = feishu_bridge._webhook_fallback("正文", "某bot", "原因", "ou_x")
        self.assertTrue(ok)
        self.assertEqual(notify.bodies, [], "peer 私聊成功后不该再刷群")
