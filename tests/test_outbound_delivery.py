import asyncio
import io
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
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

    async def new_card(self, text, route=None, purpose="answer", fragment=None):
        self.cards.append((text, route))
        return None if self.fail else f"m{len(self.cards)}"

    async def edit_card(self, _mid, _text):
        return True

    async def send_plain(self, text, route=None, purpose="answer", fragment=None):
        self.plain.append((text, route))
        return not self.fail


class SuccessfulChannel:
    def __init__(self):
        self.payloads = []

    async def send(self, _target, payload):
        self.payloads.append(payload)
        return type("Result", (), {"success": True, "chunk_ids": []})()


class OutboundLinkTests(unittest.TestCase):
    def test_indented_blockquote_regression_stays_visible(self):
        source = (
            "- **How do you plan to use**：复制这段：\n"
            "  > This is an academic research project using "
            "(`/graph/v1/author/{id}/papers`) for offline analysis.\n"
            "- **Which endpoints**：`/graph/v1/author/{id}/papers`"
        )
        expected = (
            "- **How do you plan to use**：复制这段：\n\n"
            "This is an academic research project using "
            "(`/graph/v1/author/{id}/papers`) for offline analysis.\n\n"
            "- **Which endpoints**：`/graph/v1/author/{id}/papers`"
        )
        self.assertEqual(sanitize_outbound_links(source), expected)
        self.assertIn(
            "This is an academic research project",
            feishu_bridge._linkify(source),
        )

    def test_quote_flattening_is_idempotent_and_preserves_code(self):
        source = (
            "> first line\n"
            ">> second line\n"
            "\t> third line\n"
            "`  > inline code`\n"
            "```text\n  > fenced code\n```\n"
        )
        result = sanitize_outbound_links(source)
        self.assertIn("first line\nsecond line\nthird line", result)
        self.assertNotIn("\n> second line", result)
        self.assertIn("`  > inline code`", result)
        self.assertIn("```text\n  > fenced code\n```", result)
        self.assertEqual(sanitize_outbound_links(result), result)

    def test_local_markdown_targets_become_visible_non_links(self):
        cases = {
            "[PLAN](D:/repo/PLAN.md)": "PLAN — D:/repo/PLAN.md",
            "[PLAN](/D:/repo/PLAN.md)": "PLAN — D:/repo/PLAN.md",
            "[PLAN](file:///D:/repo/PLAN.md)": "PLAN — D:/repo/PLAN.md",
            "[PLAN](docs/PLAN.md)": "PLAN — docs/PLAN.md",
            "[share](\\\\server\\share\\x.md)": "share — \\\\server\\share\\x.md",
            "[share](//server/share/x.md)": "share — //server/share/x.md",
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
        self.assertIn("D:/repo/PLAN.md", result)
        self.assertNotIn("`D:/repo/PLAN.md`", result)

    def test_card_linkify_applies_sanitizer_and_keeps_url_visible(self):
        result = feishu_bridge._linkify(
            "[PLAN](/D:/repo/PLAN.md)\nhttps://example.com/a"
        )
        self.assertIn("PLAN — D:/repo/PLAN.md", result)
        self.assertIn("[https://example.com/a](https://example.com/a)", result)


class FallbackLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_markdown_fallback_flattens_nested_quote(self):
        channel = SuccessfulChannel()
        payload = {"markdown": "- copy:\n  > keep this paragraph\n- next"}
        ok, _error, _transient = await feishu_bridge._send_checked(
            channel, "ou_owner", payload, "bot", "markdown"
        )
        self.assertTrue(ok)
        self.assertEqual(
            channel.payloads[0]["markdown"],
            "- copy:\n\nkeep this paragraph\n\n- next",
        )

    async def test_markdown_fallback_applies_same_local_path_rule(self):
        channel = SuccessfulChannel()
        payload = {"markdown": "[PLAN](/D:/repo/PLAN.md)"}
        ok, _error, _transient = await feishu_bridge._send_checked(
            channel, "ou_owner", payload, "bot", "markdown"
        )
        self.assertTrue(ok)
        self.assertEqual(channel.payloads[0]["markdown"], "PLAN — D:/repo/PLAN.md")

    async def test_text_fallback_exposes_delivery_url(self):
        channel = SuccessfulChannel()
        url = "https://show.pages.dev/demo"
        payload = {"text": f"[工作台]({url})"}
        await feishu_bridge._send_checked(channel, "ou_owner", payload, "bot", "text")
        self.assertIn("\n" + url, channel.payloads[0]["text"])


class ProviderMessageUuidTests(unittest.TestCase):
    def test_local_fragment_id_maps_to_stable_standard_uuid(self):
        local_id = "a" * 64
        provider_id = feishu_bridge._provider_message_uuid(
            {"fragment_id": local_id}
        )
        self.assertEqual(len(local_id), 64)
        self.assertEqual(len(provider_id), 36)
        self.assertEqual(str(uuid.UUID(provider_id)), provider_id)
        self.assertEqual(
            provider_id,
            str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"link16:feishu:fragment:{local_id}",
            )),
        )
        self.assertEqual(
            feishu_bridge._provider_message_uuid({"fragment_id": local_id}),
            provider_id,
        )
        self.assertNotEqual(
            feishu_bridge._provider_message_uuid({"fragment_id": "b" * 64}),
            provider_id,
        )
        self.assertNotEqual(provider_id, local_id)

    def test_card_and_text_rest_bodies_use_same_provider_uuid(self):
        provider_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "link16-test-fragment"))
        payloads = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return json.dumps({"code": 0, "data": {"message_id": "om_test"}}).encode()

        def urlopen(request, timeout=None):
            payloads.append(json.loads(request.data.decode("utf-8")))
            return Response()

        with mock.patch.object(feishu_bridge, "_tenant_token", return_value="token"), \
             mock.patch("urllib.request.urlopen", side_effect=urlopen):
            feishu_bridge._send_interactive_message(
                "app", "secret", "ou_owner", {"schema": "2.0"}, provider_id,
            )
            feishu_bridge._send_text_message(
                "app", "secret", "ou_owner", "answer", None, provider_id,
            )
        self.assertEqual([row.get("uuid") for row in payloads], [provider_id, provider_id])
        self.assertTrue(all(len(row["uuid"]) == 36 for row in payloads))


class RoutedFinalDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = {"app_id": "app", "app_secret": "secret"}
        self.receipts = []
        self.route_to_dest = lambda route: (route.get("dest") or "oc_dm", route.get("at"))

    async def test_route_kind_not_target_prefix_selects_format(self):
        interactive, texts = [], []

        def card(_aid, _secret, target, payload, message_uuid=None):
            interactive.append((target, payload, message_uuid))
            return f"card-{len(interactive)}"

        def text(_aid, _secret, target, body, at=None, message_uuid=None):
            texts.append((target, body, at, message_uuid))
            return f"text-{len(texts)}"

        local_id = "f" * 64
        fragment = {"answer_id": "a", "fragment_id": local_id, "part": 1,
                    "total": 1, "content_sha256": "h"}
        with mock.patch.object(feishu_bridge, "_send_interactive_message", side_effect=card), \
             mock.patch.object(feishu_bridge, "_send_group_text", side_effect=text), \
             mock.patch.object(feishu_bridge, "receipt", side_effect=lambda _bot, row: self.receipts.append(row)):
            dm = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "DM", {"kind": "p2a", "dest": "oc_dm"},
                "answer", fragment, self.route_to_dest,
            )
            human = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "真人", {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_human"},
                "answer", fragment, self.route_to_dest,
            )
            peer = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "机器人", {"kind": "a2a", "dest": "oc_group", "at": "ou_peer"},
                "answer", fragment, self.route_to_dest,
            )
        self.assertTrue(dm["ok"] and human["ok"] and peer["ok"])
        self.assertEqual([row[0] for row in interactive], ["oc_dm", "oc_group"])
        self.assertIn("<at id=ou_human></at>", json.dumps(interactive[1][1], ensure_ascii=False))
        provider_id = feishu_bridge._provider_message_uuid(fragment)
        self.assertEqual([row[2] for row in interactive], [provider_id, provider_id])
        self.assertEqual(texts, [("oc_group", "机器人", "ou_peer", provider_id)])
        self.assertEqual(fragment["fragment_id"], local_id)

    def test_guard_metadata_is_preserved_in_receipt_and_outbound_ledger(self):
        fragment = {
            "answer_id": "a", "fragment_id": "f" * 64, "part": 1, "total": 2,
            "content_sha256": "h", "split_policy": "answer-v2-target2790-guard10",
            "render_target": 2790, "hard_budget": 2800,
            "guard_used": True, "guard_chars": 1,
        }
        self.assertEqual(feishu_bridge._fragment_receipt(fragment)["guard_chars"], 1)
        with mock.patch.object(
            feishu_bridge.bridge_outbound, "append_delivery", return_value=True,
        ) as append:
            self.assertTrue(feishu_bridge._record_automatic_outbound(
                "bot", {"kind": "p2a"}, "ou_owner", "answer", "om_1", fragment,
            ))
        kwargs = append.call_args.kwargs
        self.assertTrue(kwargs["guard_used"])
        self.assertEqual(kwargs["guard_chars"], 1)
        self.assertEqual(kwargs["render_target"], 2790)

    async def test_group_progress_is_skipped_but_robot_prefixed_final_is_not(self):
        calls = []
        with mock.patch.object(
                feishu_bridge, "_send_interactive_message",
                side_effect=lambda *_args, **_kwargs: calls.append("card") or "mid"), \
             mock.patch.object(feishu_bridge, "receipt"):
            progress = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "普通进度", {"kind": "p2a-ext", "dest": "oc_group"},
                "progress", None, self.route_to_dest,
            )
            final = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "🤖 最终结论", {"kind": "p2a-ext", "dest": "oc_group"},
                "answer", None, self.route_to_dest,
            )
        self.assertEqual(progress, "skip-progress")
        self.assertTrue(final["ok"])
        self.assertEqual(calls, ["card"])

    async def test_empty_card_id_falls_back_once_and_empty_text_id_stays_failed(self):
        texts = []
        local_id = "f" * 64
        fragment = {"answer_id": "a", "fragment_id": local_id, "part": 1,
                    "total": 1, "content_sha256": "h"}
        route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_human"}
        with mock.patch.object(feishu_bridge, "_send_interactive_message", return_value=None), \
             mock.patch.object(feishu_bridge, "_send_text_message",
                               side_effect=lambda *_args: texts.append(_args) or "text-mid"), \
             mock.patch.object(feishu_bridge, "receipt", side_effect=lambda _bot, row: self.receipts.append(row)):
            card = await feishu_bridge._deliver_routed_new(
                self.bot, "bot", "答案", route, "answer", fragment, self.route_to_dest,
            )
            plain = await feishu_bridge._deliver_routed_plain(
                self.bot, "bot", "答案", route, "answer", fragment, self.route_to_dest,
            )
        self.assertFalse(card["ok"])
        self.assertTrue(plain["ok"])
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0][-1], feishu_bridge._provider_message_uuid(fragment))
        self.assertEqual(fragment["fragment_id"], local_id)
        self.assertTrue(any(row.get("fallback") == "text" for row in self.receipts))
        self.assertTrue(any(row.get("degraded") is True for row in self.receipts))


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
    def test_renamed_file_as_text_is_explicit_and_legacy_flag_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "answer.md"
            source.write_text("正文", encoding="utf-8")
            self.assertEqual(
                feishu_bridge._read_send_text(file_as_text=source),
                "正文",
            )
        with self.assertRaisesRegex(ValueError, "--file-as-text"):
            feishu_bridge._read_send_text(legacy_file="answer.md")

    def test_send_doc_policy_gate_stops_before_bot_or_network_lookup(self):
        blocked = feishu_bridge.artifact_delivery.OnlineArtifactDeliveryDisabled("off")
        with mock.patch.object(feishu_bridge, "assert_sender_identity"), \
                mock.patch.object(
                    feishu_bridge.artifact_delivery,
                    "require_online_publication",
                    side_effect=blocked,
                ), \
                mock.patch.object(feishu_bridge, "load_bots") as load_bots:
            with self.assertRaises(SystemExit) as stopped:
                feishu_bridge.cmd_send("bot", "", doc="never-read.md")
        self.assertEqual(stopped.exception.code, 3)
        load_bots.assert_not_called()

    def test_send_doc_failure_never_falls_back_to_local_attachment(self):
        class FakeChannel:
            def __init__(self, **_kwargs):
                pass

        fake_lark = types.SimpleNamespace(
            FeishuChannel=FakeChannel,
            OutboundImage=object,
            MediaSource=object,
        )
        bot = {"name": "bot", "app_id": "app", "app_secret": "secret"}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "deliverable.md"
            source.write_text("# 原始文件", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"lark_channel": fake_lark}), \
                    mock.patch.object(feishu_bridge, "assert_sender_identity"), \
                    mock.patch.object(feishu_bridge, "load_bots", return_value=[bot]), \
                    mock.patch.object(feishu_bridge, "mirror_target", return_value="ou_owner"), \
                    mock.patch.object(feishu_bridge, "load_owner", return_value="ou_owner"), \
                    mock.patch.object(feishu_bridge, "_publish_online_doc",
                                      side_effect=RuntimeError("online denied")), \
                    mock.patch.object(feishu_bridge, "receipt"), \
                    mock.patch.object(feishu_bridge, "blog"), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                with self.assertRaises(SystemExit) as stopped:
                    feishu_bridge.cmd_send(
                        "bot", "", doc=str(source), as_json=True, online_override=True
                    )
        self.assertEqual(stopped.exception.code, 1)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["delivered"])
        self.assertFalse(payload["doc_ok"])
        self.assertEqual(payload["doc_delivery_mode"], "online_doc_failed")
        self.assertIsNone(payload["attachment_ok"])
        self.assertIsNone(payload["attachment_error"])

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
