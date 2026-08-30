import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_history  # noqa: E402
import bridge_inbound  # noqa: E402


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


def _ledger_record(mid, text, *, ts, received_ts=None, source="feishu-ws"):
    return {
        "schema": bridge_inbound.SCHEMA,
        "kind": "inbound",
        "source": source,
        "received_ts": float(received_ts if received_ts is not None else ts),
        "ts": float(ts),
        "bot": "bot",
        "message_id": mid,
        "chat_id": "oc_test",
        "chat_type": "p2p",
        "sender": {"open_id": "ou_test"},
        "message_type": "text",
        "raw_text": text,
        "text": text,
        "resources": [],
    }


def _transcript_record(ts, text):
    return {
        "type": "user",
        "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "message": {"role": "user", "content": text},
    }


class InboundHistoryTests(unittest.TestCase):
    def test_ledger_is_queryable_without_any_runtime_session(self):
        long_text = "失败前完整长消息" * 1000
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            for row in (
                _ledger_record("om_group", "你是干什么的", ts=10),
                _ledger_record("om_slash", "/account ccp2", ts=11),
                _ledger_record("om_long", long_text, ts=12),
            ):
                bridge_inbound.append_record(state, "bot", row)
            with patch.object(bridge_history, "STATE_DIR", state):
                shown, jp, n_in, n_out = bridge_history.gather("bot", 0, False)
        self.assertIsNone(jp)
        self.assertEqual(n_out, 0)
        self.assertEqual(n_in, 3)
        self.assertEqual([event["text"] for event in shown], [
            "你是干什么的", "/account ccp2", long_text,
        ])

    def test_native_cutover_keeps_only_older_legacy_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            transcript = state / "session.jsonl"
            transcript.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in (
                    _transcript_record(10, "legacy-old [飞书 from=host to=bot via=DM · route=p2a]"),
                    _transcript_record(30, "ledger-copy [飞书 from=host to=bot via=DM · route=p2a]"),
                )) + "\n",
                encoding="utf-8",
            )
            (state / "bridge-session-bot.json").write_text(
                json.dumps({"jsonl": str(transcript)}), encoding="utf-8"
            )
            bridge_inbound.append_record(
                state, "bot", _ledger_record("om_native", "ledger-copy", ts=20, received_ts=20)
            )
            with patch.object(bridge_history, "STATE_DIR", state):
                shown, _jp, n_in, _n_out = bridge_history.gather("bot", 0, False)
        self.assertEqual(n_in, 2)
        self.assertEqual([event["text"] for event in shown], ["legacy-old", "ledger-copy"])

    def test_backfill_does_not_move_native_cutover(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            transcript = state / "session.jsonl"
            transcript.write_text(
                json.dumps(_transcript_record(10, "legacy"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (state / "bridge-session-bot.json").write_text(
                json.dumps({"jsonl": str(transcript)}), encoding="utf-8"
            )
            bridge_inbound.append_record(
                state, "bot", _ledger_record("om_backfill", "group-backfill", ts=5,
                                              received_ts=100, source="backfill")
            )
            with patch.object(bridge_history, "STATE_DIR", state):
                shown, _jp, n_in, _n_out = bridge_history.gather("bot", 0, False)
        self.assertEqual(n_in, 2)
        self.assertEqual([event["text"] for event in shown], ["group-backfill", "legacy"])


if __name__ == "__main__":
    unittest.main()
