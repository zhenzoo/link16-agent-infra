"""Offline failure injection for DocIO. No live credentials or business documents.

covers: native inventory, pagination, coordinates, typed content and write conflicts.
caught: text-only false coverage, lost links/formulas, failed/empty readback and stale writes.
judge: mechanical behavior; does not certify live service permissions or document semantics.
"""
import argparse
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import docio_cli as d


def response(data, code=0, ok=True):
    return subprocess.CompletedProcess([], code, json.dumps({"ok": ok, "data": data}), "")


def cell_data(cells=None):
    return {"revision": 12, "has_more": False, "ranges": [{
        "actual_range": "G14:G14", "row_indices": [14], "col_indices": ["G"],
        "truncated": False, "cells": cells if cells is not None else [[{"value": "old"}]]}]}


class ReadContractTests(unittest.TestCase):
    def test_complete_coordinates_and_fields_are_preserved(self):
        data = cell_data([[{"value": "display", "rich_text": [{"type": "link", "text": "display", "link": "https://example.com/a"}]}]])
        with patch.object(d, "run_lark", return_value=response(data)) as run:
            result = d._read_range("sheet-token", "bot", "tab", "G14:G14")
        self.assertEqual(result, data["ranges"][0]["cells"])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--include") + 1], "value,formula")

    def test_failed_empty_clipped_misplaced_or_short_read_is_rejected(self):
        cases = []
        for location, key, value in (("body", "has_more", True), ("block", "truncated", True),
                                      ("block", "actual_range", "H14:H14"), ("block", "row_indices", [15]),
                                      ("block", "col_indices", ["H"]), ("block", "cells", [])):
            data = cell_data()
            (data if location == "body" else data["ranges"][0])[key] = value
            cases.append(response(data))
        cases += [response({}, ok=False), response({}, code=1), response({}),
                  subprocess.CompletedProcess([], 0, "not-json", "")]
        for bad in cases:
            with self.subTest(bad=bad.stdout), patch.object(d, "run_lark", return_value=bad):
                with self.assertRaises(ValueError):
                    d._read_range("token", "bot", "tab", "G14:G14")

    def test_comments_follow_pages_and_save_raw_results(self):
        pages = [response({"items": [{"comment_id": "a"}], "has_more": True, "page_token": "next"}),
                 response({"items": [{"comment_id": "b"}], "has_more": False})]
        manifest = {"resources": {}, "missing": []}
        with tempfile.TemporaryDirectory() as folder, patch.object(d, "run_lark", side_effect=pages) as run:
            d._read_comments("https://example.feishu.cn/docx/T", "bot", manifest, Path(folder))
            saved = json.loads((Path(folder) / "comments.json").read_text())
        self.assertEqual(len(saved), 2)
        self.assertEqual(manifest["resources"]["comments"]["fetched"], 2)
        self.assertTrue(manifest["resources"]["comments"]["complete"])
        self.assertIn("next", run.call_args.args[0])

    def test_repeated_comment_cursor_is_incomplete(self):
        data = {"items": [{}], "has_more": True, "page_token": "same"}
        manifest = {"resources": {}, "missing": []}
        with patch.object(d, "run_lark", return_value=response(data)) as run:
            d._read_comments("url", "bot", manifest)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(manifest["resources"]["comments"]["complete"])
        self.assertTrue(manifest["missing"])

    def test_native_inventory_reports_unsupported_resources(self):
        """`file`/`view` are handled now; anything still unhandled must show up."""
        manifest = {"resources": {}, "missing": []}
        blocks = [{"block_id": "a", "block_type": 2, "text": {}},
                  {"block_id": "b", "block_type": 23, "file": {"token": "synthetic-media"}},
                  {"block_id": "c", "block_type": 33, "view": {"view_type": 1},
                   "children": ["b"]},
                  {"block_id": "d", "block_type": 30, "sheet": {"token": "synthetic-sheet"}}]
        with tempfile.TemporaryDirectory() as folder, patch.object(d, "run_lark", return_value=response({"items": blocks, "has_more": False})) as run:
            d._docx_inventory("T", 12, "bot", Path(folder), manifest)
        self.assertTrue(manifest["inventory"]["complete"])
        kinds = [row["kind"] for row in manifest["missing"]]
        self.assertIn("sheet", kinds)          # 真正还没实现的资源仍要报
        self.assertNotIn("file", kinds)        # file 由 _download_files 负责
        self.assertNotIn("view", kinds)        # view 只是承载子块的容器
        args = run.call_args.args[0]
        self.assertEqual(json.loads(args[args.index("--params") + 1])["document_revision_id"], 12)

    def test_view_without_children_is_still_reported(self):
        """A wrapper with nothing inside means its resource could not be located."""
        manifest = {"resources": {}, "missing": []}
        blocks = [{"block_id": "c", "block_type": 33, "view": {"view_type": 1}}]
        with tempfile.TemporaryDirectory() as folder, patch.object(d, "run_lark", return_value=response({"items": blocks, "has_more": False})):
            d._docx_inventory("T", 1, "bot", Path(folder), manifest)
        self.assertEqual([row["kind"] for row in manifest["missing"]], ["view"])


