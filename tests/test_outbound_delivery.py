import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_outbox  # noqa: E402
import feishu_bridge  # noqa: E402
from outbound_links import sanitize_outbound_links  # noqa: E402


def fresh_state():
    return {
        "turn": None, "steps": [], "usage": {}, "seg_start": 0,
        "cur_mid": None, "flushed": 0, "last_flush": 0,
        "sent": set(), "picker_active": False, "pending_docs": [],
    }


class FakeDelivery:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.cards = []
        self.plain = []

    async def new_card(self, text, route=None):
        self.cards.append((text, route))
        return None if self.fail else f"m{len(self.cards)}"

    async def edit_card(self, _mid, _text):
        return True

    async def send_plain(self, text, route=None):
        self.plain.append((text, route))
        return not self.fail


class SuccessfulChannel:
    def __init__(self):
        self.payloads = []

    async def send(self, _target, payload):
        self.payloads.append(payload)
        return type("Result", (), {"success": True, "chunk_ids": []})()


class OutboundLinkTests(unittest.TestCase):
    def test_local_markdown_targets_become_visible_non_links(self):
        cases = {
            "[PLAN](D:/repo/PLAN.md)": "PLAN — `D:/repo/PLAN.md`",
            "[PLAN](/D:/repo/PLAN.md)": "PLAN — `D:/repo/PLAN.md`",
            "[PLAN](file:///D:/repo/PLAN.md)": "PLAN — `D:/repo/PLAN.md`",
            "[PLAN](docs/PLAN.md)": "PLAN — `docs/PLAN.md`",
            "[share](\\\\server\\share\\x.md)": "share — `\\\\server\\share\\x.md`",
            "[share](//server/share/x.md)": "share — `//server/share/x.md`",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(sanitize_outbound_links(source), expected)

    def test_external_visibility_matrix_and_deduplication(self):
        ordinary_inline = "See [site](https://example.com/a) now."
        self.assertEqual(sanitize_outbound_links(ordinary_inline), ordinary_inline)

        standalone = sanitize_outbound_links("[site](https://example.com/a)")
        self.assertIn("\nhttps://example.com/a", standalone)

        docx = sanitize_outbound_links("Read [PLAN](https://my.feishu.cn/docx/abc) now.")
        pages = sanitize_outbound_links("Open [demo](https://show.pages.dev/x) now.")
        self.assertIn("\nhttps://my.feishu.cn/docx/abc", docx)
        self.assertIn("\nhttps://show.pages.dev/x", pages)

        already_visible = "[PLAN](https://my.feishu.cn/docx/abc)\nhttps://my.feishu.cn/docx/abc"
        self.assertEqual(sanitize_outbound_links(already_visible).count("https://my.feishu.cn/docx/abc"), 2)
        self.assertEqual(sanitize_outbound_links(docx), docx)

    def test_code_images_anchors_and_bare_urls_are_preserved(self):
        source = (
            "`[PLAN](D:/repo/PLAN.md)`\n"
            "```\n[PLAN](D:/repo/PLAN.md)\n```\n"
            "![alt](D:/image.png)\n[section](#part)\nhttps://example.com/a\n"
            "file:///D:/repo/PLAN.md"
        )
        result = sanitize_outbound_links(source)
        self.assertIn("`[PLAN](D:/repo/PLAN.md)`", result)
        self.assertIn("```\n[PLAN](D:/repo/PLAN.md)\n```", result)
        self.assertIn("![alt](D:/image.png)", result)
        self.assertIn("[section](#part)", result)
        self.assertIn("https://example.com/a", result)
        self.assertIn("`D:/repo/PLAN.md`", result)

    def test_card_linkify_applies_sanitizer_and_keeps_url_visible(self):
        result = feishu_bridge._linkify(
            "[PLAN](/D:/repo/PLAN.md)\nhttps://example.com/a"
        )
        self.assertIn("PLAN — `D:/repo/PLAN.md`", result)
        self.assertIn("[https://example.com/a](https://example.com/a)", result)


class FallbackLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_fallback_applies_same_local_path_rule(self):
        channel = SuccessfulChannel()
        payload = {"markdown": "[PLAN](/D:/repo/PLAN.md)"}
        ok, _error, _transient = await feishu_bridge._send_checked(
            channel, "ou_owner", payload, "bot", "markdown"
        )
        self.assertTrue(ok)
        self.assertEqual(channel.payloads[0]["markdown"], "PLAN — `D:/repo/PLAN.md`")

    async def test_text_fallback_exposes_delivery_url(self):
        channel = SuccessfulChannel()
        url = "https://show.pages.dev/demo"
        payload = {"text": f"[工作台]({url})"}
        await feishu_bridge._send_checked(channel, "ou_owner", payload, "bot", "text")
        self.assertIn("\n" + url, channel.payloads[0]["text"])


class DocumentReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, records, state, fake):
        return await bridge_outbox.drain_batch(
            records, new_card=fake.new_card, edit_card=fake.edit_card,
            send_plain=fake.send_plain, state=state, coalesce_sec=0,
            clock=lambda: 100, force_flush=True,
        )

    @staticmethod
    def doc(url, route=None):
        return {
            "kind": "doc_delivery", "url": url, "title": "PLAN",
            "route": route or {"kind": "p2a"}, "source_bytes": 10,
            "source_chars": 8, "direct_delivered": True, "ts": 1,
        }

    async def test_two_docs_append_to_next_answer_once_and_clear(self):
        state, fake = fresh_state(), FakeDelivery()
        records = [
            self.doc("https://my.feishu.cn/docx/one"),
            self.doc("https://my.feishu.cn/docx/two"),
            self.doc("https://my.feishu.cn/docx/one"),
            {"kind": "answer", "session": "s", "anchor": "a", "text": "完成", "route": {"kind": "p2a"}},
        ]
        await self.drain(records, state, fake)
        final = fake.cards[-1][0]
        self.assertEqual(final.count("https://my.feishu.cn/docx/one"), 1)
        self.assertEqual(final.count("https://my.feishu.cn/docx/two"), 1)
        self.assertIn("本轮在线文档：", final)
        self.assertEqual(state["pending_docs"], [])

    async def test_existing_raw_url_is_not_duplicated(self):
        state, fake = fresh_state(), FakeDelivery()
        url = "https://my.feishu.cn/docx/one"
        await self.drain([
            self.doc(url),
            {"kind": "answer", "session": "s", "anchor": "a", "text": f"完成\n{url}", "route": {"kind": "p2a"}},
        ], state, fake)
        self.assertEqual(fake.cards[-1][0].count(url), 1)

    async def test_failed_answer_retains_docs_for_retry(self):
        state, fake = fresh_state(), FakeDelivery(fail=True)
        records = [
            self.doc("https://my.feishu.cn/docx/one"),
            {"kind": "answer", "session": "s", "anchor": "a", "text": "完成", "route": {"kind": "p2a"}},
        ]
        with self.assertRaises(bridge_outbox.RetrySend):
            await self.drain(records, state, fake)
        self.assertEqual(len(state["pending_docs"]), 1)
        fake.fail = False
        await self.drain(records, state, fake)
        self.assertEqual(state["pending_docs"], [])
        self.assertIn("https://my.feishu.cn/docx/one", fake.cards[-1][0])

    async def test_route_isolation_keeps_p2a_doc_out_of_a2a_answer(self):
        state, fake = fresh_state(), FakeDelivery()
        await self.drain([
            self.doc("https://my.feishu.cn/docx/one"),
            {"kind": "answer", "session": "s", "anchor": "a", "text": "群回复",
             "route": {"kind": "a2a", "dest": "oc_group", "at": "ou_peer"}},
        ], state, fake)
        self.assertNotIn("docx/one", fake.cards[-1][0])
        self.assertEqual(len(state["pending_docs"]), 1)

    def test_delivery_state_survives_restart_and_malformed_state_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = [self.doc("https://my.feishu.cn/docx/one")]
            bridge_outbox.save_delivery_state(tmp, "bot", docs)
            self.assertEqual(bridge_outbox.load_delivery_state(tmp, "bot"), docs)
            bridge_outbox.delivery_state_path(tmp, "bot").write_text("not json", encoding="utf-8")
            self.assertEqual(bridge_outbox.load_delivery_state(tmp, "bot"), [])

    def test_delivery_state_write_failure_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(bridge_outbox.os, "replace", side_effect=PermissionError), \
                    mock.patch.object(bridge_outbox.time, "sleep") as asleep:
                saved = bridge_outbox.save_delivery_state(
                    tmp, "bot", [self.doc("https://my.feishu.cn/docx/one")]
                )
        self.assertFalse(saved)
        self.assertEqual(asleep.call_count, 4)


