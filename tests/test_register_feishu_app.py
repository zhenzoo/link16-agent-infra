#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import register_feishu_app as register  # noqa: E402


class DeviceGrantRecoveryTests(unittest.TestCase):
    def test_explicit_kimi_runtime_registration_dry_run_is_read_only(self):
        profile = mock.Mock(name="kimi_profile")
        profile.name = "kimi-work"
        profile.runtime = "kimi"
        with mock.patch.object(register.agent_runtime, "profile_spec", return_value=profile), \
             mock.patch.object(register.agent_runtime, "profile_doctor", return_value={"ok": True}), \
             mock.patch.object(register.registration_transport, "preflight", return_value="test-sdk"), \
             mock.patch.object(register, "_recovery_app_id", return_value=None), \
             mock.patch.object(register.registration_monitor, "arm_job") as arm, \
             mock.patch.object(register, "_run_device_grant") as grant, \
             mock.patch.object(register, "write_env") as write, \
             mock.patch.object(sys, "argv", ["register", "--name", "test", "--bot", "test",
                 "--profile", "kimi-work", "--runtime", "kimi", "--tenant-kind", "personal",
                 "--capability", "core", "--dry-run"]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            register.main()
        self.assertIn("profile=kimi-work runtime=kimi", output.getvalue())
        arm.assert_not_called()
        grant.assert_not_called()
        write.assert_not_called()

    def test_known_app_id_recovers_without_requesting_credentials(self):
        with mock.patch.object(register.bridge_scope_audit, "_env_val", return_value="cli_original"), \
             mock.patch.object(register.registration_monitor, "get_job", return_value=None):
            self.assertEqual(register._recovery_app_id(None, "APP_ID", "test"), "cli_original")
            with self.assertRaisesRegex(ValueError, "不一致"):
                register._recovery_app_id("cli_other", "APP_ID", "test")

    def test_job_app_id_recovers_when_env_was_not_written(self):
        with mock.patch.object(register.bridge_scope_audit, "_env_val", return_value=None), \
             mock.patch.object(register.registration_monitor, "get_job", return_value={"app_id": "cli_original"}):
            self.assertEqual(register._recovery_app_id(None, "APP_ID", "test"), "cli_original")

    def test_new_registration_passes_none_app_id(self):
        with mock.patch.object(register.lark, "register_app", return_value={}) as call:
            register._run_device_grant("tb26-new")
        self.assertIsNone(call.call_args.kwargs["app_id"])
        self.assertEqual(call.call_args.kwargs["app_preset"], {"name": "tb26-new"})
        self.assertIsNone(call.call_args.kwargs["addons"])
        self.assertTrue(call.call_args.kwargs["create_only"])

    def test_recovery_passes_existing_cli_app_id_unchanged(self):
        app_id = "cli_existing_app"
        with mock.patch.object(register.lark, "register_app", return_value={}) as call:
            register._run_device_grant("tb26-link16", app_id)
        self.assertEqual(call.call_args.kwargs["app_id"], app_id)
        self.assertEqual(call.call_args.kwargs["app_preset"], {"name": "tb26-link16"})
        self.assertFalse(call.call_args.kwargs["create_only"])

    def test_first_link_never_receives_capability_addons(self):
        with mock.patch.object(register.lark, "register_app", return_value={}) as call:
            register._run_device_grant("tb26-docs")
        self.assertIsNone(call.call_args.kwargs["addons"])
        self.assertTrue(call.call_args.kwargs["create_only"])

    def test_default_capabilities_never_include_broad_drive(self):
        scopes = register.bridge_scope_audit.requested_scopes()
        self.assertNotIn("drive:drive", scopes)

    def test_no_missing_capabilities_produces_no_fix_scopes(self):
        self.assertEqual(register.bridge_scope_audit.requested_scopes([], for_fix=True), ())

    def test_second_link_default_scopes_do_not_include_broad_drive(self):
        scopes = register.bridge_scope_audit.requested_scopes(
            ["core", "group-a2a"], for_fix=True
        )
        self.assertNotIn("drive:drive", scopes)

    def test_docs_import_requests_the_self_serve_scope_not_broad_drive(self):
        """drive:drive needs tenant-admin approval; the import link must not need one."""
        scopes = register.bridge_scope_audit.requested_scopes(
            ["core", "docs-import"], for_fix=True
        )
        self.assertIn("docs:document:import", scopes)
        self.assertNotIn("drive:drive", scopes)
        audit = register.bridge_scope_audit
        self.assertTrue(audit.self_serve("docs:document:import"))
        self.assertFalse(audit.self_serve("drive:drive"))
        # Either scope still satisfies the capability, so an app that was granted
        # the umbrella one before this change does not regress to "missing".
        self.assertTrue(audit.cap_ok({"drive:drive"},
                                     audit.CAPABILITY_SPECS["docs-import"]["groups"]))

    def test_docs_consume_requests_only_verified_read_scopes(self):
        scopes = register.bridge_scope_audit.requested_scopes(
            ["docs-consume"], for_fix=True
        )
        self.assertEqual(scopes, (
            "sheets:spreadsheet:read",
            "docs:document.media:download",
            "board:whiteboard:node:read",
        ))
        self.assertNotIn("drive:drive", scopes)

    def test_enterprise_registration_defaults_to_docs_consume(self):
        capabilities = register._registration_capabilities(None, "enterprise")
        self.assertEqual(capabilities, ("core", "group-a2a", "docs-consume"))

    def test_personal_registration_keeps_safe_default(self):
        capabilities = register._registration_capabilities(None, "personal")
        self.assertEqual(capabilities, ("core", "group-a2a"))

    def test_enterprise_group_resolves_from_registry_not_bot_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(
                '{"tenants":[{"kind":"enterprise","group_name":"obsagent 大乱斗",'
                '"group_chat_id":"oc_company"}]}',
                encoding="utf-8",
            )
            self.assertEqual(
                register._tenant_kind_from_group("obsagent 大乱斗", registry),
                "enterprise",
            )
            self.assertIsNone(
                register._tenant_kind_from_group("tb26-looking-name-only", registry)
            )

    def test_group_a2a_registration_requires_explicit_group(self):
        with self.assertRaisesRegex(ValueError, "必须显式给 --group"):
            register._validate_group_choice(["core", "group-a2a"], None)
        register._validate_group_choice(
            ["core", "group-a2a"], "tb24-25交流水吧"
        )

    def test_core_only_registration_does_not_require_group(self):
        register._validate_group_choice(["core"], None)


class CredentialSafetyTests(unittest.TestCase):
    def test_failure_summary_never_contains_secret_values(self):
        secret = "super-sensitive-client-secret"
        token = "tenant-token-must-not-leak"
        summary = register._credential_failure_summary({
            "client_secret": secret,
            "tenant_access_token": token,
            "status": "failed",
            "code": 400,
        })
        self.assertNotIn(secret, summary)
        self.assertNotIn(token, summary)
        self.assertIn("client_secret", summary)
        self.assertIn("status=failed", summary)

    def test_first_env_creation_is_atomic_and_keeps_both_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / ".env"
            register.write_env("cli_test", "secret_test", "APP_ID", "APP_SECRET", target)
            content = target.read_text(encoding="utf-8")
            self.assertIn("APP_ID=cli_test", content)
            self.assertIn("APP_SECRET=secret_test", content)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_default_guidance_does_not_tell_coworker_to_sync_env(self):
        guidance = register._sync_guidance(False)
        self.assertIn("不同同事", guidance)
        self.assertNotIn("运行 envsync", guidance)
        self.assertIn("--trusted-same-owner-devices", guidance)

    def test_trusted_same_owner_guidance_is_explicit(self):
        guidance = register._sync_guidance(True)
        self.assertIn("同一所有者", guidance)
        self.assertIn("envsync", guidance)


class RegistrationLifecycleTests(unittest.TestCase):
    def test_sdk_callback_to_env_roster_and_ready_without_secret_in_output(self):
        sdk = register.registration_transport
        def response(payload):
            result = sdk.requests.Response()
            result.status_code = 200
            result._content = json.dumps(payload).encode()
            result._content_consumed = True
            return result
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            root = Path(tmp)
            monitor = register.registration_monitor
            stack.enter_context(mock.patch.object(monitor, "STATE_DIR", root / "state"))
            stack.enter_context(mock.patch.object(monitor, "launch_monitor", return_value=os.getpid()))
            stack.enter_context(mock.patch.object(monitor.bridge_injection, "inject_bot_prompt", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(register.bridge_scope_audit, "_env_val", return_value=None))
            stack.enter_context(mock.patch.object(register.agent_runtime, "profile_doctor", return_value={"ok": True}))
            stack.enter_context(mock.patch.object(register, "ENV_PATH", root / ".env"))
            stack.enter_context(mock.patch.object(register, "append_registry_stub", return_value={"send_key": "test", "at_name": "@test"}))
            roster = stack.enter_context(mock.patch.object(register.agent_runtime, "upsert_runtime_bot", return_value={"name": "test", "profile": "cxp"}))
            post = stack.enter_context(mock.patch.object(sdk.requests, "post", side_effect=[
                response({"supported_auth_methods": ["client_secret"]}),
                response({"device_code": "device-private", "interval": 0, "expires_in": 600,
                          "verification_uri_complete": "https://example.invalid/authorize"}),
                response({"client_id": "cli_test", "client_secret": "secret-private"}),
            ]))
            # This callback test owns an explicit mocked recipient; it must not
            # depend on whether pytest itself was launched inside a Feishu bot.
            stack.enter_context(mock.patch.object(sys, "argv", ["register", "--name", "test", "--bot", "test",
                               "--profile", "cxp", "--cwd", str(root), "--tenant-kind", "personal", "--capability", "core",
                               "--notify-bot", "test-controller"]))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            register.main()
            state = monitor.get_job(bot="test")
            self.assertEqual(state["notify_bot"], "test-controller")
            self.assertEqual(state["registration_source"], "official_sdk")
            self.assertTrue(state["sdk_returned_at"])
            self.assertTrue(state["milestones"]["registered"])
            self.assertIn("FEISHU_BRIDGE_TEST_APP_SECRET=secret-private", (root / ".env").read_text())
            self.assertNotIn("secret-private", output.getvalue())
            self.assertNotIn("secret-private", json.dumps(state))
            self.assertNotIn("device-private", json.dumps(state))
            self.assertEqual(roster.call_args.args[4], "cxp")
            self.assertEqual(roster.call_args.kwargs["cwd"], root.as_posix())
            self.assertEqual(post.call_count, 3)
            stack.enter_context(mock.patch.object(monitor, "_permission_check", return_value={"status": "ready"}))
            stack.enter_context(mock.patch.object(monitor, "_owner_check", return_value={"status": "ready"}))
            result = monitor.check_once(state["job_id"], emit=False)
            self.assertEqual(result["status"], "ready")
            self.assertNotIn("group_ready", result["events"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
