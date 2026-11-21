#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified Feishu bridge lifecycle hook for Antigravity CLI (agy).

Installed into ``~/.gemini/config/hooks.json`` under the ``link16-bridge`` key.
Strictly env-scoped to ``FEISHU_BRIDGE_SESSION``: ordinary interactive ``agy``
sessions exit immediately with a no-op JSON response and zero side effects.

Events handled:
  - SessionStart   -> bind exact conversationId & transcriptPath for this bot.
  - PreInvocation  -> on invocationNum==0, lock turn route, confirm inbox,
                      begin workline turn, and inject Link16 bridge context.
  - PreToolUse     -> deny ``ask_question`` picker in Feishu-hosted sessions.
  - PostToolUse    -> append sanitized ``milestone-v1`` progress to outbox.
  - Stop           -> enforce workline/delivery stop gate once, then emit
                      deduplicated final ``answer`` and clear active route.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FEISHU_DIR = Path(__file__).resolve().parents[1]
HOOKS_DIR = Path(__file__).resolve().parent
for _p in (str(FEISHU_DIR), str(HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agy_events  # noqa: E402
import bridge_env  # noqa: E402
import bridge_inbox  # noqa: E402
import bridge_injection  # noqa: E402
import session_work  # noqa: E402
import turn_delivery_guard  # noqa: E402

AGY_BRIDGE_POLICY = (
    "[Link16 Antigravity bridge policy] 本会话由飞书/wmux 托管。"
    "1) 绝对不要调用 ask_question 弹终端选择题；授权范围内的可逆决定直接执行，"
    "确需主人决定时改用普通文字说明选项与推荐并回复。"
    "2) 私有 thinking 不会展示在飞书卡片中；多步任务进行中请在调用工具时输出简短公开进度"
    "（当前阶段/结论/ETA），它们会实时更新到进行中卡片。"
)


def _read_stdin_json() -> dict:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read()
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    if not text.strip():
        return {}
    data = json.loads(text.lstrip("\ufeff"))
    return data if isinstance(data, dict) else {}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _noop(event: str) -> None:
    if event == "PreToolUse":
        _emit({"decision": "allow"})
    elif event == "Stop":
        _emit({"decision": "stop"})
    else:
        _emit({})


def _state_dir() -> Path:
    d = os.environ.get("FEISHU_BRIDGE_OUTBOX_DIR")
    if d and os.path.isdir(d):
        return Path(d)
    return Path(__file__).resolve().parents[2] / "_autopilot"


def _session_binding_path(sd: Path, bot: str) -> Path:
    return sd / f"bridge-agy-session-{bot}.json"


def _stop_cursor_path(sd: Path, bot: str) -> Path:
    return sd / f"bridge-agy-stop-cursor-{bot}.json"


def _save_session_binding(sd: Path, bot: str, inp: dict) -> None:
    cid = str(inp.get("conversationId") or "").strip()
    tp = str(inp.get("transcriptPath") or "").strip()
    if not cid and not tp:
        return
    record = {
        "bot": bot,
        "profile": os.environ.get("LINK16_AGENT_PROFILE") or "",
        "cwd": str(inp.get("cwd") or ""),
        "conversation_id": cid,
        "transcript_path": tp,
        "closed": False,
        "ts": int(time.time()),
    }
    try:
        sd.mkdir(parents=True, exist_ok=True)
        bridge_injection.atomic_write_json(_session_binding_path(sd, bot), record)
    except OSError:
        pass


def _append_outbox(sd: Path, bot: str, record: dict) -> None:
    outbox = sd / f"bridge-outbox-{bot}.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    with outbox.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _already_stopped(sd: Path, bot: str, cid: str, user_step: int) -> bool:
    path = _stop_cursor_path(sd, bot)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and str(data.get("conversation_id") or "") == str(cid)
        and data.get("user_step") == user_step
    )


def _save_stop_cursor(sd: Path, bot: str, cid: str, user_step: int) -> None:
    try:
        bridge_injection.atomic_write_json(
            _stop_cursor_path(sd, bot),
            {"conversation_id": str(cid), "user_step": user_step, "ts": int(time.time())},
        )
    except OSError:
        pass


def _handle_pre_invocation(sd: Path, bot: str, inp: dict) -> None:
    _save_session_binding(sd, bot, inp)
    inv_num = inp.get("invocationNum")
    if inv_num not in (0, None):
        _emit({})
        return
    prompt = agy_events.strip_user_wrappers(inp.get("lastUserInput"))
    cid = str(inp.get("conversationId") or "")
    bridge_inbox.confirm_prompt(sd, bot, prompt, cid or None)
    route = turn_delivery_guard.route_from_prompt(prompt)
    active = None
    try:
        gate_on = session_work.enabled()
        hint = session_work.resolve(bot, sd).get("project") or ""
        active = turn_delivery_guard.activate(
            sd, bot, route, session=cid or None,
            metadata={"workline_gate": session_work.GATE_CONTRACT} if gate_on else None,
        )
        if gate_on:
            session_work.begin_turn(
                bot, active["turn_key"], session=cid or None, runtime="agy",
                project_hint=hint, prompt_digest=bridge_inbox.prompt_digest(prompt),
                state_dir=sd,
            )
    except (OSError, ValueError):
        pass

    parts = [AGY_BRIDGE_POLICY]
    try:
        import bridge_userprompt as up
        profile = up._profile()
        work_ctx = up._work_context(bot, sd, active)
        if work_ctx:
            parts.append(work_ctx)
        rules_inp = {
            "session_id": cid,
            "cwd": inp.get("cwd") or "",
            "transcript_path": inp.get("transcriptPath") or "",
        }
        rules_ctx = up._rules_context(bot, sd, rules_inp, profile)
        if rules_ctx:
            parts.append(rules_ctx)
        delivery_ctx = up._delivery_context(bot, sd)
        if delivery_ctx:
            parts.append(delivery_ctx)
    except Exception:  # noqa: BLE001 — auxiliary context must never break turn start
        pass

    context = "\n\n".join(p for p in parts if p)
    if context:
        _emit({
            "injectSteps": [
                {"systemMessage": {"systemMessage": context}},
            ]
        })
    else:
        _emit({})


def _handle_pre_tool_use(inp: dict) -> None:
    tool_call = inp.get("toolCall") if isinstance(inp.get("toolCall"), dict) else {}
    if tool_call.get("name") == "ask_question":
        _emit({
            "decision": "deny",
            "reason": (
                "飞书/wmux 托管会话禁止使用 ask_question 和终端选择题。"
                "请自主处理授权内的可逆决定；确需主人决定时改用普通文字说明，"
                "并继续不依赖该决定的工作。"
            ),
        })
        return
    _emit({"decision": "allow"})


def _handle_post_tool_use(sd: Path, bot: str, inp: dict) -> None:
    _save_session_binding(sd, bot, inp)
    cid = str(inp.get("conversationId") or "")
    tp = inp.get("transcriptPath")
    workspace = inp.get("cwd") or ((inp.get("workspacePaths") or [None])[0])
    tool_call = inp.get("toolCall") if isinstance(inp.get("toolCall"), dict) else {}
    fallback = {**tool_call, "stepIdx": inp.get("stepIdx")} if tool_call else None

    route = None
    turn_key = None
    workline_gate = None
    try:
        active_route = turn_delivery_guard.read_route(sd, bot)
        if isinstance(active_route, dict):
            route = turn_delivery_guard.public_route(active_route)
            turn_key = active_route.get("turn_key")
            workline_gate = active_route.get("workline_gate")
    except OSError:
        pass

    rec = agy_events.build_progress_record(
        tp,
        conversation_id=cid,
        workspace_root=workspace,
        route=route,
        turn_key=turn_key,
        workline_gate=workline_gate,
        fallback_tool=fallback,
    )
    if rec:
        try:
            _append_outbox(sd, bot, rec)
        except OSError:
            pass
    _emit({})


def _handle_stop(sd: Path, bot: str, inp: dict) -> None:
    _save_session_binding(sd, bot, inp)
    cid = str(inp.get("conversationId") or "")
    tp = inp.get("transcriptPath")
    term_reason = str(inp.get("terminationReason") or "NO_TOOL_CALL")
    continuation_active = bool(inp.get("continuationActive"))

    active_route = None
    try:
        active_route = turn_delivery_guard.read_route(sd, bot)
    except OSError:
        active_route = None

    turn_key = str((active_route or {}).get("turn_key") or "")
    # 1. Synchronous feishu-workline + stuck-delivery gate (normal completion only).
    if (
        session_work.enabled()
        and isinstance(active_route, dict)
        and active_route.get("workline_gate") == session_work.GATE_CONTRACT
        and term_reason == "NO_TOOL_CALL"
        and not continuation_active
    ):
        reason = session_work.request_stop_repair(bot, turn_key, state_dir=sd)
        if not reason:
            try:
                from bridge_workline_stop import _stuck_delivery
                reason = _stuck_delivery(bot, turn_key, sd)
            except Exception:  # noqa: BLE001
                reason = ""
        if reason:
            _emit({"decision": "continue", "reason": reason})
            return

    # 2. If active turn is already closed (e.g. peer A2A sealed), do not re-emit.
    if isinstance(active_route, dict) and active_route.get("active") is False:
        _emit({"decision": "stop"})
        return

    public_route = (
        turn_delivery_guard.public_route(active_route)
        if isinstance(active_route, dict)
        else turn_delivery_guard.route_from_prompt(
            agy_events.strip_user_wrappers(inp.get("lastUserInput"))
        )
    )
    user_step, answer = agy_events.build_answer_record(
        tp,
        conversation_id=cid,
        termination_reason=term_reason,
        route=public_route,
        turn_key=turn_key or None,
    )
    if cid and _already_stopped(sd, bot, cid, user_step):
        _emit({"decision": "stop"})
        return

    try:
        _append_outbox(sd, bot, answer)
        if cid:
            _save_stop_cursor(sd, bot, cid, user_step)
        if turn_key:
            turn_delivery_guard.compare_and_clear(sd, bot, turn_key)
    except OSError:
        pass
    _emit({"decision": "stop"})


def main() -> int:
    try:
        inp = _read_stdin_json()
    except Exception:  # noqa: BLE001
        inp = {}
    event = str(inp.get("hookEventName") or "")
    bot = os.environ.get("FEISHU_BRIDGE_SESSION")
    if not bot:
        _noop(event)
        return 0
    if bridge_env.nested_agent(inp):
        _noop(event)
        return 0
    sd = _state_dir()
    if event == "SessionStart":
        _save_session_binding(sd, bot, inp)
        _emit({})
    elif event == "PreInvocation":
        _handle_pre_invocation(sd, bot, inp)
    elif event == "PreToolUse":
        _handle_pre_tool_use(inp)
    elif event == "PostToolUse":
        _handle_post_tool_use(sd, bot, inp)
    elif event == "Stop":
        _handle_stop(sd, bot, inp)
    else:
        _noop(event)
    return 0


if __name__ == "__main__":
    try:
        bridge_env.force_utf8_std()
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
