#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whoami 不许再把【主人的】open_id 报成【bot 自己的】。

2026-08-18 血证：`bridge-session-<bot>.json` 里那个字段【叫】open_id，语义却是 DM 对端
（= 主人）。旧版 whoami 直接把它填进 info["open_id"] 并打印成「我的 open_id」，还因为
`info["open_id"] or oid` 的短路，把当场从飞书 API 查回来的**真** bot id 给丢了。
结果 tb25-link16 照单全收，据此对外发布了一条错误的 per-app 隔离实证。

open_id 是**按 app 隔离**的：只有 bot/v3/info（本 app 视角）拿到的才是它自己。
查不到就留 None —— 宁可不显示，也别显示一个会误导下一个读它的人的值。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import whoami  # noqa: E402


class WhoamiKeepsOwnerAndSelfApart(unittest.TestCase):
    def _whoami_with(self, session_payload, bot_self=None):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "bridge-session-fakebot.json").write_text(
                json.dumps(session_payload), encoding="utf-8")
            env = {"FEISHU_BRIDGE_SESSION": "fakebot", "FEISHU_BRIDGE_OUTBOX_DIR": str(state)}
            with mock.patch.dict("os.environ", env, clear=False), \
                 mock.patch.object(whoami, "_load_roster", return_value=([], None)):
                if bot_self is None:
                    # 拿不到凭据 / 网络挂了：真 id 查不到
                    with mock.patch.dict(sys.modules, {"send_feishu_msg": _NoCreds()}):
                        return whoami.whoami()
                with mock.patch.dict(sys.modules, {"send_feishu_msg": _FakeSfm(bot_self)}):
                    return whoami.whoami()

    def test_session_open_id_is_reported_as_owner_not_self(self):
        info = self._whoami_with({"open_id": "ou_OWNER", "chat_id": "oc_x"},
                                 bot_self=("ou_BOTSELF", "fakebot"))
        self.assertEqual(info["owner_open_id"], "ou_OWNER", "session 文件里那个是主人的")
        self.assertEqual(info["open_id"], "ou_BOTSELF", "自己的只能来自 bot/v3/info")

    def test_unknown_self_id_stays_empty_instead_of_borrowing_owner(self):
        # 这条正是旧 bug：查不到自己时，绝不能拿主人的顶上
        info = self._whoami_with({"open_id": "ou_OWNER", "chat_id": "oc_x"}, bot_self=None)
        self.assertIsNone(info["open_id"], "查不到就留空，不许拿主人的冒充")
        self.assertEqual(info["owner_open_id"], "ou_OWNER")


class _FakeSfm:
    def __init__(self, bot_self):
        self._bot = bot_self

    def _creds_for(self, _slug):
        return ("app_id", "app_secret")

    def _bot_self(self, *_a):
        return self._bot


class _NoCreds:
    def _creds_for(self, _slug):
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
