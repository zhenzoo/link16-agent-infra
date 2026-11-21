#!/usr/bin/env python3
"""Sanitize Antigravity CLI (agy) transcripts into Link16's milestone-v1 contract.

Private ``thinking`` blocks, raw shell commands, tool arguments/outputs, and raw
provider error messages never enter public progress cards, final answers, or
handoff excerpts. When compact ``transcript.jsonl`` marks ``"content"`` inside
``truncated_fields``, the untruncated public content is resolved from the
sibling ``transcript_full.jsonl`` by matching ``step_index`` (falling back to
line index).
"""
from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path

from bridge_events import (
    MilestoneAccumulator,
    _literal_command_paths,
    _ordered_unique,
    _safe_relative_path,
)

CONTRACT = "milestone-v1"

_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
_TRAILING_META_RE = re.compile(
    r"\s*<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>.*$",
    re.DOTALL,
)

# Canonical Antigravity CLI tool name -> (tool_family, display program).
_TOOLS = {
    "read_file": ("read", "读取"),
    "view_file": ("read", "读取"),
    "list_dir": ("read", "目录"),
    "find_by_name": ("search", "文件搜索"),
    "grep_search": ("search", "内容搜索"),
    "search_web": ("search", "Web 搜索"),
    "write_file": ("file_change", "文件修改"),
    "write_to_file": ("file_change", "文件修改"),
    "edit_file": ("file_change", "文件修改"),
    "replace_file_content": ("file_change", "文件修改"),
    "multi_replace_file_content": ("file_change", "文件修改"),
    "run_command": ("shell", "Shell"),
    "manage_task": ("shell", "后台任务"),
    "invoke_subagent": ("external", "子智能体"),
    "define_subagent": ("external", "子智能体"),
    "manage_subagents": ("external", "子智能体"),
    "send_message": ("external", "代理消息"),
    "schedule": ("external", "定时任务"),
    "run_workflow": ("external", "工作流"),
    "swarm": ("external", "Swarm"),
    "ask_question": ("external", "提问"),
}

_READ_TOOLS = {"read_file", "view_file", "list_dir", "find_by_name", "grep_search"}
_WRITE_TOOLS = {"write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"}
_PATH_ARG_KEYS = ("Path", "path", "TargetFile", "File", "file_path", "DirectoryPath", "SearchPath")


def strip_user_wrappers(text: object) -> str:
    """Extract the clean user prompt from Antigravity's <USER_REQUEST> envelope."""
    raw = str(text or "")
    match = _USER_REQUEST_RE.search(raw)
    if match:
        return match.group(1).strip()
    return _TRAILING_META_RE.sub("", raw).strip()


def _unquote_arg(value: object) -> str:
    """Antigravity compact transcripts may JSON-encode individual arg strings."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                return decoded
        except ValueError:
            pass
    return text


def _read_jsonl(path: Path) -> list[tuple[int, dict]]:
    rows: list[tuple[int, dict]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line_no, raw in enumerate(text.splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            rows.append((line_no, rec))
    return rows


def _full_content_index(transcript_path: Path, needed_steps: set[int], needed_lines: set[int]) -> tuple[dict[int, str], dict[int, str]]:
    """Read only requested records' ``content`` from sibling transcript_full.jsonl."""
    full_path = transcript_path.with_name("transcript_full.jsonl")
    if not full_path.is_file() or (not needed_steps and not needed_lines):
        return {}, {}
    by_step: dict[int, str] = {}
    by_line: dict[int, str] = {}
    try:
        with full_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, 1):
                if not raw.strip():
                    continue
                if line_no not in needed_lines and not needed_steps:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                step = rec.get("step_index")
                content = rec.get("content")
                if not isinstance(content, str):
                    continue
                if isinstance(step, int) and step in needed_steps:
                    by_step[step] = content
                if line_no in needed_lines:
                    by_line[line_no] = content
    except OSError:
        return {}, {}
    return by_step, by_line


def load_transcript(transcript_path: str | Path) -> list[tuple[int, dict]]:
    """Load transcript records, hydrating truncated public ``content`` when needed."""
    path = Path(transcript_path)
    rows = _read_jsonl(path)
    if not rows:
        return rows
    needed_steps: set[int] = set()
    needed_lines: set[int] = set()
    for line_no, rec in rows:
        trunc = rec.get("truncated_fields")
        if isinstance(trunc, list) and "content" in trunc and rec.get("type") in {"PLANNER_RESPONSE", "USER_INPUT"}:
            if isinstance(rec.get("step_index"), int):
                needed_steps.add(rec["step_index"])
            needed_lines.add(line_no)
    if not needed_steps and not needed_lines:
        return rows
    by_step, by_line = _full_content_index(path, needed_steps, needed_lines)
    hydrated: list[tuple[int, dict]] = []
    for line_no, rec in rows:
        trunc = rec.get("truncated_fields")
        if isinstance(trunc, list) and "content" in trunc and rec.get("type") in {"PLANNER_RESPONSE", "USER_INPUT"}:
            step = rec.get("step_index")
            full_text = by_step.get(step) if isinstance(step, int) else None
            if full_text is None:
                full_text = by_line.get(line_no)
            if isinstance(full_text, str):
                rec = {**rec, "content": full_text}
        hydrated.append((line_no, rec))
    return hydrated


