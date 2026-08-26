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


class ProxyDiscoveryTests(unittest.TestCase):
    def test_discovers_configured_system_and_common_without_duplicates(self):
        rows = nr.discover_proxy_candidates(
            "127.0.0.1:7897",
            {"https": "http://localhost:10808", "http": "http://127.0.0.1:7897"},
            common_ports=(7897, 7890),
        )
        displays = [nr._proxy_display(row.url) for row in rows]
        self.assertEqual(displays, [
            "http://127.0.0.1:7897",
            "http://localhost:10808",
            "http://127.0.0.1:7890",
        ])

    def test_ignores_non_loopback_system_proxy_but_keeps_explicit_config(self):
        rows = nr.discover_proxy_candidates(
            "http://proxy.example.test:8080",
            {"https": "http://corp.example.test:3128"},
            common_ports=(),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "PROXY_URL")

    def test_doctor_functionally_selects_working_candidate(self):
        candidates = (
            nr.ProxyCandidate("http://127.0.0.1:7897", "configured"),
            nr.ProxyCandidate("http://127.0.0.1:10808", "common"),
        )

        def fake_probe(url, route, proxy, **kwargs):
            if route == "direct":
                return result(route, False, error="blocked")
            if proxy.endswith(":7897"):
                return result(route, False, error="port open but proxy unusable")
            return result(route, True, elapsed=80)

        decision, route_map = nr.diagnose_proxy_candidates(
            "https://example.test", "proxy", candidates, probe_fn=fake_probe
        )
        self.assertEqual(decision.selected, "proxy-2")
        self.assertEqual(route_map[decision.selected].url, "http://127.0.0.1:10808")

    def test_proxy_display_redacts_credentials(self):
        label = nr._proxy_display("http://user:secret@127.0.0.1:7897")
        self.assertEqual(label, "http://127.0.0.1:7897")
        self.assertNotIn("secret", label)

if __name__ == "__main__":
    unittest.main(verbosity=2)
