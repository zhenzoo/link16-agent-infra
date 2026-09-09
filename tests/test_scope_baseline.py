import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_scope_audit as audit


class ScopeLevelSnapshotTests(unittest.TestCase):
    """SPEC-220: the snapshot is the only machine answer to "needs admin approval"."""

    def test_snapshot_matches_its_declared_contract(self):
        data = json.loads(audit.SCOPE_LEVELS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "link16-feishu-scope-levels-v1")
        self.assertIn(data["snapshot"]["tenant_kind"], ("enterprise", "personal"))
        self.assertEqual(data["counts"]["total"], len(data["scopes"]))
        self.assertEqual(
            data["counts"]["level_3"] + data["counts"]["level_4"], data["counts"]["total"]
        )

    def test_snapshot_carries_no_tenant_or_app_identifiers(self):
        """The repo is publishable: a snapshot must describe the catalog, not an app."""
        blob = audit.SCOPE_LEVELS_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bcli_[0-9a-z]{6,}", blob), "app id leaked")
        self.assertIsNone(re.search(r"\btenant_key\b|\bgrant_status\b", blob),
                          "per-app state leaked")
        # The endpoint may be documented, but only with a placeholder app id.
        if "app_id" in blob:
            self.assertIn("<app_id>", blob)

    def test_every_baseline_scope_is_self_serve(self):
        """A baseline entry that needs approval would park a bot behind a queue."""
        offenders = [s for s in audit.BASELINE_SCOPES if not audit.self_serve(s)]
        self.assertEqual(offenders, [])

    def test_umbrella_scopes_are_recognised_as_approval_gated(self):
        for scope in ("drive:drive", "drive:drive:readonly", "drive:file", "docs:doc"):
            self.assertFalse(audit.self_serve(scope), scope)

    def test_unknown_scope_never_reports_self_serve(self):
        self.assertFalse(audit.self_serve("definitely:not:a:real:scope"))

    def test_gap_splits_by_level_and_never_links_an_approval_scope(self):
        granted = set(audit.BASELINE_SCOPES) - {"sheets:spreadsheet", "docx:document"}
        gap = audit.baseline_gap(granted)
        self.assertEqual(sorted(gap["self_serve"]), ["docx:document", "sheets:spreadsheet"])
        self.assertEqual(gap["needs_approval"], [])
        self.assertEqual(gap["unknown_level"], [])
        self.assertEqual(audit.baseline_gap(set(audit.BASELINE_SCOPES))["self_serve"], [])


if __name__ == "__main__":
    unittest.main()


class RegistrationBaselineTests(unittest.TestCase):
    """A newly registered bot must not leave registration with a partial baseline."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
        import register_feishu_app  # noqa: PLC0415
        self.register = register_feishu_app

    def test_second_link_covers_the_whole_self_serve_baseline(self):
        scopes = audit.registration_scopes(("core", "group-a2a", "docs-consume"))
        for wanted in ("sheets:spreadsheet", "docx:document", "drive:file:download",
                       "docs:document:import", "docs:document.comment:read"):
            self.assertIn(wanted, scopes)
        self.assertNotIn("drive:drive", scopes)

    def test_enterprise_default_capabilities_unchanged(self):
        self.assertEqual(
            self.register._registration_capabilities(None, "enterprise"),
            ("core", "group-a2a", "docs-consume"),
        )

    def test_registration_scopes_is_the_single_source_and_subtracts_granted(self):
        """Dry-run, console print and the monitor's second link must all agree."""
        full = audit.registration_scopes(("core", "group-a2a", "group-listen"))
        self.assertIn("im:message.group_msg", full)
        for scope in audit.BASELINE_SCOPES:
            self.assertIn(scope, full)
        self.assertNotIn("drive:drive", full)
        held = set(audit.BASELINE_SCOPES)
        remaining = audit.registration_scopes(("core", "group-a2a", "group-listen"), held)
        self.assertEqual(remaining, ("im:message.group_msg",))
        self.assertEqual(audit.registration_scopes(("core", "group-a2a"), held), ())
