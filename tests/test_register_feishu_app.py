#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
