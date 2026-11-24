import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import proactive_scanner as ps


class ProactiveScannerTests(unittest.TestCase):
    def test_assignment_shows_real_executor_separately_from_the_approval_handler(self):
        dispatch = {"machine": "TX26", "agent": "baseball-2", "state": "恢复", "last_event": "收到重试回执",
                    "at": "2026-11-24 13:40", "receipt_url": "https://example.com/review",
                    "profile": "example-profile", "model": "confirmed-model", "verification": "已核",
                    "quota": "未经核验的数值", "quota_verification": "待目标机核", "error": "PRIVATE_TRACE"}
        pack = {
            "drafts": [{"target_name": "评审群", "message": "请审阅", "assignment": dispatch}],
            "files": [{"title": "收纳包", "assignment": dispatch}],
            "sheet_previews": [{"title": "周报", "assignment": dispatch}],
            "approvals": [{"title": "采购", "assignment": {"machine": "TB26", "handler": "产品负责人", "verified_by": "主号核验",
                          "at": "13:34", "last_event": "当前节点为交付确认"}}],
            "sources": ["历史纯文本来源", {"title": "独立待跟进项", "detail": "周会上需补齐", "assignment": dispatch},
                        {"title": "仅作依据", "actionable": False, "assignment": dispatch}],
        }
        result = ps.render_html({"bot": "test", "generated_at": "13:41"}, pack)
        main = result
        self.assertEqual(main.count("TX26 / baseball-2"), 4)
        self.assertNotIn("最新回执", main)
        self.assertIn("独立待跟进项", main)
        self.assertNotIn("仅作依据", main)
        self.assertNotIn("历史纯文本来源", main)
        self.assertNotIn('id="audit"', main)
        self.assertIn('办理　<b>产品负责人</b>', main)
        self.assertIn('核验　<b>TB26 / 主号核验</b>', main)
        self.assertNotIn('TB26 / 智能体待核', main)
        self.assertIn("模型 · confirmed-model（已核）", main)
        self.assertIn("额度 · 待目标机核", main)
        self.assertNotIn("未经核验的数值", result)
        self.assertNotIn("PRIVATE_TRACE", result)

    def test_review_decision_removes_only_the_original_draft_and_keeps_sent_record(self):
        draft = {"id": "#1", "target_name": "收件人", "message": "旧问题", "links": [{"url": "https://example.com/old"}]}
        new = dict(draft, message="新问题", links=[{"url": "https://example.com/new"}])
        sent = dict(draft, message="已发原话", verified=True, message_id="m1", receipt_url="https://example.com/sent")
        sent_in_older_pack = dict(sent, message="旧格式已发消息", status="已发送", message_id="m2")
        result = ps.render_html({"bot":"test"}, {"drafts":[draft, new, sent_in_older_pack], "sent_messages":[sent],
                                "owner_closed_keys":[ps.review_key("drafts", draft)], "reminders": []})
        self.assertNotIn("旧问题", result)
        self.assertIn("新问题", result)
        self.assertIn("已发原话", result)
        self.assertIn("旧格式已发消息", result)
        self.assertIn('href="#reminders"', result)

    def test_raw_account_errors_temporary_links_and_unconfirmed_models_stay_off_board(self):
        record = {"assignment": {"machine": "TB26", "agent": "baseball-3", "state": "卡住",
                  "last_event": "auth error PRIVATE_ACCOUNT@example.com", "receipt_url": "https://example.com/?user_code=PRIVATE_CODE",
                  "model": "PRIVATE_MODEL", "verification": "待目标机核"}}
        result = ps._assignment_line(record)
        self.assertIn("卡住", result)
        self.assertIn("模型 · 待目标机核", result)
        self.assertNotIn("PRIVATE_", result)
        self.assertIn("验收回执待核", ps._assignment_line({"assignment": {"agent": "peer", "state": "已验"}}))

    def test_partial_grants_and_unverified_identity_are_not_ready(self):
        identity = {
            "owner_matches": True, "verified": True, "status": "ready",
            "scopes": ["minutes:minutes.search:read", "wiki:node:read", "sheets:spreadsheet:write_only", "docx:document:write_only"],
        }
        web = SimpleNamespace(discover_chrome_direct_browser=lambda: {"ok": True})
        with patch.object(ps.audit, "user_identity_status", return_value=identity), patch.dict(sys.modules, {"send_as_user_web": web}):
            result = ps.run_preflight("test")
            identity["verified"] = False
            stale = ps.run_preflight("test")
        self.assertFalse(result["checks"]["wiki_and_minutes"]["ok"])
        self.assertIn("docs:document.comment:create", result["checks"]["doc_sheet_write"]["missing"])
        self.assertFalse(result["checks"]["doc_sheet_write"]["ok"])
        self.assertFalse(stale["identity_ready"])

    def test_failed_search_and_unrelated_group_reply_do_not_close_an_item(self):
        messages = [
            {"message_id": "question", "content": "请帮忙确认", "chat_type": "group", "chat_id": "team", "sender": {"id": "peer"}},
        ]
        def api(_bot, args):
            if "--is-at-me" in args:
                return {"data": {"messages": messages}}
            if "--query" in args:
                return {"ok": False, "error": {"type": "scope_missing"}}
            if "+chat-messages-list" in args:
                return {"data": {"messages": [
                    {"message_id": "answer", "sender": {"id": "owner"}, "parent_id": "a-different-question"},
                    *messages,
                ]}}
            return {"data": {"items": []}}

        with (patch.object(ps, "run_preflight", return_value={"identity_ready": True, "user_name": "产品负责人"}),
              patch.object(ps.docio, "owner_open_id", return_value="owner"),
              patch.object(ps.audit, "roster_bots", return_value=[]), patch.object(ps, "run_lark_json", side_effect=api)):
            scan = ps.scan_feishu("test")
        self.assertFalse(scan["items"][0]["owner_replied_after"])
        self.assertEqual(scan["scan_sources"][0]["status"], "读取失败")
        self.assertEqual(scan["scan_sources"][0]["detail"], "scope_missing")

    def test_proof_bound_messages_priority_and_safe_source_links(self):
        payload = {"bot": "test", "generated_at": "2026-11-24", "preflight": {}}
        sent = {"id": "#1", "target_name": "A", "message": "已确认的原话", "verified": True,
                "message_id": "example", "receipt_url": "https://applink.feishu.cn/client/chat/open"}
        pack = {
            "drafts": [sent, dict(sent, target_name="另一个项目", message="同编号的新草稿")],
            "sent_messages": [sent, dict(sent, target_name="乙", message="待核回执的原话", verified=False)],
            "files": [{"title": "低优先级文件", "priority": "P2", "url": "draft.txt"},
                      {"title": "优先评审件", "priority": "P0", "preview": "版本 <A> & B", "url": "reference.html"}],
            "approvals": [{"title": "差旅", "fields": [{"label": "出差地点", "value": "目的地"}], "missing": ["票据"]}],
            "sources": [{"title": "周会", "status": "全文可读", "detail": "已核对"}],
        }
        result = ps.render_html(payload, pack)
        self.assertEqual(result.count("已确认的原话"), 1)
        self.assertIn("1 待发 · 1 已发", result)
        self.assertIn("发送回执待核", result)
        self.assertIn(">乙</span>", result)
        self.assertEqual(result.count('data-copy="'), 1)
        self.assertIn("同编号的新草稿", result)
        self.assertIn("版本 &lt;A&gt; &amp; B", result)
        self.assertLess(result.index("优先评审件"), result.index("低优先级文件"))
        self.assertIn('href="reference.html"', result)
        self.assertNotIn('href="draft.txt"', result)
        self.assertEqual(ps._url("file://server/share/private.html", local=True), "")
        self.assertIn("<dt>出差地点</dt><dd>目的地</dd>", result)
        self.assertIn("待补 票据", result)

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

    def test_weekly_sheet_preserves_header_origin_blank_column_zero_and_named_link(self):
        preview = {"columns": ["A", "B", "C"], "headers": ["完成度", "提交人", "成果"], "header_row": 8,
                   "rows": [{"row": 21, "url": "https://example.com/sheet?range=A21", "cells": {
                       "A": {"value": 0}, "C": {"rich_text": [
                           {"type": "text", "text": "版本 <一> "},
                           {"type": "link", "link": "https://example.com/result", "text": "原稿"},
                       ]},
                   }}]}
        html = ps._render_sheet_previews([preview])
        self.assertIn('<th class="gutter">8</th>', html)
        self.assertIn('rel="noopener noreferrer">21</a>', html)
        self.assertIn('class="cell-txt">0</div></td><td class="cell-empty">', html)
        self.assertIn('版本 &lt;一&gt; <a href="https://example.com/result"', html)
        self.assertIn('rel="noopener noreferrer">原稿</a>', html)

    def test_native_approval_order_detail_rows_draft_links_and_payment_privacy(self):
        template = [
            {"name": "用途", "type": "input", "required": True, "value": "评审样品"},
            {"name": "采购明细", "children": ["名称", "数量"], "rows": [
                [{"key": "名称", "value": "样品 A"}, {"key": "数量", "value": 0}],
                [{"key": "名称", "value": "样品 B"}, {"key": "数量", "value": 2}],
            ]},
            {"name": "报销分支", "conditional": True, "children": [{"name": "收款人", "value": "PRIVATE_HOLDER"}]},
            {"name": "账号", "type": "input", "value": "PRIVATE_ACCOUNT"},
        ]
        cards = [
            {"title": "采购", "form_fields": template, "preview": [{"key": "金额", "value": "旧摘要"}]},
            {"title": "费用报销", "local_draft": True, "url": "https://example.com/new", "preview": [
                {"key": "附件", "value": "待发票"}, {"key": "总金额", "value": "待票据核算"},
                {"key": "银行卡号", "value": "PRIVATE_CARD"}, {"key": "备注", "value": "卡号：PRIVATE_NOTE"},
            ], "form_fields": [{"name": "附件", "type": "attachmentV2"}, {"name": "差旅明细", "conditional": True, "children": ["日期", "金额"]}],
             "trip_url": "https://example.com/trip", "history_trip_url": "https://example.com/history"},
        ]
        result = ps._render_approvals_section(cards)
        self.assertLess(result.index("评审样品"), result.index("采购明细"))
        self.assertLess(result.index("样品 A"), result.index("样品 B"))
        self.assertIn("第 2 项", result)
        self.assertIn("<div>0</div>", result)
        self.assertIn("按条件显示", result)
        self.assertIn("待发票", result)
        self.assertIn("打开飞书空白模板", result)
        self.assertIn('href="https://example.com/history"', result)
        self.assertNotIn("旧摘要", result)
        self.assertNotIn("PRIVATE_", result)

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
        self.assertIn('class="message-body"', html_text)
        self.assertIn("feishu-sheet", html_text)
        self.assertIn("cell-new", html_text)
        self.assertNotIn("已回复的旧消息不应出现在工作台", html_text)


if __name__ == "__main__":
    unittest.main()