class DocumentCommandHelpersTests(unittest.TestCase):
    def test_text_document_reports_real_source_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "PLAN.md"
            source.write_text("中文 plan\n", encoding="utf-8")
            stats = feishu_bridge._doc_source_stats(source)
        self.assertEqual(stats["source_chars"], len("中文 plan\n"))
        self.assertGreater(stats["source_bytes"], stats["source_chars"])
        self.assertIn("文档源", feishu_bridge._send_size_label("", stats))
        self.assertNotEqual(feishu_bridge._send_size_label("", stats), "0 字")

    def test_only_current_p2a_bot_can_queue_doc_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                feishu_bridge._turn_route_path("bot").write_text(
                    json.dumps({"kind": "p2a"}), encoding="utf-8"
                )
                stats = {"source_bytes": 10, "source_chars": 8}
                with mock.patch.dict(os.environ, {"FEISHU_BRIDGE_SESSION": "bot"}, clear=False):
                    self.assertTrue(feishu_bridge._queue_doc_delivery(
                        "bot", explicit_to=None, doc_url="https://my.feishu.cn/docx/one",
                        title="PLAN", stats=stats, direct_delivered=True,
                    ))
                    self.assertIsNone(feishu_bridge._queue_doc_delivery(
                        "bot", explicit_to="ou_other", doc_url="https://my.feishu.cn/docx/two",
                        title="PLAN", stats=stats, direct_delivered=True,
                    ))
                records, _ = bridge_outbox.read_new_records(
                    bridge_outbox.outbox_path(tmp, "bot"), 0
                )
            finally:
                feishu_bridge.STATE_DIR = previous
        self.assertEqual([record["url"] for record in records], ["https://my.feishu.cn/docx/one"])

    def test_a2a_route_does_not_queue_owner_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                feishu_bridge._turn_route_path("bot").write_text(
                    json.dumps({"kind": "a2a", "dest": "oc_group"}), encoding="utf-8"
                )
                with mock.patch.dict(os.environ, {"FEISHU_BRIDGE_SESSION": "bot"}, clear=False):
                    queued = feishu_bridge._queue_doc_delivery(
                        "bot", explicit_to=None, doc_url="https://my.feishu.cn/docx/one",
                        title="PLAN", stats={"source_bytes": 10, "source_chars": 8},
                        direct_delivered=True,
                    )
            finally:
                feishu_bridge.STATE_DIR = previous
        self.assertIsNone(queued)

    def test_queue_failure_is_distinct_from_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = feishu_bridge.STATE_DIR
            feishu_bridge.STATE_DIR = Path(tmp)
            try:
                feishu_bridge._turn_route_path("bot").write_text(
                    json.dumps({"kind": "p2a"}), encoding="utf-8"
                )
                with mock.patch.dict(os.environ, {"FEISHU_BRIDGE_SESSION": "bot"}, clear=False), \
                        mock.patch.object(bridge_outbox, "append_record", return_value=False):
                    queued = feishu_bridge._queue_doc_delivery(
                        "bot", explicit_to=None, doc_url="https://my.feishu.cn/docx/one",
                        title="PLAN", stats={"source_bytes": 10, "source_chars": 8},
                        direct_delivered=True,
                    )
            finally:
                feishu_bridge.STATE_DIR = previous
        self.assertFalse(queued)


if __name__ == "__main__":
    unittest.main()
