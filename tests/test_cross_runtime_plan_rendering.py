import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

from bridge_events import normalize_codex_notification  # noqa: E402
from jsonl_reply_extract import progress  # noqa: E402
from kimi_events import KimiEvents  # noqa: E402


PLAN = [
    {"step": "Stage 1｜确认基线（实际 01:00）", "status": "completed"},
    {"step": "Stage 2｜统一 renderer（ETA 01:15）\n　2.1 🔄 修改共享投影（ETA 01:10）",
     "status": "inProgress"},
    {"step": "Stage 3｜真实 DM 验收（ETA 01:30）", "status": "pending"},
]


def _claude_label():
    todos = [
        {"content": item["step"],
         "status": {"inProgress": "in_progress"}.get(item["status"], item["status"])}
        for item in PLAN
    ]
    records = [
        {"type": "user", "message": {"content": "start"}},
        {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "todo", "name": "TodoWrite", "input": {"todos": todos},
        }]}},
        {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "todo", "content": "PRIVATE",
        }]}},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "claude.jsonl"
        transcript.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in records),
            encoding="utf-8",
        )
        record = progress(transcript)["milestone"]
    return next(step["label"] for step in record["steps"] if step["kind"] == "plan")


def _codex_label():
    event = normalize_codex_notification({
        "method": "turn/plan/updated",
        "params": {"threadId": "root", "turnId": "turn", "plan": PLAN},
    }, "root")
    return event["label"]


def _kimi_label():
    reducer = KimiEvents("session", ROOT)
    rows = [
        {"type": "metadata", "protocol_version": "1.5"},
        {"type": "turn.prompt", "agentId": "main", "time": 1, "input": []},
        {"type": "tools.update_store", "agentId": "main", "time": 2, "key": "todo", "value": [
            {"title": item["step"],
             "status": {"completed": "done", "inProgress": "in_progress"}.get(
                 item["status"], item["status"])}
            for item in PLAN
        ]},
    ]
    outputs = [record for offset, row in enumerate(rows) for record in reducer.consume(row, offset)]
    return next(step["label"] for step in outputs[-1]["steps"] if step["kind"] == "plan")


def test_claude_codex_kimi_render_the_same_canonical_plan():
    expected = (
        "📋 当前计划\n"
        "✅ Stage 1｜确认基线（实际 01:00）\n"
        "🔄 Stage 2｜统一 renderer（ETA 01:15）\n"
        "　2.1 🔄 修改共享投影（ETA 01:10）\n"
        "⏳ Stage 3｜真实 DM 验收（ETA 01:30）"
    )
    assert _claude_label() == _codex_label() == _kimi_label() == expected
