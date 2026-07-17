#!/usr/bin/env python3
"""Cut over one Codex bot and close PLAN-916 with a live typed-event canary.

The helper starts before the authorizing Feishu turn ends.  It waits for that
exact answer and its matching delivery receipt before replacing the worker,
then observes the new turn through the ledger, outbox, restart state, and
delivery receipts.  Only the explicitly named bot/workspace is touched.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEISHU = ROOT / "feishu"
sys.path.insert(0, str(FEISHU))

import feishu_bridge  # noqa: E402
import wmux_session  # noqa: E402
from activate_plan915_canary import (  # noqa: E402
    _notify,
    _records,
    _surface_leaks,
    _wait,
    _write_status,
)


COMMENTARY_MARKER = "PLAN916_LIVE_COMMENTARY"
SPLIT_MARKER = "PLAN916_LIVE_SPLIT"
FINAL_MARKER = "PLAN916_LIVE_FINAL"
RAW_MARKER = "PLAN916_RAW_COMMAND_SHOULD_NOT_APPEAR"
ARTIFACT_REL = "feishu/_state/plan916-canary-artifact.txt"
SAFE_PROGRAMS = {"rg", "Get-Content", "Python"}


def _matching_answer_receipt(records, answer, *, since=0):
    """Return the receipt for one short answer, never an unrelated progress edit."""
    text = str((answer or {}).get("text") or "")
    answer_ts = float((answer or {}).get("ts") or 0)
    for record in records:
        if not record.get("delivered"):
            continue
        if record.get("kind") not in {"new_card", "group_text"}:
            continue
        if float(record.get("ts") or 0) < max(float(since or 0), answer_ts):
            continue
        try:
            if int(record.get("len") or -1) != len(text):
                continue
        except (TypeError, ValueError):
            continue
        return record
    return None


def _tool_metrics(progress_records):
    """Summarize the latest structured milestone snapshot for assertions."""
    latest = progress_records[-1] if progress_records else {}
    steps = latest.get("steps") or []
    tools = [step for step in steps if step.get("kind") == "tool"]
    programs = set()
    for step in tools:
        for item in step.get("tool_types") or []:
            if item.get("program"):
                programs.add(str(item["program"]))
    return {
        "steps": steps,
        "tool_count": sum(int(step.get("tool_count") or 0) for step in tools),
        "programs": sorted(programs),
        "access_total": sum(int(step.get("access_total") or 0) for step in tools),
        "create_paths": sorted({
            str(path)
            for step in tools
            for path in (step.get("create_paths") or [])
        }),
        "labels": "\n".join(str(step.get("label") or "") for step in steps),
        "plan_total": max(
            [int(step.get("plan_total") or 0) for step in steps if step.get("kind") == "plan"] or [0]
        ),
        "plan_completed": max(
            [int(step.get("plan_completed") or 0) for step in steps if step.get("kind") == "plan"] or [0]
        ),
    }


def _canary_prompt(bot: str) -> str:
    files = (
        "AGENTS.md, TOOLS.md, feishu/agent_runtime.py, feishu/bridge_events.py, "
        "feishu/bridge_outbox.py, feishu/codex_app_server_worker.py, "
        "docs/ARCH-110-feishu-bridge.md, docs/PLAN-916-feishu-tool-observability.md"
    )
    return (
        "This is the approved PLAN-916 live canary. Do not use network and do not modify any file except the disposable artifact named below. "
        f"First send commentary beginning exactly with {COMMENTARY_MARKER}. Call update_plan with exactly three steps. "
        "Then run one adjacent tool block with no commentary between its actions: "
        "(1) use rg to search for CONTRACT in feishu/bridge_events.py, feishu/bridge_outbox.py, and feishu/codex_app_server_worker.py; "
        f"(2) use one PowerShell Get-Content action to read only the first line of each of these eight safe files: {files}; "
        f"the same PowerShell command must also print {RAW_MARKER}; "
        "(3) run Python syntax validation for feishu/bridge_events.py; "
        f"(4) use apply_patch to add {ARTIFACT_REL} containing only PLAN916_CANARY_ARTIFACT. Leave it for the harness to clean up. "
        f"After those tools, send commentary beginning exactly with {SPLIT_MARKER}. Mark all three plan steps complete. "
        f"Finish with one concise answer beginning exactly with {FINAL_MARKER}. Never quote the raw marker in commentary or final.\n\n"
        f"[飞书 from=host to={bot} via=DM route=p2a]"
    )


def run(args) -> int:
    state_dir = FEISHU / "_state"
    outbox = state_dir / f"bridge-outbox-{args.bot}.jsonl"
    ledger = state_dir / f"bridge-event-ledger-{args.bot}.jsonl"
    receipts = state_dir / f"bridge-receipts-{args.bot}.jsonl"
    session_path = state_dir / f"bridge-session-{args.bot}.json"
    progress_state_path = state_dir / f"bridge-progress-state-{args.bot}.json"
    status = state_dir / f"plan916-activation-{args.bot}.json"
    log_path = state_dir / f"plan916-activation-{args.bot}.log"
    artifact = ROOT / ARTIFACT_REL
    # Runtime records use whole-second timestamps. Include the current second
    # without widening the window enough to capture an earlier completed turn.
    start_ts = time.time() - 1.0

    with open(log_path, "a", encoding="utf-8") as log:
        try:
            _write_status(
                status,
                status="waiting_for_current_answer",
                old_workspace=args.old_workspace,
                old_pty=args.old_pty,
                current_thread=args.current_thread,
            )
            current_answer = _wait(
                lambda: next((
                    record for record in _records(outbox)
                    if record.get("kind") == "answer"
                    and record.get("session") == args.current_thread
                    and float(record.get("ts") or 0) >= start_ts
                ), None),
                args.timeout,
                "current answer did not reach outbox",
            )
            current_receipt = _wait(
                lambda: _matching_answer_receipt(
                    _records(receipts), current_answer, since=start_ts
                ),
                120,
                "current answer did not receive an exact delivery receipt",
            )

            current_session = feishu_bridge.load_session(args.bot) or {}
            if current_session.get("workspace_id") != args.old_workspace:
                raise RuntimeError("session workspace changed before cutover")
            if args.old_pty and current_session.get("pty") != args.old_pty:
                raise RuntimeError("session pty changed before cutover")

            _write_status(
                status,
                status="closing_old_workspace",
                current_receipt=current_receipt,
            )
            wmux_session.close(args.old_workspace)

            bots = {bot["name"]: bot for bot in feishu_bridge.load_bots()}
            bot = bots.get(args.bot)
            if not bot or bot.get("agent") != "codex":
                raise RuntimeError("target bot is not a registered Codex bot")
            if bot.get("codex_transport") != "app-server-canary":
                raise RuntimeError("target bot is missing codex_transport=app-server-canary")
            ws, pty, created, _ = feishu_bridge.ensure_session(bot)
            if not created or ws == args.old_workspace:
                raise RuntimeError("target worker was not recreated in a new workspace")
            _write_status(status, status="canary_worker_ready", workspace=ws, pty=pty)

            inject_ts = time.time() - 1.0
            prompt = _canary_prompt(args.bot)
            if not feishu_bridge._inject(pty, ws, prompt):
                raise RuntimeError("canary prompt did not leave the composer")
            _write_status(status, status="canary_injected", workspace=ws, pty=pty)

            commentary = _wait(
                lambda: next((
                    record for record in _records(ledger)
                    if record.get("event_type") == "commentary"
                    and COMMENTARY_MARKER in str((record.get("payload") or {}).get("text") or "")
                    and float(record.get("ts") or 0) >= inject_ts
                ), None),
                args.timeout,
                "typed commentary missing from ledger",
            )
            root_turn = commentary.get("root_turn")
            answer = _wait(
                lambda: next((
                    record for record in _records(outbox)
                    if record.get("kind") == "answer"
                    and FINAL_MARKER in str(record.get("text") or "")
                    and float(record.get("ts") or 0) >= inject_ts
                ), None),
                args.timeout,
                "canary final missing from outbox",
            )

            progress_receipt = _wait(
                lambda: next((
                    record for record in _records(receipts)
                    if record.get("delivered")
                    and record.get("kind") == "edit_card"
                    and float(record.get("ts") or 0) >= inject_ts
                ), None),
                120,
                "no delivered progress-card edit in canary window",
            )

            def _canary_progress_state():
                try:
                    value = json.loads(progress_state_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
                return value if value.get("turn") == root_turn else None

            state_payload = _wait(
                _canary_progress_state,
                120,
                "canary turn did not reach durable progress state",
            )

            progress = [
                record for record in _records(outbox)
                if record.get("contract") == "milestone-v1"
                and record.get("root_turn") == root_turn
                and float(record.get("ts") or 0) >= inject_ts
            ]
            turn_ledger = [
                record for record in _records(ledger)
                if record.get("root_turn") == root_turn
                and float(record.get("ts") or 0) >= inject_ts
            ]
            metrics = _tool_metrics(progress)
            missing_programs = SAFE_PROGRAMS - set(metrics["programs"])
            if missing_programs:
                raise RuntimeError(f"missing safe program summaries: {sorted(missing_programs)}")
            if metrics["tool_count"] < 4:
                raise RuntimeError("actual tool count is below the four canary actions")
            if metrics["access_total"] < 8:
                raise RuntimeError("fewer than eight safe access paths were aggregated")
            if ARTIFACT_REL not in metrics["create_paths"]:
                raise RuntimeError("canary artifact is absent from create_paths")
            if "另有 " not in metrics["labels"]:
                raise RuntimeError("path overflow summary is missing")
            if "修改：无" not in metrics["labels"]:
                raise RuntimeError("read-only/write-none summary is missing")
            if metrics["plan_total"] != 3 or metrics["plan_completed"] != 3:
                raise RuntimeError("three-step plan did not reach 3/3")
            if not artifact.exists():
                raise RuntimeError("canary artifact was not created")

            progress_state = json.dumps(state_payload, ensure_ascii=False)
            leak_report, raw_leaks = _surface_leaks({
                "ledger": json.dumps(turn_ledger, ensure_ascii=False),
                "progress_outbox": json.dumps(progress, ensure_ascii=False),
                "progress_state": progress_state,
                "render_inputs": metrics["labels"],
            }, (RAW_MARKER, "reasoning"))
            if raw_leaks:
                raise RuntimeError(f"unsafe marker leaked across surfaces: {leak_report}")

            final_count = sum(
                1 for record in _records(outbox)
                if record.get("kind") == "answer"
                and FINAL_MARKER in str(record.get("text") or "")
                and float(record.get("ts") or 0) >= inject_ts
            )
            if final_count != 1:
                raise RuntimeError(f"expected one final answer, found {final_count}")
            final_receipt = _wait(
                lambda: _matching_answer_receipt(_records(receipts), answer, since=inject_ts),
                120,
                "canary final did not receive an exact delivery receipt",
            )
            artifact.unlink(missing_ok=True)
            result = {
                "status": "complete",
                "workspace": ws,
                "pty": pty,
                "root_turn": root_turn,
                "tool_count": metrics["tool_count"],
                "programs": metrics["programs"],
                "access_total": metrics["access_total"],
                "create_paths": metrics["create_paths"],
                "progress_snapshots": len(progress),
                "raw_leaks": raw_leaks,
                "leak_report": leak_report,
                "final_answers": final_count,
                "current_answer_receipt": current_receipt,
                "progress_receipt": progress_receipt,
                "final_receipt": final_receipt,
            }
            _write_status(status, **result)
            _notify(
                args.bot,
                "PLAN-916 自动验收完成\n\n"
                f"- 工具：{metrics['tool_count']} 次；类型：{', '.join(metrics['programs'])}\n"
                f"- 安全访问路径：{metrics['access_total']} 个；新增路径：{len(metrics['create_paths'])} 个\n"
                f"- ledger / outbox / progress-state / render-input 泄漏：{raw_leaks}\n"
                f"- final answer：{final_count} 条且精确送达\n"
                "- 范围：仅 tb25-link16-codex；其他 bot 未重启、未切换",
                log,
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            artifact.unlink(missing_ok=True)
            _write_status(status, status="failed", error=str(exc))
            log.write(f"FAILED: {exc}\n")
            log.flush()
            _notify(args.bot, f"PLAN-916 canary 激活失败：{exc}", log)
            return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", required=True)
    parser.add_argument("--current-thread", required=True)
    parser.add_argument("--old-workspace", required=True)
    parser.add_argument("--old-pty", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
