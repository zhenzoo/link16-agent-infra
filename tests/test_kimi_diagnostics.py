import asyncio
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "feishu"))
import bridge_outbox
import bridge_inbox
import kimi_native_worker as worker
import session_work
import turn_delivery_guard
from kimi_diagnostics import error_detail
from kimi_events import KimiEvents
from tests.test_kimi_events import fixture, loop, row, text


@pytest.mark.parametrize("fields, expected", [
    ({"errorName": "APITimeoutError"}, "请求超时（APITimeoutError）"),
    ({"errorName": "APIConnectionError"}, "连接失败（APIConnectionError）"),
    ({"errorName": "APIProviderOverloadedError"}, "接口报告过载"),
    ({"errorName": "APIStatusError", "statusCode": 503}, "HTTP 503"),
    ({"errorName": "APIProviderRateLimitError", "statusCode": 429}, "HTTP 429"),
    ({"error": {"code": "provider.auth_error", "details": {"statusCode": 401}}}, "HTTP 401"),
    ({"error": {"details": {"statusCode": 403}}}, "HTTP 403"),
    ({"error": {"details": {"statusCode": 400}}}, "HTTP 400"),
    ({"errorName": "PRIVATE_UNKNOWN", "statusCode": "PRIVATE_STATUS"}, "没有可安全确认"),
])
def test_error_classification_uses_structured_fields_without_raw_secrets(fields, expected):
    fields["errorMessage"] = "PRIVATE_TOKEN https://private.example/?key=PRIVATE"
    fields.setdefault("error", {})["message"] = "PRIVATE_TOOL_OUTPUT"
    fields["error"]["stack"] = "PRIVATE_STACK"
    result = error_detail(fields)
    assert expected in result
    assert "PRIVATE" not in result
    assert "https://" not in result


def init_reducer():
    reducer = KimiEvents("session_test", Path.cwd())
    for i, r in enumerate(fixture()[:2]):
        reducer.consume(r, i)
    return reducer


def test_retry_recovery_and_terminal_failure_use_real_wire_252_shape():
    reducer = init_reducer()
    reducer.consume(row("llm.request", kind="loop"), 2)
    retry = reducer.consume(row("turn.step.retrying", failedAttempt=1, nextAttempt=2,
                                maxAttempts=10, delayMs=599.9, errorName="APITimeoutError",
                                errorMessage="PRIVATE"), 3)
    rendered = json.dumps(retry, ensure_ascii=False)
    assert "APITimeoutError" in rendered and "第 2 次／最多 10 次" in rendered
    assert "PRIVATE" not in rendered
    reducer.consume(row("llm.request", kind="loop"), 4)
    recovered = reducer.consume(row("usage.record", usageScope="turn"), 5)
    assert "已产生新的模型响应记录" in json.dumps(recovered, ensure_ascii=False)
    assert reducer.consume(row("usage.record"), 6) == []
    reducer.consume(text("f", "原样的正常答案", "last"), 7)
    final = reducer.consume(row("turn.ended", reason="completed"), 8)[0]
    assert final["text"] == "原样的正常答案\n\n---\n✅ 已完成"
    assert "超时" not in final["text"]

    reducer = init_reducer()
    final = reducer.consume(row("turn.ended", reason="failed", error={
        "code": "provider.rate_limit", "name": "APIProviderRateLimitError",
        "details": {"statusCode": 429, "requestId": "PRIVATE"},
        "message": "PRIVATE", "retryable": True,
    }), 9)[0]
    assert "HTTP 429" in final["text"] and "不再自动重试" in final["text"]
    assert "PRIVATE" not in json.dumps(final)
    assert "✅ 已完成" not in final["text"]


def test_wait_timer_measures_record_silence_not_model_thinking_or_tool_runtime():
    reducer = init_reducer()
    assert reducer.waiting(999) is None  # No LLM request yet.
    reducer.consume(row("llm.request", kind="loop", time=100000), 2)
    assert reducer.waiting(159.999) is None
    assert "60 秒" in json.dumps(reducer.waiting(160), ensure_ascii=False)
    assert "5 分钟" in json.dumps(reducer.waiting(400), ensure_ascii=False)
    recovered = reducer.consume(loop("content.part", "think", part={"type": "think", "think": "PRIVATE"}), 3)
    assert "恢复" in json.dumps(recovered, ensure_ascii=False)
    assert "PRIVATE" not in json.dumps(recovered)
    assert reducer.waiting(1000) is None
    reducer.consume(loop("tool.call", "t", name="Bash", args={"command": "PRIVATE"}), 4)
    assert reducer.waiting(2000) is None
    reducer.consume(row("llm.request", agentId="child", time=200000), 5)
    assert reducer.waiting(2000) is None
    reducer.consume(row("turn.ended", reason="cancelled"), 6)
    assert reducer.waiting(3000) is None


