import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))
import approval_draft_check as checker

EXAMPLE = ROOT / "tests" / "fixtures" / "approval_draft.example.json"


class DraftCheckTests(unittest.TestCase):
    def setUp(self):
        self.report = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_template(self):
        result = checker.check(self.report)
        self.assertEqual((result["required_controls"], result["rows"], result["attachments"]), (4, 1, 1))

    def test_conditional_star_empty_only_after_reopen(self):
        # Reproduce a form that restores its line items and attachments but loses a starred field.
        self.report["reopened"]["fields"]["dependent"] = False
        with self.assertRaisesRegex(checker.CheckError, "重开后.*必填"):
            checker.check(self.report)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "mock.json"
            bad.write_text(json.dumps(self.report), encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertNotEqual(checker.main([str(bad)]), 0)

    def test_definition_detects_missing_active_field_even_if_star_is_missing(self):
        self.report["expected"]["schema"][1]["required"] = True
        for stage in ("before", "reopened"):
            del self.report[stage]["fields"]["dependent"]
            self.report[stage]["stars"].remove("dependent")
        with self.assertRaisesRegex(checker.CheckError, "必填"):
            checker.check(self.report)

    def test_inactive_conditional_field_is_not_required(self):
        self.report["expected"]["schema"][1]["required"] = True
        self.report["expected"]["historical"] = []
        for stage in ("before", "reopened"):
            ui = self.report[stage]
            ui["choices"]["branch"] = "option-b"
            del ui["fields"]["dependent"]
            ui["stars"].remove("dependent")
            ui["historical"] = {}
        self.assertTrue(checker.check(self.report)["ok"])

    def test_row_conditions_can_use_a_parent_selection(self):
        widget = self.report["expected"]["schema"][2]["children"][0]
        widget["display_condition"] = self.report["expected"]["schema"][1]["display_condition"]
        self.assertTrue(checker.check(self.report)["ok"])
        del self.report["reopened"]["choices"]["branch"]
        with self.assertRaisesRegex(checker.CheckError, "条件驱动"):
            checker.check(self.report)

    def test_wrong_link_amount_attachment_or_actor_fails(self):
        for case in ("link", "amount", "attachment", "owner", "extra-row", "submit", "no-exit", "not-saved"):
            with self.subTest(case=case):
                report = copy.deepcopy(self.report)
                ui = report["reopened"]
                row = ui["tables"]["lines"][0]
                if case == "link":
                    row["links"] = ["other-source"]
                elif case == "amount":
                    row["amount"] = "NaN"
                elif case == "attachment":
                    ui["attachments"] *= 2
                elif case == "owner":
                    ui["owner"] = "other-owner"
                elif case == "extra-row":
                    ui["tables"]["lines"].append(copy.deepcopy(row))
                elif case == "submit":
                    report["events"].insert(1, {"action": "submit"})
                elif case == "no-exit":
                    report["events"][1]["draft"] = report["expected"]["draft"]
                else:
                    report["events"][0]["persisted"] = False
                with self.assertRaises(checker.CheckError):
                    checker.check(report)

    def test_current_definition_drift_and_wrong_profile_are_rejected(self):
        exp = self.report["expected"]
        with patch("bridge_scope_audit.user_identity_status", return_value={
                "owner_matches": True, "verified_owner_id": exp["owner"]}), \
                patch("proactive_sources.user_read", return_value={"form": []}):
            with self.assertRaisesRegex(checker.CheckError, "当前原表定义"):
                checker.verify_profile("example", exp)
        with patch("bridge_scope_audit.user_identity_status", return_value={"owner_matches": False}):
            with self.assertRaisesRegex(checker.CheckError, "身份"):
                checker.verify_profile("example", exp)


if __name__ == "__main__":
    unittest.main()
