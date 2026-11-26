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
        self.assertEqual((result["required_controls"], result["rows"], result["attachments"]), (3, 1, 1))

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

    def test_only_observed_stars_require_values_in_form_and_rows(self):
        for where in ("form", "row"):
            for api_required, starred, filled, passes in (
                (True, False, False, True), (False, True, False, False),
                (False, True, True, True), (True, True, False, False),
            ):
                with self.subTest(where=where, api=api_required, star=starred, filled=filled):
                    r = copy.deepcopy(self.report)
                    field = "dependent" if where == "form" else "relation"
                    schema = r["expected"]["schema"]
                    widget = schema[1] if where == "form" else schema[2]["children"][0]
                    widget["required"] = api_required
                    if where == "row" and not filled:
                        r["expected"]["tables"]["lines"][0]["links"] = []
                    for stage in ("before", "reopened"):
                        ui = r[stage]
                        scope = ui if where == "form" else ui["tables"]["lines"][0]
                        scope["fields"][field] = filled
                        if where == "row" and not filled:
                            scope["links"] = []
                        if not starred:
                            scope["stars"].remove(field)
                        if where == "form" and not filled:
                            ui["historical"] = {}
                    if passes:
                        self.assertEqual(checker.check(r)["required_controls"], 2 + int(starred))
                    else:
                        with self.assertRaisesRegex(checker.CheckError, "红星.*必填"):
                            checker.check(r)

    def test_unread_star_scope_or_field_is_not_an_observed_optional_blank(self):
        for where in ("form", "row"):
            for missing in ("stars", "null-stars", "field", "all"):
                with self.subTest(where=where, missing=missing):
                    r = copy.deepcopy(self.report)
                    ui = r["reopened"]
                    scope = ui if where == "form" else ui["tables"]["lines"][0]
                    field = "dependent" if where == "form" else "relation"
                    if missing == "stars":
                        del scope["stars"]
                    elif missing == "null-stars":
                        scope["stars"] = None
                    else:
                        scope["stars"].remove(field)
                        del scope["fields"][field]
                        if missing == "all":
                            scope.update(stars=[], fields={}, choices={})
                    with self.assertRaisesRegex(checker.CheckError, "重开后.*(未读|没有控件读数)"):
                        checker.check(r)
        del self.report["reopened"]
        with self.assertRaisesRegex(checker.CheckError, "重开后编辑页未读"):
            checker.check(self.report)

    def test_unstarred_history_only_compares_values_already_filled(self):
        for stage in ("before", "reopened"):
            self.report[stage]["stars"].remove("dependent")
        self.report["reopened"]["historical"]["dependent"] = False
        with self.assertRaisesRegex(checker.CheckError, "历史字段"):
            checker.check(self.report)
        for stage in ("before", "reopened"):
            self.report[stage]["fields"]["dependent"] = False
            self.report[stage]["historical"] = {}
        self.assertEqual(checker.check(self.report)["required_controls"], 2)

    def test_native_visible_and_hidden_observations_override_api_visibility(self):
        self.report["expected"]["schema"][0]["visible"] = False
        for stage in ("before", "reopened"):
            scope = self.report[stage]
            scope["hidden"] = ["dependent"]
            scope["stars"].remove("dependent")
            del scope["fields"]["dependent"]
            scope["historical"] = {}
        self.assertEqual(checker.check(self.report)["required_controls"], 2)
        self.report["reopened"]["fields"]["branch"] = False
        self.report["reopened"]["choices"]["branch"] = ""
        with self.assertRaisesRegex(checker.CheckError, "重开后.*红星"):
            checker.check(self.report)

    def test_each_row_has_independent_star_and_value_observations(self):
        exp = self.report["expected"]
        second = dict(exp["tables"]["lines"][0], key="item-b", amount="2.00", links=[], purpose="")
        exp["tables"]["lines"].append(second)
        exp["total"] = "3.00"
        for stage in ("before", "reopened"):
            ui = self.report[stage]
            ui["total"] = "3.00"
            ui["tables"]["lines"].append(dict(second, fields={"relation": False}, stars=[], choices={}))
        self.assertEqual(checker.check(self.report)["required_controls"], 3)
        self.report["reopened"]["tables"]["lines"][1]["stars"].append("relation")
        with self.assertRaisesRegex(checker.CheckError, "重开后.*第 2 行.*必填"):
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
        condition = copy.deepcopy(self.report["expected"]["schema"][1]["display_condition"])
        condition["expressions"][0]["standard_value"] = "option-b"
        self.report["expected"]["schema"][2]["children"].append(
            dict(id="other", visible=True, required=True, display_condition=condition))
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