def _is_user_turn_start(rec: dict) -> bool:
    return rec.get("type") == "USER_INPUT"


def _current_turn_slice(rows: list[tuple[int, dict]]) -> tuple[int, list[tuple[int, dict]]]:
    """Return (user_step_index, slice_of_records_from_last_user_input)."""
    last_idx = -1
    for idx, (_line_no, rec) in enumerate(rows):
        if _is_user_turn_start(rec):
            last_idx = idx
    if last_idx < 0:
        return 0, rows
    line_no, user_rec = rows[last_idx]
    step_idx = user_rec.get("step_index")
    return (int(step_idx) if isinstance(step_idx, int) else line_no), rows[last_idx:]


def _tool_event_payload(name: str, args: dict | None, workspace_root: str | Path | None) -> tuple[str, dict]:
    tool_name = str(name or "").strip()
    family, program = _TOOLS.get(tool_name, ("external", "工具"))
    payload: dict = {"tool_family": family, "program": program, "status": "completed"}
    args = args if isinstance(args, dict) else {}
    if workspace_root:
        if tool_name in _READ_TOOLS or tool_name in _WRITE_TOOLS:
            paths = []
            for key in _PATH_ARG_KEYS:
                raw = _unquote_arg(args.get(key))
                rel = _safe_relative_path(raw, workspace_root)
                if rel:
                    paths.append(rel)
            paths = _ordered_unique(paths)
            if paths:
                if tool_name in {"write_file", "write_to_file"}:
                    payload["write_paths"] = paths
                elif tool_name in _WRITE_TOOLS:
                    payload["write_paths"] = paths
                else:
                    payload["access_paths"] = paths
        elif tool_name == "run_command":
            cmd = _unquote_arg(args.get("CommandLine") or args.get("command"))
            paths = _literal_command_paths(cmd, workspace_root)
            if paths:
                payload["access_paths"] = paths
    return program, payload


def build_progress_record(
    transcript_path: str | Path | None,
    *,
    conversation_id: str,
    workspace_root: str | Path | None = None,
    route: dict | None = None,
    turn_key: str | None = None,
    workline_gate: str | None = None,
    fallback_tool: dict | None = None,
) -> dict | None:
    """Build one sanitized milestone-v1 progress record for the active turn."""
    cid = str(conversation_id or "agy")
    rows = load_transcript(transcript_path) if transcript_path else []
    user_step, turn_rows = _current_turn_slice(rows)
    turn_id = f"agy:{cid}:{user_step}"
    acc = MilestoneAccumulator()
    acc.turn = turn_id
    applied = 0
    usage: dict[str, int] = {}

    for line_no, rec in turn_rows:
        if rec.get("type") != "PLANNER_RESPONSE":
            continue
        step = rec.get("step_index")
        step_id = step if isinstance(step, int) else line_no
        for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
            val = rec.get(key)
            if isinstance(val, int) and val >= 0:
                usage[key] = usage.get(key, 0) + val
        tool_calls = rec.get("tool_calls")
        has_tools = isinstance(tool_calls, list) and len(tool_calls) > 0
        if not has_tools:
            # Final non-tool response belongs to Stop/answer only, never progress.
            continue
        # Only explicit public content (NEVER private `thinking`) enters commentary.
        content = str(rec.get("content") or "").strip()
        if content:
            if acc.apply({
                "event_id": f"agy:{cid}:{step_id}:commentary",
                "event_type": "commentary",
                "turn": turn_id,
                "label": "💬 " + content,
                "payload": {"text": content},
            }):
                applied += 1
        for idx, call in enumerate(tool_calls):
            if not isinstance(call, dict):
                continue
            label, payload = _tool_event_payload(
                call.get("name"), call.get("args"), workspace_root
            )
            if acc.apply({
                "event_id": f"agy:{cid}:{step_id}:tool:{idx}",
                "event_type": "tool",
                "turn": turn_id,
                "label": label,
                "payload": payload,
            }):
                applied += 1

    if not applied and isinstance(fallback_tool, dict) and fallback_tool.get("name"):
        label, payload = _tool_event_payload(
            fallback_tool.get("name"), fallback_tool.get("args"), workspace_root
        )
        step_id = fallback_tool.get("stepIdx") or fallback_tool.get("id") or "0"
        if acc.apply({
            "event_id": f"agy:{cid}:hook:{step_id}",
            "event_type": "tool",
            "turn": turn_id,
            "label": label,
            "payload": payload,
        }):
            applied += 1

    if not applied:
        return None
    record = acc.progress_record(session=cid, route=deepcopy(route) if route else None)
    record["runtime"] = "agy"
    record["root_turn"] = turn_id
    record["turn"] = turn_id
    if usage:
        record["usage"] = usage
    if turn_key:
        record["turn_key"] = turn_key
    if workline_gate:
        record["workline_gate"] = workline_gate
    return record


