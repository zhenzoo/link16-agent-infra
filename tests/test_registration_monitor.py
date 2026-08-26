#!/usr/bin/env python3
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import registration_monitor as monitor  # noqa: E402


class RegistrationMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)
        self.state_patch = mock.patch.object(monitor, "STATE_DIR", self.state_dir)
        self.state_patch.start()

    def tearDown(self):
        self.state_patch.stop()
        self.tmp.cleanup()

    def arm(self, capabilities=None):
        return monitor.arm_job(
            "tb26-test",
            capabilities or ["core", "group-a2a"],
            notify_bot="tb26-link16",
            group="交流水吧",
            app_id="cli_safe",
            id_env="APP_ID",
            secret_env="APP_SECRET",
            launch=False,
        )

    def test_state_contains_names_but_no_credentials(self):
        state = self.arm()
        raw = monitor._state_path(state["job_id"]).read_text(encoding="utf-8")
        self.assertIn("APP_SECRET", raw)
        self.assertNotIn("actual-secret", raw)
        self.assertNotIn("access_token", raw)
        parsed = json.loads(raw)
        self.assertNotIn("client_secret", parsed)

    def test_ready_path_emits_stable_milestone_ids(self):
        state = self.arm()
        monitor.record_stage(state["job_id"], "registered", app_id="cli_safe")
        with mock.patch.object(monitor.bridge_injection, "inject_bot_prompt",
                               return_value={"ok": True, "workspace_id": "ws"}):
            monitor.request_permission_review(state["job_id"])
        (self.state_dir / "bridge-owner-tb26-test.json").write_text(
            '{"open_id":"ou_owner"}', encoding="utf-8"
        )
        probe = types.SimpleNamespace(
            bot_groups=lambda _bot: [{"chat_id": "oc_group", "name": "tb24-25交流水吧"}]
        )
        with mock.patch.dict(sys.modules, {"bridge_feishu_probe": probe}), \
             mock.patch.object(monitor.bridge_scope_audit, "audit_entry",
                               return_value={"status": "ready", "missing": []}), \
             mock.patch.object(monitor.bridge_injection, "inject_bot_prompt",
                               return_value={"ok": True, "workspace_id": "ws"}) as inject:
            result = monitor.check_once(state["job_id"])
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["milestones"]["ready"])
        self.assertEqual(
            result["events"]["ready"]["event_id"], f"{state['job_id']}:ready"
        )
        self.assertGreaterEqual(inject.call_count, 4)
        self.assertTrue(all(event.get("delivered_at") for event in result["events"].values()))

    def test_permission_review_event_has_stable_naked_link_and_no_persisted_url(self):
        state = self.arm(["core", "docs-text"])
        monitor.record_stage(state["job_id"], "registered", app_id="cli_safe")
        with mock.patch.object(monitor.bridge_injection, "inject_bot_prompt",
                               return_value={"ok": True}) as inject:
            result = monitor.request_permission_review(state["job_id"])
        messages = [call.args[1] for call in inject.call_args_list]
        review = next(text for text in messages if ":permissions_review" in text)
        self.assertIn("https://open.feishu.cn/app/cli_safe/auth?q=", review)
        self.assertIn("docs-text", review)
        self.assertIn("docx:document:create", review)
        self.assertEqual(
            result["events"]["permissions_review"]["event_id"],
            f"{state['job_id']}:permissions_review",
        )
        raw = monitor._state_path(state["job_id"]).read_text(encoding="utf-8")
        self.assertNotIn("/auth?q=", raw)

    def test_failed_permission_review_delivery_is_retried(self):
        state = self.arm(["core"])
        monitor.record_stage(state["job_id"], "registered", app_id="cli_safe")
        outcomes = [
            {"ok": True},
            {"ok": False, "error": "composer stuck"},
            {"ok": True},
        ]
        with mock.patch.object(monitor.bridge_injection, "inject_bot_prompt",
                               side_effect=outcomes):
            first = monitor.request_permission_review(state["job_id"])
            second = monitor._emit_pending(state["job_id"])
        self.assertNotIn("delivered_at", first["events"]["permissions_review"])
        self.assertEqual(first["events"]["permissions_review"]["last_error"], "composer stuck")
        self.assertIn("delivered_at", second["events"]["permissions_review"])
        self.assertEqual(
            second["events"]["permissions_review"]["event_id"],
            f"{state['job_id']}:permissions_review",
        )

    def test_ready_state_with_undelivered_event_still_has_monitor_work(self):
        state = self.arm(["core"])
        state = monitor.record_stage(state["job_id"], "registered", app_id="cli_safe")
        state["status"] = "ready"
        state["events"]["permissions_review"] = {
            "event_id": f"{state['job_id']}:permissions_review"
        }
        self.assertEqual(
            monitor.pending_event_names(state),
            ["registered", "permissions_review"],
        )

    def test_api_error_stays_unknown_and_does_not_become_missing(self):
        state = self.arm(["core"])
        monitor.record_stage(state["job_id"], "registered")
        with mock.patch.object(monitor.bridge_scope_audit, "audit_entry",
                               return_value={"status": "unknown", "error": "timeout"}):
            result = monitor.check_once(state["job_id"], emit=False)
        self.assertEqual(result["status"], "manual_pending")
        self.assertEqual(result["checks"]["permissions"]["status"], "unknown")
        self.assertEqual(result["last_error"], "timeout")

    def test_failed_injection_is_retried_without_marking_delivered(self):
        state = self.arm(["core"])
        monitor.record_stage(state["job_id"], "registered")
        with mock.patch.object(monitor.bridge_scope_audit, "audit_entry",
                               return_value={"status": "missing", "missing": ["core"]}), \
             mock.patch.object(monitor.bridge_injection, "inject_bot_prompt",
                               return_value={"ok": False, "error": "composer stuck"}):
            first = monitor.check_once(state["job_id"])
            second = monitor.check_once(state["job_id"])
        event = second["events"]["registered"]
        self.assertNotIn("delivered_at", event)
        self.assertEqual(event["last_error"], "composer stuck")
        self.assertEqual(event["event_id"], first["events"]["registered"]["event_id"])


if __name__ == "__main__":
    unittest.main()
