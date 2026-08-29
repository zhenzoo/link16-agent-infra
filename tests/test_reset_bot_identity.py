#!/usr/bin/env python3
"""同名重建 bot 时的身份状态清理（2026-08-27 · SOP-120 §4.3）。

锁死两条会出事的边界：
1. 聊天记录（inbound/outbox/outbound/receipts）+ 防重发游标（outbox-hwm/stop-cursor）
   **绝不能**被当成 per-app 状态清掉 —— 前者是用户要接管的东西，后者清了会把历史全重发。
2. per-app 的 open_id/chat_id（owner/session/turn-route/…）**必须**清 —— 留着会 230013 → 刷群。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import reset_bot_identity as reset  # noqa: E402

BOT = "tb26-demo"

PER_APP = [
    "bridge-owner-{}.json",
    "bridge-session-{}.json",
    "bridge-turn-route-{}.json",
    "bridge-delivery-state-{}.json",
    "bridge-answer-state-{}.json",
    "bridge-pending-{}.json",
    "watchdog-handoff-{}.json",
]
MUST_KEEP = [
    "bridge-inbound-{}.jsonl",
    "bridge-outbox-{}.jsonl",
    "bridge-outbound-{}.jsonl",
    "bridge-receipts-{}.jsonl",
    "bridge-outbox-hwm-{}.json",
    "bridge-stop-cursor-{}.json",
]


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        for pattern in PER_APP + MUST_KEEP:
            (self.state / pattern.format(BOT)).write_text("{}", encoding="utf-8")
        self.patch = mock.patch.object(reset, "STATE_DIR", self.state)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_every_per_app_file_is_scheduled_for_archive(self):
        to_archive, _keep = reset.plan(BOT)
        names = {p.name for p, _why in to_archive}
        self.assertEqual(names, {pattern.format(BOT) for pattern in PER_APP})

    def test_chat_records_and_cursors_are_never_archived(self):
        to_archive, _keep = reset.plan(BOT)
        names = {p.name for p, _why in to_archive}
        for pattern in MUST_KEEP:
            self.assertNotIn(pattern.format(BOT), names,
                             f"{pattern} 是要接管/防重发的，绝不能清")

    def test_keep_list_reports_existence(self):
        _archive, keep = reset.plan(BOT)
        self.assertTrue(all(exists for _p, _why, exists in keep))

    def test_missing_files_are_skipped_not_invented(self):
        (self.state / f"bridge-owner-{BOT}.json").unlink()
        to_archive, _keep = reset.plan(BOT)
        names = {p.name for p, _why in to_archive}
        self.assertNotIn(f"bridge-owner-{BOT}.json", names)

    def test_every_archived_entry_carries_a_reason(self):
        to_archive, _keep = reset.plan(BOT)
        for _path, why in to_archive:
            self.assertTrue(why and why.strip(), "清单每条都要写清为什么清")


class RegistryOpenIdTests(unittest.TestCase):
    def _registry(self, payload):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        tmp.write(json.dumps(payload, ensure_ascii=False, indent=1))
        tmp.close()
        return Path(tmp.name)

    def test_rewrite_swaps_only_that_bots_open_id(self):
        path = self._registry({"agents": [
            {"name": BOT, "open_id": "ou_old"},
            {"name": "other", "open_id": "ou_other"},
        ]})
        with mock.patch.object(reset, "registry_path", return_value=path):
            result = reset.rewrite_registry_openid(BOT, "ou_new", apply=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["agents"][0]["open_id"], "ou_new")
        self.assertEqual(data["agents"][1]["open_id"], "ou_other")

    def test_dry_run_writes_nothing(self):
        path = self._registry({"agents": [{"name": BOT, "open_id": "ou_old"}]})
        with mock.patch.object(reset, "registry_path", return_value=path):
            result = reset.rewrite_registry_openid(BOT, "ou_new", apply=False)
        self.assertTrue(result["changed"])
        self.assertIn("ou_old", path.read_text(encoding="utf-8"))

    def test_refuses_when_old_id_is_ambiguous(self):
        """同一个 open_id 出现多次时就地替换会误伤别人 —— 必须拒绝，不能猜。"""
        path = self._registry({"agents": [
            {"name": BOT, "open_id": "ou_dup"},
            {"name": "other", "open_id": "ou_dup"},
        ]})
        with mock.patch.object(reset, "registry_path", return_value=path):
            result = reset.rewrite_registry_openid(BOT, "ou_new", apply=True)
        self.assertFalse(result["ok"])
        self.assertIn("ou_dup", path.read_text(encoding="utf-8"))

    def test_noop_when_already_current(self):
        path = self._registry({"agents": [{"name": BOT, "open_id": "ou_same"}]})
        with mock.patch.object(reset, "registry_path", return_value=path):
            result = reset.rewrite_registry_openid(BOT, "ou_same", apply=True)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])

    def test_unknown_bot_is_reported_not_silently_ignored(self):
        path = self._registry({"agents": [{"name": "other", "open_id": "ou_other"}]})
        with mock.patch.object(reset, "registry_path", return_value=path):
            result = reset.rewrite_registry_openid(BOT, "ou_new", apply=True)
        self.assertFalse(result["ok"])
        self.assertIn(BOT, result["why"])


if __name__ == "__main__":
    unittest.main()
