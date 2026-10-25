import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_inbound  # noqa: E402
import bridge_inbox  # noqa: E402
import feishu_bridge  # noqa: E402


def _message(mid, text, *, create_time=1_700_000_000_123, chat_type="p2p", resources=None):
    sender = SimpleNamespace(
        open_id="ou_sender", union_id="on_sender", user_id="user_sender",
        display_name="测试用户", sender_type="user", is_bot=False,
    )
    return SimpleNamespace(
        id=mid,
        create_time=create_time,
        chat_id="oc_chat",
        chat_type=chat_type,
        sender=sender,
        content=SimpleNamespace(),
        raw_content_type="text",
        mentioned_bot=chat_type == "group",
        resources=resources or [],
        content_text=text,
    )


class InboundLedgerTests(unittest.TestCase):
    def test_group_slash_and_long_message_survive_without_session(self):
        long_text = "长消息" * 4000
        cases = [
            (_message("om_group", "@_user_1 你是干什么的", chat_type="group"), "你是干什么的"),
            (_message("om_slash", "/account ccp2"), "/account ccp2"),
            (_message("om_long", long_text), long_text),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for msg, text in cases:
                bridge_inbound.append_message(
                    state, "bot", msg, raw_text=msg.content_text, text=text,
                    received_ts=1_700_000_001,
                )
            rows = bridge_inbound.read_records(state, "bot")
        self.assertEqual([row["message_id"] for row in rows], ["om_group", "om_slash", "om_long"])
        self.assertEqual(rows[0]["text"], "你是干什么的")
        self.assertEqual(rows[1]["text"], "/account ccp2")
        self.assertEqual(rows[2]["text"], long_text)
        self.assertAlmostEqual(rows[0]["ts"], 1_700_000_000.123)

    def test_attachment_summary_excludes_download_key(self):
        resource = SimpleNamespace(
            type="image", file_name="swing.png", duration_ms=None,
            file_key="do-not-persist-this-key",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            msg = _message("om_media", "", resources=[resource])
            bridge_inbound.append_message(state, "bot", msg, raw_text="", text="")
            raw = bridge_inbound.ledger_path(state, "bot").read_text(encoding="utf-8")
            row = bridge_inbound.read_records(state, "bot")[0]
        self.assertNotIn("do-not-persist-this-key", raw)
        self.assertEqual(row["resources"], [{
            "type": "image", "file_name": "swing.png", "duration_ms": None,
        }])

    def test_redelivery_dedupes_by_id_but_same_text_different_id_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for mid in ("om_same", "om_same", "om_other"):
                msg = _message(mid, "hi")
                bridge_inbound.append_message(state, "bot", msg, raw_text="hi", text="hi")
            rows = bridge_inbound.read_records(state, "bot")
        self.assertEqual([row["message_id"] for row in rows], ["om_same", "om_other"])

    def test_concurrent_appends_are_complete_and_bad_line_does_not_hide_later_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)

            def write(i):
                msg = _message(f"om_{i}", f"message-{i}")
                bridge_inbound.append_message(state, "bot", msg, raw_text=msg.content_text,
                                              text=msg.content_text)

            with ThreadPoolExecutor(max_workers=12) as pool:
                list(pool.map(write, range(60)))
            path = bridge_inbound.ledger_path(state, "bot")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("{torn-json\n")
                handle.write(json.dumps(bridge_inbound.build_record(
                    "bot", _message("om_after", "after"), raw_text="after", text="after"
                ), ensure_ascii=False) + "\n")
            rows = bridge_inbound.read_records(state, "bot")
        self.assertEqual(len(rows), 61)
        self.assertEqual(len({row["message_id"] for row in rows}), 61)
        self.assertIn("om_after", {row["message_id"] for row in rows})


class TuiTextHandoffTests(unittest.TestCase):
    def test_numbered_multiline_below_length_limit_stays_inline_without_a_file(self):
        body = ("6. 录制与存储\r\n• 云台 ± 10°：“完整原文” 😀\n" * 250)[:5716]
        self.assertLess(feishu_bridge._cmdline_units(body), feishu_bridge.SEND_MAX_CHARS)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(feishu_bridge, "STATE_DIR", Path(tmp)):
            self.assertEqual(feishu_bridge._prepare_tui_text("unit", "om_list", body), (body, ""))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_plain_single_line_stays_inline_without_a_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(feishu_bridge, "STATE_DIR", Path(tmp)):
            self.assertEqual(feishu_bridge._prepare_tui_text("unit", "om_short", "继续检查"), ("继续检查", ""))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_emoji_does_not_replace_the_original_prompt_with_a_file_pointer(self):
        body = '1. 原文 📄\r\n2. 云台 • ± 10°\n🧭 当前进度'
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(feishu_bridge, 'STATE_DIR', Path(tmp)):
            self.assertEqual(feishu_bridge._prepare_tui_text('unit', 'om_unicode', body), (body, ''))
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_tab_and_utf16_limits_keep_existing_file_policy(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(feishu_bridge, "STATE_DIR", Path(tmp)):
            for mid, body in [("om_tab", "name\tvalue"), ("om_emoji", "😀" * 8001)]:
                pointer, reason = feishu_bridge._prepare_tui_text("unit", mid, body)
                saved = Path(tmp) / f"bridge-inbox-unit-{bridge_inbox.prompt_digest(mid)[:24]}.txt"
                self.assertEqual(saved.read_bytes(), body.encode("utf-8"))
                self.assertIn(saved.as_posix(), pointer)
                self.assertTrue(reason)

    def test_failed_save_never_returns_a_partial_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(feishu_bridge, "STATE_DIR", Path(tmp)), \
                mock.patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(RuntimeError, "没有截断提交"):
                feishu_bridge._prepare_tui_text("unit", "om_full", "1. 一\t2. 二")


if __name__ == "__main__":
    unittest.main()