def write_rows(path, rows):
    with path.open("a", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")


def outbox(observer):
    return [json.loads(line) for line in observer.outbox.read_text(encoding="utf-8").splitlines()]


def setup_observer(root, *, gated=False):
    wire = root / "wire.jsonl"
    state = root / "state"
    rows = fixture()[:2] + [row("llm.request", kind="loop", time=100000)]
    if gated:
        prompt = rows[1]["input"][0]["text"]
        session_work.begin_turn("test", "hook-key", prompt_digest=bridge_inbox.prompt_digest(prompt),
                                session="session_test", runtime="kimi", state_dir=state, now=100)
    write_rows(wire, rows)
    return worker.WireObserver(bot="test", session="session_test", wire=wire,
                               state_dir=state, cwd=root)


def test_pending_workline_does_not_block_request_notice_but_normal_content_stays_gated(tmp_path):
    observer = setup_observer(tmp_path, gated=True)
    observer.poll(now=159)
    observer.poll(now=160)
    records = outbox(observer)
    assert observer.reducer.route["workline_gate"] == session_work.GATE_CONTRACT
    assert observer.reducer.turn == "hook-key"
    assert all(r["route"]["turn_key"] == "hook-key" for r in records)
    with patch.object(session_work.time, "time", return_value=160):
        assert all(session_work.delivery_work("test", r, observer.state_dir) is None for r in records)
        assert session_work._load_gate("test", observer.state_dir)["turns"]["hook-key"]["status"] == "pending"
    write_rows(observer.wire, [text("c", "模型正文不能越过工作行"), loop("tool.call", "t", name="Read", args={})])
    observer.poll(now=161)
    normal = outbox(observer)[-1]
    assert normal["route"]["workline_gate"] == session_work.GATE_CONTRACT
    with patch.object(session_work.time, "time", return_value=161):
        assert session_work.delivery_work("test", normal, observer.state_dir) is False
    diagnostic = [r for r in outbox(observer) if r.get("kimi_diagnostic")]
    assert "模型正文" not in json.dumps(diagnostic, ensure_ascii=False)


def test_restart_preserves_hook_identity_wait_dedup_and_one_recovery(tmp_path):
    observer = setup_observer(tmp_path, gated=True)
    observer.poll(now=160)
    observer.poll(now=161)
    restarted = worker.WireObserver(bot="test", session="session_test", wire=observer.wire,
                                    state_dir=observer.state_dir, cwd=tmp_path)
    assert restarted.poll(now=170) == 0
    assert restarted.reducer.turn == "hook-key"
    assert restarted.reducer.route["workline_gate"] == session_work.GATE_CONTRACT
    assert restarted.poll(now=400) == 1
    write_rows(observer.wire, [row("usage.record", time=401000), text("f", "正常答案", "last"),
                              row("turn.ended", reason="completed", time=402000)])
    restarted.poll(now=403)
    records = outbox(restarted)
    phases = [r["kimi_diagnostic"]["phase"] for r in records if r.get("kimi_diagnostic")]
    assert phases.count("wait-60") == phases.count("wait-300") == phases.count("response") == 1
    assert all(r["route"]["turn_key"] == "hook-key" for r in records)
    again = worker.WireObserver(bot="test", session="session_test", wire=observer.wire,
                                state_dir=observer.state_dir, cwd=tmp_path)
    assert again.poll(now=900) == 0


def test_history_and_replaced_turn_do_not_raise_fresh_wait_warnings(tmp_path):
    observer = setup_observer(tmp_path)
    observer.poll(now=100)
    turn_delivery_guard.activate(observer.state_dir, "test", {"kind": "p2a"}, turn_key="new-owner")
    assert observer.poll(now=900) == 0
    seeded = worker.WireObserver(bot="seed", session="session_test", wire=observer.wire,
                                state_dir=observer.state_dir, cwd=tmp_path,
                                initial_offset=observer.wire.stat().st_size)
    assert seeded.poll(now=900) == 0
    assert not seeded.outbox.exists()


def test_corrupt_local_journal_reports_exact_local_boundary_without_raw_data(tmp_path):
    observer = setup_observer(tmp_path, gated=True)
    observer.poll(now=100)
    with observer.wire.open("a", encoding="utf-8") as handle:
        handle.write("PRIVATE_INVALID_JSON\n")
    with pytest.raises(json.JSONDecodeError) as exc:
        observer.poll(now=100)
    observer.report_failure(error=exc.value)
    diagnostic = outbox(observer)[-1]
    rendered = json.dumps(diagnostic, ensure_ascii=False)
    assert "完整记录不是合法 JSON" in rendered and "PRIVATE" not in rendered
    assert "workline_gate" not in diagnostic["route"]
    assert diagnostic["kind"] == "progress"


def test_protocol_failure_before_first_prompt_uses_only_exact_hook_session(tmp_path):
    wire = tmp_path / "wire.jsonl"
    write_rows(wire, [{"type": "metadata", "protocol_version": "PRIVATE_UNKNOWN"}])
    observer = worker.WireObserver(bot="test", session="session_test", wire=wire,
                                   state_dir=tmp_path / "state", cwd=tmp_path)
    for session in ("wrong_session", "session_test"):
        turn_delivery_guard.activate(observer.state_dir, "test", {"kind": "p2a"},
                                     session=session, turn_key="hook-key")
        with pytest.raises(ValueError) as exc:
            observer.poll(now=100)
        observer.report_failure(error=exc.value)
        assert observer.outbox.exists() == (session == "session_test")
    rendered = json.dumps(outbox(observer), ensure_ascii=False)
    assert "协议版本与桥适配版本不兼容" in rendered
    assert "PRIVATE" not in rendered


def test_observer_recovery_notice_is_persistent_and_does_not_end_live_turn(tmp_path):
    observer = setup_observer(tmp_path)
    observer.poll(now=100)
    observer.report_failure(error=PermissionError("PRIVATE"))
    restarted = worker.WireObserver(bot="test", session="session_test", wire=observer.wire,
                                    state_dir=observer.state_dir, cwd=tmp_path)
    assert restarted.poll(now=101) == 1
    assert restarted.poll(now=102) == 0
    records = outbox(restarted)
    assert "已恢复读取和回传" in json.dumps(records[-1], ensure_ascii=False)
    assert all(r["kind"] == "progress" for r in records)
    assert turn_delivery_guard.read_route(observer.state_dir, "test")["active"]


def test_diagnostic_retains_tool_count_without_ungating_plan_or_paths():
    reducer = init_reducer()
    reducer.consume(loop("tool.call", "t", name="Read", args={"path": "PRIVATE.md"}), 2)
    reducer.consume(row("tools.update_store", key="todo", value=[{"title": "PRIVATE_PLAN", "status": "pending"}]), 3)
    reducer.consume(row("llm.request", kind="loop"), 4)
    diagnostic = reducer.waiting(160)
    assert sum(s.get("tool_count", 0) for s in diagnostic["steps"]) == 1
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_real_writer_process_notice_recovery_and_final_reach_renderer(tmp_path):
    """A real append-only producer, independent observer and real gate/renderer.

    Only the external card HTTP callback is replaced; no production bot is used.
    """
    observer = setup_observer(tmp_path, gated=True)
    observer.poll(now=160)
    rows = [row("usage.record", time=162000), text("f", "原生结果保留", "last"),
            row("turn.ended", reason="completed", time=163000)]
    source = tmp_path / "tail.json"
    source.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    code = "import json,sys; rows=json.load(open(sys.argv[2],encoding='utf-8')); f=open(sys.argv[1],'a',encoding='utf-8'); [f.write(json.dumps(r,ensure_ascii=False)+'\\n') for r in rows]; f.close()"
    process = subprocess.Popen([sys.executable, "-c", code, str(observer.wire), str(source)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr.decode()
    observer.poll(now=164)
    session_work.decide_work("test", "hook-key", "replace", project="Probe", task="核验诊断送达",
                             progress="✅ 验证", state_dir=observer.state_dir, now=164)
    cards, edits = [], []

    async def new_card(body, route=None, **kwargs):
        cards.append((body, route, kwargs.get("purpose")))
        return {"ok": True, "message_id": f"m{len(cards)}"}

    async def edit_card(mid, body):
        edits.append(body)
        return True

    async def plain(*args, **kwargs):
        pytest.fail("diagnostics must use the existing card delivery contract")

    state = {"turn": None, "steps": [], "usage": {}, "seg_start": 0, "cur_mid": None,
             "flushed": 0, "last_flush": 0, "sent": set(), "picker_active": False}

    async def deliver():
        for record in outbox(observer):
            await bridge_outbox.drain_batch([record], new_card=new_card, edit_card=edit_card,
                send_plain=plain, state=state, coalesce_sec=0, clock=lambda:164, force_flush=True,
                workline_gate=lambda r: session_work.delivery_work("test", r, observer.state_dir))
    asyncio.run(deliver())
    rendered = "\n".join([c[0] for c in cards] + edits)
    assert "60 秒" in rendered and "已产生新的模型响应记录" in rendered
    answers = [c for c in cards if c[2] == "answer"]
    assert len(answers) == 1 and "原生结果保留" in answers[0][0]
    assert all(c[1]["dest"] == "oc_group" for c in cards)
