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
        before = {"owner_matches": True, "personnel_search_scope": True, "status": "needs_refresh", "scopes": list(auth.SCOPES)}
        after = dict(before, personnel_ready=True, status="ready")
        with mock.patch.object(auth.audit, "user_identity_status", side_effect=[before, after]), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)) as run, \
             mock.patch.object(auth.docio, "_json_out", return_value={"ok": True, "data": {
                 "users": [{"open_id": "ou_example", "localized_name": "测试同事"}]}}):
            result = auth.verify("enterprise-bot", "测试同事")
        self.assertEqual(result["state"], "verified")
        self.assertEqual(run.call_args.args[0][:2], ["contact", "+search-user"])

    def test_empty_success_is_not_a_verified_name_search(self):
        identity = {"owner_matches": True, "personnel_search_scope": True, "personnel_ready": True, "scopes": list(auth.SCOPES)}
        with mock.patch.object(auth.audit, "user_identity_status", return_value=identity), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)), \
             mock.patch.object(auth.docio, "_json_out", return_value={"ok": True, "data": {"users": []}}):
            self.assertEqual(auth.verify("bot", "同事")["state"], "query_unverified")

    def test_refresh_that_confirms_token_missing_moves_to_consent(self):
        before = {"owner_matches": True, "personnel_search_scope": True, "status": "needs_refresh", "scopes": list(auth.SCOPES)}
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

    def test_personnel_success_does_not_hide_missing_docio_consent(self):
        scopes = auth.requested_scopes(SimpleNamespace(capability=["docio-read"]))
        identity = {"owner_matches": True, "personnel_search_scope": True, "personnel_ready": True,
                    "status": "ready", "scopes": [s for s in scopes if s != "drive:drive.metadata:readonly"]}
        with mock.patch.object(auth.audit, "user_identity_status", return_value=identity), \
             mock.patch.object(auth.docio, "run_lark") as run:
            result = auth.verify("bot", "同事", scopes)
        self.assertEqual(result["state"], "consent_missing")
        self.assertEqual(result["missing_requested_user_scopes"], ["drive:drive.metadata:readonly"])
        run.assert_not_called()

    def test_complete_docio_consent_reuses_current_identity_without_login(self):
        scopes = auth.requested_scopes(SimpleNamespace(capability=["docio-read"]))
        identity = {"owner_matches": True, "personnel_search_scope": True, "personnel_ready": True,
                    "status": "ready", "scopes": list(scopes)}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(auth, "preflight"), \
             mock.patch.object(auth.audit, "user_identity_status", return_value=identity), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)) as run, \
             mock.patch.object(auth.docio, "_json_out", return_value={"ok": True, "data": {
                 "users": [{"open_id": "ou_test", "localized_name": "同事"}]}}):
            result = auth.run(SimpleNamespace(bot="bot", query="同事", state=tmp,
                                             capability=["docio-read"], notify_bot="initiator"))
            self.assertFalse((Path(tmp) / "confirmation.json").exists())
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["missing_requested_user_scopes"], [])
        self.assertEqual(result["business_acceptance"], "personnel_search_only")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][:2], ["contact", "+search-user"])

    def test_check_and_identity_failures_do_not_start_oauth(self):
        for state, check in [("consent_missing", True), ("identity_unverified", False),
                             ("identity_mismatch", False), ("query_unverified", False)]:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(auth, "preflight"), \
                 mock.patch.object(auth, "verify", return_value={"state": state}), \
                 mock.patch.object(auth.docio, "run_lark") as run:
                result = auth.run(SimpleNamespace(bot="bot", query="同事", state=tmp,
                                                 notify_bot=None, check=check))
                self.assertEqual(result["state"], state)
                run.assert_not_called()

    def test_extra_scope_requires_user_grant_and_current_self_serve_catalog(self):
        for held, eligible in [(set(), ["docs:document.comment:create"]),
                               ({("docs:document.comment:create", "tenant")}, ["docs:document.comment:create"]),
                               ({("docs:document.comment:create", "user")}, [])]:
            with self.subTest(held=held, eligible=eligible), \
                 mock.patch.object(auth.audit, "registered_tenant_kind", return_value="enterprise"), \
                 mock.patch.object(auth.audit, "roster_bots", return_value=[("bot", "ID", "SECRET")]), \
                 mock.patch.object(auth.docio, "owner_open_id", return_value="ou_owner"), \
                 mock.patch.object(auth.docio, "require_profile_identity"), \
                 mock.patch.object(auth.audit, "granted_scope_pairs", return_value=(held, None)), \
                 mock.patch.object(auth.audit, "registration_scope_plan", return_value={"user": eligible}):
                with self.assertRaises(ValueError):
                    auth.preflight("bot", ("docs:document.comment:create",))

    def test_unknown_or_wrong_current_user_is_not_used(self):
        for identity, expected in [({"status": "unknown"}, "identity_unverified"),
                                    ({"status": "ready", "owner_matches": False}, "identity_mismatch")]:
            with self.subTest(identity=identity), \
                 mock.patch.object(auth.audit, "user_identity_status", return_value=identity), \
                 mock.patch.object(auth.docio, "run_lark") as run:
                self.assertEqual(auth.verify("bot", "同事")["state"], expected)
                run.assert_not_called()

    def test_requested_business_scopes_reach_native_login_and_waiting_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(auth, "preflight") as preflight, \
             mock.patch.object(auth, "verify", side_effect=[{"state": "consent_missing"}, {"state": "verified"}]), \
             mock.patch.object(auth.docio, "run_lark", return_value=SimpleNamespace(returncode=0)) as run, \
             mock.patch.object(auth.docio, "_json_out", return_value={"device_code": "secret-device", "verification_uri_complete": "https://accounts.feishu.cn/verify"}):
            args = SimpleNamespace(bot="bot", query="同事", state=tmp, notify_bot=None,
                                   capability=["docio-read"], scope=["docs:document.comment:create"])
            auth.run(args)
            scopes = auth.requested_scopes(args)
            preflight.assert_called_once_with("bot", scopes)
            self.assertIn("drive:drive.metadata:readonly", run.call_args_list[0].args[0][3])
            self.assertIn("docs:document.comment:create", run.call_args_list[0].args[0][3])
            self.assertFalse((Path(tmp) / "confirmation.json").exists())


if __name__ == "__main__":
    unittest.main()
