#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
