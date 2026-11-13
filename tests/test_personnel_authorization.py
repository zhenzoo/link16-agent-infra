import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import personnel_authorization as auth


class PersonnelConsentTests(unittest.TestCase):
    def test_refreshable_identity_runs_real_search_without_new_consent(self):
        before = {"owner_matches": True, "personnel_search_scope": True, "status": "needs_refresh"}
        after = dict(before, personnel_ready=True, status="ready")
        with mock.patch.object(auth.audit, "user_identity_status", side_effect=[before, after]), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)) as run, \
             mock.patch.object(auth.docio, "_json_out", return_value={"ok": True, "data": {
                 "users": [{"open_id": "ou_example", "localized_name": "测试同事"}]}}):
            result = auth.verify("enterprise-bot", "测试同事")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(run.call_args.args[0][:2], ["contact", "+search-user"])

    def test_empty_success_is_not_a_verified_name_search(self):
        identity = {"owner_matches": True, "personnel_search_scope": True, "personnel_ready": True}
        with mock.patch.object(auth.audit, "user_identity_status", return_value=identity), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(auth.docio, "_json_out", return_value={"ok": True, "data": {"users": []}}):
            self.assertEqual(auth.verify("bot", "同事")["state"], "query_unverified")

    def test_refresh_that_confirms_token_missing_moves_to_consent(self):
        before = {"owner_matches": True, "personnel_search_scope": True, "status": "needs_refresh"}
        with mock.patch.object(auth.audit, "user_identity_status", side_effect=[before, {"status": "missing"}]), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=3)), \
             mock.patch.object(auth.docio, "_json_out", return_value={"error": {"type": "authentication", "subtype": "token_missing"}}):
            self.assertEqual(auth.verify("bot", "同事")["state"], "consent_missing")

    def test_wrong_owner_is_never_used_for_a_personnel_query(self):
        with mock.patch.object(auth.audit, "user_identity_status", return_value={"owner_matches": False}), \
             mock.patch.object(auth.docio, "run_lark") as run:
            self.assertEqual(auth.verify("bot", "同事")["state"], "consent_missing")
        run.assert_not_called()

    def test_personal_tenant_never_starts_enterprise_consent(self):
        with mock.patch.object(auth.audit, "registered_tenant_kind", return_value="personal"), \
             mock.patch.object(auth.docio, "run_lark") as run:
            with self.assertRaises(ValueError):
                auth.preflight("personal-bot")
        run.assert_not_called()

    def test_native_device_code_is_not_in_receipts_and_poll_has_no_short_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(bot="bot", query="同事", state=tmp, notify_bot="initiator")
            with mock.patch.object(auth, "preflight"), \
                 mock.patch.object(auth, "verify", side_effect=[{"state": "consent_missing"}, {"state": "verified"}]), \
                 mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)) as run, \
                 mock.patch.object(auth.docio, "_json_out", return_value={"device_code": "secret-device", "verification_uri_complete": "https://accounts.feishu.cn/verify", "expires_in": 600}), \
                 mock.patch.object(auth.bridge_injection, "inject_bot_prompt", return_value={"ok": True}):
                result = auth.run(args)
            self.assertEqual(result["state"], "verified")
            self.assertIsNone(run.call_args.kwargs["timeout"])
            self.assertFalse((Path(tmp) / "confirmation.json").exists())
            self.assertNotIn("secret-device", (Path(tmp) / "status.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
