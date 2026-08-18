#!/usr/bin/env python3
"""Finish PLAN-915 activation after the current Feishu turn is delivered.

This helper exists because replacing the active bot worker during its own turn
would kill the response that authorized the operation.  It waits for that
turn's Stop answer record, then touches only the named workspace/bot.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEISHU = ROOT / "feishu"
sys.path.insert(0, str(FEISHU))

import feishu_bridge  # noqa: E402
import wmux_session  # noqa: E402


COMMENTARY_MARKER = "PLAN915_LIVE_COMMENTARY"
FINAL_MARKER = "PLAN915_LIVE_FINAL"
RAW_MARKER = "PLAN915_RAW_COMMAND_SHOULD_NOT_APPEAR"


def _records(path: Path):
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _wait(predicate, timeout, label):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.5)
    raise TimeoutError(label)


def _write_status(path: Path, **payload):
    payload["ts"] = int(time.time())
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _notify(bot: str, text: str, log):
    result = subprocess.run(
        [sys.executable, str(FEISHU / "feishu_bridge.py"), "send", "--bot", bot, "--text", text, "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log.write((result.stdout or "") + (result.stderr or ""))
    log.flush()


def _surface_leaks(surfaces: dict[str, str], markers) -> tuple[dict, int]:
    report = {}
    total = 0
    for surface, text in surfaces.items():
        counts = {marker: str(text or "").count(marker) for marker in markers}
        counts = {marker: count for marker, count in counts.items() if count}
        report[surface] = counts
        total += sum(counts.values())
    return report, total


def run(args) -> int:
    state_dir = FEISHU / "_state"
    outbox = state_dir / f"bridge-outbox-{args.bot}.jsonl"
    ledger = state_dir / f"bridge-event-ledger-{args.bot}.jsonl"
    receipts = state_dir / f"bridge-receipts-{args.bot}.jsonl"
    status = state_dir / f"plan915-activation-{args.bot}.json"
    log_path = state_dir / f"plan915-activation-{args.bot}.log"
    start_ts = time.time()
    with open(log_path, "a", encoding="utf-8") as log:
        try:
            _write_status(status, status="waiting_for_current_answer", old_thread=args.current_thread)
            _wait(
                lambda: next((
                    record for record in _records(outbox)
                    if record.get("kind") == "answer"
                    and record.get("session") == args.current_thread
                    and float(record.get("ts") or 0) >= start_ts
                ), None),
                args.timeout,
                "current Stop answer did not reach outbox",
            )
            _write_status(status, status="closing_old_workspace", workspace=args.old_workspace)
            wmux_session.close(args.old_workspace)

            bots = {bot["name"]: bot for bot in feishu_bridge.load_bots()}
            bot = bots[args.bot]
            if bot.get("codex_transport") != "app-server-canary":
                raise RuntimeError("target bot is missing codex_transport=app-server-canary")
            ws, pty, created, _ = feishu_bridge.ensure_session(bot)
            if not created:
                raise RuntimeError("target worker was not recreated")
            _write_status(status, status="canary_worker_ready", workspace=ws, pty=pty)

            prompt = (
                f"This is the approved PLAN-915 live canary. First send a commentary message starting with {COMMENTARY_MARKER}. "
                "Call update_plan with three steps. Run three read-only tool actions, including a PowerShell command that prints "
                f"{RAW_MARKER}. Spawn one read-only subagent for a trivial verification and wait for it. Do not edit files or use network. "
                "Then mark the plan complete and finish with a concise answer starting with "
                f"{FINAL_MARKER}.\n\n[飞书 from=host to={args.bot} via=DM route=p2a]"
            )
            if not feishu_bridge._inject(pty, ws, prompt):
                raise RuntimeError("canary prompt did not leave the composer")
            _write_status(status, status="canary_injected", workspace=ws, pty=pty)

            event = _wait(
                lambda: next((
                    record for record in _records(ledger)
                    if record.get("event_type") == "commentary"
                    and COMMENTARY_MARKER in str((record.get("payload") or {}).get("text") or "")
                    and float(record.get("ts") or 0) >= start_ts
                ), None),
                args.timeout,
                "typed commentary missing from ledger",
            )
            root_turn = event.get("root_turn")
            answer = _wait(
                lambda: next((
                    record for record in _records(outbox)
                    if record.get("kind") == "answer"
                    and FINAL_MARKER in str(record.get("text") or "")
                    and float(record.get("ts") or 0) >= start_ts
                ), None),
                args.timeout,
                "canary final missing from outbox",
            )
            progress = [
                record for record in _records(outbox)
                if record.get("contract") == "milestone-v1" and record.get("root_turn") == root_turn
            ]
            turn_ledger = [
                record for record in _records(ledger)
                if record.get("root_turn") == root_turn and float(record.get("ts") or 0) >= start_ts
            ]
            progress_state_path = state_dir / f"bridge-progress-state-{args.bot}.json"
            try:
                progress_state = progress_state_path.read_text(encoding="utf-8")
            except OSError:
                progress_state = ""
            commentary_count = sum(
                1 for record in _records(ledger)
                if record.get("root_turn") == root_turn and record.get("event_type") == "commentary"
            )
            final_count = sum(
                1 for record in _records(outbox)
                if record.get("kind") == "answer" and FINAL_MARKER in str(record.get("text") or "")
            )
            leak_report, raw_leaks = _surface_leaks({
                "ledger": json.dumps(turn_ledger, ensure_ascii=False),
                "progress_outbox": json.dumps(progress, ensure_ascii=False),
                "progress_state": progress_state,
            }, (RAW_MARKER, "reasoning"))
            _wait(
                lambda: any(
                    float(record.get("ts") or 0) >= float(answer.get("ts") or 0)
                    and record.get("delivered")
                    for record in _records(receipts)
                ),
                90,
                "no delivered receipt after canary final",
            )
            result = {
                "status": "complete",
                "workspace": ws,
                "pty": pty,
                "root_turn": root_turn,
                "commentary_events": commentary_count,
                "progress_snapshots": len(progress),
                "raw_leaks": raw_leaks,
                "leak_report": leak_report,
                "final_answers": final_count,
            }
            _write_status(status, **result)
            _notify(
                args.bot,
                "PLAN-915 自动验收完成\n\n"
                f"- typed commentary：{commentary_count} 条\n"
                f"- milestone snapshots：{len(progress)} 条\n"
                f"- raw command / reasoning 泄漏：{raw_leaks}\n"
                f"- final answer：{final_count} 条\n"
                "- 范围：仅 tb25-link16-codex；其他 bot 未重启、未切换\n\n"
                "请直接看上一张真实进行中卡和独立最终答案卡做肉眼验收。",
                log,
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            _write_status(status, status="failed", error=str(exc))
            log.write(f"FAILED: {exc}\n")
            log.flush()
            _notify(args.bot, f"PLAN-915 canary 激活失败：{exc}", log)
            return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True)
    parser.add_argument("--current-thread", required=True)
    parser.add_argument("--old-workspace", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    try:                       # PLAN-929：GBK 机器上 ✅❌ 打不出来会崩掉整条链，先把输出流顶成 UTF-8
        from bridge_env import force_utf8_std as _f8; _f8()
    except Exception:          # noqa: BLE001 — 顶不动也不许挡住本命令
        pass
    main()
