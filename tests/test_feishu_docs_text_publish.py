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
            if _method == "GET":
                raise json.JSONDecodeError("empty response", "", 0)
            calls += 1
            raise json.JSONDecodeError("empty response", "", 0)

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep"):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 1, ["content"],
            )

        self.assertTrue(ok)
        self.assertEqual(filled, 0)
        self.assertEqual(failed, [0])
        # 首发 + _WRITE_RETRIES 次重试全是空正文，核实(GET)也都拿不到 → 才判失败
        self.assertEqual(calls, 1 + feishu_docs._WRITE_RETRIES)

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

    def test_rate_limited_write_is_verified_before_retry_and_not_duplicated(self):
        """限频空正文 → 核实未落地 → 重试一次成功；内容写入总共 2 次请求、落地 1 段，无重复。"""
        posts, gets = [], 0

        def api(method, url, token=None, body=None):
            nonlocal gets
            if method == "GET":
                gets += 1
                return {"code": 0, "data": {"items": []}}   # 核实：什么都没写进去
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{"table": {"cells": ["cell-1"]}}]}}
            posts.append(body["children"][0]["text"]["elements"][0]["text_run"]["content"])
            if len(posts) == 1:
                raise json.JSONDecodeError("empty response", "", 0)   # 429 空正文
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep"):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 1, ["content"],
            )

        self.assertTrue(ok)
        self.assertEqual((filled, failed), (1, []))
        self.assertEqual(posts, ["content", "content"])
        self.assertEqual(gets, 1)

    def test_landed_write_with_empty_response_is_not_resent(self):
        """空正文但核实发现那段已经在格子里 → 视为成功，绝不重发（否则正文重复）。"""
        posts = []

        def api(method, url, token=None, body=None):
            if method == "GET":
                return {"code": 0, "data": {"items": [
                    {"text": {"elements": [{"text_run": {"content": "content"}}]}},
                ]}}
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{"table": {"cells": ["cell-1"]}}]}}
            posts.append(1)
            raise json.JSONDecodeError("empty response", "", 0)

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep"):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 1, ["content"],
            )

        self.assertTrue(ok)
        self.assertEqual((filled, failed), (1, []))
        self.assertEqual(len(posts), 1)

    def test_cell_writes_are_throttled_to_about_three_per_second(self):
        sleeps = []

        def api(_method, url, token=None, body=None):
            if "blocks/doc/children" in url:
                return {"code": 0, "data": {"children": [{"table": {"cells": ["c1", "c2", "c3"]}}]}}
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep", side_effect=sleeps.append),                 mock.patch.object(feishu_docs, "_last_write_at", 0.0):
            feishu_docs._insert_real_table("token", "doc", 0, 1, 3, ["a", "b", "c"])

        # 建表 + 3 格 = 4 次写；第一次不用等，后面每次都被节流到 ≥ _WRITE_MIN_INTERVAL 间隔
        self.assertEqual(len(sleeps), 3)
        self.assertTrue(all(0 < s <= feishu_docs._WRITE_MIN_INTERVAL for s in sleeps))

    def test_tables_wider_or_taller_than_nine_are_created_then_grown(self):
        """飞书 children 建表上限 9×9：15×3 先建 9×3，再 PATCH 插 6 行，再 GET 取回 45 格逐格填。"""
        posts, patches, gets = [], [], 0

        def api(method, url, token=None, body=None):
            nonlocal gets
            if method == "GET":
                gets += 1
                return {"code": 0, "data": {"block": {"table": {
                    "cells": [f"c{i}" for i in range(45)]}}}}
            if method == "PATCH":
                patches.append(body)
                return {"code": 0}
            if "blocks/doc/children" in url:
                prop = body["children"][0]["table"]["property"]
                self.assertEqual((prop["row_size"], prop["column_size"]), (9, 3))
                self.assertEqual(sum(prop["column_width"]), feishu_docs._TABLE_FILL_WIDTH)
                return {"code": 0, "data": {"children": [{"block_id": "tbl", "table": {
                    "cells": [f"c{i}" for i in range(27)]}}]}}
            posts.append(url.split("/blocks/")[1].split("/")[0])
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep"):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 15, 3, [f"v{i}" for i in range(45)],
            )

        self.assertTrue(ok)
        self.assertEqual((filled, failed), (45, []))
        self.assertEqual(patches, [{"insert_table_row": {"row_index": -1}}] * 6)
        self.assertEqual(gets, 1)
        self.assertEqual(posts, [f"c{i}" for i in range(45)])

    def test_column_widths_fill_the_page_in_proportion_to_content(self):
        """默认 taste：表铺满正文宽（732），列宽 ∝ 该列最长内容 → 各列换行后行数接近；短列有下限。"""
        texts = ["#", "步骤", "依据",
                 "1", "单机位转播视频", "README 原文：" + "很长的引用" * 20,
                 "2", "计算机视觉逐帧识别球员和球", "PRIMER"]
        widths = feishu_docs._fill_widths(3, texts)
        # 内容多 → 总宽从 732 往上拉，但不超过页宽上限
        self.assertGreater(sum(widths), feishu_docs._TABLE_FILL_WIDTH)
        self.assertLessEqual(sum(widths), feishu_docs._TABLE_MAX_WIDTH)
        self.assertEqual(widths[0], feishu_docs._TABLE_MIN_COL_WIDTH)   # "#" 列压到下限
        self.assertGreater(widths[2], widths[1] * 2)                     # 依据列内容最长 → 最宽
        # 内容少的表保持正文宽 732
        small = feishu_docs._fill_widths(3, ["a", "b", "c", "1", "2", "3"])
        self.assertEqual(sum(small), feishu_docs._TABLE_FILL_WIDTH)
        self.assertEqual(feishu_docs._fill_widths(3), [244, 244, 244])   # 无内容信息时等分（= 飞书 import 约定）
        self.assertEqual(feishu_docs._fill_widths(0), [])

    def test_retune_patches_only_changed_columns_of_matching_live_tables(self):
        """已发布文档原位重算列宽：按顺序对应 markdown 表；结构不符的表跳过；只 PATCH 变了的列。"""
        blocks = [
            {"block_id": "t1", "block_type": 31,
             "table": {"property": {"row_size": 1, "column_size": 2}}, "children": ["c1", "c2"]},
            {"block_id": "c1", "block_type": 32, "children": ["p1"]},
            {"block_id": "p1", "block_type": 2, "text": {"elements": [{"text_run": {"content": "短"}}]}},
            {"block_id": "c2", "block_type": 32, "children": ["p2"]},
            {"block_id": "p2", "block_type": 2, "text": {"elements": [{"text_run": {"content": "很长" * 30}}]}},
        ]
        live = [{"block_id": "L1", "block_type": 31, "table": {"property": {
                    "row_size": 1, "column_size": 2, "column_width": [90, 100]}}},
                {"block_id": "L2", "block_type": 31, "table": {"property": {
                    "row_size": 3, "column_size": 3, "column_width": [100, 100, 100]}}}]
        patches = []

        def api(method, url, token=None, body=None):
            if method == "GET":
                return {"code": 0, "data": {"items": live, "has_more": False}}
            patches.append((url.split("/blocks/")[1].split("?")[0], body["update_table_property"]))
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_convert_markdown", return_value=(blocks, ["t1"])),                 mock.patch.object(feishu_docs, "_sleep"):
            done = feishu_docs.retune_table_widths("token", "doc", "md")

        self.assertEqual(len(done), 1)
        self.assertTrue(all(tid == "L1" for tid, _ in patches))          # 结构不符的 L2 没被碰
        self.assertEqual([p["column_index"] for _, p in patches], [1])   # 第 0 列已是 90 → 不重写

    def test_columns_beyond_nine_get_their_width_patched_after_growth(self):
        patches = []

        def api(method, url, token=None, body=None):
            if method == "GET":
                return {"code": 0, "data": {"block": {"table": {"cells": [f"c{i}" for i in range(12)]}}}}
            if method == "PATCH":
                patches.append(body)
                return {"code": 0}
            if "blocks/doc/children" in url:
                self.assertEqual(len(body["children"][0]["table"]["property"]["column_width"]), 9)
                return {"code": 0, "data": {"children": [{"block_id": "tbl", "table": {
                    "cells": [f"c{i}" for i in range(9)]}}]}}
            return {"code": 0}

        with mock.patch.object(feishu_docs, "api", side_effect=api),                 mock.patch.object(feishu_docs, "_sleep"):
            ok, filled, failed = feishu_docs._insert_real_table(
                "token", "doc", 0, 1, 12, [f"v{i}" for i in range(12)],
            )

        self.assertTrue(ok)
        self.assertEqual((filled, failed), (12, []))
        self.assertEqual(patches[:3], [{"insert_table_column": {"column_index": -1}}] * 3)
        self.assertEqual([p["update_table_property"]["column_index"] for p in patches[3:]], [9, 10, 11])
        self.assertTrue(all("column_width" in p["update_table_property"] for p in patches[3:]))

    def test_every_table_is_attempted_as_a_real_table_regardless_of_size(self):
        """2026-08-30 的"全篇 24 格"硬闸已删：45 格和 1 格的表一律先真表写入，不按尺寸降级。"""
        def table(tid, cid, pid, rows, cols):
            return [
                {"block_id": tid, "block_type": 31,
                 "table": {"property": {"row_size": rows, "column_size": cols}},
                 "children": [cid]},
                {"block_id": cid, "block_type": 32, "children": [pid]},
                {"block_id": pid, "block_type": 2,
                 "text": {"elements": [{"text_run": {"content": "x"}}]}},
            ]
        blocks = table("t1", "c1", "p1", 15, 3) + table("t2", "c2", "p2", 1, 1)
        with mock.patch.object(feishu_docs, "_tenant_token", return_value="token"),                 mock.patch.object(feishu_docs, "_create_docx", return_value="doc"),                 mock.patch.object(feishu_docs, "_convert_markdown",
                                  return_value=(blocks, ["t1", "t2"])),                 mock.patch.object(feishu_docs, "_insert_real_table",
                                  return_value=(True, 1, [])) as insert,                 mock.patch.object(feishu_docs, "_create_text_block") as text_block,                 mock.patch.object(feishu_docs, "_doc_url", return_value="https://doc"),                 mock.patch.object(feishu_docs, "_set_visibility"):
            result = feishu_docs.publish_text_as_doc(
                "app", "secret", markdown="tables", visibility="none",
            )

        self.assertEqual(insert.call_count, 2)
        self.assertEqual(result["tables_real"], 2)
        self.assertEqual(result["tables_degraded"], 0)
        self.assertEqual(result["table_cells_attempted"], 46)
        text_block.assert_not_called()
        self.assertNotIn("table_cell_budget_cap", result)


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

    def test_degraded_tables_are_never_accepted_as_native_success(self):
        """原生链哪怕拿到 URL，只要有表格降级成纯文本就不算成功：转 import；import 也失败则整体报错。"""
        fake = types.SimpleNamespace(
            publish_file_as_doc=mock.Mock(return_value={"url": "https://doc/import"}),
            publish_text_as_doc=mock.Mock(return_value={
                "url": "https://doc/native", "visibility": True,
                "tables_real": 1, "tables_degraded": 2, "table_cells_failed": 3}),
        )
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}):
            result, mode = feishu_bridge._publish_online_doc(self.bot, "answer.md", name="Answer")
        self.assertEqual((result["url"], mode), ("https://doc/import", "online_doc_import"))

        fake.publish_file_as_doc = mock.Mock(side_effect=RuntimeError("no drive:drive"))
        with mock.patch.dict(sys.modules, {"feishu_docs": fake}),                 self.assertRaisesRegex(RuntimeError, "2 张表格降级"):
            feishu_bridge._publish_online_doc(self.bot, "answer.md", name="Answer")

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
