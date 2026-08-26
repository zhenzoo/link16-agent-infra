#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import register_feishu_app as register  # noqa: E402


class DeviceGrantRecoveryTests(unittest.TestCase):
    def test_new_registration_passes_none_app_id(self):
        with mock.patch.object(register.lark, "register_app", return_value={}) as call:
            register._run_device_grant("tb26-new")
        self.assertIsNone(call.call_args.kwargs["app_id"])
        self.assertEqual(call.call_args.kwargs["app_preset"], {"name": "tb26-new"})

    def test_recovery_passes_existing_cli_app_id_unchanged(self):
        app_id = "cli_existing_app"
        with mock.patch.object(register.lark, "register_app", return_value={}) as call:
            register._run_device_grant("tb26-link16", app_id)
        self.assertEqual(call.call_args.kwargs["app_id"], app_id)
        self.assertEqual(call.call_args.kwargs["app_preset"], {"name": "tb26-link16"})


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