def build_answer_record(
    transcript_path: str | Path | None,
    *,
    conversation_id: str,
    termination_reason: str = "NO_TOOL_CALL",
    route: dict | None = None,
    turn_key: str | None = None,
) -> tuple[int, dict]:
    """Extract the final public answer for the current turn from transcript.

    Private ``thinking``, tool outputs, and raw error strings are excluded.
    """
    cid = str(conversation_id or "agy")
    rows = load_transcript(transcript_path) if transcript_path else []
    user_step, turn_rows = _current_turn_slice(rows)
    anchor = f"agy:{cid}:{user_step}"

    final_parts: list[str] = []
    for _line_no, rec in turn_rows:
        if rec.get("type") != "PLANNER_RESPONSE":
            continue
        tool_calls = rec.get("tool_calls")
        if isinstance(tool_calls, list) and len(tool_calls) > 0:
            continue
        text = str(rec.get("content") or "").strip()
        if text:
            final_parts.append(text)

    reason = str(termination_reason or "").strip().upper()
    joined = "\n\n".join(final_parts).strip()
    if reason in {"", "NO_TOOL_CALL", "TERMINAL_STEP_TYPE", "INJECTED_RESPONSE"} and joined:
        answer_text = joined + "\n\n---\n✅ 已完成"
    elif reason == "USER_CANCELED":
        answer_text = "⏹ Antigravity 本轮已取消。"
    elif reason == "ERROR":
        answer_text = "⚠️ Antigravity 本轮执行出错，未完成。"
    elif reason in {"MAX_INVOCATIONS", "MAX_FORCED_INVOCATIONS"}:
        answer_text = "⚠️ Antigravity 已达到本轮最大调用步数上限，未完成。"
    elif reason == "MAX_TOKEN_BUDGET_EXCEEDED":
        answer_text = "⚠️ Antigravity 已超出本轮 Token 预算上限，未完成。"
    elif reason == "TERMINAL_CUSTOM_HOOK":
        answer_text = "⚠️ Antigravity 本轮被自定义 Hook 终止，未完成。"
    else:
        answer_text = "⚠️ Antigravity 已结束本轮，但没有可验证的最终回复。"

    record = {
        "kind": "answer",
        "runtime": "agy",
        "session": cid,
        "anchor": anchor,
        "text": answer_text,
        "ts": int(time.time()),
    }
    if route:
        record["route"] = deepcopy(route)
    if turn_key:
        record["turn_key"] = turn_key
    return user_step, record


def activity_from_transcript(transcript_path: str | Path) -> dict:
    """Compute Watchdog R8 live activity strictly from structured transcript rows."""
    path = Path(transcript_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    rows = _read_jsonl(path)
    key = None
    ended = True
    pending: list[str] = []
    for line_no, rec in rows:
        kind = rec.get("type")
        if kind == "USER_INPUT":
            step = rec.get("step_index")
            key = (path.name, step if isinstance(step, int) else line_no)
            ended = False
            pending = []
        elif kind == "PLANNER_RESPONSE":
            tool_calls = rec.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                for call in tool_calls:
                    if isinstance(call, dict) and call.get("name"):
                        pending.append(str(call["name"]))
            elif rec.get("status") == "DONE" and not pending:
                ended = True
        elif kind == "GENERIC" and pending:
            pending.pop(0)
    if key is None or ended:
        return {"state": "idle", "key": None, "reason": "回合已结束，等待下一步指令"}
    if "ask_question" in pending:
        return {"state": "waiting", "key": None, "reason": "等待用户回答或确认"}
    return {
        "state": "active",
        "key": key,
        "tool_running": bool(pending),
        "reason": "工具尚未结束" if pending else "当前回合执行中",
    }


def public_transcript_messages(transcript_path: str | Path) -> list[dict]:
    """Extract only public user/assistant text for bounded handoff context."""
    rows = load_transcript(transcript_path)
    messages: list[dict] = []
    for line_no, rec in rows:
        kind = rec.get("type")
        if kind == "USER_INPUT":
            text = strip_user_wrappers(rec.get("content"))
            if text:
                messages.append({"line": line_no, "role": "user", "text": text})
        elif kind == "PLANNER_RESPONSE":
            text = str(rec.get("content") or "").strip()
            if text:
                messages.append({"line": line_no, "role": "assistant", "text": text})
    return messages
