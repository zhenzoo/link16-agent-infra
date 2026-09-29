#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import machine_identity as mi  # noqa: E402


def identity(manufacturer="LENOVO", model="21VR", version="ThinkBook 14 G8+ AHP",
             bios="2026-04-14"):
    return mi.MachineIdentity(manufacturer, model, model, version, bios, "LAPTOP-TEST")


class PrefixSuggestionTests(unittest.TestCase):
    def test_thinkbook_uses_tb_and_bios_year_as_medium_confidence(self):
        row = mi.suggest_prefix(identity())
        self.assertEqual(row.prefix, "tb26")
        self.assertEqual(row.year_source, "BIOS release year")
        self.assertEqual(row.confidence, "medium")

    def test_tuf_and_user_year_override(self):
        row = mi.suggest_prefix(identity("ASUSTeK", "FX505", "TUF Gaming", "2020-01-01"), year="2019")
        self.assertEqual(row.prefix, "tuf19")
        self.assertEqual(row.confidence, "high")

    def test_unknown_model_has_stable_manufacturer_family(self):
        row = mi.suggest_prefix(identity("ACME Corp", "X1", "", ""))
        self.assertEqual(row.prefix, "acme")
        self.assertEqual(row.confidence, "low")

    def test_collision_is_reported_without_silent_rename(self):
        row = mi.suggest_prefix(identity(), existing={"tb26"})
        self.assertTrue(row.collision)
        self.assertEqual(row.prefix, "tb26")

    def test_same_hostname_reuses_existing_machine_prefix(self):
        row = mi.suggest_prefix(identity(), existing={
            "tb26": {"hostname": "LAPTOP-TEST"},
        })
        self.assertFalse(row.collision)

    def test_explicit_prefix_has_highest_priority(self):
        row = mi.suggest_prefix(identity(), year="25", explicit_prefix="Desk-26")
        self.assertEqual(row.prefix, "desk-26")
        self.assertEqual(row.year_source, "user prefix")


MAC_SYSTEM_PROFILER = json.dumps({
    "SPHardwareDataType": [{
        "machine_name": "MacBook Pro",
        "machine_model": "Mac14,7",
        "chip_type": "Apple M2 Pro",
        "model_number": "Z17G000W0CH/A",
    }],
})


def _fake_run(stdout, returncode=0):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


class MacOSIdentityTests(unittest.TestCase):
    def test_system_profiler_json_shape(self):
        with mock.patch.object(mi.subprocess, "run",
                               return_value=_fake_run(MAC_SYSTEM_PROFILER)), \
             mock.patch.object(mi.socket, "gethostname", return_value="kinto-mac"):
            identity = mi.collect_macos_identity()
        self.assertEqual(identity.manufacturer, "Apple")
        self.assertEqual(identity.model, "Mac14,7")
        self.assertEqual(identity.product_name, "MacBook Pro")
        self.assertEqual(identity.version, "Apple M2 Pro")
        self.assertEqual(identity.bios_release_date, "")
        self.assertEqual(identity.hostname, "kinto-mac")

    def test_sysctl_fallback_when_profiler_fails(self):
        def run(cmd, **kwargs):
            if cmd[0] == "system_profiler":
                return _fake_run("", returncode=1)
            return _fake_run({"hw.model": "Mac14,7", "kern.osproductversion": "14.5"}[cmd[-1]])

        with mock.patch.object(mi.subprocess, "run", side_effect=run), \
             mock.patch.object(mi.socket, "gethostname", return_value="kinto-mac"):
            identity = mi.collect_macos_identity()
        self.assertEqual(identity.model, "Mac14,7")
        self.assertEqual(identity.version, "macOS 14.5")

    def test_mac_without_bios_year_is_low_confidence_until_user_overrides(self):
        mac = mi.MachineIdentity("Apple", "Mac14,7", "MacBook Pro", "Apple M2 Pro", "", "kinto-mac")
        row = mi.suggest_prefix(mac)
        self.assertEqual(row.prefix, "mb")
        self.assertEqual(row.year_source, "unknown")
        self.assertEqual(row.confidence, "low")
        row = mi.suggest_prefix(mac, year="2026")
        self.assertEqual(row.prefix, "mb26")
        self.assertEqual(row.year_source, "user")
        self.assertEqual(row.confidence, "high")

    def test_collect_identity_dispatches_by_platform(self):
        with mock.patch.object(mi.os, "name", "nt"), \
             mock.patch.object(mi, "collect_windows_identity",
                               return_value="win") as win:
            self.assertEqual(mi.collect_identity(), "win")
            win.assert_called_once()
        with mock.patch.object(mi.os, "name", "posix"), \
             mock.patch.object(mi.sys, "platform", "darwin"), \
             mock.patch.object(mi, "collect_macos_identity",
                               return_value="mac") as mac:
            self.assertEqual(mi.collect_identity(), "mac")
            mac.assert_called_once()
        with mock.patch.object(mi.os, "name", "posix"), \
             mock.patch.object(mi.sys, "platform", "linux"):
            with self.assertRaises(OSError):
                mi.collect_identity()


if __name__ == "__main__":
    unittest.main(verbosity=2)
