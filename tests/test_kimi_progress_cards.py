"""Kimi infrastructure snapshots may update status, never erase public work."""
import asyncio
from copy import deepcopy

import pytest

from tests.test_bridge_outbox import bridge_outbox, FakeCards, fresh_state


def comment(identity, label, kind="commentary", **fields):
    return {"event_id": identity, "revision": 1, "kind": kind, "label": label, **fields}


def diagnostic(offset, phase="retry"):
    labels = {
        "retry": "⚠️ Kimi 连接失败（APIConnectionError）：详细说明。Kimi CLI 已安排自动重试，下一次为第 2 次／最多 10 次；本轮尚未完成。",
        "response": "🟢 Kimi 已产生新的模型响应记录，先前的请求等待／重试已恢复；本轮任务尚未完成，继续等待正常回复。",
        "wait-60": "⏳ Kimi 已发起模型请求，至少 60 秒没有新增模型响应记录。",
    }
    return comment(f"kimi-diagnostic:session:{offset}:{phase}", labels[phase])


def record(steps, *, partial=False, turn="t", route=None):
    value = {"kind": "progress", "runtime": "kimi", "contract": "milestone-v1",
             "root_turn": turn, "session": "session", "steps": deepcopy(steps),
             "route": route or {"kind": "p2a", "turn_key": turn}}
    if partial:
        value["kimi_diagnostic"] = {"phase": "retry"}
    return value


async def drain(records, state, cards, gate=None):
    return await bridge_outbox.drain_batch(
        records, new_card=cards.new_card, edit_card=cards.edit_card, send_plain=cards.send_plain,
        state=state, coalesce_sec=0, clock=lambda: 100, force_flush=True, workline_gate=gate,
    )


def test_retry_and_recovery_preserve_public_commentary_plan_and_tool_counts(tmp_path):
    async def run():
        state, cards = fresh_state(), FakeCards()
        normal = [comment("c1", "💬 已写入九个规格项目"),
                  comment("plan", "🔄 回读原表", "plan", plan_total=5, plan_completed=1),
                  comment("tools", "PRIVATE_TOOL_BODY", "tool", tool_count=43)]
        work = {"project": "TC101S", "task": "回读原表", "progress": "进行中"}
        await drain([record(normal)], state, cards, lambda _: work)
        retry = diagnostic(100)
        await drain([record([retry, {"event_id": "tools", "revision": 2, "kind": "tool", "tool_count": 44}], partial=True)], state, cards)
        assert "已写入九个规格项目" in cards.edits[-1][1]
        assert "计划 1/5" in cards.edits[-1][1] and "工具 44 次" in cards.edits[-1][1]
        assert state["v2_workline"] == work
        # The same partial update after a drainer restart keeps acknowledged prose.
        assert bridge_outbox.save_progress_state(tmp_path, "test", state)
        state = {**fresh_state(), **bridge_outbox.load_progress_state(tmp_path, "test")}
        recovered = diagnostic(200, "response")
        await drain([record([retry, recovered], partial=True)], state, cards)
        restored = cards.edits[-1][1]
        assert "已写入九个规格项目" in restored and "模型请求已恢复" in restored
        assert "APIConnectionError" not in restored
        updated = [*normal, retry, recovered, comment("c2", "💬 建表预检通过")]
        updated[2].update(revision=3, tool_count=45)
        await drain([record(updated)], state, cards)
        assert "已写入九个规格项目" in cards.edits[-1][1] and "建表预检通过" in cards.edits[-1][1]
        assert "PRIVATE_TOOL_BODY" not in cards.edits[-1][1]
        sent = len(cards.new), len(cards.edits)
        await drain([record(updated)], state, cards)
        assert (len(cards.new), len(cards.edits)) == sent
        assert len(cards.new) == 1
    asyncio.run(run())


@pytest.mark.parametrize("new_turn,new_route", [
    ("new", {"kind": "p2a", "turn_key": "new"}),
    ("t", {"kind": "a2a", "peer": "other", "mid": "peer-message"}),
])
def test_diagnostic_never_inherits_other_turn_or_destination(new_turn, new_route):
    async def run():
        state, cards = fresh_state(), FakeCards()
        await drain([record([comment("private", "PREVIOUS_OWNER_CONTEXT")])], state, cards)
        await drain([record([diagnostic(100)], partial=True, turn=new_turn, route=new_route)], state, cards)
        assert "PREVIOUS_OWNER_CONTEXT" not in str(state["v2_steps"])
        if new_route["kind"] == "a2a":
            assert len(cards.new) == 1 and not cards.edits
        else:
            assert "PREVIOUS_OWNER_CONTEXT" not in cards.new[-1][0]
    asyncio.run(run())


def test_unapproved_model_text_never_leaks_through_diagnostic_merge():
    async def run():
        state, cards = fresh_state(), FakeCards()
        with pytest.raises(bridge_outbox.RetrySend):
            await drain([record([comment("private", "UNAPPROVED_TEXT")])], state, cards, lambda _: False)
        await drain([record([diagnostic(100)], partial=True)], state, cards, lambda _: None)
        assert "UNAPPROVED_TEXT" not in str(state)
        assert "UNAPPROVED_TEXT" not in str(cards.new)
    asyncio.run(run())


def test_many_retry_records_render_one_current_status_without_altering_failure_answer():
    async def run():
        state, cards = fresh_state(), FakeCards()
        steps = [comment("c1", "💬 原业务过程")]
        for offset in range(16):
            steps.append(diagnostic(offset, "response" if offset % 2 else "retry"))
        await drain([record(steps)], state, cards)
        body = cards.new[-1][0]
        assert "原业务过程" in body and body.count("模型请求已恢复") == 1
        assert "APIConnectionError" not in body and len(cards.new) == 1
        assert len(state["v2_steps"]) == 17  # Evidence is retained, not deleted.
        failure = "❌ Kimi 本轮未完成：连接失败。本轮已结束，CLI 不再自动重试。"
        await drain([{"kind": "answer", "runtime": "kimi", "session": "session", "anchor": "t",
                      "route": {"kind": "p2a"}, "text": failure}], state, cards)
        assert cards.new[-1][0] == failure
    asyncio.run(run())


def test_preupgrade_diagnostic_only_card_recovers_latest_authorized_context():
    async def run():
        state, cards = fresh_state(), FakeCards()
        normal, old = comment("c1", "💬 参考布局已核"), diagnostic(100)
        await drain([record([normal, old])], state, cards)
        state["v2_card_ids"] = [old["event_id"]]  # State left by old sparse replacement.
        await drain([record([normal, old, diagnostic(200, "response")])], state, cards)
        assert "参考布局已核" in cards.edits[-1][1]
        assert "模型请求已恢复" in cards.edits[-1][1]
        assert "APIConnectionError" not in cards.edits[-1][1]
    asyncio.run(run())


def test_long_business_progress_splits_without_reviving_old_retry_warnings():
    async def run():
        state, cards = fresh_state(), FakeCards()
        steps = []
        for i in range(8):
            steps.extend([comment(f"c{i}", f"业务进度{i}：" + "核对规格。" * 100), diagnostic(i)])
        steps.append(diagnostic(100, "response"))
        await drain([record(steps)], state, cards)
        text = "\n".join(body for body, _ in cards.new)
        assert len(cards.new) > 1
        assert "APIConnectionError" not in text
        assert text.count("模型请求已恢复") == 1
        for i in range(8):
            assert text.count(f"业务进度{i}：") == 1
        assert all(len(body) <= bridge_outbox.CARD_BUDGET for body, _ in cards.new)
    asyncio.run(run())
