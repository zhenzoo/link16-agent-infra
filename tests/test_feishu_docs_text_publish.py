import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_bridge  # noqa: E402
import feishu_docs  # noqa: E402
import bridge_scope_audit  # noqa: E402
import capability_probe  # noqa: E402


def table_fixture(rows=8, cols=8, prefix="a"):
    cells = [f"{prefix}-cell-{i}" for i in range(rows*cols)]
    blocks = [{"block_id": f"{prefix}-table", "block_type": 31,
               "table": {"property": {"row_size": rows, "column_size": cols}},
               "children": cells}]
    for i, cid in enumerate(cells):
        text_id = f"{prefix}-text-{i}"
        blocks.extend([{"block_id": cid, "block_type": 32, "children": [text_id]},
                       {"block_id": text_id, "block_type": 2,
                        "text": {"elements": [{"text_run": {"content": f"第{i}格"}}]}}])
    return blocks


class NativeTableSafetyTests(unittest.TestCase):
    def test_unordered_convert_pool_uses_declared_reading_order(self):
        first, second = table_fixture(2, 3), table_fixture(3, 2, "b")
        reply = {"code": 0, "data": {"blocks": second + first,
                 "first_level_block_ids": ["a-table", "b-table"]}}
        with mock.patch.object(feishu_docs, "api", return_value=reply):
            blocks, _ = feishu_docs._convert_markdown("token", "tables")
        self.assertEqual(feishu_docs._native_table_signature(blocks),
                         feishu_docs._native_table_signature(first + second))

    def test_append_must_add_table_even_when_same_table_already_exists(self):
        old, addition = table_fixture(2, 3), table_fixture(2, 3, "b")
        with mock.patch.object(feishu_docs, "_read_document_blocks", return_value=old):
            with self.assertRaises(feishu_docs.NativeTableError):
                feishu_docs.verify_native_tables("token", "doc", addition, old)

    def test_revision_change_during_readback_fails(self):
        replies = [{"code": 0, "data": {"document": {"revision_id": 1}}},
                   {"code": 0, "data": {"items": [], "has_more": False}},
                   {"code": 0, "data": {"document": {"revision_id": 2}}}]
        with mock.patch.object(feishu_docs, "api", side_effect=replies):
            with self.assertRaises(feishu_docs.NativeTableError):
                feishu_docs._read_document_blocks("token", "doc")

    def test_large_native_table_passes_without_cell_budget(self):
        blocks = table_fixture()
        with mock.patch.object(feishu_docs, "_read_document_blocks", return_value=blocks):
            result = feishu_docs.verify_native_tables("token", "doc", blocks)
        self.assertEqual(result["table_cells_verified"], 64)
        self.assertEqual(result["tables_degraded"], 0)

    def test_text_containing_all_cells_is_not_a_native_table(self):
        blocks = table_fixture()
        fake = [{"block_id": "plain", "block_type": 2,
                 "text": {"elements": [{"text_run": {"content":
                    " | ".join(f"第{i}格" for i in range(64))}}]}}]
        with mock.patch.object(feishu_docs, "_read_document_blocks", return_value=fake):
            with self.assertRaises(feishu_docs.NativeTableError):
                feishu_docs.verify_native_tables("token", "doc", blocks)

    def test_one_missing_cell_wrong_value_or_wrong_shape_fails(self):
        expected = table_fixture()
        for kind in ("value", "cell", "shape"):
            actual = json.loads(json.dumps(expected))
            if kind == "value":
                actual[-1]["text"]["elements"][0]["text_run"]["content"] = "wrong"
            elif kind == "cell":
                actual = actual[:-2]
            else:
                actual[0]["table"]["property"].update(row_size=4, column_size=16)
            with self.subTest(kind=kind), mock.patch.object(
                    feishu_docs, "_read_document_blocks", return_value=actual):
                with self.assertRaises(feishu_docs.NativeTableError):
                    feishu_docs.verify_native_tables("token", "doc", expected)

    def test_document_total_can_exceed_24_across_multiple_tables(self):
        blocks = table_fixture(4, 4) + table_fixture(4, 4, "b")
        with mock.patch.object(feishu_docs, "_read_document_blocks", return_value=blocks):
            result = feishu_docs.verify_native_tables("token", "doc", blocks)
        self.assertEqual(result["tables_real"], 2)
        self.assertEqual(result["table_cells_verified"], 32)

    def test_missing_pagination_status_fails(self):
        responses = [{"code": 0, "data": {"document": {"revision_id": 3}}},
                     {"code": 0, "data": {"items": []}}]
        with mock.patch.object(feishu_docs, "api", side_effect=responses):
            with self.assertRaises(feishu_docs.NativeTableError):
                feishu_docs._read_document_blocks("token", "doc")

    def test_native_publisher_uses_registered_bot_writer_and_verification(self):
        import docio_cli
        blocks = table_fixture()
        completed = types.SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")
        with mock.patch.object(docio_cli, "resolve_bot", return_value="bot"), \
             mock.patch.object(docio_cli, "app_id_of", return_value="app"), \
             mock.patch.object(docio_cli, "run_lark", return_value=completed) as writer, \
             mock.patch.object(feishu_docs, "_tenant_token", return_value="token"), \
             mock.patch.object(feishu_docs, "_create_docx", return_value="doc"), \
             mock.patch.object(feishu_docs, "_convert_markdown", return_value=(blocks, ["a-table"])), \
             mock.patch.object(feishu_docs, "_read_document_blocks", return_value=blocks), \
             mock.patch.object(feishu_docs, "_doc_url", return_value="https://doc"):
            result = feishu_docs.publish_text_as_doc(
                "app", "secret", markdown="---\ndoc_type: TEST\n---\nsource", bot_name="bot", visibility="none")
        self.assertTrue(result["tables_verified"])
        self.assertEqual(result["table_cells_verified"], 64)
        self.assertEqual(writer.call_args.kwargs["profile"], "bot")
        self.assertIn("--doc-format", writer.call_args.args[0])
        self.assertIn("--content=---\ndoc_type: TEST\n---\nsource", writer.call_args.args[0])

    def test_wrong_bot_identity_fails_before_remote_creation(self):
        import docio_cli
        with mock.patch.object(docio_cli, "resolve_bot", return_value="other"), \
             mock.patch.object(docio_cli, "app_id_of", return_value="other-app"), \
             mock.patch.object(feishu_docs, "_create_docx") as create:
            with self.assertRaises(feishu_docs.DocImportError):
                feishu_docs.publish_text_as_doc("app", "secret", markdown="source")
        create.assert_not_called()