class TypedCellTests(unittest.TestCase):
    def test_same_label_wrong_link_fails(self):
        want = {"rich_text": [{"type": "link", "text": "Document", "link": "https://example.com/new"}]}
        got = {"value": "Document", "rich_text": [{"type": "link", "text": "Document", "link": "https://example.com/old"}]}
        self.assertFalse(d._matches_cell(want, got))
        self.assertFalse(d._matches_cell(want, {"value": "Document"}))
        self.assertTrue(d._matches_cell(want, {"value": "Document", **want}))

    def test_formula_cannot_be_verified_by_computed_value(self):
        self.assertFalse(d._matches_cell({"formula": "=1+1"}, {"value": 2}))
        self.assertTrue(d._matches_cell({"formula": "=1+1"}, {"value": 2, "formula": "=1+1"}))
        self.assertFalse(d._matches_cell({"value": 2}, {"value": 2, "formula": "=1+1"}))

    def test_clear_and_noop_are_distinct(self):
        self.assertFalse(d._matches_cell({"value": ""}, {"value": "still there"}))
        self.assertTrue(d._matches_cell({"value": ""}, {}))
        self.assertTrue(d._matches_cell({}, {"value": "unchanged"}))
        self.assertFalse(d._matches_cell({"value": True}, {"value": 1}))


class WriteTransactionTests(unittest.TestCase):
    def run_write(self, new_cell, readbacks, expected=None, apply=True):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "patch.json"
            item = {"sheet_id": "tab", "range": "G14:G14", "cells": [[new_cell]]}
            if expected is not None:
                item["expected"] = expected
            path.write_text(json.dumps({"writes": [item]}), encoding="utf-8")
            args = argparse.Namespace(bot="bot", url="https://example.feishu.cn/sheets/T", patch=str(path), apply=apply)
            with patch.object(d, "resolve_bot", return_value="bot"), \
                 patch.object(d, "inspect_url", return_value={"ok": True, "type": "sheet", "token": "T"}), \
                 patch.object(d, "_read_range", side_effect=readbacks), \
                 patch.object(d, "run_lark", return_value=response({})) as run, contextlib.redirect_stdout(io.StringIO()):
                result = d.cmd_write(args)
            receipts = [json.loads(p.read_text(encoding="utf-8")) for p in Path(folder).glob("*.receipt.json")]
            return result, run, receipts

    def test_missing_expected_unsupported_style_or_formula_in_value_never_writes(self):
        for cell, expected in (({"value": "new"}, None), ({"cell_styles": {}}, [[{}]]),
                               ({"value": "=1+1"}, [[{}]])):
            result, run, _ = self.run_write(cell, [], expected)
            self.assertEqual(result, 2)
            run.assert_not_called()

    def test_user_edit_before_or_during_preflight_blocks_write(self):
        old = [[{"value": "old"}]]
        newer = [[{"value": "human edit"}]]
        for reads in ([newer], [old, newer]):
            result, run, receipts = self.run_write({"value": "new"}, reads, old)
            self.assertEqual(result, 2)
            run.assert_not_called()
            self.assertEqual(receipts[0]["state"], "preflight_failed_zero_write")

    def test_lost_link_failed_or_empty_readback_never_verifies(self):
        old = [[{"value": "old"}]]
        want = {"rich_text": [{"type": "link", "text": "Document", "link": "https://example.com/new"}]}
        for after in ([[{"value": "Document"}]], [], ValueError("read failed")):
            result, run, receipts = self.run_write(want, [old, old, after], old)
            self.assertEqual(result, 2)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(receipts[0]["state"], "written_unverified")

    def test_verified_write_omits_local_expected_from_vendor_payload(self):
        old = [[{"value": "old"}]]
        want = {"formula": "=1+1"}
        result, run, receipts = self.run_write(want, [old, old, [[{"value": 2, **want}]]], old)
        self.assertEqual(result, 0)
        self.assertNotIn("expected", json.loads(run.call_args.kwargs["stdin"])[0])
        self.assertEqual(receipts[0]["state"], "verified")

    def test_dry_run_produces_prepared_patch_without_apply(self):
        old = [[{"value": "old"}]]
        result, run, receipts = self.run_write({"value": "new"}, [old], apply=False)
        self.assertEqual(result, 0)
        self.assertIn("--dry-run", run.call_args.args[0])
        self.assertEqual(receipts[0]["state"], "prepared_zero_write")
        self.assertEqual(receipts[0]["prepared_patch"]["writes"][0]["expected"], old)


if __name__ == "__main__":
    unittest.main()
