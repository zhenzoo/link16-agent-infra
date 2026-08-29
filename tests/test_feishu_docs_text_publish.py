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


class NativeTableSafetyTests(unittest.TestCase):
    def test_long_cells_are_chunked_without_truncation(self):
        writes = []

        def api(_method, url, token=None, body=None):
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{
                    "table": {"cells": ["cell-1"]},
                }]}}
            writes.append(body["children"][0]["text"]["elements"][0]["text_run"]["content"])
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 1, ["x" * 3700],
            )

        self.assertTrue(ok)
        self.assertEqual(filled, 1)
        self.assertEqual(failed, [])
        self.assertEqual("".join(writes), "x" * 3700)
        self.assertEqual([len(value) for value in writes], [1800, 1800, 100])

    def test_failed_cell_is_reported_instead_of_silent_success(self):
        call_count = 0

        def api(_method, url, token=None, body=None):
            nonlocal call_count
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{
                    "table": {"cells": ["cell-1", "cell-2"]},
                }]}}
            call_count += 1
            return {"code": 0 if call_count == 1 else 1770001}

        with mock.patch.object(feishu_docs, "api", side_effect=api):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 2, ["first", "second"],
            )

        self.assertTrue(ok)
        self.assertEqual(filled, 1)
        self.assertEqual(failed, [1])

    def test_empty_response_in_table_cell_enters_plain_text_fallback(self):
        calls = 0

        def api(_method, url, token=None, body=None):
            nonlocal calls
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{
                    "table": {"cells": ["cell-1"]},
                }]}}
            calls += 1
            raise json.JSONDecodeError("empty response", "", 0)

        with mock.patch.object(feishu_docs, "api", side_effect=api):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 1, ["content"],
            )

        self.assertTrue(ok)
        self.assertEqual(filled, 0)
        self.assertEqual(failed, [0])
        self.assertEqual(calls, 1)

    def test_missing_returned_cells_are_reported(self):
        with mock.patch.object(feishu_docs, "api", return_value={
            "code": 0, "data": {"children": [{"table": {"cells": ["cell-1"]}}]},
        }):
            ok, _filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 2, ["", "missing"],
            )
        self.assertTrue(ok)
        self.assertEqual(failed, [1])

    def test_plain_text_fallback_no_longer_truncates_at_2000(self):
        value = "z" * 2500
        blocks = {"a": {"block_id": "a", "text": {
            "elements": [{"text_run": {"content": value}}],
        }}}
        self.assertEqual(feishu_docs._plain_text_of(blocks, "a"), value)

    def test_native_publish_defaults_to_a_bounded_real_table_budget(self):
        defaults = feishu_docs.publish_text_as_doc.__kwdefaults__
        self.assertEqual(defaults["cell_budget"], 24)


class BridgeOnlineDocFallbackTests(unittest.TestCase):
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
