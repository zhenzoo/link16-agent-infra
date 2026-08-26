#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_feishu_probe as probe  # noqa: E402


class CredentialOutputSafetyTests(unittest.TestCase):
    def test_tenant_token_status_never_contains_prefix_or_suffix(self):
        token = "tenant-token-prefix-and-secret-suffix"
        message = probe.token_status_message("tb26-link16", token)
        self.assertNotIn(token, message)
        self.assertNotIn(token[:12], message)
        self.assertNotIn(token[-8:], message)
        self.assertIn(f"len={len(token)}", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
