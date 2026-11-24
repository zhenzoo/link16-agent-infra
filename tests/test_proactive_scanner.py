import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_scanner as ps


class ProactiveScannerTests(unittest.TestCase):
    def test_audio_listen_request_routes_to_human_decision(self):
        msg = {
            "message_id": "om_1",
            "create_time": "2026-11-23 10:24",
            "chat_id": "oc_audio",
            "chat_name": "TC101S音频方案确认",
            "chat_type": "group",
            "sender": {"id": "ou_wu", "name": "吴泰云"},
            "content": "@朱镇 加不加的输出都弄来听听吧，信噪比会变差",
            "message_app_link": "https://applink.feishu.cn/client/chat/open?openChatId=oc_audio&position=1174",
        }
        item = ps.classify_message(msg, owner_name="朱镇", owner_open_id="ou_owner")
        self.assertEqual(item["lane"], "action_human")
        self.assertIn("position=1174", item["app_link"])

    def test_prd_link_request_routes_to_auto_agent(self):
        msg = {
            "message_id": "om_2",
            "create_time": "2026-11-18 10:27",
            "chat_id": "oc_patent",
            "chat_name": "TC101S专利拟写",
            "chat_type": "group",
            "sender": {"id": "ou_tang", "name": "汤张昕"},
            "content": "@朱镇 朱镇可以把音频算法相关的prd也可以发一下",
        }
        item = ps.classify_message(msg, owner_name="朱镇", owner_open_id="ou_owner")
        self.assertEqual(item["lane"], "action_auto")

    def test_owner_replied_after_closes_item(self):
        msg = {
            "message_id": "om_3",
            "create_time": "2026-11-20 18:26",
            "chat_id": "oc_sport",
            "chat_name": "Sport业务产品团队",
            "chat_type": "group",
            "sender": {"id": "ou_mi", "name": "米相维"},
            "content": "@朱镇 你下周要用X6是吗",
        }
        item = ps.classify_message(
            msg, owner_name="朱镇", owner_open_id="ou_owner", owner_replied_after=True
        )
        self.assertEqual(item["lane"], "fyi_closed")

    def test_approval_card_extracts_direct_links_and_ignores_self_urge(self):
        bot_card = {
            "message_id": "om_4",
            "create_time": "2026-11-23 11:15",
            "chat_id": "oc_app",
            "chat_type": "p2p",
            "sender": {"id": "cli_app", "name": "审批"},
            "content": '<card title="⏰ 每日待办提醒">[1. 办公用品采购](https://applink.feishu.cn/client/mini_program/open?id=1)</card>',
        }
        self_urge = dict(bot_card, message_id="om_4b", sender={"id": "ou_owner", "name": "朱镇"})
        it_bot = ps.classify_message(bot_card, owner_name="朱镇", owner_open_id="ou_owner")
        it_self = ps.classify_message(self_urge, owner_name="朱镇", owner_open_id="ou_owner")
        self.assertEqual(it_bot["lane"], "action_human")
        self.assertEqual(len(it_bot["extra_links"]), 1)
        self.assertEqual(it_self["lane"], "fyi_closed")

    def test_render_html_hides_replied_noise_and_renders_workbench_preview(self):
        replied_item = ps.classify_message({
            "message_id": "om_old", "create_time": "2026-11-20 18:26",
            "chat_name": "Sport业务产品团队", "sender": {"id": "ou_mi", "name": "米相维"},
            "content": "@朱镇 已回复的旧消息不应出现在工作台",
        }, owner_name="朱镇", owner_open_id="ou_owner", owner_replied_after=True)
        payload = {
            "schema": "link16-proactive-workbench-v3",
            "generated_at": "2026-11-23 15:12",
            "bot": "tb26-link16",
            "owner_name": "朱镇",
            "days": 7,
            "preflight": {"passed": 5, "total": 5, "all_ready": True, "checks": {}, "excluded": []},
            "counts": {"total": 1, "action_human": 0, "action_auto": 0, "fyi_closed": 1, "fleet_total": 1, "fleet_chat_ok": 1},
            "items": [replied_item],
            "fleet": [{"bot": "tb26-link16", "app_id": "cli_1", "status": "ready", "owner_matches": True, "chat_scopes_ok": True, "has_wiki_node": True, "has_sheet_write": True, "has_minutes": True, "scope_count": 51}],
        }
        workbench = {
            "drafts": [{
                "id": "#1", "target_name": "TC101S音频方案确认", "status": "待确认发送",
                "message": "@于洋 泡棉仅衰减 1.7dB",
                "basis": "4段WAV实测",
                "links": [{"label": "直达于洋", "url": "https://applink.feishu.cn/client/chat/open?openChatId=oc_d273&position=1181"}],
            }],
            "sheet_previews": [{
                "title": "TC101S-专利挖掘", "sheet_name": "Sheet1", "feishu_patent_header": True,
                "rows": [{"row": 5, "cells": [{"value": "产品结构", "kind": "existing"}, {"value": "1", "kind": "existing"}, {"value": "双IMU解耦", "kind": "added"}]}],
            }],
        }
        html_text = ps.render_html(payload, actions_pack=workbench)
        self.assertIn("https://applink.feishu.cn/client/chat/open?openChatId=oc_d273&amp;position=1181", html_text)
        self.assertIn("feishu-bubble", html_text)
        self.assertIn("feishu-sheet", html_text)
        self.assertIn("cell-new", html_text)
        self.assertNotIn("已回复的旧消息不应出现在工作台", html_text)


if __name__ == "__main__":
    unittest.main()
