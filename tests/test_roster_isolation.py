#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PLAN-928 · 本机名册隔离：committed 名册永远不能是「能直接跑的真名册」。

背景（2026-08-17 真实事故）：committed 的 `bridge-bots.json` 里躺着 7 只真 tb24 bot，
用的是真实 .env 键名。而 .env 跨机同步、每 bot = 一个飞书应用、一个应用只允许一条长连接。
于是 tuf19 首次起桥（本机还没有 local 名册）直接连上了 tb24 的 7 个应用，抢了 6.5 小时消息。

这两条测试是**防复发的机械闸**：谁要是再把真 bot 提交进模板、或把「没名册也能兜底跑」加回来，
测试立刻红。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_bridge  # noqa: E402


class CommittedRosterIsTemplateOnly(unittest.TestCase):
    def test_committed_roster_lists_zero_bots(self):
        raw = json.loads((ROOT / "feishu" / "bridge-bots.json").read_text(encoding="utf-8"))
        self.assertEqual(
            raw.get("bots"), [],
            "committed bridge-bots.json 必须永远是空模板 —— 往它加真 bot 会让"
            "任何没配本机名册的机器抢走别人的飞书应用（PLAN-928 事故）",
        )

    def test_no_hardcoded_fallback_bot(self):
        # 曾经的 DEFAULT_BOT 写死 FEISHU_BRIDGE_APP_ID + @tb24-xhs-autopilot，
        # 等于「没名册就去连 tb24 的 autopilot」。这条闸禁止它以任何形式回来。
        self.assertFalse(
            hasattr(feishu_bridge, "DEFAULT_BOT"),
            "不许有单 bot 兜底默认：它会把没配名册的机器接到别人的飞书应用上",
        )

    def test_local_example_is_safe_to_copy_before_registration(self):
        raw = json.loads(
            (ROOT / "feishu" / "bridge-bots.local.example.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            raw.get("bots"), [],
            "SOP-100 会让新机器直接复制 local example；它的 bots 必须为空，"
            "否则会留下一个带假凭据键的可运行 bot",
        )


class MissingRosterFailsClosed(unittest.TestCase):
    def _load_with_roster(self, roster_dir):
        original = feishu_bridge.PROJECT
        try:
            feishu_bridge.PROJECT = roster_dir
            return feishu_bridge.load_bots()
        finally:
            feishu_bridge.PROJECT = original

    def test_empty_roster_raises_with_actionable_guidance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "feishu").mkdir()
            (proj / "feishu" / "bridge-bots.json").write_text(
                json.dumps({"bots": []}), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                self._load_with_roster(proj)
        msg = str(ctx.exception)
        # 报错必须【能照着做】：说清读到哪个文件、为什么不兜底、下一步敲什么
        self.assertIn("bridge-bots.local.json", msg)
        self.assertIn("preflight.py", msg)
        self.assertIn("SOP-100", msg)

    def test_absent_roster_also_fails_closed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "feishu").mkdir()
            with self.assertRaises(SystemExit):
                self._load_with_roster(proj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
