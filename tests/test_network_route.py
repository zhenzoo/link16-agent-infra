#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import network_route as nr  # noqa: E402


def result(route, ok, elapsed=100, error=""):
    return nr.ProbeResult(route, ok, elapsed_ms=elapsed, status=200 if ok else None,
                          bytes_read=1024 if ok else 0, error=error)


class RouteSelectionTests(unittest.TestCase):
    def test_direct_is_selected_when_proxy_fails(self):
        decision = nr.choose_route(
            [result("direct", True, 120), result("proxy", False, error="timeout")],
            "proxy",
        )
        self.assertTrue(decision.usable)
        self.assertEqual(decision.selected, "direct")

    def test_proxy_is_selected_when_direct_fails(self):
        decision = nr.choose_route(
            [result("direct", False, error="timeout"), result("proxy", True, 150)],
            "direct",
        )
        self.assertTrue(decision.usable)
        self.assertEqual(decision.selected, "proxy")

    def test_faster_route_must_beat_preference_by_threshold(self):
        close = nr.choose_route(
            [result("direct", True, 90), result("proxy", True, 100)], "proxy", min_gain=0.15)
        clear = nr.choose_route(
            [result("direct", True, 70), result("proxy", True, 100)], "proxy", min_gain=0.15)
        self.assertEqual(close.selected, "proxy")
        self.assertEqual(clear.selected, "direct")

    def test_both_failed_is_not_usable(self):
        decision = nr.choose_route(
            [result("direct", False, error="a"), result("proxy", False, error="b")],
            "proxy",
        )
        self.assertFalse(decision.usable)
        self.assertEqual(decision.selected, "proxy")
        self.assertEqual({row.error for row in decision.results}, {"a", "b"})

    def test_no_proxy_probes_direct_only(self):
        called = []

        def fake_probe(url, route, proxy, **kwargs):
            called.append((route, proxy))
            return result(route, True)

        decision = nr.detect("https://example.test", "proxy", None, probe_fn=fake_probe)
        self.assertEqual(called, [("direct", None)])
        self.assertEqual(decision.selected, "direct")


class ChildEnvironmentTests(unittest.TestCase):
    BASE = {
        "KEEP": "yes",
        "HTTP_PROXY": "old",
        "https_proxy": "old",
        "NO_PROXY": "old",
    }

    def test_direct_clears_proxy_only_in_child_copy(self):
        env = nr.child_environment("direct", "http://127.0.0.1:7897", self.BASE)
        self.assertEqual(env["KEEP"], "yes")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertEqual(env["NO_PROXY"], "*")
        self.assertEqual(self.BASE["HTTP_PROXY"], "old")

    def test_proxy_injects_all_common_keys_without_printing_or_mutating_parent(self):
        proxy = "http://127.0.0.1:7897"
        env = nr.child_environment("proxy", proxy, self.BASE)
        self.assertTrue(all(env[key] == proxy for key in nr.PROXY_KEYS))
        self.assertEqual(env["NO_PROXY"], "localhost,127.0.0.1")
        self.assertEqual(self.BASE["HTTP_PROXY"], "old")


if __name__ == "__main__":
    unittest.main(verbosity=2)
