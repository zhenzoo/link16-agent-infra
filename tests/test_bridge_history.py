import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_history  # noqa: E402


class MilestoneHistoryTests(unittest.TestCase):
    def test_snapshot_records_expand_to_each_event_revision_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            records = [
                {"kind": "progress", "contract": "milestone-v1", "ts": 1, "steps": [
                    {"event_id": "c1", "revision": 1, "label": "commentary"}
                ]},
                {"kind": "progress", "contract": "milestone-v1", "ts": 2, "steps": [
                    {"event_id": "c1", "revision": 1, "label": "commentary"},
                    {"event_id": "p1", "revision": 1, "label": "plan pending"},
                ]},
                {"kind": "progress", "contract": "milestone-v1", "ts": 3, "steps": [
                    {"event_id": "c1", "revision": 1, "label": "commentary"},
                    {"event_id": "p1", "revision": 2, "label": "plan done"},
                ]},
            ]
            (state / "bridge-outbox-bot.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
            )
            with patch.object(bridge_history, "STATE_DIR", state):
                events = bridge_history._outbound_from_outbox("bot", True)
        self.assertEqual([event["text"] for event in events], [
            "commentary", "plan pending", "plan done"
        ])


if __name__ == "__main__":
    unittest.main()
