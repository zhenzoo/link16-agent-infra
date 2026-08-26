#!/usr/bin/env python3
"""Deterministic acceptance scorer for PLAN-970.

The evaluator uses only temporary homes, fake send functions, and local files.
It never touches credentials, Windows startup state, or the production bridge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "feishu"))

from feishu import bridge_outbox  # noqa: E402
from feishu import feishu_bridge  # noqa: E402
from feishu import profile_bootstrap  # noqa: E402
from feishu import service_doctor  # noqa: E402
from feishu import service_installer  # noqa: E402
from feishu import turn_delivery_guard  # noqa: E402


BAD = {"missing", "outdated", "conflict", "drift", "manifest-drift", "name-conflict"}


def _dimension(name: str, checks: list[tuple[str, bool]]) -> dict:
    return {
        "name": name,
        "score": sum(bool(ok) for _label, ok in checks),
        "max": len(checks),
        "checks": [{"name": label, "ok": bool(ok)} for label, ok in checks],
    }


def _self_contained_checks() -> list[tuple[str, bool]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / "user"
        registry = root / "profiles.json"
        profile_bootstrap.initialize_registry(registry, [
            {"name": "claude-work", "runtime": "claude", "home": "~/.claude-work"},
            {"name": "codex-work", "runtime": "codex", "home": "~/.codex-work"},
        ], apply=True)
        profile_bootstrap.bootstrap(
            home, apply=True, profiles=("claude-work", "codex-work"), registry_path=registry,
        )
        second = profile_bootstrap.bootstrap(
            home, apply=True, profiles=("claude-work", "codex-work"), registry_path=registry,
        )
        hooks_path = home / ".codex-work" / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        return [
            ("repo-owned feishu skill source", (ROOT / ".agents/skills/feishu/SKILL.md").is_file()),
            ("Claude and Codex skill targets", all(path.is_file() for path in (
                home / ".claude-work/skills/feishu/SKILL.md",
                home / ".agents/skills/feishu/SKILL.md",
            ))),
            ("Codex three-event hooks", set(hooks.get("hooks") or {}) == {
                "Stop", "PostToolUse", "UserPromptSubmit",
            }),
            ("second apply healthy and no auth copy",
             not any(row.get("status") in BAD for row in second)
             and not list(home.rglob("auth.json"))
             and not list(home.rglob("credentials.json"))),
        ]


async def _route_checks_async() -> list[tuple[str, bool]]:
    calls = []
    originals = {
        "card": feishu_bridge._send_interactive_message,
        "text": feishu_bridge._send_group_text,
        "history": feishu_bridge._record_automatic_outbound,
        "receipt": feishu_bridge.receipt,
    }

    def fake_card(_app, _secret, target, payload, _uuid=None):
        calls.append(("card", target, payload))
        return f"om_card_{len(calls)}"

    def fake_text(_app, _secret, target, text, at=None, _uuid=None):
        calls.append(("text", target, text, at))
        return f"om_text_{len(calls)}"

    def route_to_dest(route):
        return route.get("dest") or "oc_dm", route.get("at")

    try:
        feishu_bridge._send_interactive_message = fake_card
        feishu_bridge._send_group_text = fake_text
        feishu_bridge._record_automatic_outbound = lambda *_args, **_kwargs: True
        feishu_bridge.receipt = lambda *_args, **_kwargs: None
        bot = {"app_id": "app", "app_secret": "secret"}
        await feishu_bridge._deliver_routed_new(
            bot, "bot", "dm", {"kind": "p2a"}, "answer", {}, route_to_dest,
        )
        await feishu_bridge._deliver_routed_new(
            bot, "bot", "human", {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_human"},
            "answer", {}, route_to_dest,
        )
        await feishu_bridge._deliver_routed_new(
            bot, "bot", "peer", {"kind": "a2a", "dest": "oc_group", "at": "ou_peer"},
            "answer", {}, route_to_dest,
        )
    finally:
        feishu_bridge._send_interactive_message = originals["card"]
        feishu_bridge._send_group_text = originals["text"]
        feishu_bridge._record_automatic_outbound = originals["history"]
        feishu_bridge.receipt = originals["receipt"]
    return [
        ("p2a DM interactive", calls[0][0] == "card" and calls[0][1] == "oc_dm"),
        ("p2a-ext human group interactive", calls[1][0] == "card" and "ou_human" in json.dumps(calls[1][2])),
        ("a2a peer text", calls[2][0] == "text" and calls[2][3] == "ou_peer"),
    ]


def _fragment_checks(*, mutate=False) -> list[tuple[str, bool]]:
    route = {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_human"}
    record = {"session": "session", "anchor": "anchor", "ts": 1}
    short = bridge_outbox._answer_fragments(record, "short", route)
    source = ("一段正文。\n" * 900) + "END"
    long = bridge_outbox._answer_fragments(record, source, route)
    stable = bridge_outbox._answer_fragments(record, source, route)
    if mutate:
        long.append(dict(long[0]))
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp)
        delivery = {"version": 1, "answers": {"a": {"fragments": {
            "f1": {"acked": True, "message_id": "om_1"},
        }}}}
        persisted = bridge_outbox.save_answer_state(state, "bot", delivery)
        restored = bridge_outbox.load_answer_state(state, "bot")
        active = turn_delivery_guard.activate(
            state, "bot", {"kind": "p2a-ext", "dest": "oc_group", "at": "ou_human"},
            session="s",
        )
        try:
            turn_delivery_guard.guard_outbound(
                state, "bot", "oc_group", bridge_session="bot",
            )
            blocked = False
        except RuntimeError:
            blocked = True
        override = turn_delivery_guard.guard_outbound(
            state, "bot", "oc_group", bridge_session="bot", proactive=True,
        )
    return [
        ("short answer one fragment", len(short) == 1 and short[0]["rendered"] == "short"),
        ("long answer lossless and bounded",
         "".join(item["content"] for item in long) == source
         and all(len(item["rendered"]) <= bridge_outbox.CARD_BUDGET for item in long)),
        ("stable unique fragment IDs",
         [item["fragment_id"] for item in long] == [item["fragment_id"] for item in stable]
         and len({item["fragment_id"] for item in long}) == len(long)),
        ("durable ACK and active-turn guard",
         persisted and restored == delivery and active.get("active") is True
         and blocked and override["reason"] == "proactive-override"),
    ]


def _service_checks() -> list[tuple[str, bool]]:
    desired = service_installer.desired_state(
        ROOT, "MACHINE\\user", ROOT / "pythonw.exe", ROOT / "wmux" / "wmux.exe", 1,
    )
    return [
        ("stable wmux login entry", desired["wmux_run"]["value"].endswith('wmux\\wmux.exe"')),
        ("single bridge scheduled task", "feishu_bridge.py" in desired["bridge_task"]["arguments"]),
        ("no second watchdog task", desired["legacy_watchdog_task"] == {"exists": False, "enabled": False}),
        ("layered service doctor", callable(service_doctor.evaluate)),
        ("cron watchdog registration history assets", all((ROOT / "feishu" / name).is_file() for name in (
            "bridge_cron.py", "bridge_watchdog.py", "registration_monitor.py", "bridge_history.py",
        ))),
    ]


def _doc_checks() -> list[tuple[str, bool]]:
    role = (ROOT / "docs/ROLE-010-link16-deployment-engineer.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/SPEC-210-outbound-delivery.md").read_text(encoding="utf-8")
    tools = (ROOT / "TOOLS.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents/skills/feishu/SKILL.md").read_text(encoding="utf-8")
    return [
        ("runtime-neutral deployment role", "AGENTS.md" in role and "CLAUDE.md" in role),
        ("delivery SSOT routed from architecture", "p2a-ext" in spec and "fragment_id" in spec
         and "SPEC-210" in (ROOT / "docs/ARCH-110-feishu-bridge.md").read_text(encoding="utf-8")),
        ("repo skill and tool index expose guard/history", "--proactive" in skill
         and "bridge-outbound" in tools and "私人 workflow" in role),
    ]


def evaluate(*, mutate_fragments=False) -> dict:
    dimensions = [
        _dimension("self_contained", _self_contained_checks()),
        _dimension("routing", asyncio.run(_route_checks_async())),
        _dimension("fragment_dedup", _fragment_checks(mutate=mutate_fragments)),
        _dimension("services", _service_checks()),
        _dimension("docs", _doc_checks()),
    ]
    score = sum(row["score"] for row in dimensions)
    maximum = sum(row["max"] for row in dimensions)
    return {"score": score, "max": maximum, "passed": score == maximum, "dimensions": dimensions}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true",
                        help="inject one duplicate fragment and prove the evaluator rejects it")
    args = parser.parse_args(argv)
    baseline = evaluate()
    output = {"baseline": baseline}
    if args.self_test:
        mutation = evaluate(mutate_fragments=True)
        caught = baseline["passed"] and not mutation["passed"]
        output.update({"mutation": mutation, "self_test_caught": caught})
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if caught else 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if baseline["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
