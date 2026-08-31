#!/usr/bin/env python3
"""Deterministic local acceptance scorer for PLAN-991.

Only temporary outboxes and fake delivery callbacks are used.  The evaluator
does not read credentials, production HWM files, or the network.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import bridge_outbox  # noqa: E402


def _dimension(name: str, checks: list[tuple[str, bool]]) -> dict:
    return {
        "name": name,
        "score": sum(bool(ok) for _label, ok in checks),
        "max": len(checks),
        "checks": [{"name": label, "ok": bool(ok)} for label, ok in checks],
    }


def _boundary_source() -> str:
    prefix = "**回复 3/3**\n\n"
    capacity = bridge_outbox.CARD_BUDGET - len(prefix)
    return ("a" * capacity) + "\n" + ("中" * 2765) + "\n" + ("🙂" * 522)


def _record() -> dict:
    return {
        "kind": "answer",
        "session": "boundary",
        "anchor": "plan220",
        "text": _boundary_source(),
        "route": {"kind": "p2a"},
    }


def _guard_source() -> str:
    prefix = "**回复 3/3**\n\n"
    capacity = bridge_outbox.ANSWER_TARGET_BUDGET - len(prefix)
    return ("a" * capacity) + "\n" + ("中" * 2755) + "\n" + ("🙂" * 522)


def _fresh_state() -> dict:
    return {
        "turn": None, "steps": [], "usage": {}, "seg_start": 0,
        "cur_mid": None, "flushed": 0, "last_flush": 0, "sent": set(),
        "picker_active": False,
    }


def _old_split_exact(text: str, capacity: int) -> list[str]:
    """PLAN-991 mutation: the pre-fix inclusive-right search."""
    if capacity <= 0:
        raise ValueError("fragment capacity 必须为正数")
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + capacity)
        if end < len(text):
            newline = text.rfind("\n", start, end + 1)
            if newline >= start:
                end = newline + 1
        if end <= start:
            end = min(len(text), start + capacity)
        chunks.append(text[start:end])
        start = end
    return chunks or [text]


def _split_checks(splitter=bridge_outbox._split_exact) -> list[tuple[str, bool]]:
    exact = "abcd\nx"
    exact_chunks = splitter(exact, 4)
    matrix = [
        ("abc\n中🙂", 4),
        ("abcd\n中🙂", 4),
        ("abcde\n中🙂", 4),
        ("中文🙂abcdef", 4),
    ]
    matrix_results = [
        (splitter(source, capacity), source, capacity)
        for source, capacity in matrix
    ]
    newline_start = splitter("\nabcde", 4)
    repeat = splitter("中文🙂abcdef", 4)
    return [
        ("exact-right-bound is bounded", all(len(chunk) <= 4 for chunk in exact_chunks)),
        ("exact-right-bound is lossless", "".join(exact_chunks) == exact),
        ("before/at/after/no-newline matrix is bounded and lossless", all(
            "".join(chunks) == source and all(len(chunk) <= capacity for chunk in chunks)
            for chunks, source, capacity in matrix_results
        )),
        ("historical newline-at-start split is preserved", newline_start == ["\n", "abcd", "e"]),
        ("unicode/no-newline split is deterministic", repeat == splitter("中文🙂abcdef", 4)),
    ]


def _answer_checks() -> list[tuple[str, bool]]:
    record = _record()
    source = record["text"]
    options = {
        "budget": bridge_outbox.ANSWER_TARGET_BUDGET,
        "hard_budget": bridge_outbox.CARD_BUDGET,
        "split_policy": bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
    }
    first = bridge_outbox._answer_fragments(record, source, record["route"], **options)
    second = bridge_outbox._answer_fragments(record, source, record["route"], **options)
    return [
        ("PLAN-220-equivalent answer produces three fragments", len(first) == 3),
        ("all rendered cards fit CARD_BUDGET", all(
            len(row["rendered"]) <= bridge_outbox.CARD_BUDGET for row in first
        )),
        ("fragment content rejoins byte-for-character", "".join(
            row["content"] for row in first
        ) == source),
        ("fragment identities are stable and unique", first == second and len({
            row["fragment_id"] for row in first
        }) == len(first)),
    ]


def _guard_checks(*, hard_budget=bridge_outbox.CARD_BUDGET) -> list[tuple[str, bool]]:
    record = {"session": "guard", "anchor": "eval"}
    route = {"kind": "p2a"}
    options = {
        "budget": bridge_outbox.ANSWER_TARGET_BUDGET,
        "hard_budget": hard_budget,
        "split_policy": bridge_outbox.ANSWER_SPLIT_POLICY_GUARD10,
    }
    normal = bridge_outbox._answer_fragments(record, "x" * 2795, route, **options)
    guarded = None
    try:
        with mock.patch.object(
            bridge_outbox, "_split_exact", side_effect=_old_split_exact,
        ):
            guarded = bridge_outbox._answer_fragments(
                record, _guard_source(), route, **options,
            )
    except ValueError:
        pass

    def overrun(text, capacity):
        # Model a splitter that consistently consumes eleven reserve chars per
        # full chunk.  Keep every chunk independently bounded at target+11 so
        # a hard-limit mutation from 2800 to 2801 cannot hide behind an
        # oversized remainder.
        width = capacity + 11
        return [text[start:start + width] for start in range(0, len(text), width)]

    rejected = False
    try:
        with mock.patch.object(bridge_outbox, "_split_exact", side_effect=overrun):
            bridge_outbox._answer_fragments(record, "x" * 6000, route, **options)
    except ValueError:
        rejected = True
    return [
        ("hard-target difference is exactly ten",
         bridge_outbox.CARD_BUDGET - bridge_outbox.ANSWER_TARGET_BUDGET == 10),
        ("2795-char answer splits instead of using reserve", len(normal) > 1),
        ("normal fragments use zero guard", all(
            row["guard_chars"] == 0
            and len(row["rendered"]) <= bridge_outbox.ANSWER_TARGET_BUDGET
            for row in normal
        )),
        ("old off-by-one remains lossless inside hard limit", bool(guarded)
         and "".join(row["content"] for row in guarded) == _guard_source()
         and all(len(row["rendered"]) <= hard_budget for row in guarded)),
        ("old off-by-one records exactly one guard character", bool(guarded)
         and max(row["guard_chars"] for row in guarded) == 1
         and any(row["guard_used"] for row in guarded)),
        ("eleven-character overrun is rejected", rejected),
    ]


async def _manifest_checks() -> list[tuple[str, bool]]:
    class Cards:
        def __init__(self, fail_parts=()):
            self.fail_parts = set(fail_parts)
            self.calls = []

        async def new_card(self, text, route=None, purpose="answer", fragment=None):
            self.calls.append((text, dict(fragment)))
            return None if fragment["part"] in self.fail_parts else f"m{fragment['part']}"

        async def edit_card(self, _mid, _text):
            return True

        async def send_plain(self, text, route=None, purpose="answer", fragment=None):
            return False if fragment["part"] in self.fail_parts else f"t{fragment['part']}"

    with tempfile.TemporaryDirectory() as tmp:
        record = {"kind": "answer", "session": "guard", "anchor": "manifest",
                  "text": _guard_source(), "route": {"kind": "p2a"}}
        first_state = _fresh_state()
        first_state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
        first = Cards({2})
        try:
            with mock.patch.object(
                bridge_outbox, "_split_exact", side_effect=_old_split_exact,
            ):
                await bridge_outbox.drain_batch(
                    [record], new_card=first.new_card, edit_card=first.edit_card,
                    send_plain=first.send_plain, state=first_state, coalesce_sec=0,
                    clock=lambda: 100, force_flush=True,
                    persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
                )
        except bridge_outbox.RetrySend:
            pass
        persisted = bridge_outbox.load_answer_state(tmp, "bot")
        saved = next(iter(persisted.get("answers", {}).values()), {})
        manifest = saved.get("manifest") or []
        manifest_ids = [row.get("fragment_id") for row in manifest]

        second_state = _fresh_state()
        second_state["answer_delivery"] = persisted
        second = Cards()
        await bridge_outbox.drain_batch(
            [record], new_card=second.new_card, edit_card=second.edit_card,
            send_plain=second.send_plain, state=second_state, coalesce_sec=0,
            clock=lambda: 101, force_flush=True,
            persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
        )

    with tempfile.TemporaryDirectory() as tmp:
        source = ("旧分片\n" * 1200).strip()
        record = {"kind": "answer", "session": "legacy", "anchor": "a",
                  "text": source, "route": {"kind": "p2a"}}
        legacy = bridge_outbox._answer_fragments(record, source, record["route"])
        first_fragment = legacy[0]
        delivery = {"version": 1, "answers": {first_fragment["answer_id"]: {
            "text_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "route": record["route"], "total": len(legacy),
            "fragments": {first_fragment["fragment_id"]: {
                "acked": True, "message_id": "old-m1", "part": 1,
                "content_sha256": first_fragment["content_sha256"], "acked_at": 1,
            }}, "completed_at": None,
        }}}
        bridge_outbox.save_answer_state(tmp, "bot", delivery)
        legacy_state = _fresh_state()
        legacy_state["answer_delivery"] = bridge_outbox.load_answer_state(tmp, "bot")
        legacy_cards = Cards()
        await bridge_outbox.drain_batch(
            [record], new_card=legacy_cards.new_card, edit_card=legacy_cards.edit_card,
            send_plain=legacy_cards.send_plain, state=legacy_state, coalesce_sec=0,
            clock=lambda: 102, force_flush=True,
            persist_answer=lambda value: bridge_outbox.save_answer_state(tmp, "bot", value),
        )
        legacy_saved = bridge_outbox.load_answer_state(
            tmp, "bot",
        )["answers"][first_fragment["answer_id"]]

    return [
        ("manifest is durable before retry", len(manifest) == 3),
        ("manifest preserves emergency guard boundary", bool(manifest)
         and manifest[0].get("guard_chars") == 1),
        ("restart sends only missing parts", [row[1]["part"] for row in second.calls] == [2, 3]),
        ("restart uses persisted fragment IDs", [
            row[1]["fragment_id"] for row in second.calls
        ] == manifest_ids[1:]),
        ("legacy ACK backfills old policy without part-one resend",
         legacy_saved.get("split_policy") == bridge_outbox.ANSWER_SPLIT_POLICY_LEGACY
         and [row.get("fragment_id") for row in legacy_saved.get("manifest") or []]
         == [row["fragment_id"] for row in legacy]
         and 1 not in [row[1]["part"] for row in legacy_cards.calls]),
    ]


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _drainer_checks() -> list[tuple[str, bool]]:
    with tempfile.TemporaryDirectory() as tmp:
        bot = "plan991-replay"
        bridge_outbox.append_record(tmp, bot, _record())
        eof = Path(bridge_outbox.outbox_path(tmp, bot)).stat().st_size
        reached_eof = asyncio.Event()
        cards, fallbacks, errors, offsets = [], [], [], []

        async def new_card(text, route=None, purpose="answer", fragment=None):
            cards.append((text, route, purpose, fragment))
            return {"ok": True, "message_id": f"om_{fragment['part']}"}

        async def edit_card(_mid, _text):
            return True

        async def send_plain(text, route=None, purpose="answer", fragment=None):
            fallbacks.append((text, route, purpose, fragment))
            return True

        async def asleep(_delay):
            await asyncio.sleep(0)

        def save_offset(offset):
            offsets.append(offset)
            if offset == eof:
                reached_eof.set()

        task = asyncio.create_task(bridge_outbox.outbox_drainer(
            bot, state_dir=tmp, new_card=new_card, edit_card=edit_card,
            send_plain=send_plain, asleep=asleep, hwm_load=lambda: 0,
            hwm_save=save_offset, on_error=errors.append, poll=0,
            coalesce_sec=0,
        ))
        try:
            await asyncio.wait_for(reached_eof.wait(), timeout=1)
        finally:
            await _cancel(task)
        return [
            ("three fragment ACKs", len(cards) == 3 and [
                row[3]["part"] for row in cards
            ] == [1, 2, 3]),
            ("HWM reaches exact outbox EOF", bool(offsets) and offsets[-1] == eof),
            ("interactive delivery needs no text fallback", not fallbacks),
            ("healthy replay emits no error event", not errors),
        ]


async def _observability_checks() -> list[tuple[str, bool]]:
    with tempfile.TemporaryDirectory() as tmp:
        bot = "plan991-error"
        bridge_outbox.append_record(tmp, bot, {"kind": "noop"})
        bridge_outbox.append_record(tmp, bot, {
            "kind": "answer", "session": "s", "anchor": "a",
            "text": "answer", "route": {"kind": "p2a"},
        })
        first_error = asyncio.Event()
        failed_twice = asyncio.Event()
        block = asyncio.Event()
        errors, offsets, failures = [], [], []

        async def no_send(*_args, **_kwargs):
            raise AssertionError("delivery callback must not run")

        async def asleep(delay):
            if delay >= 1 and len(failures) >= 2:
                await block.wait()
            else:
                await asyncio.sleep(0)

        def on_error(event):
            errors.append(event)
            first_error.set()

        def fail(*_args, **_kwargs):
            failures.append(1)
            if len(failures) >= 2:
                failed_twice.set()
            raise RuntimeError("SECRET_MARKER token=supersecret injected failure")

        with mock.patch.object(bridge_outbox, "_answer_fragments", side_effect=fail):
            task = asyncio.create_task(bridge_outbox.outbox_drainer(
                bot, state_dir=tmp, new_card=no_send, edit_card=no_send,
                send_plain=no_send, asleep=asleep, hwm_load=lambda: 0,
                hwm_save=offsets.append, on_error=on_error, poll=0,
                coalesce_sec=0,
            ))
            try:
                await asyncio.wait_for(first_error.wait(), timeout=1)
                await asyncio.wait_for(failed_twice.wait(), timeout=1)
            finally:
                await _cancel(task)
        event = errors[0] if errors else {}
        safe_error = str(event.get("error") or "")
        return [
            ("event identifies bot and blocked offset", event.get("bot") == bot
             and event.get("offset") == 0),
            ("event identifies actual record kind", event.get("kind") == "answer"),
            ("event identifies exception type", event.get("error_type") == "RuntimeError"),
            ("unknown error is digest-only", safe_error.startswith("internal error digest=")
             and "SECRET_MARKER" not in safe_error and "supersecret" not in safe_error),
            ("same poison reports once and never advances HWM", len(errors) == 1
             and len(failures) == 2 and not offsets),
        ]


async def evaluate(splitter=bridge_outbox._split_exact, *,
                   guard_hard=bridge_outbox.CARD_BUDGET) -> dict:
    dimensions = [
        _dimension("split_boundaries", _split_checks(splitter)),
        _dimension("answer_fragments", _answer_checks()),
        _dimension("guard_band", _guard_checks(hard_budget=guard_hard)),
        _dimension("manifest_compat", await _manifest_checks()),
        _dimension("drainer_replay", await _drainer_checks()),
        _dimension("observability", await _observability_checks()),
    ]
    score = sum(row["score"] for row in dimensions)
    maximum = sum(row["max"] for row in dimensions)
    return {"score": score, "max": maximum, "passed": score == maximum,
            "dimensions": dimensions}


async def _main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true",
                        help="inject the old inclusive-right splitter and prove rejection")
    args = parser.parse_args(argv)
    baseline = await evaluate()
    output = {"baseline": baseline}
    if args.self_test:
        mutation = await evaluate(_old_split_exact)
        no_guard_mutation = await evaluate(guard_hard=bridge_outbox.ANSWER_TARGET_BUDGET)
        relaxed_hard_mutation = await evaluate(guard_hard=bridge_outbox.CARD_BUDGET + 1)
        baseline_split = baseline["dimensions"][0]
        mutation_split = mutation["dimensions"][0]
        baseline_guard = baseline["dimensions"][2]
        no_guard = no_guard_mutation["dimensions"][2]
        relaxed_hard = relaxed_hard_mutation["dimensions"][2]
        caught = (baseline["passed"] and not mutation["passed"]
                  and mutation_split["score"] < baseline_split["score"]
                  and not no_guard_mutation["passed"]
                  and no_guard["score"] < baseline_guard["score"]
                  and not relaxed_hard_mutation["passed"]
                  and relaxed_hard["score"] < baseline_guard["score"])
        output.update({
            "splitter_mutation": mutation,
            "no_guard_mutation": no_guard_mutation,
            "relaxed_hard_mutation": relaxed_hard_mutation,
            "self_test_caught": caught,
        })
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if caught else 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if baseline["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