class BridgeOnlineDocFallbackTests(unittest.TestCase):
    def test_partial_native_doc_is_not_republished_via_import(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(),
            publish_text_as_doc=mock.Mock(side_effect=feishu_docs.NativeTableError("doc_id=partial")))
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            with self.assertRaisesRegex(feishu_docs.NativeTableError, "doc_id=partial"):
                feishu_bridge._publish_online_doc({"app_id": "app", "app_secret": "secret"}, "answer.md")
        fake.publish_file_as_doc.assert_not_called()

    def setUp(self):
        self.bot = {"app_id": "app", "app_secret": "secret"}

    def test_text_source_uses_native_chain_first(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(return_value={"url": "https://doc/import"}),
            publish_text_as_doc=mock.Mock(return_value={
                "url": "https://doc/native", "visibility": True, "granted": False,
            }),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            result, mode = feishu_bridge._publish_online_doc(
                self.bot, "answer.md", grant_open_id="ou_owner", name="Answer",
            )
        self.assertEqual(result["url"], "https://doc/native")
        self.assertEqual(mode, "online_doc_native")
        fake.publish_file_as_doc.assert_not_called()

    def test_import_recovers_native_failure(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(return_value={"url": "https://doc/import"}),
            publish_text_as_doc=mock.Mock(side_effect=RuntimeError("native denied")),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            result, mode = feishu_bridge._publish_online_doc(
                self.bot, "answer.md", grant_open_id="ou_owner", name="Answer",
            )
        self.assertEqual(result["url"], "https://doc/import")
        self.assertEqual(mode, "online_doc_import")
        fake.publish_file_as_doc.assert_called_once()

    def test_missing_native_url_enters_import_fallback(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(return_value={"url": "https://doc/import"}),
            publish_text_as_doc=mock.Mock(return_value={
                "url": None, "visibility": True, "granted": False,
            }),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            result, mode = feishu_bridge._publish_online_doc(self.bot, "answer.md")
        self.assertEqual((result["url"], mode), ("https://doc/import", "online_doc_import"))

    def test_unreadable_native_doc_is_not_reported_as_delivered(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(side_effect=RuntimeError("import denied")),
            publish_text_as_doc=mock.Mock(return_value={
                "url": "https://doc/private", "visibility": False, "granted": False,
            }),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            with self.assertRaisesRegex(RuntimeError, "未证实收件人可读"):
                feishu_bridge._publish_online_doc(
                    self.bot, "answer.md", grant_open_id="ou_owner", name="Answer",
                )

    def test_binary_doc_does_not_enter_markdown_native_path(self):
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(side_effect=RuntimeError("import denied")),
            publish_text_as_doc=mock.Mock(),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            with self.assertRaisesRegex(RuntimeError, "import denied"):
                feishu_bridge._publish_online_doc(self.bot, "answer.docx")
        fake.publish_text_as_doc.assert_not_called()


class PermissionProbeSafetyTests(unittest.TestCase):
    def test_docs_text_includes_a_readability_gate(self):
        groups = bridge_scope_audit.CAPABILITY_SPECS["docs-text"]["groups"]
        self.assertIn(
            ["docs:permission.setting:write_only", "docs:permission.member:create", "drive:drive"],
            groups,
        )

    def test_upload_probes_are_skipped_unless_explicitly_enabled(self):
        upload_caps = [
            cap for cap in capability_probe.CAPABILITIES if cap.get("write_probe")
        ]
        self.assertEqual({cap["key"] for cap in upload_caps}, {
            "upload-media", "upload-im-image",
        })
        with mock.patch.object(capability_probe, "req") as request:
            for cap in upload_caps:
                status, alternatives = capability_probe.probe(
                    cap, "token", {"doc": "", "media": "", "msg": "", "key": ""},
                )
                self.assertEqual((status, alternatives), ("skipped", []))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
