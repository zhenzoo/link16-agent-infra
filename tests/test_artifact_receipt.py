"""SPEC-210 固定三行产物回执：标题行 / URL 行 / 本机绝对路径行，缺行用括号说明。"""
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import artifact_delivery  # noqa: E402
import bridge_outbox  # noqa: E402
import feishu_bridge  # noqa: E402


URL = "https://my.feishu.cn/docx/abc123"


class RenderReceiptTests(unittest.TestCase):
    def test_full_receipt_is_exactly_three_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "PLAN-1.md"
            local.write_text("x", encoding="utf-8")
            text = artifact_delivery.render_artifact_receipt("当前计划", url=URL, local_paths=[local])
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "📄 当前计划（飞书在线文档·登录飞书查看）：")
        self.assertEqual(lines[1], URL)
        self.assertEqual(lines[2], str(local.resolve()))
        self.assertNotIn("`", text)
        self.assertNotIn("file:///", text)

    def test_missing_url_line_is_parenthesised_not_dropped(self):
        text = artifact_delivery.render_artifact_receipt(
            "研究稿", local_paths=[r"C:\x\RESEARCH-1.md"],
            url_missing_reason=artifact_delivery.NO_ONLINE_SWITCH_OFF,
        )
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1], "（本机在线开关 off，本轮未建在线副本）")
        self.assertTrue(lines[2].endswith("RESEARCH-1.md"))

    def test_missing_local_path_line_is_parenthesised_not_dropped(self):
        text = artifact_delivery.render_artifact_receipt("在线稿", url=URL)
        lines = text.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[2], "（本地无此文件，仅在线文档）")

    def test_reason_never_double_wraps_parentheses(self):
        text = artifact_delivery.render_artifact_receipt(
            "t", url_missing_reason="（在线副本创建失败：403）")
        self.assertEqual(text.split("\n")[1], "（在线副本创建失败：403）")

    def test_media_receipt_lists_one_path_per_file(self):
        text = artifact_delivery.render_artifact_receipt(
            "截图", url=URL, local_paths=[r"C:\a\1.png", r"C:\a\2.png"], icon="🖼",
            label="飞书在线文档·2 个图片·在线看不占手机内存",
        )
        lines = text.split("\n")
        self.assertEqual(lines[0], "🖼 截图（飞书在线文档·2 个图片·在线看不占手机内存）：")
        self.assertEqual(len(lines), 4)

    def test_cli_receipt_infers_switch_off_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / "artifact-delivery.local.json"
            artifact_delivery.set_online_setting(False, policy)
            with mock.patch.object(artifact_delivery, "POLICY_PATH", policy), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                artifact_delivery.main(["receipt", "--title", "T", "--path", "a.md", "--json"])
        receipt = json.loads(out.getvalue())["receipt"].split("\n")
        self.assertEqual(receipt[1], "（本机在线开关 off，本轮未建在线副本）")
        self.assertTrue(receipt[2].endswith("a.md"))


class SendDocCardTests(unittest.TestCase):
    def test_send_doc_card_body_carries_title_url_and_local_path(self):
        class FakeChannel:
            def __init__(self, **_kwargs):
                pass

        fake_lark = types.SimpleNamespace(FeishuChannel=FakeChannel, OutboundImage=object, MediaSource=object)
        bot = {"name": "bot", "app_id": "app", "app_secret": "secret"}
        sent = {}

        async def fake_card_send(_ch, _target, body, _name):
            sent["body"] = body
            return "card"

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "deliverable.md"
            source.write_text("# 原始文件", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"lark_channel": fake_lark}), \
                    mock.patch.object(feishu_bridge, "assert_sender_identity"), \
                    mock.patch.object(feishu_bridge, "load_bots", return_value=[bot]), \
                    mock.patch.object(feishu_bridge, "mirror_target", return_value="ou_owner"), \
                    mock.patch.object(feishu_bridge, "load_owner", return_value="ou_owner"), \
                    mock.patch.object(feishu_bridge, "_publish_online_doc",
                                      return_value=({"url": URL, "granted": True}, "online_doc_native")), \
                    mock.patch.object(feishu_bridge, "card_send", side_effect=fake_card_send), \
                    mock.patch.object(feishu_bridge, "_queue_doc_delivery", return_value=None), \
                    mock.patch.object(feishu_bridge, "receipt"), \
                    mock.patch.object(feishu_bridge, "blog"), \
                    mock.patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as stopped:
                    feishu_bridge.cmd_send("bot", "", doc=str(source), doc_name="嵌软需求怎么写",
                                           as_json=True, online_override=True)
            self.assertEqual(stopped.exception.code, 0)
            lines = sent["body"].split("\n")
            self.assertEqual(lines[0], "📄 嵌软需求怎么写（飞书在线文档·登录飞书查看）：")
            self.assertEqual(lines[1], URL)
            self.assertEqual(lines[2], str(source.resolve()))
            self.assertEqual(len(lines), 3)

    def test_queue_record_carries_local_path_for_final_reconciliation(self):
        captured = {}
        with mock.patch.dict("os.environ", {"FEISHU_BRIDGE_SESSION": "bot"}), \
                mock.patch.object(feishu_bridge, "_load_turn_route", return_value={"kind": "p2a"}), \
                mock.patch.object(feishu_bridge.bridge_outbox, "append_record",
                                  side_effect=lambda _d, _b, rec: captured.update(rec) or True):
            feishu_bridge._queue_doc_delivery(
                "bot", explicit_to=None, doc_url=URL, title="T", stats={},
                direct_delivered=True, local_path=r"C:\x\T.md",
            )
        self.assertEqual(captured["local_path"], r"C:\x\T.md")


class DrainerFinalAnswerTests(unittest.TestCase):
    def test_final_answer_doc_block_has_three_lines_with_local_path(self):
        state = {}
        bridge_outbox._remember_doc_delivery(state, {
            "url": URL, "title": "当前计划", "route": {"kind": "p2a"}, "local_path": r"C:\x\PLAN-1.md",
        })
        out = bridge_outbox._answer_with_docs("完成", state["pending_docs"])
        self.assertIn("本轮在线文档：", out)
        block = out.split("本轮在线文档：\n", 1)[1].split("\n")
        self.assertEqual(block[0], "📄 当前计划（飞书在线文档·登录飞书查看）：")
        self.assertEqual(block[1], URL)
        self.assertTrue(block[2].endswith("PLAN-1.md"))

    def test_final_answer_doc_block_without_local_path_uses_placeholder(self):
        state = {}
        bridge_outbox._remember_doc_delivery(state, {"url": URL, "title": "旧记录", "route": {"kind": "p2a"}})
        out = bridge_outbox._answer_with_docs("完成", state["pending_docs"])
        self.assertIn("（本地无此文件，仅在线文档）", out)


if __name__ == "__main__":
    unittest.main()
