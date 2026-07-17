import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

from activate_plan915_canary import _surface_leaks  # noqa: E402
from activate_plan916_canary import _matching_answer_receipt, _tool_metrics  # noqa: E402


class Plan916CanaryTests(unittest.TestCase):
    def test_surface_leaks_reports_each_persistence_boundary(self):
        report, total = _surface_leaks({
            "ledger": "RAW RAW",
            "outbox": "clean",
            "progress_state": "REASON",
        }, ("RAW", "REASON"))
        self.assertEqual(total, 3)
        self.assertEqual(report["ledger"], {"RAW": 2})
        self.assertEqual(report["outbox"], {})
        self.assertEqual(report["progress_state"], {"REASON": 1})

    def test_answer_receipt_requires_kind_time_and_exact_length(self):
        answer = {"text": "PLAN916_LIVE_FINAL ok", "ts": 100}
        records = [
            {"kind": "edit_card", "delivered": True, "len": 21, "ts": 101},
            {"kind": "new_card", "delivered": True, "len": 20, "ts": 101},
            {"kind": "new_card", "delivered": True, "len": 21, "ts": 99},
            {"kind": "new_card", "delivered": True, "len": 21, "ts": 101},
        ]
        self.assertEqual(_matching_answer_receipt(records, answer, since=90), records[-1])
        self.assertIsNone(_matching_answer_receipt(records[:-1], answer, since=90))

    def test_tool_metrics_use_structured_counts_and_paths(self):
        progress = [{"steps": [
            {
                "kind": "tool",
                "tool_count": 4,
                "tool_types": [
                    {"program": "rg", "count": 1},
                    {"program": "Get-Content", "count": 1},
                    {"program": "Python", "count": 1},
                ],
                "access_total": 8,
                "create_paths": ["feishu/_state/plan916-canary-artifact.txt"],
                "label": "访问：a · b · c · d · e · 另有 3 个\n修改：无",
            },
            {"kind": "plan", "plan_total": 3, "plan_completed": 3, "label": "done"},
        ]}]
        metrics = _tool_metrics(progress)
        self.assertEqual(metrics["tool_count"], 4)
        self.assertEqual(metrics["access_total"], 8)
        self.assertEqual(metrics["programs"], ["Get-Content", "Python", "rg"])
        self.assertEqual(metrics["plan_completed"], 3)
        self.assertIn("另有 3 个", metrics["labels"])


if __name__ == "__main__":
    unittest.main()
