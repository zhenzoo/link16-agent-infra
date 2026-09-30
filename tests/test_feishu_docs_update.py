"""Offline tests for Markdown → existing docx in-place update (send --doc --update).

covers: root diff keeps unchanged blocks, edits run end-first, foreign blocks and
concurrent edits stop before any write, overwrite, no-op, layout inference, bridge route.
judge: mechanical call contract; live Feishu behaviour is verified separately (SOP-140).
"""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_bridge  # noqa: E402
import feishu_docs  # noqa: E402
from doc_structure import DocStructureError  # noqa: E402

DOC = "GQ82d9TS3ooCADx8va4c8go1nyd"


def para(bid, text):
    return {"block_id": bid, "block_type": 2, "text": {"elements": [{"text_run": {"content": text}}]}}


def table(bid, text):
    return [{"block_id": bid, "block_type": 31,
             "table": {"property": {"row_size": 1, "column_size": 1}}, "children": [f"{bid}-c"]},
            {"block_id": f"{bid}-c", "block_type": 32, "children": [f"{bid}-p"]},
            para(f"{bid}-p", text)]


class UpdateTextDocTests(unittest.TestCase):
    def run_update(self, live, live_roots, source, source_roots, *, revisions=(7, 7, 8), **kwargs):
        writes, deletes = [], []

        def write_roots(_token, _doc, _by_id, roots, index=0):
            writes.append((index, list(roots)))
            return {"tables_real": 0, "batches": 1, "table_cells_attempted": 0,
                    "table_cells_filled": 0, "table_cells_failed": 0}

        def throttled(method, url, _token, body):
            deletes.append((method, body))
            return {"code": 0}

        patches = [
            mock.patch.object(feishu_docs, "_tenant_token", return_value="token"),
            mock.patch.object(feishu_docs, "_doc_revision", side_effect=list(revisions)),
            mock.patch.object(feishu_docs, "_read_doc_blocks",
                              side_effect=[(live, live_roots), (source, source_roots)]),
            mock.patch.object(feishu_docs, "_convert_markdown", return_value=(source, source_roots)),
            mock.patch.object(feishu_docs, "_write_roots", side_effect=write_roots),
            mock.patch.object(feishu_docs, "_throttled_write", side_effect=throttled),
            mock.patch.object(feishu_docs, "verify_structure", return_value={"structure_verified": True}),
            mock.patch.object(feishu_docs, "_doc_url", return_value="https://doc"),
        ]
        for p in patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        result = feishu_docs.update_text_doc("app", "secret", DOC, markdown="# src", **kwargs)
        return result, writes, deletes

    def test_only_changed_roots_are_rewritten_end_first(self):
        live = [para("a", "一"), para("b", "二"), para("c", "三")]
        source = [para("s1", "一"), para("s2", "二改"), para("s3", "三"), para("s4", "四")]
        result, writes, deletes = self.run_update(live, ["a", "b", "c"], source, ["s1", "s2", "s3", "s4"])
        self.assertEqual(writes, [(3, ["s4"]), (1, ["s2"])])      # 尾部先改，前面的下标不受影响
        self.assertEqual(deletes, [("DELETE", {"start_index": 1, "end_index": 2})])
        self.assertEqual((result["kept_roots"], result["deleted_roots"], result["inserted_roots"]), (2, 1, 2))
        self.assertTrue(result["structure_verified"])

    def test_online_only_media_stops_the_diff_before_any_write(self):
        live = [para("a", "一"), {"block_id": "img", "block_type": 27, "image": {"token": "x"}}]
        with self.assertRaises(DocStructureError) as caught:
            self.run_update(live, ["a", "img"], [para("s1", "一")], ["s1"])
        self.assertIn("--overwrite", str(caught.exception))

    def test_overwrite_replaces_everything_including_foreign_blocks(self):
        live = [para("a", "一"), {"block_id": "img", "block_type": 27, "image": {"token": "x"}}]
        result, writes, deletes = self.run_update(live, ["a", "img"], [para("s1", "新")], ["s1"], overwrite=True)
        self.assertEqual(deletes, [("DELETE", {"start_index": 0, "end_index": 2})])
        self.assertEqual(writes, [(0, ["s1"])])
        self.assertEqual(result["mode"], "overwrite")

    def test_concurrent_edit_aborts_without_writing(self):
        live = [para("a", "一")]
        with self.assertRaises(DocStructureError):
            self.run_update(live, ["a"], [para("s1", "二")], ["s1"], revisions=(7, 9))

    def test_unchanged_source_writes_nothing(self):
        result, writes, deletes = self.run_update([para("a", "一")], ["a"], [para("s1", "一")], ["s1"])
        self.assertFalse(result["changed"])
        self.assertEqual((writes, deletes), ([], []))

    def test_auto_layout_keeps_native_tables_already_online(self):
        live = table("t", "格")
        with mock.patch.object(feishu_docs, "source_body", wraps=feishu_docs.source_body) as body:
            result, _writes, _deletes = self.run_update(live, ["t"], table("u", "格"), ["u"])
        self.assertEqual(body.call_args.args[1], "native")
        self.assertEqual(result["table_layout"], "native")
        self.assertFalse(result["changed"])


class BridgeUpdateRouteTests(unittest.TestCase):
    def test_update_routes_to_in_place_update_not_create(self):
        fake = types.SimpleNamespace(
            publish_text_as_doc=mock.Mock(),
            publish_file_as_doc=mock.Mock(),
            update_text_doc=mock.Mock(return_value={"url": "https://doc", "changed": True,
                                                    "structure_verified": True}),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            result, mode = feishu_bridge._publish_online_doc(
                {"app_id": "app", "app_secret": "secret"}, "a.md",
                update="https://x.feishu.cn/docx/" + DOC, table_layout="native")
        self.assertEqual(mode, "online_doc_updated")
        fake.publish_text_as_doc.assert_not_called()
        self.assertEqual(fake.update_text_doc.call_args.kwargs["table_layout"], "native")


if __name__ == "__main__":
    unittest.main()
