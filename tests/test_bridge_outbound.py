import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_history  # noqa: E402
import bridge_outbound as outbound  # noqa: E402


def test_outbound_ledger_dedupes_only_by_nonempty_message_id(tmp_path):
    base = dict(origin="send_feishu_msg", route={"kind": "direct"},
                target="oc_group", text="相同正文")
    assert outbound.append_delivery(tmp_path, "bot", message_id="m1", now=1, **base)
    assert outbound.append_delivery(tmp_path, "bot", message_id="m1", now=2, **base)
    assert outbound.append_delivery(tmp_path, "bot", message_id="m2", now=3, **base)
    rows = outbound.read_records(tmp_path, "bot")
    assert [row["message_id"] for row in rows] == ["m1", "m2"]
    assert [row["text"] for row in rows] == ["相同正文", "相同正文"]


def test_history_prefers_delivered_fragment_ledger_over_same_auto_outbox(tmp_path):
    record = {"kind": "answer", "ts": 10, "session": "s", "anchor": "a", "text": "完整答案"}
    (tmp_path / "bridge-outbox-bot.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    outbound.append_delivery(
        tmp_path, "bot", origin="bridge_outbox", route={"kind": "p2a-ext"},
        target="oc_group", text="**回复 1/2**\n\n第一片", message_id="m1",
        session="s", anchor="a", answer_id="answer", fragment_id="f1", part=1, total=2, now=11,
    )
    outbound.append_delivery(
        tmp_path, "bot", origin="bridge_outbox", route={"kind": "p2a-ext"},
        target="oc_group", text="**回复 2/2**\n\n第二片", message_id="m2",
        session="s", anchor="a", answer_id="answer", fragment_id="f2", part=2, total=2, now=12,
    )
    with patch.object(bridge_history, "STATE_DIR", tmp_path):
        events, _jp, _n_in, n_out = bridge_history.gather("bot", 0, False)
    assert n_out == 2
    assert [event["message_id"] for event in events] == ["m1", "m2"]
    assert all(event["source"] == "outbound-ledger" for event in events)


def test_history_keeps_full_legacy_answer_until_every_fragment_is_recorded(tmp_path):
    record = {"kind": "answer", "ts": 10, "session": "s", "anchor": "a", "text": "完整答案"}
    (tmp_path / "bridge-outbox-bot.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    outbound.append_delivery(
        tmp_path, "bot", origin="bridge_outbox", route={"kind": "p2a-ext"},
        target="oc_group", text="第一片", message_id="m1", answer_id="answer",
        session="s", anchor="a", fragment_id="f1", part=1, total=2, now=11,
    )
    with patch.object(bridge_history, "STATE_DIR", tmp_path):
        events, _jp, _n_in, _n_out = bridge_history.gather("bot", 0, False)
    assert [event["text"] for event in events] == ["完整答案", "第一片"]


def test_history_keeps_same_text_with_different_message_ids(tmp_path):
    for index in (1, 2):
        outbound.append_delivery(
            tmp_path, "bot", origin="send_feishu_msg", route={"kind": "direct"},
            target="oc_group", text="相同正文", message_id=f"m{index}", now=index,
        )
    with patch.object(bridge_history, "STATE_DIR", tmp_path):
        events, _jp, _n_in, _n_out = bridge_history.gather("bot", 0, False)
    assert [event["message_id"] for event in events] == ["m1", "m2"]


def test_ledger_failure_never_raises_after_remote_send(tmp_path):
    with patch.object(outbound.bridge_injection, "injection_lock", side_effect=RuntimeError("lock failed")):
        assert outbound.append_delivery(
            tmp_path, "bot", origin="bridge_outbox", route={"kind": "p2a"},
            target="ou_owner", text="sent already", message_id="m1",
        ) is False


def test_reserved_fields_cannot_be_overridden_and_truncated_tail_isolated(tmp_path):
    path = outbound.ledger_path(tmp_path, "bot")
    path.write_bytes(b'{"truncated":')
    assert outbound.append_delivery(
        tmp_path, "bot", origin="bridge_outbox", route={"kind": "p2a"},
        target="ou_owner", text="real", message_id="m1",
        schema="evil", kind="evil", ts="evil",
    )
    rows = outbound.read_records(tmp_path, "bot")
    assert len(rows) == 1
    assert rows[0]["schema"] == outbound.SCHEMA
    assert rows[0]["kind"] == "outbound"
    assert rows[0]["text"] == "real"
