#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook 的 cursor 身份闸：认不出就作废，且不许因为"认不出"而白白退化。

2026-08-18 事故（PLAN-929）：判据 `d.get("session") and sid and d.get("session") != sid`
里的 `and sid` 在 session_id 缺失时把整条件短路成 False ⇒ 闸不触发 ⇒
**把别的 session 的 floor 原样用在另一份 transcript 上**。
后果不是"多发一遍"而是**一个字都不发**：floor=1354 而那份文件只有 1121 行 ⇒
`ln > floor` 匹配数 0 ⇒ cards 空 ⇒ 静默 return ⇒ cursor 永远不动。

现在身份挂在 **tp（transcript 路径）** 上——它是钩子每次必有的输入，session_id 会缺。
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bs", ROOT / "feishu" / "hooks" / "bridge_stop.py")
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


class CursorIdentityGate(unittest.TestCase):
    def _read(self, payload, sid, tp):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bridge-stop-cursor-b.json").write_text(json.dumps(payload), encoding="utf-8")
            return bs._read_cursor(Path(d), "b", sid, tp)

    # ---- 新格式：tp 当身份 ----
    def test_same_transcript_keeps_floor_even_without_session_id(self):
        """核心收益：sid 缺失但 tp 一致 → cursor 仍可信，不必退回裸 anchor（避免质量退化）。"""
        cur = {"session": "AAA", "tp": bs._norm_tp("/x/a.jsonl"), "line": 1354}
        self.assertEqual(self._read(cur, "", "/x/a.jsonl"), 1354)
        self.assertEqual(self._read(cur, None, "/x/a.jsonl"), 1354)

    def test_different_transcript_invalidates_even_if_session_matches(self):
        """正是这次的病：cursor 属于另一份 transcript → 必须作废，否则 floor 会滤光新文件的全部内容。"""
        cur = {"session": "AAA", "tp": bs._norm_tp("/x/a.jsonl"), "line": 1354}
        self.assertEqual(self._read(cur, "AAA", "/x/b.jsonl"), 0)
        self.assertEqual(self._read(cur, "", "/x/b.jsonl"), 0)

    def test_path_spelling_does_not_split_one_file_into_two(self):
        """Windows 大小写/斜杠混写不该让同一份文件被判成两份（否则每轮都白白作废）。"""
        cur = {"session": "AAA", "tp": bs._norm_tp("C:/X/A.jsonl"), "line": 99}
        win_style = "C:" + chr(92) + "x" + chr(92) + "a.jsonl"   # 避开 heredoc 转义
        self.assertEqual(self._read(cur, "", win_style), 99)

    # ---- 老格式：向后兼容 ----
    def test_legacy_cursor_without_tp_still_honoured_by_session(self):
        """⚠️ 三台机现存 11 份 cursor 都没有 tp 字段。一刀切要求 tp 匹配 = 升级当天全舰队同时失效。"""
        legacy = {"session": "AAA", "line": 500}
        self.assertEqual(self._read(legacy, "AAA", "/x/a.jsonl"), 500)
        self.assertEqual(self._read(legacy, "BBB", "/x/a.jsonl"), 0)

    def test_legacy_cursor_with_empty_session_is_never_trusted(self):
        """存量地雷：历史上 sid 空时落盘的 {"session": ""} 对任何 session 都生效过。现在恒判无效。"""
        self.assertEqual(self._read({"session": "", "line": 500}, "AAA", "/x/a.jsonl"), 0)
        self.assertEqual(self._read({"session": "", "line": 500}, "", "/x/a.jsonl"), 0)

    def test_legacy_cursor_without_session_id_input_is_not_short_circuited(self):
        """原缺陷本体：老格式 + sid 缺失，绝不能再短路放行。只需退化一轮，下次写出的就带 tp、自愈。"""
        self.assertEqual(self._read({"session": "AAA", "line": 500}, "", "/x/a.jsonl"), 0)

    # ---- 写侧 ----
    def test_write_records_tp_so_next_read_can_self_heal(self):
        with tempfile.TemporaryDirectory() as d:
            bs._write_cursor(Path(d), "b", "", 42, "/x/a.jsonl")
            got = json.loads((Path(d) / "bridge-stop-cursor-b.json").read_text(encoding="utf-8"))
        self.assertEqual(got["tp"], bs._norm_tp("/x/a.jsonl"), "必须落 tp，否则下轮又只能靠会缺的 sid")
        self.assertEqual(got["line"], 42)


if __name__ == "__main__":
    unittest.main(verbosity=2)
